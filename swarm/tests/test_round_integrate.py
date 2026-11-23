"""pai round integrate with synthetic lane patches: checkpoint patch vs worktree fallback, order,
conflicts reported and kept going, hot files, dependencies, a merge plan draft, --test on the
merged copy, INTEGRATION.md as a managed note; the repository itself is never changed."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_round_new import RoundFixture, git, needs_git  # noqa: E402
import round as rnd  # noqa: E402
import round_integrate as ri  # noqa: E402

VERIFY = '''import json, os, sys
w = sys.argv[sys.argv.index("--work") + 1]
os.makedirs(w)
ok = open("README.md").read().count("alpha") == 1
json.dump({"steps": [{"step": "core", "result": "PASS" if ok else "FAIL", "seconds": 1, "detail": "1 test"}]},
          open(os.path.join(w, "verify-report.json"), "w"))
print("core PASS" if ok else "core FAIL")
sys.exit(0 if ok else 1)
'''


def diff_new(path, text):
    lines = text.splitlines()
    return "diff --git a/{0} b/{0}\nnew file mode 100644\n--- /dev/null\n+++ b/{0}\n@@ -0,0 +1,{1} @@\n{2}\n".format(
        path, len(lines), "\n".join("+" + l for l in lines))


@needs_git
class Integrate(RoundFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.new("--lane", "alpha", "--lane", "beta", "--lane", "gamma")[0], 0)
        self.path = os.path.join(self.vault, "Agent Inbox", "R9-TEST", "ROUND.json")
        self.r = rnd.load_round(self.path)
        lanes = {l["name"]: l for l in self.r["lanes"]}
        # alpha: a sealed checkpoint patch (README line + tools/verify.py + allowlist)
        cp = os.path.join(lanes["alpha"]["handoff_dir"], "cp-A")
        os.makedirs(cp)
        patch = ("diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n hello\n+alpha\n"
                 + diff_new("tools/verify.py", VERIFY) + diff_new("release/allowlist.txt", "swarm/alpha.py"))
        with open(os.path.join(cp, "cumulative.patch"), "w") as f:
            f.write(patch)
        with open(os.path.join(cp, "HANDOFF.md"), "w") as f:
            f.write("# h\n## Blockers\n- 等 core 的 HANDOFF-V2 定稿\n## Next\nx\n")
        with open(os.path.join(cp, "NEXT-PR.md"), "w") as f:
            f.write("# NEXT-PR\n## Commits\n1. `feat(alpha): add alpha`\n   git add a\n## 合并前要跑\n- tests\n")
        # beta: no patch in its checkpoint, so its worktree is used: the same README line -> conflict
        os.makedirs(os.path.join(lanes["beta"]["handoff_dir"], "cp-A"))
        with open(os.path.join(lanes["beta"]["worktree"], "README.md"), "a") as f:
            f.write("beta\n")
        with open(os.path.join(lanes["beta"]["worktree"], "b.txt"), "w") as f:
            f.write("b\n")
        # gamma: only a proposal that also creates release/allowlist.txt (a hot file)
        os.makedirs(os.path.join(lanes["gamma"]["handoff_dir"], "proposals"))
        with open(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "allowlist.patch"), "w") as f:
            f.write(diff_new("release/allowlist.txt", "swarm/gamma.py"))
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.lines = []

    def run_it(self, **kw):
        return ri.integrate(self.r, log=self.lines.append, **kw)

    def test_plan(self):
        rep = self.run_it(order=["alpha", "beta", "gamma"])
        by = {p["name"]: p for p in rep["patches"]}
        self.assertEqual([p["name"] for p in rep["patches"]], ["alpha-lane", "beta-lane", "gamma-allowlist"])
        self.assertEqual(by["alpha-lane"]["result"], "ok")
        self.assertEqual(by["alpha-lane"]["source"], "checkpoint")
        self.assertTrue(by["beta-lane"]["source"].startswith("worktree"))
        self.assertEqual(by["beta-lane"]["result"], "CONFLICT")
        self.assertEqual(by["beta-lane"]["files"], ["README.md"])
        self.assertEqual(by["gamma-allowlist"]["result"], "ok (line union)")  # a set of lines: merged as a union
        self.assertEqual(by["gamma-allowlist"]["union"]["release/allowlist.txt"]["missing_file"], ["swarm/gamma.py"])
        self.assertEqual(rep["unions"][0]["patch"], "gamma-allowlist")
        tree = rep["tree"]
        with open(os.path.join(tree, "b.txt")) as f:  # the rest of beta still applied
            self.assertEqual(f.read(), "b\n")
        hot = {h["path"]: h["patches"] for h in rep["hot_files"]}
        self.assertEqual(rep["hot_files"][0]["path"], "release/allowlist.txt")  # known hot spot first
        self.assertEqual(hot["README.md"], ["alpha-lane", "beta-lane"])
        self.assertEqual(rep["dependencies"][0]["text"], "等 core 的 HANDOFF-V2 定稿")
        self.assertEqual(rep["pr_plan"][0]["commits"], ["feat(alpha): add alpha"])
        self.assertEqual(rep["pr_plan"][0]["checks"], ["tests"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(rep["test"], None)

    def test_order_and_no_proposals(self):
        rep = self.run_it(order=["beta", "alpha"], proposals="none")
        self.assertEqual([p["name"] for p in rep["patches"]], ["beta-lane", "alpha-lane"])
        self.assertEqual(rep["patches"][1]["result"], "CONFLICT")  # alpha now meets beta's README line
        with self.assertRaises(rnd.RoundError):
            self.run_it(order=["nope"])

    def test_test_runs_verify_on_the_merged_copy(self):
        rep = self.run_it(order=["alpha", "gamma"])
        self.assertEqual(rep["test"], None)
        rep = self.run_it(order=["alpha"], proposals="none", test=True)
        self.assertEqual(rep["test"]["exit"], 0)
        self.assertEqual(rep["test"]["steps"]["core"]["status"], "PASS")
        rep = self.run_it(order=["alpha", "beta"], proposals="none", test=True)  # beta's partial apply leaves alpha's line
        self.assertIn(rep["test"]["exit"], (0, 1))

    def test_write_integration_note(self):
        rep = self.run_it(order=["alpha", "beta", "gamma"])
        res = ri.write(self.r, rep, self.lines.append)
        self.assertEqual(res, "created")
        note = os.path.join(self.r["state_dir"], "INTEGRATION.md")
        with open(note, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('pai_managed: "integration/1"', text)
        self.assertIn("## 冲突（1）", text)
        self.assertIn("## 自动合并", text)
        self.assertIn("README.md", text)
        self.assertIn("feat(alpha): add alpha", text)
        self.assertIn("PAI 不会 push", text)
        with open(os.path.join(self.r["state_dir"], "INTEGRATION.json")) as f:
            self.assertEqual(json.load(f)["order"], ["alpha", "beta", "gamma"])

    def test_union_adds_lines_for_files_in_the_copy_and_never_duplicates(self):
        lanes = {l["name"]: l for l in self.r["lanes"]}
        with open(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "allowlist.patch"), "w") as f:
            f.write(diff_new("release/allowlist.txt", "swarm/alpha.py\nREADME.md\ntools/verify.py"))
        rep = self.run_it(order=["alpha", "gamma"])
        u = rep["patches"][-1]["union"]["release/allowlist.txt"]
        self.assertEqual((u["present"], u["added"]), (["swarm/alpha.py"], ["README.md", "tools/verify.py"]))
        with open(os.path.join(rep["tree"], "release", "allowlist.txt")) as f:
            self.assertEqual(f.read().split(), ["swarm/alpha.py", "README.md", "tools/verify.py"])

    def test_a_base_to_both_proposal_is_already_contained(self):
        lanes = {l["name"]: l for l in self.r["lanes"]}
        os.remove(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "allowlist.patch"))
        with open(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "readme-both.patch"), "w") as f:
            f.write("--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n hello\n+alpha\n")  # what alpha already did
        rep = self.run_it(order=["alpha", "gamma"])
        self.assertEqual(rep["patches"][-1]["result"], "ok (already in the merged copy)")
        self.assertEqual(rep["conflicts"], [])
        with open(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "readme-both.patch"), "w") as f:
            f.write("--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n hello\n+other\n")
        rep = self.run_it(order=["alpha", "gamma"])
        self.assertEqual(rep["patches"][-1]["result"], "CONFLICT")

    def test_cli(self):
        code, out, err = self.run_round("integrate", self.path, "--plan", "--order", "alpha", "--proposals", "none", "--write")
        self.assertEqual(code, 0, out + err)
        self.assertIn("0 conflict(s)", out)
        code, out, err = self.run_round("integrate", self.path)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
