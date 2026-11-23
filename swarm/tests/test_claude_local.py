"""The lane's L2 instructions in CLAUDE.local.md (PRM-D9 = A; R6-COR-057..062): `pai worktree init
--target claude-local` writes them there, git never sees the file (info/exclude), the worktree stays
clean, so `pai recover --continue` and worktree reuse are no longer refused; splitplan and
`pai context --worktree` read the section from there. Fake claude only."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git  # noqa: E402
from test_workbench import WB, needs_311  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import gitprep  # noqa: E402
import splitplan  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")


def init(env, wt, *extra):
    return subprocess.run([sys.executable, "-B", PAI, "worktree", "init", wt, "--goal", "the API", "--scope", "src",
                           "--lane", "a"] + list(extra), capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=120)


@helpers.needs_git
class Target(helpers.Fixture):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.upstream, "src"))
        with open(os.path.join(self.upstream, "src", "x.py"), "w") as f:
            f.write("x = 1\n")
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-qm", "src")
        git(self.upstream, "push", "-q", "origin", "main")
        git(self.repo, "pull", "-q", "origin", "main")
        self.wt = os.path.join(self.tmp, "lane a")
        git(self.repo, "worktree", "add", "-q", "-b", "lane/a", self.wt)

    def exclude(self):
        with open(os.path.join(self.repo, ".git", "info", "exclude"), encoding="utf-8") as f:  # shared by the worktrees
            return f.read()

    def test_claude_local_keeps_the_worktree_clean_and_out_of_git(self):  # R6-COR-057, 058, 059
        p = init(self.env, self.wt, "--target", "claude-local", "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(self.wt, "CLAUDE.local.md"), encoding="utf-8") as f:
            self.assertIn("Change only files under: `src/`.", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.wt, "AGENTS.md")), "AGENTS.md is not written")
        self.assertEqual(gitprep.dirty(self.wt), [], "nothing for git to see: continuation and reuse are not refused")
        git(self.wt, "add", "-A")
        self.assertEqual(git(self.wt, "diff", "--cached", "--name-only"), "", "git add -A does not stage it")
        self.assertEqual(self.exclude().count("/CLAUDE.local.md"), 1)
        self.assertEqual(init(self.env, self.wt, "--target", "claude-local", "--write").returncode, 0)
        self.assertEqual(self.exclude().count("/CLAUDE.local.md"), 1, "added once")

    def test_the_default_still_writes_agents_md_which_is_a_change(self):  # Codex lanes: unchanged
        self.assertEqual(init(self.env, self.wt, "--write").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.wt, "AGENTS.md")))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "CLAUDE.local.md")))
        self.assertEqual(gitprep.dirty(self.wt), ["?? AGENTS.md"], "the old cause of the refusals")
        p = init(self.env, self.wt, "--target", "claude-local", "--file", "X.md")
        self.assertNotEqual(p.returncode, 0)

    def test_splitplan_sees_the_lane(self):  # R6-COR-060
        self.assertEqual(init(self.env, self.wt, "--target", "claude-local", "--write").returncode, 0)
        owned, _ = splitplan.ownership(self.repo)
        self.assertEqual((owned["src"]["lane"], owned["src"]["file"]), ("a", "CLAUDE.local.md"))


@needs_311
@helpers.needs_tmux
class Continue(WB):
    """`pai recover --continue --write` on a lane whose L2 is in CLAUDE.local.md (R6-COR-058, 062)."""

    def setUp(self):
        super().setUp()
        self.note("task-a", priority=9, body="Write STEP-1.md.\n\n## Acceptance criteria\n\n- STEP-1.md exists")
        self.env["PAI_DISPATCH_CONFIRM_SECONDS"] = "2"
        self.init()

    def session(self, target):
        self.behavior()
        p = self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "8", "--run-id", "s1")
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        wt = json.loads(self.pai("status", "--json", "--run", "s1").stdout)["lanes"][0]["worktree"]
        p = init(self.env, wt, "--write", *(("--target", "claude-local") if target else ()))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        helpers.commit(wt, "STEP-1.md", "done\n", "step 1")
        self.assert_ok(self.pai("cleanup", "--run", "s1", "--apply", "--stop-running"))
        return self.pai("recover", "--run", "s1", "--continue", "a", "--write")

    def test_continuation_is_not_refused_with_claude_local(self):
        p = self.session(target=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("carries commits only", p.stdout)

    def test_continuation_was_refused_with_agents_md(self):
        p = self.session(target=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("a continuation carries commits only", p.stdout)


if __name__ == "__main__":
    unittest.main()
