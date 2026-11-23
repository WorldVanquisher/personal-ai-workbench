"""docs/research/tools: the CLAUDE.local.md verification (R6-PRM-064) and experiment E2 harness (R6-PRM-065), in
dry-run mode with their fake agents. No network; git only in temporary folders with a test identity."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "docs", "research", "tools")


def run(script, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", os.path.join(TOOLS, script), *args], capture_output=True, text=True,
                          env=env, timeout=300)


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-prompt-tools-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_verify_claude_local_dry_run_writes_result(self):
        if not shutil.which("git"):
            self.skipTest("git")
        p = run("verify_claude_local.py", "--dry-run", "--out", self.tmp)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(self.tmp, "RESULT.json"), encoding="utf-8") as f:
            r = json.load(f)
        self.assertEqual(r["format"], "pai-verify-claude-local/1")
        self.assertEqual([c["case"] for c in r["cases"]], ["local-only", "agents-shadowed", "agents-imported", "local-plus-claude"])
        self.assertTrue(r["all_pass"])
        self.assertEqual([c["expect_found"] for c in r["cases"]], [True, False, True, True])

    def test_experiment_e2_dry_run_resumes_and_respects_budget(self):
        out = os.path.join(self.tmp, "e2")
        p = run("experiment_e2.py", "--dry-run", "--runs", "1", "--out", out)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = run("experiment_e2.py", "--dry-run", "--runs", "2", "--out", out)
        with open(os.path.join(out, "runs.jsonl"), encoding="utf-8") as f:
            ids = [json.loads(line)["id"] for line in f]
        self.assertEqual(len(ids), 6, "3 from the first call, only the 3 missing ones from the second")
        self.assertEqual(len(set(ids)), 6)
        with open(os.path.join(out, "RESULT.json"), encoding="utf-8") as f:
            r = json.load(f)
        self.assertTrue(r["complete"])
        self.assertEqual(r["variants"]["B"]["mean"], 1.0, "the fake agent's B reply satisfies the first-reply contract")
        self.assertEqual(r["variants"]["A"]["first_ids_pass"], 0.0, "the fake A reply abbreviates IDs")
        p = run("experiment_e2.py", "--dry-run", "--runs", "3", "--budget", "0", "--out", out)
        self.assertIn("budget $0.00 reached", p.stdout)


    def test_experiment_e3_dry_run_separates_the_fake_variants_and_resumes(self):
        out = os.path.join(self.tmp, "e3")
        p = run("experiment_e3.py", "--dry-run", "--runs", "2", "--out", out)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = run("experiment_e3.py", "--dry-run", "--runs", "2", "--out", out)
        self.assertNotIn("score", p.stdout, "a rerun with the same plan starts no new run")
        with open(os.path.join(out, "RESULT.json"), encoding="utf-8") as f:
            r = json.load(f)
        self.assertEqual(r["format"], "pai-experiment-e3/1")
        self.assertEqual(r["variants"]["B"]["mean"], 1.0)
        a = r["variants"]["A"]["per_check"]
        self.assertEqual((a["first5-doable"], a["deps-named"], a["decisions-named"]), (0.0, 0.0, 0.0),
                         "the fake A reply starts with the blocked R9-DOC-002, misses R9-RND-002 and D3")

    def test_e3_task_checks_respect_the_dependency_order(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("e3", os.path.join(TOOLS, "experiment_e3.py"))
        e3 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(e3)
        reply = "**先做的 5 个 ID**：R9-DOC-001、R9-DOC-004、R9-DOC-003、R9-DOC-005、R9-DOC-009\n**依赖其他线的地方**\n- x"
        first = next(c for c in e3.task_checks(reply) if c["id"] == "first5-doable")
        self.assertFalse(first["ok"], "R9-DOC-004 needs R9-DOC-009 first")

if __name__ == "__main__":
    unittest.main()
