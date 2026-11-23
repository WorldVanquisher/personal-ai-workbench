"""pai round prplan: NEXT-PR.md -> a wtpr preview plan with the round's fields; reads only."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_round_new import RoundFixture, git, needs_git  # noqa: E402
import round as rnd  # noqa: E402
import round_prplan as pp  # noqa: E402

NEXT_PR = """# NEXT-PR

- 分支：worker/R9-TEST/alpha

## Commits
1. `feat(alpha): add the tool`
   git add tool.py docs
2. `test(alpha): cover the tool`
   git add test_tool.py

## PR 正文草稿
Adds the tool. Lane tree; rerun on merged main needed.

## 合并前要跑
- tests
"""


class Parse(unittest.TestCase):
    def test_parse(self):
        d = pp.parse(NEXT_PR)
        self.assertEqual(d["branch"], "worker/R9-TEST/alpha")
        self.assertEqual([c["message"] for c in d["commits"]], ["feat(alpha): add the tool", "test(alpha): cover the tool"])
        self.assertEqual(d["commits"][0]["paths"], ["tool.py", "docs"])
        self.assertEqual(d["body"], "Adds the tool. Lane tree; rerun on merged main needed.")


@needs_git
class Plan(RoundFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.new("--lane", "alpha")[0], 0)
        self.path = os.path.join(self.vault, "Agent Inbox", "R9-TEST", "ROUND.json")
        self.r = rnd.load_round(self.path)
        self.ln = self.r["lanes"][0]
        wt = self.ln["worktree"]
        for name in ("tool.py", "test_tool.py", "docs/a.md", "docs/b.md", "stray.txt"):
            os.makedirs(os.path.dirname(os.path.join(wt, name)) or wt, exist_ok=True)
            with open(os.path.join(wt, name), "w") as f:
                f.write(name + "\n")
        cp = os.path.join(self.ln["handoff_dir"], "cp-A")
        os.makedirs(cp)
        with open(os.path.join(cp, "NEXT-PR.md"), "w", encoding="utf-8") as f:
            f.write(NEXT_PR)
        with open(os.path.join(cp, "HANDOFF.md"), "w") as f:
            f.write("handoff\n")

    def test_first_commit_preview_with_round_fields(self):
        head = git(self.ln["worktree"], "rev-parse", "HEAD")
        pv = pp.plan(self.r, self.ln, 1, offline=True)
        self.assertEqual(pv["task"], "R9-TEST/alpha#1")
        self.assertEqual(pv["commits"], 2)
        self.assertEqual(pv["handoff_sha256"], rnd.sha256_bytes(b"handoff\n"))
        self.assertIsNone(pv["queue_sha256"])
        text = json.dumps(pv)
        self.assertIn("docs/a.md", text)
        self.assertIn("feat(alpha): add the tool", text)
        self.assertEqual(git(self.ln["worktree"], "rev-parse", "HEAD"), head)  # read only
        self.assertEqual(git(self.ln["worktree"], "diff", "--cached", "--name-only"), "")

    def test_out_writes_a_new_file_only(self):
        out = os.path.join(self.tmp, "plan.json")
        code, text, err = self.run_round("prplan", "alpha", "--round-file", self.path, "--offline", "--out", out)
        self.assertIn(code, (0, 1), err)
        with open(out) as f:
            self.assertEqual(json.load(f)["task"], "R9-TEST/alpha#1")
        self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)
        self.assertIn("--expect-plan", text)
        code, _, err = self.run_round("prplan", "alpha", "--round-file", self.path, "--offline", "--out", out)
        self.assertEqual(code, 2)
        self.assertIn("never overwritten", err)

    def test_wrong_branch_and_bad_commit(self):
        other = os.path.join(self.tmp, "np.md")
        with open(other, "w") as f:
            f.write(NEXT_PR.replace("worker/R9-TEST/alpha", "worker/R9-TEST/beta"))
        with self.assertRaises(rnd.RoundError):
            pp.plan(self.r, self.ln, 1, next_pr=other, offline=True)
        with self.assertRaises(rnd.RoundError):
            pp.plan(self.r, self.ln, 3, offline=True)


if __name__ == "__main__":
    unittest.main()
