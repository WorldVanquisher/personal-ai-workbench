"""The strict test runner (1.0 readiness 089): a file left open, or a ResourceWarning raised in a
finalizer, fails the run even though `-W error::ResourceWarning -m unittest` only prints
"Exception ignored" for it. These are the regression checks for the file-handle fixes: the
suites run under this runner (tools/verify.py, CI on Linux), so a reintroduced leak fails them."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRICT = ROOT / "tools" / "strict_unittest.py"
CASES = {
    "clean": "def test_ok(self):\n        with open(__file__) as f:\n            f.read()\n",
    "leak": "def test_leak(self):\n        open(__file__).read()\n",
    "finalizer": ("def test_finalizer(self):\n        import warnings\n\n        class Holder:\n"
                  "            def __del__(self):\n                warnings.warn('unclosed thing', ResourceWarning)\n\n"
                  "        Holder()\n"),
    "failing": "def test_fails(self):\n        self.assertEqual(1, 2)\n",
    # R6-COR-047: setUpClass fails after creating a TemporaryDirectory, so tearDownClass never cleans it
    "tempdir": ("@classmethod\n    def setUpClass(cls):\n        import tempfile\n        cls.t = tempfile.TemporaryDirectory()\n"
                "        raise RuntimeError('build failed')\n\n    def test_x(self):\n        pass\n"),
}


class StrictRunner(unittest.TestCase):
    def run_case(self, body):
        d = tempfile.mkdtemp(prefix="pai-strict-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "test_case.py"), "w") as f:
            f.write("import unittest\n\n\nclass T(unittest.TestCase):\n    %s" % body)
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONWARNINGS",)}
        return subprocess.run([sys.executable, "-B", str(STRICT), d], capture_output=True, text=True, env=env, timeout=120)

    def test_clean_passes_and_each_problem_fails(self):
        p = self.run_case(CASES["clean"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("strict_unittest: OK", p.stdout + p.stderr)
        for name in ("leak", "finalizer", "failing", "tempdir"):
            p = self.run_case(CASES[name])
            self.assertEqual(p.returncode, 1, "%s: %s" % (name, p.stdout + p.stderr))
            self.assertIn("strict_unittest: FAILED", p.stdout + p.stderr, name)

    def test_the_plain_runner_would_have_passed_the_finalizer_case(self):
        d = tempfile.mkdtemp(prefix="pai-strict-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "test_case.py"), "w") as f:
            f.write("import unittest\n\n\nclass T(unittest.TestCase):\n    %s" % CASES["finalizer"])
        p = subprocess.run([sys.executable, "-B", "-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", d],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, "why the strict runner exists: the plain command misses it")

    def test_jobs_runs_each_module_in_its_own_strict_process(self):
        """R6-COR-007: --jobs N keeps the strict checks per module and fails on any failing module."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for name in ("clean", "leak", "failing"):
            with open(os.path.join(d, "test_%s.py" % name), "w") as f:
                f.write("import unittest\n\n\nclass T(unittest.TestCase):\n    %s" % CASES[name])
        def run(*extra):
            return subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "strict_unittest.py"), d, "--jobs", "3"]
                                  + list(extra), capture_output=True, text=True, timeout=120)
        p = run()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAILED (modules: ", p.stderr)
        self.assertIn("test_leak.py", p.stderr)
        self.assertIn("test_failing.py", p.stderr)
        self.assertNotIn("test_clean.py", p.stderr.split("FAILED (modules: ")[1])
        self.assertIn("Ran 3 tests", p.stdout)
        p = run("--pattern", "test_clean.py")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("OK (1 modules in 3 processes", p.stderr)


if __name__ == "__main__":
    unittest.main()
