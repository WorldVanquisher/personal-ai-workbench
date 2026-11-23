"""The round's vault pages: BOARD.md (generated block, your notes kept), COORDINATOR-LOG.md
(append-only) and the brief (size-bounded)."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import round as rnd  # noqa: E402
import round_board as rb  # noqa: E402


def make_round(tmp):
    vault = os.path.join(tmp, "vault")
    sd = os.path.join(vault, "Agent Inbox", "R9")
    os.makedirs(sd)
    lanes = [{"name": n, "tmux_target": "r9:" + n, "session_name": "W R9 " + n, "worktree": os.path.join(tmp, n),
              "handoff_dir": os.path.join(sd, n), "work_dir": os.path.join(tmp, "work", n)} for n in ("alpha", "beta")]
    return {"format": "pai-round", "version": 1, "round": "R9", "project": "w", "base_sha": "a" * 40, "vault": vault,
            "state_dir": sd, "lanes": lanes, "decision_sheets": ["sheet.md"]}


ROWS = [{"lane": "alpha", "state": "blocked-on-dialog", "dialog": "trust", "window": "r9:1", "target": "r9:alpha", "worktree": "clean",
         "checkpoint": {"name": "cp-A", "restart": "/x/RESTART.md"}, "queue": {"done": 3, "todo": 9, "next": "X-004"}},
        {"lane": "beta", "state": "idle", "window": "r9:2", "target": "r9:beta", "worktree": "2 changed",
         "checkpoint": {"name": "cp-B", "restart": "/y/RESTART.md"}, "queue": None}]


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = make_round(self.tmp)
        with open(os.path.join(self.r["vault"], "sheet.md"), "w", encoding="utf-8") as f:
            f.write("- [ ] **D7 预算**：$5。\n  - 推荐：$5\n- [x] **D8 FLH**：先不贴。\n")

    def test_board_rows_and_open_decisions(self):
        path = rb.write_board(self.r, ROWS, lambda s: None)
        text = slurp(path)
        self.assertIn("| alpha | W R9 alpha | r9:1 | blocked-on-dialog | clean | cp-A（有 RESTART） | done 3", text)
        self.assertIn("**D7 预算**", text)
        self.assertIn("推荐 $5", text)
        self.assertNotIn("D8", text)

    def test_notes_outside_the_block_are_kept(self):
        path = rb.write_board(self.r, None, lambda s: None)
        with open(path, "a", encoding="utf-8") as f:
            f.write("我的笔记：beta 下午再看。\n")
        rb.write_board(self.r, ROWS, lambda s: None)
        text = slurp(path)
        self.assertIn("我的笔记：beta 下午再看。", text)
        self.assertEqual(text.count(rb.BEGIN), 1)
        self.assertIn("blocked-on-dialog", text)

    def test_log_appends_one_line_each(self):
        rb.log_decision(self.r, "beta 先停", "等 core 的 v2 格式", at="2026-11-22T01:00:00Z", day="2026-11-22")
        rb.log_decision(self.r, "多行\n决定 | 有竖线", "理由", at="2026-11-22T02:00:00Z", day="2026-11-22")
        entries = rb.log_entries(self.r)
        self.assertEqual(entries, ["- 2026-11-22T01:00:00Z | 决定：beta 先停 | 理由：等 core 的 v2 格式 | 日志：[[PAI/log/2026-11-22]]",
                                   "- 2026-11-22T02:00:00Z | 决定：多行 决定 / 有竖线 | 理由：理由 | 日志：[[PAI/log/2026-11-22]]"])
        with self.assertRaises(rnd.RoundError):
            rb.log_decision(self.r, "x", " ")

    def test_daily_page_is_a_managed_note_with_the_entries(self):
        rb.log_decision(self.r, "D1", "因为 A", at="2026-11-21T23:00:00Z", day="2026-11-21")
        rb.log_decision(self.r, "D2", "因为 B", at="2026-11-22T01:00:00Z", day="2026-11-22")
        rb.log_decision(self.r, "D3", "因为 C", at="2026-11-22T02:00:00Z", day="2026-11-22")
        page = os.path.join(self.r["vault"], "PAI", "log", "2026-11-22.md")
        text = slurp(page)
        self.assertIn('pai_managed: "log/1"', text)
        self.assertIn("| 2026-11-22T02:00:00Z | R9 | D3 | 因为 C |", text)
        self.assertEqual(len(rb.daily_entries(self.r, "2026-11-22")), 2)
        rb.log_decision(self.r, "D0", "更早", at="2026-11-20T01:00:00Z", day="2026-11-20")
        self.assertEqual([l.split("决定：")[1].split(" |")[0] for l in rb.recent_log(self.r, 2)], ["D1", "D2", "D3"])

    def test_an_edited_daily_page_is_left_alone(self):
        rb.log_decision(self.r, "D1", "A", day="2026-11-22")
        page = os.path.join(self.r["vault"], "PAI", "log", "2026-11-22.md")
        with open(page, "a", encoding="utf-8") as f:
            f.write("我的批注\n")
        before = slurp(page)
        msg = rb.log_decision(self.r, "D2", "B", day="2026-11-22")
        self.assertIn("COORDINATOR-LOG.md only", msg)
        self.assertEqual(slurp(page), before)
        self.assertIn("决定：D2", rb.log_entries(self.r)[-1])

    def test_brief_fits_and_says_what_is_next(self):
        for i in range(15):
            rb.log_decision(self.r, "决定 %d" % i, "理由 %d" % i)
        text, n = rb.brief(self.r, 2000, rows=ROWS)
        self.assertLessEqual(n, 2000)
        self.assertEqual(n, rb.tokens(text))
        self.assertIn("决定 14", text)
        self.assertNotIn("决定 4 ", text)  # only the last 10
        self.assertIn("最近两天的日志", text)
        self.assertIn("alpha：信任提示在屏幕上，由 owner 回答", text)
        self.assertIn("pai round next beta", text)
        self.assertIn("D7", text)

    def test_brief_shrinks_to_the_limit(self):
        for i in range(10):
            rb.log_decision(self.r, "很长的决定" * 20, "很长的理由" * 20)
        full, n_full = rb.brief(self.r, 100000, rows=ROWS)
        small, n_small = rb.brief(self.r, n_full - 200, rows=ROWS)
        self.assertLess(n_small, n_full)
        self.assertLessEqual(n_small, n_full - 200)

    def test_token_estimate(self):
        self.assertEqual(rb.tokens("abcd" * 10), 10)
        self.assertEqual(rb.tokens("中文"), 2)


if __name__ == "__main__":
    unittest.main()
