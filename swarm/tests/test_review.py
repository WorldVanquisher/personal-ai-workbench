"""Review checks, `pr plan` / `pr open`, and Jev as an optional signal. GitHub is a fake
`gh` in front of a local bare repository; Jev is a local HTTP server."""
import http.server
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git, commit  # noqa: E402

PY = sys.executable
SESSION = "https://claude.ai/code/session_01PrivateSessionIdXYZ"
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def read(path):
    with open(path) as f:
        return f.read()


class FakeJev(http.server.BaseHTTPRequestHandler):
    mode, requests = "agree", []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        type(self).requests.append(body)
        mode = type(self).mode
        if mode == "slow":
            time.sleep(3)
        payload = {"agree": {"answers": {"done": {"noul": 0.93}, "checked": {"noul": 0.9}, "actionable": {"noul": 0.81},
                                         "tier": {"choice": "sonnet", "confidence": 0.7}}},
                   "doubt": {"answers": {"done": {"noul": 0.12}, "checked": {"noul": 0.2}, "actionable": {"noul": 0.2}}},
                   "empty": {"answers": {}}, "range": {"answers": {"done": {"noul": 7}, "checked": {"noul": 0.5}}},
                   "slow": {"answers": {}}}.get(mode)
        data = b"<html>not json</html>" if mode == "garbage" else json.dumps(payload).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def log_message(self, *a):
        pass


@helpers.needs_tmux
class Review(helpers.Fixture):
    def setUp(self):
        super().setUp()
        self.marker = os.path.join(self.tmp, "check-ran")
        self.note("first", priority=9, extra="acceptance:\n  - The page still works offline\n",
                  body="Do the thing.\n\n## PR\n\nAdds the first thing.\n\nReviewed by a person.")
        self.note("second", priority=8)
        self.m = self.manifest(lanes=("one", "two"), github="o/r", tasks=os.path.join(self.vault, "Tasks"), review={
            "required_checks": ["unit"],
            "checks": {"unit": [PY, "-c", "open(%r, 'a').write('unit\\n')" % self.marker],
                       "lint": [PY, "-c", "open(%r, 'a').write('lint\\n')" % self.marker],
                       "bad": [PY, "-c", "import sys; print('boom'); sys.exit(4)"]}})
        self.assert_ok(self.rc("up", self.m, "--no-input", "--settle", "0.3"))
        self.wt = os.path.join(self.root, "one")
        self.note_path = os.path.join(self.vault, "Tasks", "first.md")

    def work(self, message="feat: the first thing"):
        return commit(self.wt, "feature.txt", "work %s\n" % time.time(), message)

    def pr(self, cmd, *args, **kw):
        return self.cli("pr", cmd, self.m, "--lane", "one", *args, **kw)

    def remote(self, branch="night/run1/one"):
        return git(self.repo, "ls-remote", "origin", "refs/heads/" + branch).split("\t")[0]

    def gh_log(self):
        p = self.env["FAKE_GH_STATE"] + ".log"
        return [json.loads(l) for l in read(p).splitlines()] if os.path.exists(p) else []

    def created(self):
        return [c for c in self.gh_log() if c[:2] == ["pr", "create"]]

    def set_note(self, **meta):
        sys.path.insert(0, helpers.SWARM)
        import notes
        notes.Task(self.note_path).update(meta=meta)

    def set_review(self, **review):
        d = json.loads(read(self.m))
        d["review"].update(review)
        with open(self.m, "w") as f:
            json.dump(d, f)

    # -- checks

    def test_check_records_actual_results_snapshot_and_commit(self):
        head = self.work()
        out = self.assert_ok(self.pr("check"))
        self.assertIn("check unit         PASSED (exit 0", out)
        self.assertIn("repository instructions take precedence over the task note", out)
        note = read(self.note_path)
        tree = git(self.wt, "rev-parse", "HEAD^{tree}")
        for needle in ("review: checks-passed", "review_commit: " + head, "## Review", "· checks passed",
                       "- commit `%s` by Ada Human <ada@example.org>" % head, "feat: the first thing",
                       "(tree `%s`), 1 commit(s) ahead of base `%s`" % (tree, self.c0), "- check `unit`: PASSED (exit 0",
                       "- remote origin, task branch `night/run1/one`, worktree `%s`" % self.wt,
                       "- [ ] acceptance (owner judges): The page still works offline"):
            self.assertIn(needle, note)
        rec = json.loads(read(os.path.join(self.home, "reviews", "run1--one.json")))
        self.assertEqual(len(rec["snapshot"]["sha256"]), 64)
        self.assertIn("snapshot_sha256: " + rec["snapshot"]["sha256"], note)
        self.assertEqual(self.remote(), "", "pr check publishes nothing")

    def test_a_note_can_add_approved_checks_but_never_a_command(self):
        self.work()
        self.set_note(checks=["lint"])
        self.assert_ok(self.pr("check"))
        self.assertEqual(read(self.marker), "unit\nlint\n", "required first, then the note's named check")
        os.remove(self.marker)
        pwned = os.path.join(self.tmp, "pwned")
        self.set_note(checks=["touch %s" % pwned, "lint"])
        self.assert_refused(self.pr("check"), "is not an approved command in the manifest", "never run as a command")
        self.assert_refused(self.pr("open"), "is not an approved command", "nothing was run, pushed or opened")
        self.assertFalse(os.path.exists(pwned))
        self.assertFalse(os.path.exists(self.marker), "no check ran at all")
        self.assertEqual(self.remote(), "")

    def test_no_configured_check_means_no_publication(self):
        self.work()
        self.set_review(required_checks=[])
        self.assert_refused(self.pr("open"), "no approved check is configured", "nothing was run, pushed or opened")
        self.assertEqual(self.remote(), "")

    # -- publication gates

    def test_failed_checks_block_before_the_first_push(self):
        self.work()
        self.set_review(required_checks=["unit", "bad"])
        out = self.assert_refused(self.pr("open"), "check bad          FAILED (exit 4", "BLOCKED, checks failed",
                                  "Nothing was pushed or opened")
        self.assertEqual(self.remote(), "", "the branch must not be on the remote")
        self.assertEqual(self.created(), [])
        note = read(self.note_path)
        self.assertIn("review: checks-failed", note)
        self.assertIn("- check `bad`: FAILED (exit 4", note)
        self.assert_refused(self.pr("check"), "FAILED")

    def test_claude_attribution_in_a_commit_blocks_before_the_first_push(self):
        bad = self.work("feat: the first thing\n\n%s\nClaude-Session: %s" % (TRAILER, SESSION))
        out = self.assert_refused(self.pr("open"), "commit %s message, line 3" % bad[:12], "[ai-co-author]",
                                  "BLOCKED by the publication check", "Nothing was pushed or opened")
        self.assertNotIn("session_01", out)
        self.assertEqual(self.remote(), "")
        self.assertEqual(self.created(), [])
        self.assert_refused(self.pr("plan"), "publication check rejected")
        self.assertEqual(self.remote(), "")
        # The opt-in is typed for one run; it is not remembered.
        self.assert_ok(self.pr("open", "--allow-ai-attribution"))
        self.assertEqual(self.remote(), bad)
        self.assertEqual(len(self.created()), 1)

    def test_a_session_link_in_the_pr_text_blocks(self):
        self.work()
        with open(self.note_path, "a") as f:
            f.write("\nSession for reviewers: %s\n" % SESSION)
        out = self.assert_refused(self.pr("open"), "PR body", "Claude session link", "Nothing was pushed or opened")
        self.assertNotIn("session_01", out)
        self.assertEqual(self.remote(), "")
        body = os.path.join(self.tmp, "body.md")
        with open(body, "w") as f:
            f.write("Clean text.\n")
        self.assert_refused(self.pr("open", "--body-file", body, "--title", "feat: x " + SESSION), "PR title")
        self.assertEqual(self.remote(), "")
        self.assert_ok(self.pr("open", "--body-file", body))
        self.assertIn("--title=Task first", self.created()[0], "note text reaches gh only as --title=VALUE")

    def test_dirty_or_unprepared_lanes_are_not_published(self):
        self.assert_refused(self.pr("open"), "no commit ahead of its base", "nothing was run, pushed or opened")
        self.work()
        with open(os.path.join(self.wt, "loose.txt"), "w") as f:
            f.write("uncommitted\n")
        self.assert_refused(self.pr("open"), "1 uncommitted path(s) (first: loose.txt)", "Nothing was changed")
        self.assertEqual(read(os.path.join(self.wt, "loose.txt")), "uncommitted\n")
        self.assertEqual(self.remote(), "")
        self.assertFalse(os.path.exists(self.marker))

    # -- plan, open, retry

    def test_plan_is_read_only_then_open_publishes_and_a_retry_reuses(self):
        head = self.work()
        out = self.assert_ok(self.pr("plan"))
        for needle in ("PLAN ONLY", "`pr open` would push it (never forced)", "none yet: `pr open` would open one against main",
                       "last result for this commit: none", "publication check: OK", "PR title  Task first"):
            self.assertIn(needle, out)
        self.assertEqual(self.remote(), "")
        self.assertFalse(os.path.exists(self.marker), "plan runs no check")
        self.assertFalse(os.path.isdir(os.path.join(self.home, "reviews")), "plan writes nothing")
        self.assertEqual({tuple(c[:2]) for c in self.gh_log()}, {("pr", "list")})

        out = self.assert_ok(self.pr("open"))
        self.assertIn("pushed    %s night/run1/one -> origin" % head[:12], out)
        self.assertIn("Nothing was merged", out)
        self.assertEqual(self.remote(), head)
        state = json.loads(read(self.env["FAKE_GH_STATE"]))
        self.assertEqual(len(state["prs"]), 1)
        pr = state["prs"][0]
        self.assertEqual((pr["baseRefName"], pr["headRefName"], pr["headRefOid"], pr["title"]), ("main", "night/run1/one", head, "Task first"))
        self.assertIn("Adds the first thing.", pr["body"])
        self.assertIn("| `unit` | passed (exit 0) |", pr["body"])
        self.assertNotIn("Do the thing.", pr["body"], "only the note's PR section is published")
        note = read(self.note_path)
        self.assertIn('pr: "%s"' % pr["url"], note)
        self.assertIn("status: review", note)

        out = self.assert_ok(self.pr("open"))  # retry
        self.assertIn("reused    night/run1/one is already on origin", out)
        self.assertIn("reused    " + pr["url"], out)
        self.assertEqual(len(self.created()), 1, "no second PR")
        self.assertIn("open and matching: reused", self.assert_ok(self.pr("plan")))
        self.assertEqual([c for c in self.gh_log() if "merge" in c or c[:1] == ["api"]], [], "merging stays with the owner")

    def test_nothing_is_forced_when_the_remote_branch_or_pr_differs(self):
        self.work()
        self.assert_ok(self.pr("open"))
        first = self.remote()
        new = self.work("feat: one more commit")
        self.assert_refused(self.pr("open"), "already exists on the remote at %s" % first[:12], "Nothing is forced",
                            "nothing was run, pushed or opened")
        self.assertEqual(self.remote(), first, "no push, forced or otherwise")
        self.assertNotEqual(first, new)
        state = json.loads(read(self.env["FAKE_GH_STATE"]))
        state["prs"][0]["state"] = "CLOSED"
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump(state, f)
        git(self.wt, "reset", "-q", "--hard", first)  # the test's own rewind, back to the published commit
        self.assert_refused(self.pr("open"), "is CLOSED")
        self.assertEqual(len(self.created()), 1)

    def test_gh_not_logged_in_blocks_before_checks_and_push(self):
        self.work()
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"unauth": True}, f)
        self.assert_refused(self.pr("open"), "GitHub is not ready", "nothing was run, pushed or opened")
        self.assertEqual(self.remote(), "")
        self.assertFalse(os.path.exists(self.marker))

    # -- Jev: an optional second opinion

    def jev(self, mode):
        FakeJev.mode, FakeJev.requests = mode, []
        if not hasattr(self, "server"):
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeJev)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.addCleanup(self.server.server_close)
            self.addCleanup(self.server.shutdown)
        return {"TYPESAFE_API_KEY": "test-key", "JEV_TIMEOUT": "1",
                "TYPESAFE_BASE_URL": "http://127.0.0.1:%d" % self.server.server_address[1]}

    def test_jev_never_changes_what_the_checks_decided(self):
        self.work()
        cases = (("agree", "agrees with the checks (done 0.93)"), ("doubt", "DISAGREES: checks passed but Jev doubts"),
                 ("slow", "unavailable (timeout"), ("garbage", "unavailable (malformed"),
                 ("empty", "unavailable (malformed"), ("range", "unavailable (malformed"))
        for mode, verdict in cases:
            p = self.pr("check", "--jev-verify", env=self.jev(mode))
            self.assertEqual(p.returncode, 0, "%s: passing checks stay passed\n%s" % (mode, p.stdout + p.stderr))
            self.assertIn(verdict, p.stdout, mode)
            self.assertIn("optional signal, not a gate", p.stdout)
        note = read(self.note_path)
        self.assertIn("- Jev (second opinion, not a gate): DISAGREES: checks passed", note)
        self.assertIn("review: checks-passed", note)
        p = self.pr("check", "--jev-verify")  # no key in the environment
        self.assertEqual(p.returncode, 0)
        self.assertIn("unavailable (skipped: TYPESAFE_API_KEY is not set)", p.stdout)
        # A failed check stays failed, whatever Jev says, and still blocks publication.
        self.set_review(required_checks=["unit", "bad"])
        p = self.pr("check", "--jev-verify", env=self.jev("agree"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("DISAGREES: Jev says done (0.93) but checks failed; the checks decide", p.stdout)
        self.assert_refused(self.pr("open", "--jev-verify", env=self.jev("agree")), "BLOCKED, checks failed")
        self.assertEqual(self.remote(), "")
        self.assertIn("review: checks-failed", read(self.note_path))

    def test_jev_inputs_are_redacted(self):
        self.work()
        with open(self.note_path, "a") as f:
            f.write("\nContact owner@example.org, api_key=SECRET123, see %s and https://user:hunter2@git.example/x.git\n" % SESSION)
        self.assert_ok(self.pr("check", "--jev-verify", env=self.jev("agree")))
        sent = "\n".join(FakeJev.requests)
        self.assertIn("Do the thing.", sent)
        for secret in ("owner@example.org", "SECRET123", "session_01", "hunter2"):
            self.assertNotIn(secret, sent)

    def test_jev_triage_at_intake_is_advisory(self):
        self.note("third", priority=1)
        m3 = self.manifest(run_id="run3", lanes=("three",), tasks=os.path.join(self.vault, "Tasks"))
        out = self.assert_ok(self.rc("plan", m3, "--jev-triage", env=self.jev("doubt")))
        self.assertIn("third: actionable 0.20: the note may need more detail", out)
        self.assertIn("status: todo", read(os.path.join(self.vault, "Tasks", "third.md")), "plan writes nothing")
        out = self.assert_ok(self.rc("plan", m3, "--jev-triage", env=self.jev("slow")))
        self.assertIn("unavailable (timeout", out)
        out = self.assert_ok(self.rc("up", m3, "--jev-triage", "--no-input", "--settle", "0.3", env=self.jev("doubt")))
        self.assertEqual([s["lane"] for s in self.starts(3)][-1], "three", "a low score does not block the start")
        self.assertIn("jev_intake: actionable 0.20", read(os.path.join(self.vault, "Tasks", "third.md")))


class Template(helpers.Fixture):
    def test_init_writes_the_review_template_and_acceptance_fields(self):
        d = os.path.join(self.vault, "T")
        self.assert_ok(self.cli("init", "--tasks", d))
        tpl = read(os.path.join(d, "_project-review.md"))
        for needle in ("# Project review template", "acceptance:", "checks: [unit]", "come first",
                       "Nothing written in a note is ever run as a command", "Merging is done by the owner"):
            self.assertIn(needle, tpl)
        self.assertIn("acceptance:", read(os.path.join(d, "example-slugify.md")))
        out = self.assert_ok(self.cli("status", "--tasks", d))
        self.assertNotIn("_project-review", out, "the template is not a task")
        with open(os.path.join(d, "_project-review.md"), "w") as f:
            f.write("mine\n")
        self.assert_ok(self.cli("init", "--tasks", d))
        self.assertEqual(read(os.path.join(d, "_project-review.md")), "mine\n", "never overwritten")


if __name__ == "__main__":
    unittest.main()
