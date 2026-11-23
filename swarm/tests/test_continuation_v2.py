"""A continuation also writes a pai-handoff/2 HANDOFF.md, records its sha256, and a transfer packet
carries that sha256 (R6-COR-026). Fake agents only."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311  # noqa: E402

sys.path.insert(0, os.path.dirname(helpers.SWARM))
from pai import handoff  # noqa: E402


@needs_311
@helpers.needs_tmux
class ContinuationV2(WB):
    NOTE = "PAI-R6-COR-001"

    def setUp(self):
        super().setUp()
        self.note(self.NOTE, priority=9, body="Write STEP-1.md.\n\n## Acceptance criteria\n\n- STEP-1.md exists")
        self.env.update(PAI_DISPATCH_CONFIRM_SECONDS="2", PAI_PROJECT="demo")
        self.init()

    def test_v2_handoff_written_with_its_sha_in_the_marker_and_the_packet(self):
        self.behavior()
        p = self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "8", "--run-id", "s1")
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        wt = json.loads(self.pai("status", "--json", "--run", "s1").stdout)["lanes"][0]["worktree"]
        helpers.commit(wt, "STEP-1.md", "done\n", "step 1")
        self.assert_ok(self.pai("cleanup", "--run", "s1", "--apply", "--stop-running"))
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        mt = re.search(r"v2 handoff: (.+/cp-cont1/HANDOFF\.md) \(sha256 ([0-9a-f]{64})\)", p.stdout)
        self.assertTrue(mt, p.stdout)
        path, sha = mt.group(1), mt.group(2)
        with open(path, "rb") as f:
            raw = f.read()
        self.assertEqual(__import__("hashlib").sha256(raw).hexdigest(), sha)
        text = raw.decode("utf-8")
        meta, probs = handoff.check_v2(text)
        self.assertEqual(probs, [], "the continuation handoff passes the seal's form check")
        self.assertEqual((meta["round"], meta["lane"], meta["checkpoint"], meta["project"]), ("s1", "a", "cp-cont1", "demo"))
        self.assertIn("| %s | doing |" % self.NOTE, text)
        self.assertEqual(meta["head_sha"], helpers.git(wt, "rev-parse", "HEAD"))
        with open(os.path.join(self.tasks, self.NOTE + ".md"), encoding="utf-8") as f:
            note = f.read()
        self.assertIn('"handoff_sha256": "%s"' % sha, note)
        self.assertNotIn(self.home, note, "no local path travels in the note's marker")
        packet = os.path.join(self.tmp, "packet.json")
        self.assert_ok(self.pai("task", "export", self.NOTE, "--transfer", "--out", packet))
        with open(packet, encoding="utf-8") as f:
            doc = json.load(f)
        ev = [e for t in doc["transfer"]["tasks"] for e in t["evidence"] if e["id"] == "handoff-v2"]
        self.assertEqual([e["sha256"] for e in ev], [sha])
        self.assertNotIn(self.home, json.dumps(doc))


if __name__ == "__main__":
    unittest.main()
