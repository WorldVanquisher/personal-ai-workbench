"""gitprep.shell_path: a path in a copy-paste command still expands after quoting (R6-COR-009).
`tools/verify.py --work ~/...` showed `ssh-add '~/.cache/.../id_test'`: the quoted tilde never
expands, so the advised command fails for every key kept under $HOME."""
import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gitprep  # noqa: E402


class ShellPath(unittest.TestCase):
    def expand(self, text, home):
        return subprocess.run(["bash", "--norc", "--noprofile", "-c", "printf %s " + text], capture_output=True,
                              text=True, env={"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}).stdout

    def test_paths_under_home_keep_the_tilde_outside_the_quotes(self):
        home = "/home/ada"
        with mock.patch.dict(os.environ, {"HOME": home}):
            for p in ("/home/ada/.ssh/id_ed25519", "/home/ada/my keys/it's here", "/home/ada/$x `y`"):
                shown = gitprep.shell_path(p)
                self.assertTrue(shown.startswith("~/"), shown)
                self.assertEqual(self.expand(shown, home), p)
            self.assertEqual(gitprep.shell_path("/srv/k ey"), "'/srv/k ey'")
            self.assertEqual(self.expand(gitprep.shell_path("/srv/k ey"), home), "/srv/k ey")


if __name__ == "__main__":
    unittest.main()
