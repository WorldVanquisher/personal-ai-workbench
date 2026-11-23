"""`pai doctor` (workbench) ends with the manual section that matches what it found (R6-DOC-027)."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import workbench  # noqa: E402


class DoctorNext(unittest.TestCase):
    def test_each_state_names_its_section(self):
        self.assertIn('section 8 "When something goes wrong"', workbench.doctor_next({"name": "p"}, True))
        self.assertIn("section 5", workbench.doctor_next(None, False))
        line = workbench.doctor_next({"name": "p"}, False)
        self.assertIn('section 6 "Your first round"', line)
        self.assertIn("REFRESH.md, part (b)", line)

    def test_the_local_manuals_have_those_sections(self):
        with open(workbench.manual("FIRST-USE.md"), encoding="utf-8") as f:
            text = f.read()
        for h in ("## 5. Save this checkout's defaults: `pai init`", "## 6. Your first round", "## 8. When something goes wrong"):
            self.assertIn(h, text)

    def test_an_installed_package_gets_the_github_file_for_its_version(self):
        from pai import __version__
        with mock.patch.object(workbench.os.path, "isfile", return_value=False):
            self.assertTrue(workbench.manual("FIRST-USE.md").endswith("/blob/v%s/docs/FIRST-USE.md" % __version__))


if __name__ == "__main__":
    unittest.main()
