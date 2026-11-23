"""Round state as vault notes (D9 (a)): front matter, plain tables next to Dataview, idempotent
rewrites, and never touching the owner's notes, edited notes, broken markers or notes with a
sync conflict copy. Decision notes answered from the owner's `choice` with the sha check."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import decide  # noqa: E402
import round_vault as rv  # noqa: E402
from test_round_board import make_round  # noqa: E402


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def rows():
    return [{"lane": "alpha", "state": "blocked-on-dialog", "dialog": "trust", "why": "dialog", "window": "r9:1", "worktree": "clean",
             "checkpoint": {"name": "cp-A", "restart": None},
             "queue": {"done": 3, "todo": 9, "doing": 0, "blocked": 1, "prototype": 0, "owner-only": 0, "next": "X-004",
                       "doing_ids": [], "blocked_items": [{"id": "X-002", "reason": "needs core v2"}]}},
            {"lane": "beta", "state": "running", "why": "working", "window": "r9:2", "worktree": "2 changed",
             "checkpoint": None, "queue": {"done": 1, "todo": 2, "doing": 1, "next": "Y-002", "doing_ids": ["Y-002"],
                                           "blocked_items": []}}]


SHEET = "- [ ] **D7 预算**：$5 够不够。\n  - 选项：A $5；B $10\n  - 推荐：A\n  - 默认：A\n  - 阻塞：R9-ALP-003\n- [x] **D8 FLH**：先不贴。\n"


class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = make_round(self.tmp)
        self.r["_path"] = os.path.join(self.r["state_dir"], "ROUND.json")
        self.r["status"] = "open"
        with open(os.path.join(self.r["vault"], "sheet.md"), "w", encoding="utf-8") as f:
            f.write(SHEET)
        with open(self.r["_path"], "w") as f:
            import json
            json.dump(self.r, f)
        cp = os.path.join(self.r["lanes"][0]["handoff_dir"], "cp-A")
        os.makedirs(cp)
        with open(os.path.join(cp, "HANDOFF.md"), "w") as f:
            f.write("---\nlane: alpha\ncheckpoint: cp-A\nstatus: sealed\napproved: false\n---\n# h\n")
        self.pai = os.path.join(self.r["vault"], "PAI")
        self.lane_a = os.path.join(self.pai, "rounds", "R9", "alpha.md")
        self.log = []

    def write(self, collect=None):
        return rv.write_vault(self.r, rows(), collect, self.log.append)

    def test_lane_round_home_and_decision_notes(self):
        res = self.write({"lanes": [{"lane": "beta", "jev_verdict": "DISAGREES", "sums": "ok", "checkpoint": "cp-A"}]})
        self.assertTrue(all(v == "created" for v in res.values()), res)
        fm, _, body = rv.split(slurp(self.lane_a))
        self.assertEqual((fm["lane"], fm["status"], fm["current_id"], fm["last_cp"]), ("alpha", "blocked-on-dialog", "X-004", "cp-A"))
        self.assertEqual(fm["totals"]["done"], 3)
        self.assertEqual(fm["blockers"], ["X-002: needs core v2"])
        self.assertIs(fm["needs_owner"], True)
        self.assertIn("| 当前条目 | X-004 |", body)  # readable without plugins
        self.assertIn("| 交接目录 | [[vault/Agent Inbox/R9/alpha\\|alpha]] |".replace("vault/", ""), body)
        for line in body.splitlines():  # every table row keeps its column count: no bare | inside a cell
            if line.startswith("| ") and "[[" in line:
                self.assertEqual(len([m for m in __import__("re").finditer(r"(?<!\\)\|", line)]), 3, line)
        fb, _, _ = rv.split(slurp(os.path.join(self.pai, "rounds", "R9", "beta.md")))
        self.assertIs(fb["needs_owner"], True)  # DISAGREES from collect (R6-RND-044)
        self.assertEqual(fb["current_id"], "Y-002")
        self.assertIn("claims and its evidence disagree", fb["needs_owner_why"][0])
        home = slurp(os.path.join(self.pai, "Home.md"))
        self.assertEqual(home.count("```dataview"), 4)
        self.assertIn("[[PAI/rounds/R9/alpha\\|alpha]]", home)
        self.assertIn("| [[PAI/decisions/R9-D7\\|D7]] | R9 | 预算：$5 够不够。 | A |", home)
        self.assertNotIn("D8", home.split("还没回答的决策")[1].split("##")[0])
        self.assertIn("| R9 | alpha | cp-A |", home)  # unapproved handoff
        dfm, _, _ = rv.split(slurp(os.path.join(self.pai, "decisions", "R9-D7.md")))
        self.assertEqual((dfm["status"], dfm["options"], dfm["default"], dfm["blocks"]), ("open", ["A $5", "B $10"], "A", ["R9-ALP-003"]))

    def test_rewriting_the_same_state_changes_nothing(self):
        self.write()
        before = {p: slurp(p) for p in self.all_notes()}
        res = self.write()
        self.assertEqual(set(res.values()), {"unchanged"})
        self.assertEqual({p: slurp(p) for p in self.all_notes()}, before)

    def all_notes(self):
        out = []
        for d, _, files in os.walk(self.pai):
            out += [os.path.join(d, f) for f in files]
        return sorted(out)

    def test_owner_edits_are_kept(self):
        self.write()
        with open(self.lane_a, "a", encoding="utf-8") as f:
            f.write("我的批注：alpha 明天再看。\n")
        text = slurp(self.lane_a)
        res = self.write()
        self.assertEqual(res[self.lane_a], "SKIPPED edited")
        self.assertEqual(slurp(self.lane_a), text)

    def test_owner_note_without_marker_is_never_written(self):
        os.makedirs(self.pai)
        mine = "# 我自己的 PAI 首页\n"
        with open(os.path.join(self.pai, "Home.md"), "w", encoding="utf-8") as f:
            f.write(mine)
        res = self.write()
        self.assertEqual(res[os.path.join(self.pai, "Home.md")], "SKIPPED unmanaged")
        self.assertEqual(slurp(os.path.join(self.pai, "Home.md")), mine)

    def test_broken_markers_are_not_repaired(self):
        self.write()
        text = slurp(self.lane_a).replace(rv.END, "")
        with open(self.lane_a, "w", encoding="utf-8") as f:
            f.write(text)
        self.assertEqual(self.write()[self.lane_a], "SKIPPED broken")
        self.assertEqual(slurp(self.lane_a), text)

    def test_sync_conflict_copy_blocks_the_note_and_is_never_read(self):
        self.write()
        conflict = self.lane_a[:-3] + ".sync-conflict-20261122-120000-ABCDEFG.md"
        shutil.copy(self.lane_a, conflict)
        before = slurp(self.lane_a)
        r2 = rows()
        r2[0]["state"] = "idle"
        res = rv.write_vault(self.r, r2, None, self.log.append)
        self.assertEqual(res[self.lane_a], "SKIPPED conflict")
        self.assertEqual(slurp(self.lane_a), before)
        self.assertNotIn(conflict, [p for p, _ in rv.notes_in(os.path.dirname(self.lane_a))])
        self.assertTrue(rv.is_conflict_copy(conflict))
        self.assertTrue(rv.is_conflict_copy("/v/Home (conflicted copy 2026-11-22).md"))
        self.assertFalse(rv.is_conflict_copy(self.lane_a))


class CaseSafeNames(unittest.TestCase):
    def test_a_lane_called_round_does_not_collide_with_ROUND_md(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        r = make_round(tmp)
        r["lanes"][0]["name"] = "round"
        row = rows()
        row[0]["lane"] = "round"
        rv.write_vault(r, row, None, lambda s: None)
        names = os.listdir(os.path.join(r["vault"], "PAI", "rounds", "R9"))
        self.assertEqual(sorted(names), ["ROUND.md", "beta.md", "round-lane.md"])
        self.assertEqual(len({n.casefold() for n in names}), len(names))
        self.assertIn("[[PAI/rounds/R9/round-lane\\|round]]", slurp(os.path.join(r["vault"], "PAI", "rounds", "R9", "ROUND.md")))


class DecisionNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.r = make_round(self.tmp)
        self.sheet = os.path.join(self.r["vault"], "sheet.md")
        with open(self.sheet, "w", encoding="utf-8") as f:
            f.write(SHEET)
        rv.decision_notes(self.r, lambda s: None)
        self.note = os.path.join(self.r["vault"], "PAI", "decisions", "R9-D7.md")

    def choose(self, value):
        text = slurp(self.note).replace('choice: ""', "choice: %s" % value, 1)  # as typed in Obsidian
        with open(self.note, "w", encoding="utf-8") as f:
            f.write(text)

    def test_owner_choice_survives_regeneration_and_answers_the_sheet(self):
        self.choose("A")
        self.assertEqual(rv.check(self.note, "decision/1")[0], "managed")  # choice is the owner's field
        self.assertEqual(dict(rv.decision_notes(self.r, lambda s: None))[self.note], "unchanged")
        self.assertEqual(rv.split(slurp(self.note))[0]["choice"], "A")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rv.answer_from_note(self.note, write=False)
        self.assertNotIn("回答", slurp(self.sheet))  # preview
        rv.answer_from_note(self.note, write=True, log=lambda s: None)
        self.assertIn("- [x] **D7 预算**", slurp(self.sheet))
        self.assertIn("回答：A（by pai decide", slurp(self.sheet))
        fm, _, body = rv.split(slurp(self.note))
        self.assertEqual((fm["status"], fm["answer"]), ("answered", "A"))
        self.assertEqual(rv.check(self.note, "decision/1")[0], "managed")
        with self.assertRaises(decide.DecideError):
            rv.answer_from_note(self.note, write=True, log=lambda s: None)  # answered once only

    def test_a_changed_source_decision_is_refused(self):
        self.choose("A")
        with open(self.sheet, "w", encoding="utf-8") as f:
            f.write(SHEET.replace("推荐：A", "推荐：B"))
        with self.assertRaises(decide.DecideError) as e:
            rv.answer_from_note(self.note, write=True, log=lambda s: None)
        self.assertTrue(str(e.exception).startswith("MISMATCH"))
        self.assertNotIn("回答", slurp(self.sheet))

    def test_empty_unknown_or_tampered_choice_is_refused(self):
        with self.assertRaises(decide.DecideError):
            rv.answer_from_note(self.note, log=lambda s: None)
        self.choose("C")
        with self.assertRaises(decide.DecideError) as e:
            rv.answer_from_note(self.note, log=lambda s: None)
        self.assertIn("not one of the options", str(e.exception))
        text = slurp(self.note).replace("choice: C", "choice: A").replace('default: "A"', 'default: "B"')
        with open(self.note, "w", encoding="utf-8") as f:
            f.write(text)
        with self.assertRaises(decide.DecideError) as e:
            rv.answer_from_note(self.note, log=lambda s: None)
        self.assertIn("edited", str(e.exception))

    def test_cli(self):
        self.choose("B")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(decide.main(["answer", "--from-note", self.note, "--write"]), 0)
            self.assertEqual(decide.main(["answer", "--from-note", self.note, "--choice", "A"]), 2)
        self.assertIn("回答：B", slurp(self.sheet))


if __name__ == "__main__":
    unittest.main()
