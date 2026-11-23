"""The last line of PAI's own doctor names the manual section to read next (R6-DOC-027)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pai import __version__, doctor  # noqa: E402


class NextStep(unittest.TestCase):
    def test_a_clean_doctor_points_to_the_first_round_and_the_refresh_manual(self):
        line = doctor.next_step(0)
        self.assertTrue(line.startswith("Next: "), line)
        self.assertIn("FIRST-USE.md", line)
        self.assertIn('section 6 "Your first round"', line)
        self.assertIn('REFRESH.md, part (b) "Every round"', line)

    def test_problems_point_to_the_troubleshooting_section(self):
        line = doctor.next_step(2)
        self.assertIn("fix each PROBLEM above", line)
        self.assertIn('section 8 "When something goes wrong"', line)

    def test_the_manual_named_exists_in_this_checkout_with_those_sections(self):
        path = doctor.manual("FIRST-USE.md")
        self.assertTrue(os.path.isfile(path), path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## 6. Your first round", text)
        self.assertIn("## 8. When something goes wrong", text)
        with open(doctor.manual("REFRESH.md"), encoding="utf-8") as f:
            self.assertIn("## (b) Every round", f.read())

    def test_without_a_local_copy_it_names_the_file_for_this_version_on_github(self):
        with mock.patch.object(doctor.os.path, "isfile", return_value=False):
            url = doctor.manual("FIRST-USE.md")
        self.assertEqual(url, "https://github.com/WorldVanquisher/personal-ai-workbench/blob/v%s/docs/FIRST-USE.md"
                         % __version__)

    def test_it_is_the_last_line_after_the_unchanged_summary(self):
        with tempfile.TemporaryDirectory() as d:
            r = doctor.run(os.path.join(d, "absent.toml"))
        self.assertTrue(r.lines[-2].strip().endswith("PROBLEM(S) FOUND") or "NO PROBLEMS FOUND" in r.lines[-2])
        self.assertTrue(r.lines[-1].startswith("Next: "), r.lines[-1])


class OptionalInstructions(unittest.TestCase):
    """R6-FIN-004 (core NOTES-FOR-docs C10): a missing AGENTS.md is a WARNING that says it is optional,
    and doctor still ends with NO PROBLEMS FOUND."""

    def test_a_missing_agents_md_is_called_optional(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from test_pai import Fixture
        with tempfile.TemporaryDirectory() as d:
            f = Fixture(d)
            (f.repo / "AGENTS.md").unlink()
            r = doctor.run(str(f.cfg), "demo")
        text = "\n".join(r.lines)
        line = [x for x in r.lines if "AGENTS.md" in x]
        self.assertEqual(len(line), 1, text)
        self.assertIn("instructions source MISSING: AGENTS.md (not found; optional: pai context uses it when present)",
                      line[0])
        self.assertTrue(line[0].startswith("WARNING"), line[0])
        self.assertIn("NO PROBLEMS FOUND", text)


if __name__ == "__main__":
    unittest.main()
