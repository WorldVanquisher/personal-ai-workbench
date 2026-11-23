"""Task-card export keeps run records out of the public cards (R6-COR-017).

Checked against what the runners write into a note's front matter (base c8bc51e):
pai_swarm.py writes `handoff` (a vault path), `session` (a Claude session id), `cost_usd`,
`last_run`, `jev_done`/`jev_checked`/`jev_verify`; triage writes `jev_actionable`; continuation
writes `handed_off_from`; rc writes `continued_marker`/`continued_in`. Before this fix only
RUN_FIELDS (worker, branch, ... jev_intake, jev_review) were private, so the others were exported."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import cards  # noqa: E402

RUN_RECORD = {"handoff": "/srv/vault/Agent Inbox/T-1-20261122T000000Z/HANDOFF.md", "session": "5f1c-session-id",
              "cost_usd": "0.0038", "last_run": "run1", "jev_done": "0.98", "jev_checked": "0.06",
              "jev_verify": "agree", "jev_actionable": "0.91", "handed_off_from": "run1/api@0123456789ab",
              "continued_marker": "run1/api@0123456789ab", "continued_in": "run2/api", "session_id": "abc"}


class PrivateRunFields(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="pai-cards-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        meta = "".join("%s: %s\n" % kv for kv in sorted(RUN_RECORD.items()))
        with open(os.path.join(self.d, "t1.md"), "w") as f:
            f.write("---\nstatus: done\npriority: 2\nmodel: sonnet\narea: api\n%s---\n# T1\n\nDo it.\n" % meta)

    def test_run_records_are_withheld_from_the_public_card(self):
        doc, withheld = cards.export(self.d)
        fields = doc["cards"][0].get("fields", {})
        for k in RUN_RECORD:
            self.assertNotIn(k, fields, k)
        self.assertEqual(sorted(withheld), sorted(RUN_RECORD))
        self.assertEqual(fields.get("area"), "api", "ordinary fields stay public")
        self.assertEqual(fields.get("model"), "sonnet", "the model tier is part of the task, not of a run")
        self.assertNotIn("private", doc)

    def test_include_private_puts_them_in_the_private_group(self):
        doc, withheld = cards.export(self.d, include_private=True)
        self.assertEqual(withheld, [])
        self.assertEqual(sorted(doc["private"]["t1"]), sorted(RUN_RECORD))

    def test_a_card_file_that_carries_one_publicly_is_refused_on_import(self):
        doc, _ = cards.export(self.d)
        doc["cards"][0].setdefault("fields", {})["cost_usd"] = "1"
        with self.assertRaises(cards.CardError):
            cards.load(cards.dump(doc))


if __name__ == "__main__":
    unittest.main()
