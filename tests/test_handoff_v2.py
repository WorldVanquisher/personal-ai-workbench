"""pai-handoff/2 (R6-COR-019..025): `handoff new --queue/--round/--lane`, the queue checker on v2,
`handoff seal` (form, queue, NEXT-PR.md, privacy, idempotence, fsync, a crash between the copy and
the queue record) and `handoff approve/reject --expect-sha`. Synthetic data only."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from test_pai import Fixture, run_cli
from pai import handoff, workqueue

BASE, HEAD = "c8bc51e4674f33e3afb9f8dc02d390085c840d64", "1" * 40
ITEMS = "".join("PAI-R6-COR-%03d. item %d\n" % (n, n) for n in range(1, 6))
NEXT_PR = """# NEXT-PR

## Branch

worker/R6-20261122/core from %s into main.

## Commits

### 1
git add tests/x.py
message: test: x (PAI-R6-COR-001)

## PR body

Fixes PAI-R6-COR-001 and PAI-R6-COR-002 (lane tree; rerun on merged main needed).

## Checks before merge

python3 -B tools/strict_unittest.py tests

## Not included

none
""" % BASE


class V2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)
        self.p = self.f.project()
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        (self.work / "items.txt").write_text(ITEMS, encoding="utf-8")
        self.q = str(self.work / "queue")
        self.cli("queue", "init", self.q, "--from", str(self.work / "items.txt"), "--checkpoint", "PAI-R6-COR-002",
                 "--checkpoint", "PAI-R6-COR-005", "--write")
        self.cli("queue", "mark", self.q, "PAI-R6-COR-001", "done", "--evidence", "tests/test_x.py")
        self.cli("queue", "mark", self.q, "PAI-R6-COR-002", "blocked", "--reason", "needs the docs lane")

    def cli(self, *args, ok=True):
        rc, out, err = run_cli("--config", str(self.f.cfg), *args)
        if ok:
            self.assertEqual(rc, 0, out + err)
        return rc, out, err

    def new(self, *extra):
        _, out, _ = self.cli("handoff", "new", "demo", "--task-id", "PAI-R6-COR-002", "--round", "R6-20261122",
                             "--lane", "core", "--checkpoint", "cp-A", "--queue", self.q, "--base-sha", BASE,
                             "--head-sha", HEAD, "--summary", "Block A", "--implementation", "two items",
                             "--source", "implementation=real run", "--validation", "tests/ 141 OK",
                             "--commit", "none (owner-only)", "--merge", "not merged (owner-only)", "--deployment", "none",
                             "--blocker", "none", "--next", "PAI-R6-COR-003", *extra)
        return Path(out.strip())

    def worker_copy(self, text=None, next_pr=NEXT_PR):
        """The handoff as a worker keeps it in its work folder, with NEXT-PR.md next to it."""
        d = self.work / "cp-A"
        d.mkdir(exist_ok=True)
        (d / "HANDOFF.md").write_text(text if text is not None else self.new().read_text(encoding="utf-8"), encoding="utf-8")
        if next_pr is not None:
            (d / "NEXT-PR.md").write_text(next_pr, encoding="utf-8")
        return d / "HANDOFF.md"

    # -- R6-COR-020/021: v2 draft with Items and Totals from the queue
    def test_new_writes_v2_items_and_totals_from_the_queue(self):
        path = self.new()
        self.assertEqual(path.parent.parent, Path(self.p.inbox) / "R6-20261122" / "core")
        text = path.read_text(encoding="utf-8")
        meta, probs = handoff.check_v2(text)
        self.assertEqual(probs, [])
        self.assertEqual(meta["format"], "pai-handoff/2")
        self.assertIn("| PAI-R6-COR-001 | done | tests/test_x.py |", text)
        self.assertIn("| PAI-R6-COR-002 | blocked | needs the docs lane |", text)
        self.assertIn("Totals: todo 3, doing 0, done 1, prototype 0, blocked 1, owner-only 0 (5 items)", text)  # PRM-D3
        self.assertIn("| Implementation | two items | real run |", text)
        self.assertIn("| Validation | tests/ 141 OK | self-report |", text)
        with open(self.q + ".log", "rb") as f:
            self.assertIn("queue_sha256: " + __import__("hashlib").sha256(f.read()).hexdigest(), text)
        reports, issues = handoff.discover(self.p)
        self.assertIn("R6-20261122/core/%s/HANDOFF.md" % path.parent.name, [r["label"] for r in reports])
        self.assertEqual([r for r in reports if r["meta"].get("version") == "2"][0]["meta"]["status"], "draft")

    def test_new_without_a_queue_says_the_items_are_unknown(self):
        _, out, _ = self.cli("handoff", "new", "demo", "--task-id", "T-1")
        text = Path(out.strip()).read_text(encoding="utf-8")
        self.assertIn("format: pai-handoff/2", text)
        self.assertIn("the items are UNKNOWN", text)
        self.assertIn("round: UNKNOWN", text)
        self.assertIn("Items has no `| ID | state | evidence |` rows", handoff.check_v2(text)[1])

    def test_bad_round_source_or_sha_is_refused(self):
        for extra in (("--round", "../x", "--lane", "core"), ("--round", "R6", "--lane", "a/b"),
                      ("--source", "validation=trust me"), ("--base-sha", "c8bc51e"), ("--round", "R6")):
            rc, _, err = run_cli("--config", str(self.f.cfg), "handoff", "new", "demo", "--task-id", "T-1", *extra)
            self.assertEqual(rc, 2, (extra, err))

    # -- R6-COR-022: the queue checker reads v2; the v1 list never matched
    def test_queue_check_accepts_v2_and_the_v1_list_is_the_old_incompatibility(self):
        v1 = ("---\nstatus: draft\napproved: false\ntask: T-1\nproject: demo\ncreated: 2031-01-01T00:00:00Z\n---\n\n"
              "## Evidence\n\n- PAI-R6-COR-001 done: tests/test_x.py\n- PAI-R6-COR-002 blocked: docs lane\n")
        old = self.work / "v1.md"
        old.write_text(v1, encoding="utf-8")
        rc, out, err = run_cli("queue", "check", self.q, str(old))
        self.assertNotEqual(rc, 0)
        self.assertIn("PAI-R6-COR-001 is done in the queue log but has no row in the handoff", out + err)
        rc, out, err = run_cli("queue", "check", self.q, str(self.new()))
        self.assertEqual(rc, 0, out + err)

    # -- R6-COR-025: one ID form everywhere
    def test_unified_ids(self):
        for good in ("PAI-R6-COR-001", "FLH-R6-REL-001", "R6-COR-001", "R2-001", "PARENT-001", "PAI-R6b-COR-012"):
            self.assertTrue(workqueue.ITEM_ID.fullmatch(good), good)
            self.assertTrue(handoff.TASK_ID.fullmatch(good), good)
        for bad in ("PAI-R6-COR", "001", "PAI--R6-001", "PAI-R6-COR-001x"):
            self.assertFalse(workqueue.ITEM_ID.fullmatch(bad), bad)
        self.assertEqual(workqueue.parse_id("PAI-R6-COR-001"),
                         {"project": "PAI", "round": "R6", "lane": "COR", "number": 1})
        self.assertEqual(workqueue.parse_id("R6-COR-007"), {"project": None, "round": "R6", "lane": "COR", "number": 7})
        self.assertIsNone(workqueue.parse_id("R2-001"))

    # -- R6-COR-023: seal
    def test_seal_copies_checks_records_and_is_idempotent(self):
        src = self.worker_copy()
        raw = src.read_bytes()
        with mock.patch("pai.handoff.os.fsync", wraps=os.fsync) as fs:
            rc, out, err = self.cli("handoff", "seal", "demo", str(src), "--queue", self.q)
        self.assertGreaterEqual(fs.call_count, 4, "three files and the folders are fsynced")
        dest = Path(self.p.inbox) / "R6-20261122" / "core" / "cp-A"
        sealed_text = (dest / "HANDOFF.md").read_text(encoding="utf-8")
        text = raw.decode("utf-8")
        body = handoff.body_sha(text)
        self.assertEqual(handoff.body_of(sealed_text), handoff.body_of(text), "the body is the worker's, byte for byte")
        for line in ("status: sealed", "sealed_sha256: " + body, "approval: pending", "approved: false"):
            self.assertIn("\n" + line + "\n", sealed_text)
        self.assertEqual((dest / "NEXT-PR.md").read_text(encoding="utf-8"), NEXT_PR)
        seal = json.loads((dest / "SEAL.json").read_text(encoding="utf-8"))
        sha = __import__("hashlib").sha256(raw).hexdigest()
        file_sha = __import__("hashlib").sha256(sealed_text.encode()).hexdigest()
        self.assertEqual((seal["source_sha256"], seal["body_sha256"], seal["handoff_sha256"]), (sha, body, file_sha))
        self.assertEqual(seal["queue_item"], "PAI-R6-COR-002", "cp-A is the queue's first checkpoint")
        self.assertIn("sealed   " + str(dest), out)
        self.assertIn("--expect-sha " + body[:16], out)
        evs = [e for e in workqueue.events(self.q)[0] if e["event"] == "checkpoint"]
        self.assertEqual([(e["id"], e["sha256"], e["body_sha256"]) for e in evs], [("PAI-R6-COR-002", file_sha, body)])
        _, out, _ = self.cli("handoff", "seal", "demo", str(src), "--queue", self.q)
        self.assertIn("unchanged", out)
        self.assertEqual(len([e for e in workqueue.events(self.q)[0] if e["event"] == "checkpoint"]), 1)
        self.assertEqual(sorted(n for n in os.listdir(dest.parent) if n.startswith(("cp-", "."))), ["cp-A"],
                         "no second folder, no leftover temporary one")
        reports, _ = handoff.discover(self.p)
        sealed = [r for r in reports if r["label"] == "R6-20261122/core/cp-A/HANDOFF.md"][0]
        self.assertEqual(sealed["meta"]["status"], "sealed")
        src.write_text(raw.decode("utf-8").replace("Block A", "Block A, edited"), encoding="utf-8")
        rc, out, err = self.cli("handoff", "seal", "demo", str(src), "--queue", self.q, ok=False)
        self.assertEqual(rc, 1)
        self.assertIn("already sealed with sha256 " + sha, err)
        self.assertEqual((dest / "HANDOFF.md").read_text(encoding="utf-8"), sealed_text)

    def test_a_crash_after_the_copy_is_finished_by_the_rerun(self):
        src = self.worker_copy()
        with mock.patch("pai.workqueue.append", side_effect=OSError("killed")):
            with self.assertRaises(OSError):
                handoff.seal(self.p, str(src), self.q)
        self.assertEqual([e for e in workqueue.events(self.q)[0] if e["event"] == "checkpoint"], [])
        r = handoff.seal(self.p, str(src), self.q)
        self.assertEqual(r["state"], "unchanged")
        self.assertEqual(len([e for e in workqueue.events(self.q)[0] if e["event"] == "checkpoint"]), 1)

    def test_seal_refuses_bad_form_queue_mismatch_unknown_ids_and_private_text(self):
        good = self.new().read_text(encoding="utf-8")
        cases = {
            "no totals": (good.replace("Totals: todo 3", "Total things: lots"), "Items has no `Totals:` line"),
            "short sha": (good.replace("## Evidence\n\n- None supplied.",
                                       "## Evidence\n\n- tests/x.py  1a2b3c4d  python3 -B -m unittest"),
                          "truncated sha256 (8 hex)"),
            "queue disagrees": (good.replace("| PAI-R6-COR-002 | blocked |", "| PAI-R6-COR-002 | done |")
                                .replace("done 1, prototype 0, blocked 1", "done 2, prototype 0, blocked 0"),
                                "PAI-R6-COR-002: the handoff says done, the queue log says blocked"),
            "bad source": (good.replace("| two items | real run |", "| two items | trust me |"), "source 'trust me'"),
            "approved": (good.replace("approved: false", "approved: true"), "approved must be false"),
            "session link": (good.replace("Block A", "see https://claude.ai/code/session_abc123"), "session link"),
        }
        for name, (text, needle) in cases.items():
            with self.subTest(name):
                src = self.worker_copy(text)
                r = handoff.seal(self.p, str(src), self.q)
                self.assertEqual(r["state"], "refused", name)
                self.assertTrue(any(needle in p for p in r["problems"]), (needle, r["problems"]))
                self.assertFalse((Path(self.p.inbox) / "R6-20261122" / "core" / "cp-A").exists())
        src = self.worker_copy(good, NEXT_PR.replace("PAI-R6-COR-002", "PAI-R6-COR-009"))
        r = handoff.seal(self.p, str(src), self.q)
        self.assertIn("NEXT-PR.md names PAI-R6-COR-009, which is not in the handoff's Items table", r["problems"])

    def test_dry_run_writes_nothing(self):
        src = self.worker_copy()
        rc, out, _ = self.cli("handoff", "seal", "demo", str(src), "--queue", self.q, "--dry-run")
        self.assertIn("preview", out)
        self.assertFalse((Path(self.p.inbox) / "R6-20261122" / "core" / "cp-A").exists())
        self.assertEqual([e for e in workqueue.events(self.q)[0] if e["event"] == "checkpoint"], [])

    # -- R6-COR-024: approve / reject only the bytes that were read
    def test_approve_and_reject_need_the_reviewed_sha(self):
        src = self.worker_copy()
        r = handoff.seal(self.p, str(src), self.q)
        dest, sha = r["dir"], r["sha256"]
        rc, _, err = self.cli("handoff", "approve", "demo", dest, "--expect-sha", "0" * 16, ok=False)
        self.assertEqual(rc, 2)
        self.assertIn("you did not review these bytes", err)
        self.assertFalse(os.path.exists(os.path.join(dest, "DECISION.json")))
        rc, _, err = self.cli("handoff", "approve", "demo", dest, "--expect-sha", sha[:8], ok=False)
        self.assertIn("at least 12 hex digits", err)
        _, out, _ = self.cli("handoff", "approve", "demo", dest, "--expect-sha", sha[:16], "--by", "Ada")
        self.assertIn("approved", out)
        dec = json.loads(Path(dest, "DECISION.json").read_text(encoding="utf-8"))
        self.assertEqual((dec["decision"], dec["body_sha256"], dec["by"]), ("approved", sha, "Ada"))
        _, out, _ = self.cli("handoff", "approve", "demo", dest, "--expect-sha", sha)
        self.assertIn("unchanged", out)
        rc, _, err = self.cli("handoff", "reject", "demo", dest, "--expect-sha", sha, "--reason", "no", ok=False)
        self.assertIn("was already approved", err)
        reports, _ = handoff.discover(self.p)
        sealed = [r for r in reports if r["label"] == "R6-20261122/core/cp-A/HANDOFF.md"][0]
        self.assertEqual((sealed["meta"]["status"], sealed["meta"]["approved"]), ("approved", "true"))
        self.assertEqual(handoff.body_of(Path(dest, "HANDOFF.md").read_text(encoding="utf-8")),
                         handoff.body_of(src.read_text(encoding="utf-8")), "deciding never touches the body")
        self.assertIn("approval: approved", Path(dest, "HANDOFF.md").read_text(encoding="utf-8"))

    def test_a_handoff_changed_after_sealing_cannot_be_decided(self):
        r = handoff.seal(self.p, str(self.worker_copy()), self.q)
        hp = Path(r["dir"], "HANDOFF.md")
        hp.write_text(hp.read_text(encoding="utf-8") + "\nlater edit\n", encoding="utf-8")
        with self.assertRaises(handoff.HandoffError) as cm:
            handoff.decide(r["dir"], "approved", r["sha256"])
        self.assertIn("changed after sealing", str(cm.exception))
        _, issues = handoff.discover(self.p)
        self.assertTrue(any("SEAL.json was recorded for other bytes" in i for i in issues), issues)

    def test_reject_needs_a_reason_and_unsealed_handoffs_cannot_be_decided(self):
        r = handoff.seal(self.p, str(self.worker_copy()), self.q)
        with self.assertRaises(SystemExit):  # argparse: --reason is required to reject
            run_cli("--config", str(self.f.cfg), "handoff", "reject", "demo", r["dir"], "--expect-sha", r["sha256"])
        with self.assertRaises(handoff.HandoffError):
            handoff.decide(r["dir"], "rejected", r["sha256"], reason=" ")
        self.cli("handoff", "reject", "demo", r["dir"], "--expect-sha", r["sha256"], "--reason", "evidence missing")
        draft = self.new()
        with self.assertRaises(handoff.HandoffError) as cm:
            handoff.decide(str(draft), "approved", "0" * 64)
        self.assertIn("not a sealed handoff", str(cm.exception))

    # -- R6-COR-035: --jev-verify is recorded, never enforced
    def test_seal_records_a_jev_disagreement_and_still_seals(self):
        class Stub:
            calls = []

            def signal(self, kind, title, body, report):
                self.calls.append(report)
                return {"state": "ok", "detail": "", "done": 0.9, "checked": 0.95, "evidence": 0.9}
        stub = Stub()
        src = self.worker_copy()
        r = handoff.seal(self.p, str(src), self.q, jev_verify=lambda t, s, d: handoff.jev_verify(t, s, d, [], stub))
        self.assertEqual(r["state"], "sealed")
        seal = json.loads((Path(r["dir"]) / "SEAL.json").read_text(encoding="utf-8"))
        self.assertEqual(seal["jev"]["verdict"], "DISAGREES", "checked 0.95 but no Evidence file: said is not shown")
        self.assertEqual(len(stub.calls), 1)
        self.assertIn("## Evidence", stub.calls[0])

    # -- prompts-lane rules accepted by the owner (PRM-D4, D6, D7)
    def test_prm_rules(self):
        good = self.new().read_text(encoding="utf-8")
        cases = {
            "D6 unknown": (good.replace("| Deployment | none |", "| Deployment | UNKNOWN |"), "not UNKNOWN (PRM-D6)"),
            "D6 blocker": (good.replace("## Blockers\n\n- none", "## Blockers\n\n- waiting for the docs lane"),
                           "ends with the command the owner runs"),
            "D7 range": (good.replace("Block A", "Block A, PAI-R6-COR-001..005"), "write IDs in full"),
            "D4 absolute": (good.replace("## Evidence\n\n- None supplied.", "## Evidence\n\n- /etc/hostname  %s  cat"
                                         % ("0" * 64)), "copy it into the checkpoint folder"),
        }
        for name, (text, needle) in cases.items():
            with self.subTest(name):
                r = handoff.seal(self.p, str(self.worker_copy(text)), self.q)
                self.assertTrue(any(needle in p for p in r["problems"]), (needle, r["problems"]))
        ok_blocker = good.replace("## Blockers\n\n- none", "## Blockers\n\n- the docs lane's allowlist line: "
                                  "`patch -p1 < proposals/allowlist-core.patch`")
        self.assertEqual(handoff.check_v2(ok_blocker)[1], [])
        self.assertEqual(handoff.check_v2(good.replace("Block A", "Block A, PAI-R6-COR-001..PAI-R6-COR-005"))[1], [])

    def test_evidence_files_are_copied_with_the_seal(self):  # PRM-D4
        d = self.work / "cp-A"
        d.mkdir(exist_ok=True)
        (d / "logs").mkdir(exist_ok=True)
        (d / "logs" / "tests.txt").write_bytes(b"Ran 3 tests\nOK\n")  # the bytes hashed below (no \r\n on Windows)
        sha = __import__("hashlib").sha256(b"Ran 3 tests\nOK\n").hexdigest()
        text = self.new().read_text(encoding="utf-8").replace(
            "## Evidence\n\n- None supplied.", "## Evidence\n\n- logs/tests.txt  %s  python3 -m unittest" % sha)
        r = handoff.seal(self.p, str(self.worker_copy(text)), self.q)
        self.assertEqual(r["state"], "sealed", r["problems"])
        self.assertEqual((Path(r["dir"]) / "logs" / "tests.txt").read_text(encoding="utf-8"), "Ran 3 tests\nOK\n")
        self.assertEqual(json.loads((Path(r["dir"]) / "SEAL.json").read_text(encoding="utf-8"))["evidence"],
                         {"logs/tests.txt": sha})
        (d / "logs" / "tests.txt").write_text("changed\n", encoding="utf-8")
        # the text still names the old sha256: sealing it as another checkpoint is refused
        r2 = handoff.seal(self.p, str(self.worker_copy(text.replace("checkpoint: cp-A", "checkpoint: cp-B"))), self.q)
        self.assertTrue(any("its sha256 is" in p for p in r2["problems"]), r2["problems"])

    # -- R6-COR-040: approval in Obsidian, checked by --from-vault
    def test_approve_from_vault(self):
        r = handoff.seal(self.p, str(self.worker_copy()), self.q)
        note = Path(r["dir"], "HANDOFF.md")
        rc, _, err = self.cli("handoff", "approve", "demo", r["dir"], "--from-vault", ok=False)
        self.assertEqual(rc, 1)
        self.assertIn("does not say `approved: true`", err)
        text = note.read_text(encoding="utf-8")
        note.write_text(text.replace("approved: false", "approved: true"), encoding="utf-8")  # the owner, in Obsidian
        reports, _ = handoff.discover(self.p)
        meta = [x for x in reports if x["label"] == "R6-20261122/core/cp-A/HANDOFF.md"][0]["meta"]
        self.assertEqual(meta["approved"], "false", "the front matter alone never approves")
        _, out, _ = self.cli("handoff", "approve", "demo", r["dir"], "--from-vault")
        self.assertIn("approved", out)
        self.assertEqual(json.loads(Path(r["dir"], "DECISION.json").read_text(encoding="utf-8"))["body_sha256"], r["sha256"])
        reports, _ = handoff.discover(self.p)
        meta = [x for x in reports if x["label"] == "R6-20261122/core/cp-A/HANDOFF.md"][0]["meta"]
        self.assertEqual((meta["status"], meta["approved"], meta["approval"]), ("approved", "true", "approved"))
        self.assertEqual(handoff.sealed_state(r["dir"])["approved"], True)

    def test_a_body_edited_in_the_vault_is_stale_not_approved(self):
        r = handoff.seal(self.p, str(self.worker_copy()), self.q)
        note = Path(r["dir"], "HANDOFF.md")
        text = note.read_text(encoding="utf-8")
        note.write_text(text.replace("approved: false", "approved: true").replace("Block A", "Block A (edited)"),
                        encoding="utf-8")
        rc, _, err = self.cli("handoff", "approve", "demo", r["dir"], "--from-vault", ok=False)
        self.assertEqual(rc, 2)
        self.assertIn("marked approval: stale", err)
        self.assertIn("\napproval: stale\n", note.read_text(encoding="utf-8"))
        self.assertFalse(Path(r["dir"], "DECISION.json").exists())
        state = handoff.sealed_state(r["dir"])
        self.assertEqual((state["approved"], state["approval"]), (False, "stale"))
        self.assertIn("HANDOFF.md changed after sealing", state["problems"])


if __name__ == "__main__":
    unittest.main()
