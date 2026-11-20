"""pai queue: a worker's numbered items, their states and the next one, kept in a queue file and an
append-only log instead of a conversation (0.5.0 candidate, R2-041..R2-050). Core command: runs on
every system, no workbench needed."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAI = os.path.join(ROOT, "bin", "pai.py")

ITEMS = "\n".join("Q-%03d. Item number %d" % (i, i) for i in range(1, 11)) + "\n"


class Queue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-queue-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.q = os.path.join(self.tmp, "work queue.json")  # a space in the path on purpose
        with open(os.path.join(self.tmp, "items.md"), "w", encoding="utf-8") as f:
            f.write("# Ten items\n\n" + ITEMS)
        self.ok("queue", "init", self.q, "--from", os.path.join(self.tmp, "items.md"), "--name", "demo",
                "--checkpoint", "Q-005", "--checkpoint", "Q-010", "--write")

    def pai(self, *args, session=None):
        env = dict(os.environ)
        env.pop("PAI_QUEUE_SESSION", None)
        if session:
            env["PAI_QUEUE_SESSION"] = session
        return subprocess.run([sys.executable, "-B", PAI] + list(args), capture_output=True, text=True, env=env,
                              timeout=60, cwd=self.tmp)

    def ok(self, *args, session=None):
        p = self.pai(*args, session=session)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def status(self):
        return json.loads(self.ok("queue", "status", self.q, "--json"))

    def log_lines(self):
        with open(self.q + ".log", "rb") as f:
            return f.read().split(b"\n")

    def test_init_previews_then_writes_once(self):
        p = self.pai("queue", "init", self.q, "--from", os.path.join(self.tmp, "items.md"), "--write")
        self.assertEqual(p.returncode, 2)
        self.assertIn("exists; nothing was written", p.stderr)
        other = os.path.join(self.tmp, "other.json")
        out = self.ok("queue", "init", other, "--from", os.path.join(self.tmp, "items.md"))
        self.assertIn("PREVIEW (nothing written): %s: 10 item(s) Q-001 .. Q-010" % other, out)
        self.assertFalse(os.path.exists(other))

    def test_next_comes_from_the_files_alone(self):
        """R2-041: a fresh process (no conversation) finds the next item."""
        self.assertEqual(self.ok("queue", "next", self.q).splitlines()[0], "Q-001 Item number 1")
        self.ok("queue", "mark", self.q, "Q-001", "done", "--evidence", "tests/a.log")
        self.assertEqual(self.ok("queue", "next", self.q).splitlines()[0], "Q-002 Item number 2")

    def test_states_need_their_reasons_and_evidence(self):
        """R2-042."""
        for state, extra, needle in (("blocked", [], "blocked needs --reason"), ("owner-only", [], "owner-only needs --reason"),
                                     ("prototype", ["--evidence", "x"], "prototype needs --reason"),
                                     ("prototype", ["--reason", "no browser"], "prototype needs --evidence"),
                                     ("done", [], "done needs --evidence")):
            p = self.pai("queue", "mark", self.q, "Q-002", state, *extra)
            self.assertEqual(p.returncode, 2, state)
            self.assertIn(needle, p.stderr)
        self.assertFalse(os.path.exists(self.q + ".log"), "nothing was recorded")
        p = self.pai("queue", "mark", self.q, "Q-099", "done", "--evidence", "x")
        self.assertEqual(p.returncode, 2)
        self.assertIn("Q-099 is not an item", p.stderr)
        self.ok("queue", "mark", self.q, "Q-002", "blocked", "--reason", "needs the owner's due-date schema decision")
        self.ok("queue", "mark", self.q, "Q-003", "owner-only", "--reason", "real agent run")
        self.ok("queue", "mark", self.q, "Q-004", "prototype", "--reason", "no real browser", "--evidence", "api test")
        st = self.status()
        self.assertEqual(st["blocked"], [{"id": "Q-002", "reason": "needs the owner's due-date schema decision"}])
        self.assertEqual(st["owner_only"], [{"id": "Q-003", "reason": "real agent run"}])
        self.assertEqual(st["prototype"], ["Q-004"])

    def test_progress_is_replayed_from_the_log_and_a_torn_line_is_ignored(self):
        """R2-043, R2-048: a stop at any moment loses at most the line being written."""
        self.ok("queue", "mark", self.q, "Q-001", "done", "--evidence", "a")
        self.ok("queue", "mark", self.q, "Q-002", "done", "--evidence", "b")
        with open(self.q + ".log", "ab") as f:
            f.write(b'{"event": "state", "id": "Q-003", "state": "do')  # the process was killed mid-write
        p = self.pai("queue", "status", self.q, "--json")
        self.assertEqual(p.returncode, 0)
        st = json.loads(p.stdout)
        self.assertEqual(st["done"], ["Q-001", "Q-002"])
        self.assertEqual(st["next"]["id"], "Q-003")
        self.assertIn("incomplete (a write was interrupted); it was ignored", p.stderr)
        self.ok("queue", "mark", self.q, "Q-003", "done", "--evidence", "c")  # the next append starts on a new line
        lines = self.log_lines()
        self.assertTrue(lines[-2].startswith(b'{"at"'), lines[-3:])
        self.assertEqual(self.status()["done"], ["Q-001", "Q-002", "Q-003"])

    def test_handoffs_carry_ids_and_mismatched_totals_are_found(self):
        """R2-044, R2-045."""
        for i in (1, 2, 4):
            self.ok("queue", "mark", self.q, "Q-%03d" % i, "done", "--evidence", "e%d" % i)
        self.ok("queue", "mark", self.q, "Q-003", "blocked", "--reason", "waits for Q-099")
        table = self.ok("queue", "table", self.q)
        self.assertIn("| Q-004 | done | e4 |", table)
        self.assertIn("Totals: todo 6, doing 0, done 3, prototype 0, blocked 1, owner-only 0 (10 items)", table)
        good = os.path.join(self.tmp, "good.md")
        with open(good, "w") as f:
            f.write("# Handoff\n\n" + table + "\n")
        self.assertIn("ok: 10 row(s) and the totals line agree", self.ok("queue", "check", self.q, good))
        bad = os.path.join(self.tmp, "bad.md")
        with open(bad, "w") as f:
            f.write("| ID | State | E |\n| --- | --- | --- |\n| Q-001 | done | e1 |\n| Q-002 | done | e2 |\n"
                    "| Q-003 | done | e3 |\n| Q-004 | done | e4 |\n| Q-004 | done | again |\n\n"
                    "Totals: done 5, blocked 0 (10 items)\n")
        p = self.pai("queue", "check", self.q, bad)
        self.assertEqual(p.returncode, 1)
        for needle in ("Q-004 has 2 rows in the handoff", "Q-003: the handoff says done, the queue log says blocked "
                       "(waits for Q-099)", "totals line says done 5, but the handoff's table has 4 done row(s)",
                       "totals line says done 5, but the queue log has 3", "totals line says blocked 0, but the queue "
                       "log has 1"):
            self.assertIn(needle, p.stdout)
        p = self.pai("queue", "checkpoint", self.q, "Q-005", "--handoff", bad)
        self.assertEqual(p.returncode, 2, "a checkpoint is refused while its handoff disagrees")
        self.assertIn("no checkpoint was recorded", p.stderr)
        out = self.ok("queue", "checkpoint", self.q, "Q-005", "--handoff", good)
        self.assertIn("checkpoint Q-005 recorded (handoff sha256 ", out)
        cp = [json.loads(l) for l in self.log_lines() if l.strip()][-1]
        self.assertEqual((cp["event"], cp["done"], cp["counts"]["blocked"]), ("checkpoint", ["Q-001", "Q-002", "Q-004"], 1))

    def test_a_blocked_item_stays_visible_while_work_moves_on(self):
        """R2-046, R2-049."""
        self.ok("queue", "mark", self.q, "Q-001", "blocked", "--reason", "owner decision: schema")
        nxt = self.ok("queue", "next", self.q)
        self.assertEqual(nxt.splitlines(), ["Q-002 Item number 2", "(blocked, skipped: Q-001)"])
        out = self.ok("queue", "status", self.q)
        self.assertEqual(out.splitlines()[0], "queue demo: 10 item(s): done 0, prototype 0, blocked 1, owner-only 0, "
                                              "doing 0, todo 9")
        self.assertIn("  BLOCKED     Q-001: owner decision: schema", out)
        self.assertIn("  todo        Q-002..Q-010", out)
        self.assertIn("next: Q-002 Item number 2", out)

    def test_restart_prompt_from_the_checkpoint_state(self):
        """R2-047, R2-048: written from the files; an intentional stop between writing the handoff
        and recording its checkpoint loses nothing."""
        for i in range(1, 5):
            self.ok("queue", "mark", self.q, "Q-%03d" % i, "done", "--evidence", "e")
        self.ok("queue", "mark", self.q, "Q-005", "blocked", "--reason", "needs a WSL machine")
        hand = os.path.join(self.tmp, "h5.md")
        with open(hand, "w") as f:
            f.write(self.ok("queue", "table", self.q))
        self.ok("queue", "checkpoint", self.q, "Q-005", "--handoff", hand)
        self.ok("queue", "mark", self.q, "Q-006", "doing")
        # stop here: Q-006 in progress, the next handoff not written yet
        prompt = self.ok("queue", "restart", self.q)
        for needle in ("Resume the work queue demo (%s)" % self.q, "done (4): Q-001..Q-004",
                       "BLOCKED Q-005: needs a WSL machine (keep it visible; continue with independent items)",
                       "in progress when it stopped: Q-006", "remaining todo (4): Q-007..Q-010",
                       "last checkpoint: Q-005 at ", "next checkpoint: Q-010", "Next item: Q-006 Item number 6",
                       'pai queue status "%s"' % self.q):
            self.assertIn(needle, prompt)

    def test_ten_items_across_two_sessions_with_one_blocked(self):
        """R2-050."""
        for i in range(1, 4):
            self.ok("queue", "mark", self.q, "Q-%03d" % i, "done", "--evidence", "s1 e%d" % i, session="session-1")
        self.ok("queue", "mark", self.q, "Q-004", "blocked", "--reason", "owner decision", session="session-1")
        self.ok("queue", "mark", self.q, "Q-005", "doing", session="session-1")
        # session 1 ends without a word; session 2 knows only the file
        prompt = self.ok("queue", "restart", self.q)
        self.assertIn("in progress when it stopped: Q-005", prompt)
        n = 5
        while True:
            p = self.pai("queue", "next", self.q)
            if p.returncode:
                self.assertIn("nothing left to work on; still blocked: Q-004", p.stdout)
                break
            iid = p.stdout.split()[0]
            self.assertNotEqual(iid, "Q-004")
            self.ok("queue", "mark", self.q, iid, "done", "--evidence", "s2 %s" % iid, session="session-2")
            n += 1
        st = self.status()
        self.assertEqual(st["counts"]["done"], 9)
        self.assertEqual(st["blocked"], [{"id": "Q-004", "reason": "owner decision"}])
        self.assertEqual(st["sessions"], ["session-1", "session-2"])
        self.assertEqual(st["done"], ["Q-001", "Q-002", "Q-003", "Q-005", "Q-006", "Q-007", "Q-008", "Q-009", "Q-010"])

    def test_ids_with_several_prefix_words(self):
        """R4 ids such as R4-PAI-001: listed, marked, ranged in summaries and checked in handoff tables."""
        items = os.path.join(self.tmp, "r4.md")
        with open(items, "w", encoding="utf-8") as f:
            f.write("".join("R4-PAI-%03d. Item %d\n" % (i, i) for i in range(1, 5)))
        q = os.path.join(self.tmp, "r4.json")
        self.ok("queue", "init", q, "--from", items, "--write")
        self.ok("queue", "mark", q, "R4-PAI-001", "done", "--evidence", "e1")
        self.ok("queue", "mark", q, "R4-PAI-002", "done", "--evidence", "e2")
        self.assertIn("  done        R4-PAI-001..R4-PAI-002", self.ok("queue", "status", q))
        hand = os.path.join(self.tmp, "h.md")
        with open(hand, "w") as f:
            f.write(self.ok("queue", "table", q))
        self.assertIn("ok: 4 row(s)", self.ok("queue", "check", q, hand))

    def test_an_invalid_result_is_reopened_by_the_owner_and_retried(self):
        """R4-PAI-034: a worker marks an item done; the owner finds the result invalid and reopens it with a reason;
        the next session gets it again; a handoff still claiming done is caught; the history keeps every step."""
        self.ok("queue", "mark", self.q, "Q-001", "done", "--evidence", "tests pass", session="worker-1")
        p = self.pai("queue", "mark", self.q, "Q-001", "todo")
        self.assertEqual(p.returncode, 2)
        self.assertIn("todo needs --reason", p.stderr)
        self.ok("queue", "mark", self.q, "Q-001", "todo", "--reason", "retry: the evidence ran the wrong suite",
                session="owner")
        self.assertEqual(self.ok("queue", "next", self.q).splitlines()[0], "Q-001 Item number 1")
        stale = os.path.join(self.tmp, "stale.md")
        with open(stale, "w") as f:
            f.write("| Q-001 | done | tests pass |\n\nTotals: done 1 (10 items)\n")
        p = self.pai("queue", "check", self.q, stale)
        self.assertEqual(p.returncode, 1)
        self.assertIn("Q-001: the handoff says done, the queue log says todo (retry: the evidence ran the wrong suite)",
                      p.stdout)
        out = self.ok("queue", "show", self.q, "Q-001")
        self.assertIn("state     todo (retry: the evidence ran the wrong suite)", out)
        self.assertRegex(out, r"history   \S+ done +worker-1; evidence: tests pass")
        self.assertRegex(out, r"history   \S+ todo +owner; reason: retry")
        self.ok("queue", "mark", self.q, "Q-001", "done", "--evidence", "full suite: 125 OK", session="worker-2")
        self.assertEqual(self.status()["done"], ["Q-001"])

    def test_checkpoints_are_listed_from_the_files(self):
        """R4-PAI-033: checkpoints and item texts come from the queue files, not from a conversation."""
        self.assertIn("no checkpoint recorded yet", self.ok("queue", "show", self.q))
        hand = os.path.join(self.tmp, "cp5.md")
        self.ok("queue", "mark", self.q, "Q-001", "done", "--evidence", "e")
        with open(hand, "w") as f:
            f.write(self.ok("queue", "table", self.q))
        self.ok("queue", "checkpoint", self.q, "Q-005", "--handoff", hand)
        self.assertIn("matches its recorded sha256", self.ok("queue", "show", self.q))
        with open(hand, "a") as f:
            f.write("edited later\n")
        self.assertIn("CHANGED since it was recorded", self.ok("queue", "show", self.q))
        cps = json.loads(self.ok("queue", "show", self.q, "--json"))["checkpoints"]
        self.assertEqual((cps[0]["id"], cps[0]["matches"], cps[0]["done"]), ("Q-005", False, 1))
        self.assertIn("Q-007 Item number 7", self.ok("queue", "show", self.q, "Q-007"))

    def test_unsupported_queue_version(self):
        with open(self.q, encoding="utf-8") as f:
            doc = json.load(f)
        doc["version"] = 2
        with open(self.q, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        p = self.pai("queue", "status", self.q)
        self.assertEqual(p.returncode, 2)
        self.assertIn("has queue format version 2; this pai reads version 1. Nothing was changed.", p.stderr)


if __name__ == "__main__":
    unittest.main()
