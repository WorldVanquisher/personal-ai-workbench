"""`pai handoff seal --jev-verify` (R6-COR-035): what the handoff says against what is recorded,
`agrees` or `DISAGREES`, written into SEAL.json and never a gate. JEV is a stub here."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pai import handoff  # noqa: E402


class StubJev:
    def __init__(self, checked, evidence=0.9, state="ok"):
        self.checked, self.evidence, self.state, self.calls = checked, evidence, state, []

    def signal(self, kind, title, body, report):
        self.calls.append((kind, report))
        if self.state != "ok":
            return {"state": self.state, "detail": "no key"}
        return {"state": "ok", "detail": "", "done": 0.9, "checked": self.checked, "evidence": self.evidence}


class JevVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "log.txt").write_bytes(b"Ran 3 tests\nOK\n")  # the bytes hashed below (no \r\n on Windows)
        self.sha = hashlib.sha256(b"Ran 3 tests\nOK\n").hexdigest()

    def text(self, line):
        return "---\nformat: pai-handoff/2\n---\n\n## Summary\n\nDone.\n\n## Evidence\n\n%s\n\n## Blockers\n\n- none\n" % line

    def run_case(self, line, checked):
        return handoff.jev_verify(self.text(line), bases=[str(self.d)], jev=StubJev(checked))

    def test_the_four_outcomes(self):
        ok = "- log.txt  %s  python3 -m unittest" % self.sha
        r = self.run_case(ok, 0.9)
        self.assertEqual((r["verdict"], r["evidence"]["match"]), ("agrees", 1))
        r = self.run_case("- nothing.txt  %s  claimed" % self.sha, 0.86)
        self.assertEqual(r["verdict"], "DISAGREES")  # the 2026-11-22 case: "all tests pass", no proof
        self.assertIn("no Evidence file was found", r["detail"])
        r = self.run_case(ok, 0.06)
        self.assertEqual(r["verdict"], "DISAGREES")  # the grep self-check case: proof, low checked
        self.assertIn("may not say what was run", r["detail"])
        r = self.run_case("- log.txt  %s  edited later" % ("0" * 64), 0.9)
        self.assertEqual((r["verdict"], r["evidence"]["MISMATCH"]), ("DISAGREES", 1))
        r = handoff.jev_verify(self.text(ok), bases=[str(self.d)], jev=StubJev(0.9, state="skipped"))
        self.assertEqual(r["verdict"], "skipped")


if __name__ == "__main__":
    unittest.main()
