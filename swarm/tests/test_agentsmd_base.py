"""`pai worktree init` records the base it was told (--remote/--base), not a fixed origin/main
(R6-COR-014; was agentsmd.py:404-405)."""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git, commit  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")


@helpers.needs_git
class InitBase(helpers.Fixture):
    def setUp(self):
        super().setUp()
        git(self.upstream, "checkout", "-q", "-b", "release")
        self.rel = commit(self.upstream, "rel.txt", "r\n", "release only")
        git(self.upstream, "push", "-q", "origin", "release")
        git(self.repo, "remote", "add", "mirror", self.origin)
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "fetch", "-q", "mirror")
        self.wt = os.path.join(self.tmp, "lane wt")
        git(self.repo, "worktree", "add", "-q", "-b", "lane/a", self.wt, "origin/release")

    def init(self, *extra):
        return subprocess.run([sys.executable, "-B", PAI, "worktree", "init", self.wt, "--goal", "g"] + list(extra),
                              capture_output=True, text=True, env=dict(self.env, PAI_JEV_CREDENTIAL_STORE="none"),
                              cwd=self.tmp, stdin=subprocess.DEVNULL)

    def test_default_is_still_origin_main(self):
        p = self.init()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("base `origin/main` at `%s`" % self.c0[:12], p.stdout)

    def test_base_and_remote_are_followed(self):
        p = self.init("--base", "release")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("base `origin/release` at `%s`" % self.rel[:12], p.stdout)
        p = self.init("--remote", "mirror", "--base", "release", "--write")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(self.wt, "AGENTS.md"), encoding="utf-8") as f:
            self.assertIn("base `mirror/release` at `%s`" % self.rel[:12], f.read())

    def test_an_option_like_or_dotted_name_is_refused(self):
        for args in (("--base=-upload-pack",), ("--remote", "a..b"), ("--base", "x y")):
            p = self.init(*args)
            self.assertNotEqual(p.returncode, 0, args)
            self.assertIn("is not a plain remote or branch name", p.stderr + p.stdout)


if __name__ == "__main__":
    unittest.main()
