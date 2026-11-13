"""Publication check: what it rejects, what it leaves alone, and the regression that a
publisher with a clean tree, a human author and green tests still must not push a commit
whose message carries a Claude co-author trailer."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import SWARM, git, commit  # noqa: E402

sys.path.insert(0, SWARM)
import publish_check  # noqa: E402

GUARD = os.path.join(SWARM, "publish_check.py")
GATE = os.path.join(SWARM, "examples", "publisher-gate.sh")
SESSION = "https://claude.ai/code/session_01PrivateSessionIdXYZ"
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


class Text(unittest.TestCase):
    def rules(self, text):
        return [f["rule"] for f in publish_check.check_text("field", text)]

    def test_claude_and_anthropic_co_authors_are_rejected_in_any_casing(self):
        for line in (TRAILER, "co-authored-by: claude <noreply@anthropic.com>",
                     "CO-AUTHORED-BY:CLAUDE CODE<NOREPLY@ANTHROPIC.COM>", "Co-authored-by: Claude",
                     "  cO-aUtHoReD-bY:   Claude Sonnet 5.5 <someone@example.com>",
                     "Co-authored-by: Helper <bot@Mail.Anthropic.Com>",
                     "Co-authored-by: Claude <81847+claude@users.noreply.github.com>",
                     "Co-authored-by: Anthropic <hello@example.com>"):
            self.assertEqual(self.rules("feat: x\n\n" + line), ["ai-co-author"], line)

    def test_human_co_authors_are_preserved(self):
        for line in ("Co-authored-by: Marie Curie <marie@example.org>",
                     "Co-authored-by: Claude Dupont <claude.dupont@example.fr>",
                     "Co-Authored-By: Jean-Claude Van Damme <jcvd@example.com>",
                     "Co-authored-by: Claude Monet <monet@example.org>",
                     "Signed-off-by: Ada Human <ada@example.org>"):
            self.assertEqual(self.rules("fix: y\n\n" + line), [], line)

    def test_ordinary_prose_about_ai_is_allowed(self):
        text = ("docs: explain the workflow\n\nI drafted this with Claude and reviewed every line.\n"
                "Anthropic's Claude Code suggested the test layout; see https://claude.com/claude-code\n"
                "and https://docs.anthropic.com/en/docs. The app is at https://claude.ai/ and\n"
                "https://claude.ai/code. This file was generated with Claude Code and then edited by hand.\n"
                "We never add Co-authored-by: lines for tools.\n")
        self.assertEqual(self.rules(text), [])

    def test_session_links_are_rejected_and_never_echoed(self):
        for text in ("see " + SESSION, "Claude-Session: " + SESSION, "CLAUDE-SESSION: x",
                     "notes: HTTPS://CLAUDE.AI/CODE/SESSION_01ABC", "claude.ai/code/session_01ABC (no scheme)",
                     "https://claude.ai/chat/0b0c1d2e-aaaa-bbbb-cccc-000000000000",
                     "https://claude.ai/share/0b0c1d2e-aaaa-bbbb-cccc-000000000000"):
            found = publish_check.check_text("PR body", text)
            self.assertEqual([f["rule"] for f in found], ["claude-session"], text)
            self.assertNotIn("session_01", json.dumps(found))
            self.assertNotIn("0b0c1d2e", json.dumps(found))

    def test_generated_footer_is_rejected_only_as_a_footer_line(self):
        self.assertEqual(self.rules("body\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"), ["ai-footer"])
        self.assertEqual(self.rules("GENERATED WITH CLAUDE CODE"), ["ai-footer"])
        self.assertEqual(self.rules("The fixtures were generated with Claude Code last week."), [])

    def test_a_trailer_that_hides_a_session_link_is_redacted(self):
        found = publish_check.check_text("m", "Co-authored-by: Claude <noreply@anthropic.com> " + SESSION)
        self.assertNotIn("session_01", json.dumps(found))


@helpers.needs_git
class Commits(helpers.Fixture):
    def setUp(self):
        super().setUp()
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "-q", "-c", "feature")
        self.body = os.path.join(self.tmp, "pr-body.md")
        with open(self.body, "w") as f:
            f.write("Adds the thing.\n\nTested with the unit suite.\n")

    def guard(self, *args, env=None):
        e = dict(self.env)
        e.update(env or {})
        return subprocess.run([sys.executable, "-B", GUARD] + list(args), capture_output=True, text=True, env=e,
                              stdin=subprocess.DEVNULL)

    def refs(self):
        return git(self.repo, "for-each-ref") + git(self.repo, "status", "--porcelain=v2", "--branch")

    def test_reports_the_offending_commit_not_the_clean_one(self):
        good = commit(self.repo, "a.txt", "a\n", "feat: a\n\nCo-authored-by: Marie Curie <marie@example.org>")
        bad = commit(self.repo, "b.txt", "b\n", "feat: b\n\nMore text.\n\n%s\nClaude-Session: %s" % (TRAILER.upper(), SESSION))
        before = self.refs()
        p = self.guard("--repo", self.repo, "--range", "%s..feature" % self.base, "--pr-body-file", self.body)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        out = p.stdout + p.stderr
        self.assertIn("commit %s message, line 5" % bad[:12], out)
        self.assertIn("commit %s message, line 6" % bad[:12], out)
        self.assertNotIn(good[:12], out)
        self.assertNotIn("session_01", out)
        self.assertNotIn(SESSION, out)
        self.assertEqual(self.refs(), before, "the check must not change the repository")

    def test_pr_body_and_squash_message_are_named_as_fields(self):
        commit(self.repo, "a.txt", "a\n", "feat: a")
        squash = os.path.join(self.tmp, "squash.txt")
        with open(squash, "w") as f:
            f.write("feat: a (#12)\n\n* feat: a\n\n" + TRAILER + "\n")
        with open(self.body, "a") as f:
            f.write("\nSession: %s\n" % SESSION)
        p = self.guard("--repo", self.repo, "--range", "%s..feature" % self.base, "--pr-body-file", self.body,
                       "--squash-message-file", squash, "--json")
        self.assertEqual(p.returncode, 1)
        d = json.loads(p.stdout)
        fields = {(f["field"].split(" (")[0], f["rule"]) for f in d["findings"]}
        self.assertEqual(fields, {("PR body", "claude-session"), ("squash message", "ai-co-author")})
        self.assertNotIn("session_01", p.stdout)

    def test_squash_message_from_stdin_and_a_merged_commit(self):
        p = subprocess.run([sys.executable, "-B", GUARD, "--squash-message-file", "-"], input="feat: z\n\n" + TRAILER,
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 1)
        sha = commit(self.repo, "s.txt", "s\n", "feat: squashed (#3)\n\n" + TRAILER)
        p = self.guard("--repo", self.repo, "--commit", sha)
        self.assertEqual(p.returncode, 1)
        self.assertIn("commit %s message" % sha[:12], p.stderr)

    def test_claude_as_commit_author_is_rejected(self):
        with open(os.path.join(self.repo, "c.txt"), "w") as f:
            f.write("c\n")
        git(self.repo, "add", "c.txt")
        git(self.repo, "commit", "-q", "-m", "feat: c", env={"GIT_AUTHOR_NAME": "Claude", "GIT_AUTHOR_EMAIL": "noreply@anthropic.com"})
        p = self.guard("--repo", self.repo, "--range", "%s..feature" % self.base)
        self.assertEqual(p.returncode, 1)
        self.assertIn("author", p.stderr)
        self.assertNotIn("committer", p.stderr)

    def test_opt_in_is_per_run_and_only_a_flag(self):
        commit(self.repo, "a.txt", "a\n", "feat: a\n\n" + TRAILER)
        args = ("--repo", self.repo, "--range", "%s..feature" % self.base)
        for name in ("PAI_ALLOW_AI_ATTRIBUTION", "ALLOW_AI_ATTRIBUTION", "PUBLISH_CHECK_ALLOW"):
            self.assertEqual(self.guard(*args, env={name: "1"}).returncode, 1, name)
        p = self.guard(*args, "--allow-ai-attribution")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ALLOWED", p.stdout)
        self.assertIn("for this run by --allow-ai-attribution", p.stdout)
        self.assertEqual(self.guard(*args).returncode, 1, "the next run is rejected again")

    def test_cannot_inspect_is_not_a_pass(self):
        self.assertEqual(self.guard("--repo", self.repo, "--range", "%s..feature" % self.base).returncode, 3, "empty range")
        self.assertEqual(self.guard("--repo", self.repo, "--range", "nope..feature").returncode, 3)
        self.assertEqual(self.guard("--repo", self.repo).returncode, 3)
        self.assertEqual(self.guard("--pr-body-file", os.path.join(self.tmp, "missing.md")).returncode, 3)

    # -- the regression, through a publisher that has every other gate

    def publish(self, *extra):
        e = dict(self.env, EXPECT_AUTHOR_EMAIL="ada@example.org", TEST_CMD="true", GH=helpers.FAKE_GH, GH_REPO="o/r")
        return subprocess.run(["bash", GATE, self.repo, self.base, "feature", self.body] + list(extra),
                              capture_output=True, text=True, env=e)

    def remote_branch(self):
        return git(self.repo, "ls-remote", "origin", "refs/heads/feature")

    def gh_calls(self):
        log = self.env["FAKE_GH_STATE"] + ".log"
        if not os.path.exists(log):
            return ""
        with open(log) as f:
            return f.read()

    def test_clean_tree_human_author_green_tests_still_do_not_publish_a_claude_trailer(self):
        commit(self.repo, "a.txt", "a\n", "feat: a real change\n\nBody.\n\n" + TRAILER)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an <%ae>"), "Ada Human <ada@example.org>")
        p = self.publish()
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ai-co-author", p.stderr)
        self.assertEqual(self.remote_branch(), "", "nothing may be pushed")
        self.assertNotIn("create", self.gh_calls())

    def test_the_same_publisher_publishes_a_clean_commit_and_honours_the_opt_in(self):
        commit(self.repo, "a.txt", "a\n", "feat: a real change\n\nCo-authored-by: Marie Curie <marie@example.org>")
        p = self.publish()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(git(self.repo, "rev-parse", "feature"), self.remote_branch())
        git(self.repo, "switch", "-q", "-c", "feature2", self.base)
        commit(self.repo, "b.txt", "b\n", "feat: b\n\n" + TRAILER)
        e = dict(self.env, EXPECT_AUTHOR_EMAIL="ada@example.org", GH=helpers.FAKE_GH, GH_REPO="o/r")
        run = lambda *x: subprocess.run(["bash", GATE, self.repo, self.base, "feature2", self.body] + list(x),
                                        capture_output=True, text=True, env=e)
        self.assertNotEqual(run().returncode, 0)
        self.assertEqual(git(self.repo, "ls-remote", "origin", "refs/heads/feature2"), "")
        self.assertEqual(run("--allow-ai-attribution").returncode, 0)
        self.assertNotEqual(git(self.repo, "ls-remote", "origin", "refs/heads/feature2"), "")


if __name__ == "__main__":
    unittest.main()
