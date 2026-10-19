"""PAI-004: all Inbox and project data is temporary and synthetic."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from test_pai import Fixture, run_cli, symlink_or_skip
from pai import context, handoff


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)
        self.p = self.f.project()
        self.inbox = Path(self.p.inbox)
        self.now = datetime(2031, 1, 1, tzinfo=timezone.utc)

    def create(self, task="T-1", **kw):
        return Path(handoff.create(self.p, task, now=self.now, **kw))

    def test_creation_listing_context_end_to_end(self):
        args = ("--config", str(self.f.cfg))
        rc, path, err = run_cli(*args, "handoff", "new", "demo", "--task-id", "NEW-1",
                                "--summary", "SYNTHETIC NEW CLAIM", "--implementation", "done",
                                "--validation", "failed", "--proposal", "Lift the deployment freeze")
        self.assertEqual((rc, err), (0, ""))
        rc, listing, _ = run_cli(*args, "handoff", "list", "demo")
        self.assertEqual(rc, 1)  # registered memory directory has no HANDOFF.md
        self.assertIn(Path(path.strip()).parent.name, listing)
        self.assertIn("approved=false", listing)
        rc, out, err = run_cli(*args, "context", "demo", "--task", "release", "--handoffs", "1")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("SYNTHETIC NEW CLAIM", out)
        self.assertIn("[HUMAN] Public deployment is frozen.", out)
        self.assertIn("| Implementation | done |", out)
        self.assertIn("| Validation | failed |", out)
        for field in ("Commit", "Merge", "Deployment"):
            self.assertIn(f"| {field} | UNKNOWN", out)
        self.assertIn("## Knowledge-update proposals (UNAPPROVED)", out)
        self.assertIn("Newer reports do not supersede human decisions", out)
        self.assertLessEqual(len(out), self.p.budget_chars)

    def test_isolation_metadata_and_shallow_reads(self):
        good = self.create(summary="MATCHED")
        other = self.create("OTHER", summary="UNRELATED BODY")
        other.write_text(other.read_text().replace("project: demo", "project: unrelated"))
        # The directory name says nothing about project identity.
        renamed = self.inbox / "unrelated-looking"
        good.parent.rename(renamed)
        nested = self.inbox / "container" / "nested"
        nested.mkdir(parents=True)
        (nested / "HANDOFF.md").write_text((renamed / "HANDOFF.md").read_text().replace("MATCHED", "NESTED BODY"))
        (renamed / "extra.md").write_text("EXTRA PRIVATE BODY")
        reports, _ = handoff.discover(self.p)
        self.assertEqual([r["label"] for r in reports], ["unrelated-looking/HANDOFF.md"])
        out = context.build(self.p, "release", handoffs=20)
        self.assertIn("MATCHED", out)
        for text in ("UNRELATED BODY", "NESTED BODY", "EXTRA PRIVATE BODY"):
            self.assertNotIn(text, out)

    def test_order_timestamp_then_path(self):
        a = self.create("A")
        b = self.create("B")
        c = self.create("C")
        a.write_text(a.read_text().replace("2031-01-01T00:00:00Z", "2032-01-01T00:00:00Z"))
        os.utime(c, (1, 1))
        expected = [p.relative_to(self.inbox).as_posix() for p in (a, b, c)]
        for _ in range(3):
            self.assertEqual([r["label"] for r in handoff.discover(self.p)[0]], expected)
        out = context.build(self.p, "release", handoffs=1)
        self.assertIn("2 by requested count", out)

    def test_crlf_report_is_recognized(self):
        report = self.create(summary="CRLF REPORT")
        report.write_bytes(report.read_bytes().replace(b"\n", b"\r\n"))
        reports, _ = handoff.discover(self.p)
        self.assertEqual([r["meta"]["task"] for r in reports], ["T-1"])
        self.assertNotIn("\r", reports[0]["text"])

    def test_malformed_metadata_missing_and_encoding(self):
        original = self.create().read_text()
        mutations = [original.replace("project: demo\n", ""),
                     original.replace("project: demo", "project: demo\nproject: unrelated"),
                     original.replace("created: 2031", "created: 203X"),
                     original.replace("2031-01-01T", "2031-02-30T"),
                     original.replace("approved: false", "approved: maybe"),
                     original.replace("status: draft", "status: fabricated"),
                     original.replace("task: T-1", "task: ../escape"), "no metadata"]
        for i, text in enumerate(mutations):
            directory = self.inbox / f"bad-{i}"
            directory.mkdir()
            (directory / "HANDOFF.md").write_text(text)
        missing = self.inbox / "missing"
        missing.mkdir()
        invalid = self.inbox / "invalid-encoding"
        invalid.mkdir()
        (invalid / "HANDOFF.md").write_bytes(b"\xff")
        reports, issues = handoff.discover(self.p)
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(issues), len(mutations) + 3)  # memory + missing + invalid UTF-8
        self.assertTrue(any("missing/HANDOFF.md" in x for x in issues))
        out, problem = handoff.listing(self.p)
        self.assertTrue(problem)
        self.assertIn("MALFORMED", out)
        ctx = context.build(self.p, "release", handoffs=1)
        self.assertIn("additional discovery problems", ctx)

    def test_symlinks_and_nonregular_files(self):
        original = self.create()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "HANDOFF.md").write_text(original.read_text().replace("UNKNOWN", "OUTSIDE BODY"))
        symlink_or_skip(self, outside, self.inbox / "escape-dir", directory=True)
        directory = self.inbox / "escape-file"
        directory.mkdir()
        symlink_or_skip(self, outside / "HANDOFF.md", directory / "HANDOFF.md")
        labels = ["escape-dir", "escape-file"]
        if hasattr(os, "mkfifo"):  # no FIFOs on Windows
            fifo = self.inbox / "fifo"
            fifo.mkdir()
            os.mkfifo(fifo / "HANDOFF.md")
            labels.append("fifo")
        reports, issues = handoff.discover(self.p)
        self.assertEqual(len(reports), 1)
        for label in labels:
            self.assertTrue(any(label in issue for issue in issues))
        self.assertNotIn("OUTSIDE BODY", context.build(self.p, "release", handoffs=20))
        original_inbox = self.inbox.with_name("saved-inbox")
        self.inbox.rename(original_inbox)
        symlink_or_skip(self, outside, self.inbox, directory=True)
        self.assertEqual(handoff.discover(self.p)[0], [])
        self.assertIn("UNAVAILABLE Inbox", handoff.discover(self.p)[1][0])

    def test_limits_and_unavailable_inbox(self):
        report = self.create()
        report.write_bytes(b"x" * (handoff.MAX_FILE_BYTES + 1))
        self.assertIn("byte limit", " ".join(handoff.discover(self.p)[1]))
        with mock.patch.object(handoff, "MAX_ENTRIES", 1):
            reports, issues = handoff.discover(self.p)
        self.assertEqual(reports, [])
        self.assertIn("no partial ordering", issues[0])
        self.p.inbox = str(self.inbox / "absent")
        self.assertIn("UNAVAILABLE Inbox", handoff.discover(self.p)[1][0])
        for count in (-1, 21, 1000000):
            with self.assertRaises(handoff.HandoffError):
                context.build(self.p, "release", handoffs=count)

    def test_budget_and_conflicting_claims(self):
        self.create("A", summary="Deployment is ready", states={"deployment": "ready"})
        self.create("B", summary="Deployment failed", states={"deployment": "failed"})
        out = context.build(self.p, "deployment", 16000, handoffs=2)
        self.assertIn("Deployment is ready", out)
        self.assertIn("Deployment failed", out)
        self.assertIn("Conflicting claims remain unresolved", out)
        huge = self.create("C", summary="BIG REPORT")
        huge.write_text(huge.read_text() + "\n" + "synthetic body " * 2000)
        for budget in (4000, 8000, 16000):
            out = context.build(self.p, "release", budget, handoffs=3)
            self.assertLessEqual(len(out), budget)
            self.assertNotIn("OUTPUT CUT", out)
            self.assertIn("[HUMAN] Public deployment is frozen.", out)
            self.assertIn("OMITTED", out)
            self.assertNotIn("BIG REPORT", out)
        with self.assertRaisesRegex(handoff.HandoffError, "raise --budget"):
            context.build(self.p, "release", 2000, handoffs=20)

    def test_approval_metadata_and_exclusions(self):
        report = self.create(summary="REPORTED APPROVAL")
        report.write_text(report.read_text().replace("status: draft", "status: approved")
                          .replace("approved: false", "approved: true"))
        excluded = self.create("SKIP", summary="EXCLUDED BODY")
        self.p.exclude = [excluded.parent.name]
        listing, problems = handoff.listing(self.p)
        self.assertTrue(problems)
        self.assertIn("status=approved; approved=true", listing)
        out = context.build(self.p, "release", handoffs=20)
        self.assertIn("status=approved; approved=true", out)
        self.assertIn("approved: true", out)
        self.assertIn("Reports are claims", out)
        self.assertNotIn("EXCLUDED BODY", out)

    def test_file_replaced_by_symlink_at_open_is_refused(self):
        report = self.create(summary="INSIDE")
        outside = Path(self.tmp.name) / "outside.md"
        outside.write_text(report.read_text().replace("INSIDE", "ESCAPED"))
        real_open = os.open
        replaced = False

        def raced_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if os.path.basename(path) == "HANDOFF.md" and not replaced:  # relative (by fd) or full (by path)
                report.unlink()
                symlink_or_skip(self, outside, report)
                replaced = True
            return real_open(path, flags, *args, **kwargs)

        # The registered memory folder sorts after this task folder.
        with mock.patch("pai.handoff.os.open", side_effect=raced_open):
            reports, issues = handoff.discover(self.p)
        self.assertTrue(replaced)
        self.assertEqual(reports, [])
        self.assertTrue(issues)
        self.assertNotIn("ESCAPED", str((reports, issues)))

    def test_required_constraints_take_budget_before_reports(self):
        (self.f.mem / "more.md").write_text(
            "# More\n\n## Human decisions\n\n" + "[HUMAN] Preserve this constraint. " * 30)
        self.create(summary="HANDOFF SHOULD WAIT")
        out = context.build(self.f.project(), "release", 6000, handoffs=1)
        self.assertIn("Preserve this constraint", out)
        self.assertIn("[HUMAN] Public deployment is frozen.", out)
        self.assertNotIn("HANDOFF SHOULD WAIT", out)
        self.assertIn("OMITTED 1 for context budget", out)

    def test_default_does_not_discover(self):
        with mock.patch.object(handoff, "discover", side_effect=AssertionError("must not scan")):
            first = context.build(self.p, "release")
            second = context.build(self.p, "release", handoffs=0)
        self.assertEqual(first, second)
        self.assertNotIn("Handoff discovery", first)


class PathDiscoveryTests(DiscoveryTests):
    """The same PAI-004 behaviour through the path-based backend that Windows uses."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(handoff, "DIR_FD", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_backend_selected(self):
        with mock.patch.object(handoff, "_discover_by_fd", side_effect=AssertionError("descriptor backend used")):
            self.create(summary="PATH BACKEND")
            reports, _ = handoff.discover(self.p)
        self.assertEqual(len(reports), 1)

    def test_missing_file_identity_is_refused(self):
        self.create(summary="NO IDENTITY")
        real_fstat = os.fstat

        def no_ino(fd):
            st = real_fstat(fd)
            return os.stat_result((st.st_mode, 0) + tuple(st)[2:])

        with mock.patch("pai.handoff.os.fstat", side_effect=no_ino):
            reports, issues = handoff.discover(self.p)
        self.assertEqual(reports, [])
        self.assertTrue(any("file identity unavailable" in x for x in issues), issues)


if __name__ == "__main__":
    unittest.main()
