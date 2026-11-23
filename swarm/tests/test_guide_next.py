"""`pai guide` ends with the next step of docs/FIRST-USE.md (R6-DOC-028)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import guide  # noqa: E402


class NextLines(unittest.TestCase):
    def test_the_first_run_guide_points_to_init_then_the_first_round(self):
        (line,) = guide.next_lines()
        self.assertIn("FIRST-USE.md", line)
        self.assertIn('section 5 "Save this checkout\'s defaults: pai init"', line)
        self.assertIn('section 6 "Your first round"', line)

    def test_the_handoff_guides_point_to_recovery(self):
        (line,) = guide.next_lines("clean")
        self.assertIn('REFRESH.md, part (e) "Recovery"', line)

    def test_the_named_sections_exist_in_the_local_manuals(self):
        with open(guide.manual("FIRST-USE.md"), encoding="utf-8") as f:
            text = f.read()
        for h in ("## 2a. A Git checkout with a remote", "## 3. Check your machine: `pai doctor`",
                  "## 5. Save this checkout's defaults: `pai init`", "## 6. Your first round: one worker"):
            self.assertIn(h, text)
        with open(guide.manual("REFRESH.md"), encoding="utf-8") as f:
            self.assertIn("## (e) Recovery", f.read())

    def test_an_installed_package_gets_the_github_file_for_its_version(self):
        from pai import __version__
        with mock.patch.object(guide.os.path, "isfile", return_value=False):
            self.assertTrue(guide.manual("FIRST-USE.md").endswith("/blob/v%s/docs/FIRST-USE.md" % __version__))


@unittest.skipUnless(shutil.which("tmux") and shutil.which("git"), "pai guide needs tmux and git")
class GuideEnds(unittest.TestCase):
    def test_a_real_guide_run_ends_with_the_first_use_step(self):
        with tempfile.TemporaryDirectory() as d:
            demo = os.path.join(d, "demo")
            env = dict(os.environ, HOME=d, PYTHONDONTWRITEBYTECODE="1")
            env.pop("TMUX", None)
            p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "bin", "pai.py"), "guide", "--dir", demo],
                               capture_output=True, text=True, timeout=180, env=env)
            subprocess.run([sys.executable, "-B", os.path.join(ROOT, "bin", "pai.py"), "guide", "--cleanup", demo],
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(p.returncode, 0, p.stdout[-800:] + p.stderr[-800:])
        tail = p.stdout.strip().splitlines()[-2:]
        self.assertIn("FIRST-USE.md", tail[0])
        self.assertIn("section 5", tail[0])
        self.assertTrue(tail[1].startswith("Remove this demo: pai guide --cleanup"), tail)


if __name__ == "__main__":
    unittest.main()
