"""pai round inject (and next --send through it) with the fake `claude` on an isolated tmux:
refused at a trust dialog, at the Teach-auto-mode dialog, over an owner draft (never overwritten
or appended to), while busy unless --queue-ok, without a window; dry run sends nothing; each
send is recorded in ROUND.json with its sha."""
import hashlib
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import round as rnd  # noqa: E402
import round_launch as rl  # noqa: E402
import test_round_launch as _tl  # noqa: E402  (module import: its tests are not collected twice)

needs_tmux = _tl.needs_tmux


@needs_tmux
class Inject(unittest.TestCase):
    setUp = _tl.Launch.setUp
    kill_fakes, behave, jsonl = _tl.Launch.kill_fakes, _tl.Launch.behave, _tl.Launch.jsonl
    launch, wait_until = _tl.Launch.launch, _tl.Launch.wait_until

    def start(self, **behavior):
        if behavior:
            self.behave(**behavior)
        self.r.update(format="pai-round", version=1)
        path = os.path.join(self.tmp, "ROUND.json")
        with open(path, "w") as f:
            json.dump(self.r, f)
        self.r["_path"] = path
        self.launch()
        time.sleep(0.4)
        self.lines.clear()

    def inject(self, text="继续：下一个是 R9-ALP-002。\n第二行。", **kw):
        kw.setdefault("wait", 1.0)
        return rl.inject(self.r, self.ln, text, log=self.lines.append, tmux=self.tmux, **kw)

    def said(self):
        return "\n".join(self.lines)

    def events(self):
        return rnd.load_round(self.r["_path"])["lanes"][0].get("events") or []

    def test_idle_lane_gets_it_once_and_the_event_is_recorded(self):
        self.start()
        text = "继续：下一个是 R9-ALP-002。\n第二行。"
        self.assertEqual(self.inject(text), 0, self.said())
        self.assertTrue(self.wait_until(lambda: self.jsonl("received.jsonl")))
        got = self.jsonl("received.jsonl")
        self.assertEqual((len(got), got[0]["bracketed"]), (1, True))
        ev = self.events()
        self.assertEqual(ev[-1]["sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual((ev[-1]["kind"], ev[-1]["state"]), ("inject", "sent"))
        self.assertTrue(ev[-1]["at"])
        time.sleep(0.8)
        self.lines.clear()
        self.assertEqual(self.inject(text), 1)  # the same text twice needs --again
        self.assertIn("--again", self.said())
        self.assertEqual(self.inject(text, again=True), 0, self.said())

    def test_dry_run_sends_nothing(self):
        self.start()
        self.assertEqual(self.inject(dry_run=True), 0)
        self.assertIn("dry run: would paste", self.said())
        self.assertIn("r9:", self.said())
        time.sleep(0.5)
        self.assertEqual(self.jsonl("pasted.jsonl"), [])
        self.assertEqual(self.events(), [])

    def test_trust_dialog_refuses(self):
        self.start(trust=True)
        self.assertEqual(self.inject(), 1)
        self.assertIn("folder-trust dialog", self.said())
        self.assertEqual(self.jsonl("pasted.jsonl"), [])

    def test_teach_auto_mode_dialog_refuses(self):
        self.start(dialog=True)
        self.assertEqual(self.inject(queue_ok=True), 1)
        self.assertIn("dialog (teach-auto-mode)", self.said())
        time.sleep(0.3)
        self.assertEqual(self.jsonl("pasted.jsonl"), [])
        self.assertEqual(self.jsonl("received.jsonl"), [])

    def test_owner_draft_is_never_overwritten_or_appended(self):
        self.start(draft="我还没写完的话")
        for queue_ok in (False, True):
            self.lines.clear()
            self.assertEqual(self.inject(queue_ok=queue_ok), 1)
            self.assertIn("never overwrites it and never appends", self.said())
        time.sleep(0.3)
        self.assertEqual(self.jsonl("pasted.jsonl"), [])

    def test_busy_needs_queue_ok_and_then_claude_queues_it(self):
        self.start(busy=True)
        self.assertEqual(self.inject(), 1)
        self.assertIn("--queue-ok", self.said())
        self.assertEqual(self.jsonl("pasted.jsonl"), [])
        self.lines.clear()
        self.assertEqual(self.inject(queue_ok=True), 0, self.said())
        self.assertTrue(self.wait_until(lambda: self.jsonl("received.jsonl")))
        self.assertTrue(self.jsonl("received.jsonl")[0]["queued"])
        self.assertTrue(self.events()[-1]["queued"])

    def test_busy_with_a_draft_still_refuses(self):
        self.start(busy=True, draft="草稿")
        self.assertEqual(self.inject(queue_ok=True), 1)
        self.assertIn("unsent text", self.said())

    def test_no_window(self):
        self.r.update(format="pai-round", version=1)
        self.assertEqual(self.inject(), 1)
        self.assertIn("does not exist", self.said())

    def test_next_send_goes_through_inject(self):
        cp = os.path.join(self.ln["handoff_dir"], "cp-B")
        os.makedirs(cp)
        with open(os.path.join(cp, "RESTART.md"), "w", encoding="utf-8") as f:
            f.write("有意停在 cp-B，下一个是 R9-ALP-025。\n")
        self.start()
        msg, _, _ = rl.next_message(self.r, self.ln)
        self.assertEqual(rl.inject(self.r, self.ln, msg, log=self.lines.append, tmux=self.tmux, kind="next", wait=1.0), 0)
        self.assertTrue(self.wait_until(lambda: self.jsonl("received.jsonl")))
        self.assertEqual(self.events()[-1]["kind"], "next")


if __name__ == "__main__":
    unittest.main()
