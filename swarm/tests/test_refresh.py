"""Post-merge refresh (R3-010 and the refresh generator): a squash merge on the remote, the
owner's own fetch, a fresh worktree from the fetched base, and the old worktree with its
uncommitted files left exactly as it was. Local bare remote only; no network."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git, commit  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")


@helpers.needs_git
class PostMergeRefresh(helpers.Fixture):
    def setUp(self):
        super().setUp()
        self.old = os.path.join(self.tmp, "old wt")  # a space on purpose
        git(self.repo, "worktree", "add", "-q", "-b", "feat/x", self.old)
        commit(self.old, "x.txt", "feature\n", "feat: x")
        git(self.old, "push", "-q", "origin", "feat/x")
        # the owner squash-merges feat/x on the remote
        git(self.upstream, "fetch", "-q", "origin", "feat/x")
        git(self.upstream, "merge", "-q", "--squash", "FETCH_HEAD")
        git(self.upstream, "commit", "-q", "-m", "feat: x (#1)")
        git(self.upstream, "push", "-q", "origin", "main")
        self.squash = git(self.upstream, "rev-parse", "HEAD")
        # unfinished work in the old worktree
        with open(os.path.join(self.old, "x.txt"), "a") as f:
            f.write("uncommitted edit\n")
        with open(os.path.join(self.old, "notes é.md"), "w") as f:
            f.write("untracked\n")
        self.old_bytes = {n: self.read(os.path.join(self.old, n)) for n in ("x.txt", "notes é.md")}

    @staticmethod
    def read(path):
        with open(path, "rb") as f:
            return f.read()

    def pai(self, *args):
        return subprocess.run([sys.executable, "-B", PAI, "worktree"] + list(args), capture_output=True, text=True,
                              env=self.env, cwd=self.repo, stdin=subprocess.DEVNULL)

    def refs(self):
        return git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)")

    def run_bash(self, cmds):
        """What the owner pastes: every command but the final `pai worktree verify`."""
        script = "\n".join(c for c in cmds if not c.startswith("pai ")) + "\n"
        return subprocess.run(["bash", "-ec", script], capture_output=True, text=True, env=self.env, cwd=self.tmp)

    def test_squash_merge_then_owner_fetch_and_a_fresh_worktree_leave_the_old_one_alone(self):
        before = self.refs()
        p = self.pai("refresh", "--worktree", self.old, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        info = json.loads(p.stdout)
        self.assertEqual(info["recommendation"]["kind"], "new-worktree", "a dirty worktree is never moved")
        self.assertIn("2 uncommitted path(s)", " ".join(info["recommendation"]["reasons"]))
        self.assertEqual(self.refs(), before, "the generator fetched and changed nothing")
        self.assertEqual(info["base"]["last_fetched"], self.c0, "it shows the last fetched base, not a guess")
        new = os.path.join(self.tmp, "fresh wt")
        p = self.pai("refresh", "--new", new, "--branch", "postmerge/test", "--json")
        info = json.loads(p.stdout)
        cmds = info["commands"]["bash"]
        self.assertTrue(cmds[0].endswith("fetch origin +refs/heads/main:refs/remotes/origin/main"), cmds[0])
        self.assertIn("worktree add -b postmerge/test", cmds[2])
        self.assertTrue(cmds[-1].startswith("pai worktree verify"))
        ps = info["commands"]["powershell"]
        self.assertIn("$BASE = git -C", ps[1])
        self.assertIn("'%s'" % new, ps[2], "PowerShell quoting for a path with a space")
        r = self.run_bash(cmds)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fetched origin/main = %s" % self.squash, r.stdout)
        self.assertEqual(git(new, "rev-parse", "HEAD"), self.squash)
        v = self.pai("verify", new, "--base", self.squash, "--json")
        self.assertEqual(v.returncode, 0, v.stdout)
        self.assertTrue(json.loads(v.stdout)["contains_base"])
        v = self.pai("verify", self.old, "--base", self.squash)
        self.assertEqual(v.returncode, 1)
        self.assertIn("does not contain the base", v.stdout)
        self.assertEqual(git(self.old, "symbolic-ref", "--short", "HEAD"), "feat/x")
        for n, data in self.old_bytes.items():
            self.assertEqual(self.read(os.path.join(self.old, n)), data, n)

    def test_a_fetch_alone_does_not_make_a_worktree_ready(self):
        git(self.repo, "fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main")
        v = self.pai("verify", self.repo, "--base", "origin/main")
        self.assertEqual(v.returncode, 1, "refs moved, the checkout did not")
        self.assertIn("the fetch updated refs, not this worktree", v.stdout)

    def test_fast_forward_only_for_a_clean_worktree_the_base_contains(self):
        behind = os.path.join(self.tmp, "behind")
        git(self.repo, "worktree", "add", "-q", "-b", "trail", behind, self.c0)
        info = json.loads(self.pai("refresh", "--worktree", behind, "--json").stdout)
        self.assertEqual(info["recommendation"]["kind"], "fast-forward")
        r = self.run_bash(info["commands"]["bash"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(behind, "rev-parse", "HEAD"), self.squash)
        # a clean but squash-merged branch: the guarded fast-forward refuses and changes nothing
        os.remove(os.path.join(self.old, "notes é.md"))
        git(self.old, "checkout", "-q", "--", "x.txt")
        head = git(self.old, "rev-parse", "HEAD")
        info = json.loads(self.pai("refresh", "--worktree", self.old, "--json").stdout)
        self.assertEqual(info["recommendation"]["kind"], "fast-forward")
        r = self.run_bash(info["commands"]["bash"])
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(git(self.old, "rev-parse", "HEAD"), head, "a refused fast-forward changes nothing")

    def test_list_shows_repository_branches_and_state(self):
        p = self.pai("list", "--json")
        info = json.loads(p.stdout)
        self.assertEqual(info["repository"]["path"], self.repo)
        rows = {w["path"]: w for w in info["worktrees"]}
        self.assertEqual((rows[self.old]["branch"], rows[self.old]["uncommitted"]), ("feat/x", 2))
        self.assertEqual(rows[self.repo]["branch"], "main")
        p = self.pai("refresh", "--new", self.old)
        self.assertEqual(p.returncode, 2)
        self.assertIn("already exists", p.stderr)


    def test_help_reaches_the_subcommand_parsers(self):
        for args, needle in ((["worktree", "--help"], "refresh"), (["plan", "--help"], "propose"),
                             (["worktree", "init", "--help"], "--goal")):
            p = subprocess.run([sys.executable, "-B", PAI] + args, capture_output=True, text=True, env=self.env,
                               stdin=subprocess.DEVNULL)
            self.assertEqual(p.returncode, 0, args)
            self.assertIn(needle, p.stdout, args)


if __name__ == "__main__":
    unittest.main()
