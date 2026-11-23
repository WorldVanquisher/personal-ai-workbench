"""pai decide: list a decision sheet, answer one decision only for the exact text that was read."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import decide  # noqa: E402

SHEET = """# 决策单

- [ ] **D1 定位**：PAI 是润滑层。
  - 推荐：同意。
- [x] **D2 状态存储**：vault 是唯一的状态存储。
  - 推荐：同意。
- [ ] **D4 本轮范围**：用精简版替换 60 条的 R6。
  - 选项：A 精简版；B 60 条
  - 推荐：A，因为额度有限。
  - 默认：需要明确勾选
  - 阻塞：R6-RND-001

正文里的 - [ ] 不算，因为没有加粗的编号。
"""


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Decide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "DECISIONS.md")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SHEET)

    def items(self):
        return {d["id"]: d for d in decide.parse(slurp(self.path))}

    def test_parse(self):
        d = self.items()
        self.assertEqual(list(d), ["D1", "D2", "D4"])
        self.assertTrue(d["D2"]["answered"])
        self.assertEqual(d["D4"]["fields"]["recommendation"], "A，因为额度有限。")
        self.assertEqual(d["D4"]["fields"]["blocks"], "R6-RND-001")
        self.assertEqual(d["D4"]["title"], "本轮范围")
        self.assertEqual([x["id"] for x in decide.open_items(self.path)], ["D1", "D4"])

    def test_answer_with_the_listed_sha(self):
        sha = self.items()["D4"]["sha"][:12]
        decide.answer(self.path, "D4", sha, "A", write=True, at="2026-11-22T00:00:00Z")
        text = slurp(self.path)
        self.assertIn("- [x] **D4 本轮范围**", text)
        self.assertIn("  - 回答：A（by pai decide, 2026-11-22T00:00:00Z）\n\n正文", text)
        self.assertTrue(self.items()["D4"]["answered"])
        self.assertEqual(self.items()["D4"]["fields"]["answer"], "A（by pai decide, 2026-11-22T00:00:00Z）")
        self.assertIn("- [ ] **D1", text)  # nothing else changed

    def test_a_changed_decision_is_not_approved(self):
        sha = self.items()["D4"]["sha"][:12]
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SHEET.replace("推荐：A，因为额度有限。", "推荐：B。"))
        with self.assertRaises(decide.DecideError) as e:
            decide.answer(self.path, "D4", sha, "A", write=True)
        self.assertTrue(str(e.exception).startswith("MISMATCH"))
        self.assertNotIn("回答", slurp(self.path))

    def test_preview_writes_nothing_and_answered_is_refused(self):
        d = self.items()
        decide.answer(self.path, "D1", d["D1"]["sha"][:12], "同意")
        self.assertEqual(slurp(self.path), SHEET)
        with self.assertRaises(decide.DecideError) as e:
            decide.answer(self.path, "D2", d["D2"]["sha"][:12], "同意")
        self.assertTrue(str(e.exception).startswith("REFUSED"))

    def test_bad_input(self):
        sha = self.items()["D1"]["sha"][:12]
        for did, s, choice in (("D9", sha, "x"), ("D1", "zz", "x"), ("D1", sha, ""), ("D1", sha, "a\nb")):
            with self.subTest(did=did, s=s, choice=choice), self.assertRaises(decide.DecideError):
                decide.answer(self.path, did, s, choice)

    def test_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(decide.main(["list", self.path, "--open"]), 0)
        self.assertIn("D4", out.getvalue())
        self.assertNotIn("D2", out.getvalue())
        sha = self.items()["D1"]["sha"][:12]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(decide.main(["answer", self.path, "D1", "--sha", "0" * 12, "--choice", "x", "--write"]), 1)
            self.assertEqual(decide.main(["answer", self.path, "D1", "--sha", sha, "--choice", "同意", "--write"]), 0)
        self.assertTrue(self.items()["D1"]["answered"])


if __name__ == "__main__":
    unittest.main()
