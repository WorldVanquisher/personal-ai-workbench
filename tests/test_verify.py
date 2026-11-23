"""tools/verify.py: a missing prerequisite is SKIPPED with its name, never FAIL (R6-COR-005),
and every step says whether CI runs it too (R6-COR-006)."""
import os
import stat
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import verify  # noqa: E402

FAKE_PY = """#!/bin/sh
# A python3 without pip (the owner's WSL host, 2026-11-22): `-m pip` fails, the rest works.
for a in "$@"; do [ "$a" = pip ] && { echo "No module named pip" >&2; exit 1; }; done
exec %s "$@"
"""


@unittest.skipIf(os.name == "nt", "a POSIX shell script stands in for python")
class PackagePrerequisites(unittest.TestCase):
    def fake_python(self):
        d = tempfile.mkdtemp(prefix="pai-verify-")
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        path = os.path.join(d, "python3")
        with open(path, "w") as f:
            f.write(FAKE_PY % sys.executable)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_no_pip_is_named_as_missing(self):
        lacking = verify.package_missing(python=self.fake_python(), setuptools_dir=None)
        self.assertTrue(any(x.startswith("pip ") for x in lacking), lacking)

    def test_a_missing_interpreter_lacks_everything(self):
        lacking = verify.package_missing(python="/nonexistent/python3")
        self.assertEqual(len(lacking), 3, lacking)

    def test_setuptools_dir_is_checked_not_imported(self):
        with tempfile.TemporaryDirectory() as d:
            lacking = verify.package_missing(setuptools_dir=d)
        self.assertIn("setuptools in %s" % d, lacking)


class CiCoverage(unittest.TestCase):
    """The workflow, read as text, runs the suites that used to be local-only (R6-COR-001/002)."""

    def setUp(self):
        self.runs = verify.ci_runs(ROOT)

    def test_workbench_and_headless_run_in_ci(self):
        for step in ("core", "workbench", "headless", "archives", "bundle", "doc-check"):
            self.assertEqual(verify.ci_covers(step, self.runs), "yes", step)

    def test_doc_check_runs_with_the_schema(self):  # R6-COR-079
        cmd = verify.doc_check_cmd(ROOT)
        self.assertEqual(cmd[1:], ["--schema", os.path.join(ROOT, "docs", "frontmatter.schema.json")])
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(verify.doc_check_cmd(d))
            os.makedirs(os.path.join(d, "tools"))
            open(os.path.join(d, "tools", "doc_check.py"), "w").close()
            self.assertEqual(verify.doc_check_cmd(d), [os.path.join(d, "tools", "doc_check.py")], "no schema: no flag")

    def test_the_doc_check_step_in_ci_uses_the_schema(self):  # R6-COR-079
        self.assertIn("doc_check.py --schema docs/frontmatter.schema.json", self.runs)

    def test_local_only_steps_say_so(self):
        for step in ("quickstart", "console", "path-zsh", "no-such-step"):
            self.assertEqual(verify.ci_covers(step, self.runs), "no", step)

    def test_the_workbench_job_installs_tmux_and_sets_a_test_identity(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
            text = f.read()
        job = text.split("\n  workbench:\n", 1)[1].split("\n  userspace:\n", 1)[0]
        self.assertIn("apt-get install -y -qq tmux", job)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            self.assertIn(k + ":", job)
        self.assertIn("example.invalid", job)
        self.assertNotIn("\t", job, "YAML indentation is spaces only")
        self.assertIn("python -B tools/strict_unittest.py swarm/tests", job)
        self.assertIn("bash swarm/tests/run_tests.sh", job)

    def test_comments_do_not_count_as_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".github", "workflows"))
            with open(os.path.join(d, ".github", "workflows", "x.yml"), "w") as f:
                f.write("# runs console_walkthrough.py one day\njobs:\n  a:\n    steps:\n      - run: echo hi  # package_check.py\n")
            runs = verify.ci_runs(d)
        self.assertEqual(verify.ci_covers("console", runs), "no")
        self.assertEqual(verify.ci_covers("package", runs), "no")


if __name__ == "__main__":
    unittest.main()
