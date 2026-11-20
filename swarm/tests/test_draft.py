"""Human-reviewed Git workflow (1.0 readiness 071-079): `pai pr draft` renders the owner-review page
locally, leaves the Git index and refs as they were, strips configured and AI attribution trailers
(case-insensitively, several at once) while human co-authors and ordinary sentences stay, keeps
session links and tokens out, shows every removal before the text, and never tries to publish:
fake `git` and `gh` that refuse any push, fetch or GitHub call are first on PATH."""
import hashlib
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311, wrapper, lines  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import dispatch  # noqa: E402
import draft  # noqa: E402
import wbconfig  # noqa: E402

LINK = "https://claude.ai/code/session_01" + "Zx9" * 7
PR_SECTION = """## PR

Export now writes JSON when asked.

Remember: the Co-authored-by trailer stays for human co-authors.
We compared colours with Claude Monet's palette.
Transcript: %s

Co-authored-by: Ada Human <ada@example.org>
CO-AUTHORED-BY: Claude <noreply@anthropic.com>
co-authored-by: Claude Opus 5.5 <claude@users.noreply.github.com>
Assisted-by: an editor plugin
ASSISTED-BY: another tool
Generated with [Claude Code](https://claude.com/claude-code)
""" % LINK


class Strip(unittest.TestCase):
    def test_case_insensitive_multiple_trailers_and_ordinary_mentions(self):
        text, removed = draft.strip(PR_SECTION, keys=["assisted-by"])
        for kept in ("Co-authored-by: Ada Human <ada@example.org>", "Remember: the Co-authored-by trailer stays",
                     "Claude Monet's palette", "Export now writes JSON"):
            self.assertIn(kept, text)
        for gone in ("noreply@anthropic.com", "claude@users.noreply", "Assisted-by", "ASSISTED-BY", "Generated with",
                     LINK):
            self.assertNotIn(gone, text)
        self.assertIn("Transcript: <removed: Claude session link>", text)
        kinds = [(r["line"], r["kind"]) for r in removed]
        self.assertEqual(kinds, [(7, "Claude session link"), (10, "Co-authored-by names Claude/Anthropic"),
                                 (11, "Co-authored-by names Claude/Anthropic"), (12, "configured trailer Assisted-by"),
                                 (13, "configured trailer ASSISTED-BY"), (14, "\"Generated with Claude Code\" footer")])
        self.assertEqual(PR_SECTION.split("\n")[11].split(":")[0], "Assisted-by", "line numbers are the original text's")
        self.assertNotIn(LINK, json.dumps(removed), "a removal never repeats the secret")
        same, none = draft.strip("Plain text.\nNo trailers here.")
        self.assertEqual((same, none), ("Plain text.\nNo trailers here.", []))
        unconfigured, rem = draft.strip("Assisted-by: tool")
        self.assertEqual((unconfigured, rem), ("Assisted-by: tool", []), "only configured keys are stripped")


class Defaults(unittest.TestCase):
    def test_worker_prompts_and_defaults_never_publish(self):
        import types
        m = types.SimpleNamespace(run_id="r1", tasks_dir="/t")
        w = types.SimpleNamespace(lane="a")
        msg = dispatch.message(m, w, {}, {"note": "n", "branch": "pai/r1/a"}, None)
        self.assertIn("Do not push or open a pull request: the operator reviews and publishes.", msg)
        keys = {(t, k) for t, k, *_ in wbconfig.SCHEMA}
        for t, k in keys:
            self.assertFalse(any(w in k for w in ("auto_pr", "auto_merge", "open_pr", "merge")), (t, k))


def tree_state(repo, wt):
    def h(path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    gd = helpers.git(wt, "rev-parse", "--absolute-git-dir")
    index = os.path.join(gd, "index")
    return (helpers.git(repo, "for-each-ref"), helpers.git(repo, "stash", "list"), helpers.git(wt, "rev-parse", "HEAD"),
            h(index), os.stat(index).st_mtime_ns, helpers.git(repo, "worktree", "list", "--porcelain"),
            sorted(os.listdir(os.path.join(helpers.git(repo, "rev-parse", "--git-common-dir") if os.path.isabs(
                helpers.git(repo, "rev-parse", "--git-common-dir")) else os.path.join(repo, ".git"), "refs", "heads"))))


@helpers.needs_tmux
@needs_311
class Draft(WB):
    def setUp(self):
        super().setUp()
        self.note("task-a", priority=9, body="Write JSON when asked.\n\n" + PR_SECTION)
        self.env["PAI_DISPATCH_SETTLE"] = "0.5"
        self.init()
        self.set_config({("review", "strip_trailers"): ["Assisted-by"]})
        self.guard = os.path.join(self.tmp, "guard")
        os.makedirs(self.guard)
        for name in ("git", "gh"):
            wrapper(self.guard, name, "fake_publish_guard.py")
        self.publog = os.path.join(self.tmp, "publish.log")
        self.env.update(FAKE_PUBLISH_LOG=self.publog, FAKE_REAL_GIT=shutil.which("git"))

    def guarded(self, *args):
        return self.pai(*args, env={"PATH": self.guard + os.pathsep + self.env["PATH"]})

    def test_the_local_draft(self):
        gh_only = os.path.join(self.tmp, "gh-only")
        os.makedirs(gh_only)
        wrapper(gh_only, "gh", "fake_publish_guard.py")
        p = self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "8",
                     env={"PATH": gh_only + os.pathsep + self.env["PATH"]})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.pai("recover", env={"PATH": gh_only + os.pathsep + self.env["PATH"]})
        self.assertFalse(os.path.exists(self.publog), "run, status and recover never call gh")
        rows = {l["lane"]: l for l in json.loads(self.pai("status", "--json").stdout)["lanes"]}
        wt = rows["a"]["worktree"]
        helpers.commit(wt, "export.py", "print('json')\n", "feat: write JSON\n\nCo-authored-by: Ada Human <ada@example.org>")
        before = tree_state(self.repo, wt)

        p = self.guarded("pr", "draft", "a")
        out = p.stdout
        self.assertEqual(p.returncode, 0, out + p.stderr)
        self.assertLess(out.index("FILTER    6 removal(s)"), out.index("# Owner review: Task task-a"),
                        "the removals are shown before the text")
        for section in ("## Proposed diff", "## Test evidence", "## PR text (draft, not published)",
                        "## Removed by the metadata filter", "## Owner checklist", "## Commands for the owner (not run by pai)"):
            self.assertIn(section, out)
        self.assertIn("feat: write JSON", out)
        self.assertIn("export.py", out)
        self.assertIn("None recorded for this head.", out)
        self.assertIn("Co-authored-by: Ada Human <ada@example.org>", out)
        self.assertNotIn("noreply@anthropic.com", out)
        self.assertNotIn(LINK, out)
        self.assertIn("PR body line", out)
        self.assertEqual(tree_state(self.repo, wt), before, "index, refs, stash, HEAD and worktrees are unchanged")
        self.assertFalse(os.path.exists(self.publog), "no push, fetch or gh call was even attempted: %s" % (
            lines(self.publog) if os.path.exists(self.publog) else ""))

        out_file = os.path.join(self.tmp, "review.md")
        self.assertEqual(self.guarded("pr", "draft", "a", "--out", out_file).returncode, 0)
        with open(out_file, encoding="utf-8") as f:
            saved = f.read()
        self.assertIn("## Owner checklist", saved)
        self.assertEqual(self.guarded("pr", "draft", "a", "--out", out_file).returncode, 1, "never overwrites")
        p = self.guarded("pr", "draft", "a", "--keep-trailers")
        self.assertIn("noreply@anthropic.com", p.stdout)
        self.assertIn("FILTER    off (--keep-trailers)", p.stdout)
        self.assertEqual(tree_state(self.repo, wt), before)
        self.assertFalse(os.path.exists(self.publog))

        # the guard itself works: a push and a gh call through it are refused and logged
        import subprocess
        env = dict(self.env, PATH=self.guard + os.pathsep + self.env["PATH"])
        for argv in (["git", "-C", wt, "push", "origin", "HEAD"], ["gh", "pr", "create"]):
            q = subprocess.run(argv, env=env, capture_output=True, text=True)
            self.assertEqual(q.returncode, 99, q.stderr)
        self.assertEqual([r["refused"] for r in lines(self.publog)], ["git push", "gh pr create"])
        self.assertEqual(helpers.git(self.repo, "ls-remote", "origin", "refs/heads/pai/*"), "", "nothing reached origin")


if __name__ == "__main__":
    unittest.main()
