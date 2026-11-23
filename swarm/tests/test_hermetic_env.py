"""helpers.hermetic_env(): a test's subprocess never depends on the host's git identity, git
configuration, tmux server or a key in the operator's shell (R6-COR-003)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402


class HermeticEnv(unittest.TestCase):
    HOSTILE = {"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%1", "TYPESAFE_API_KEY": "host-key",
               "GIT_DIR": "/nonexistent/.git", "GIT_INDEX_FILE": "/nonexistent/index", "BASH_ENV": "/etc/hostile",
               "GIT_CONFIG_GLOBAL": "/nonexistent/gitconfig", "GIT_AUTHOR_NAME": "", "GIT_COMMITTER_EMAIL": ""}

    def test_host_only_variables_are_dropped_and_identity_is_fixed(self):
        with mock.patch.dict(os.environ, self.HOSTILE):
            e = helpers.hermetic_env()
        for k in ("TMUX", "TMUX_PANE", "TYPESAFE_API_KEY", "GIT_DIR", "GIT_INDEX_FILE", "BASH_ENV"):
            self.assertNotIn(k, e)
        self.assertEqual(e["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(e["GIT_CONFIG_SYSTEM"], os.devnull)
        self.assertEqual(e["GIT_AUTHOR_NAME"], helpers.HUMAN["GIT_AUTHOR_NAME"])
        self.assertEqual(e["GIT_COMMITTER_EMAIL"], helpers.HUMAN["GIT_COMMITTER_EMAIL"])
        self.assertTrue(e["GIT_COMMITTER_EMAIL"].endswith((".org", ".invalid")))

    def test_extra_values_win_and_none_removes(self):
        e = helpers.hermetic_env(PAI_X="1", PATH=None)
        self.assertEqual(e["PAI_X"], "1")
        self.assertNotIn("PATH", e)
        base = {"KEEP": "yes", "TMUX": "x"}
        self.assertEqual(helpers.hermetic_env(base)["KEEP"], "yes")
        self.assertNotIn("TMUX", helpers.hermetic_env(base))
        self.assertNotIn("KEEP", helpers.hermetic_env())

    @helpers.needs_git
    def test_a_commit_succeeds_on_a_host_without_any_git_identity(self):
        # The test_wtpr failure on the owner's WSL host: no user.name/user.email anywhere.
        d = tempfile.mkdtemp(prefix="pai-hermetic-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        bare = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
        bare.update(HOME=d, XDG_CONFIG_HOME=d, GIT_CONFIG_GLOBAL=os.devnull, EMAIL="")
        with mock.patch.dict(os.environ, bare, clear=True):
            e = helpers.hermetic_env()
            p = subprocess.run(["git", "init", "-q", d], env=e, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            p = subprocess.run(["git", "-C", d, "commit", "-q", "--allow-empty", "-m", "x"], env=e,
                               capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
