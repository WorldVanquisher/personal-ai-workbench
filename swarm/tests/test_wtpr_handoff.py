"""`pai worktree preview --handoff DIR [--require-approved-handoff]` (R6-COR-027) and the PR body
generated from a sealed pai-handoff/2 checkpoint through the privacy filter (R6-COR-028).
Local bare remote and fake gh only."""
import hashlib
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")
HANDOFF = """---
format: pai-handoff/2
status: draft
approved: false
project: demo
round: R6-20261122
lane: core
checkpoint: cp-C
task: PAI-R6-COR-030
base_sha: %s
head_sha: %s
queue: none
queue_sha256: %s
---

# core cp-C

## Summary

Handoff v2 and seal. See https://claude.ai/code/session_0123456789abcdef for the chat.

## State

| State | Value | Source |
| --- | --- | --- |
| Implementation | seal and approve | real run |
| Validation | tests/ 153 OK | real run (Arch) |
| Commit | none (owner-only) | self-report |
| Merge | not merged (owner-only) | self-report |
| Deployment | none | self-report |

## Items

| ID | state | evidence |
| --- | --- | --- |
| PAI-R6-COR-030 | done | sealed |

Totals: done 1, prototype 0, blocked 0, owner-only 0, todo 0
""" % ("a" * 40, "b" * 40, "c" * 64)


@helpers.needs_git
class PlanHandoff(helpers.Fixture):
    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.tmp, "lane wt")
        git(self.repo, "worktree", "add", "-q", "-b", "feat/v2", self.wt)
        with open(os.path.join(self.wt, "NOTE.md"), "w") as f:
            f.write("# note\n")
        self.plan = os.path.join(self.tmp, "plan.json")
        self.cp = os.path.join(self.tmp, "vault", "Agent Inbox", "R6-20261122", "core", "cp-C")
        os.makedirs(self.cp)
        raw = HANDOFF.encode()
        self.sha = hashlib.sha256(raw).hexdigest()
        with open(os.path.join(self.cp, "HANDOFF.md"), "wb") as f:
            f.write(raw)
        with open(os.path.join(self.cp, "SEAL.json"), "w") as f:
            json.dump({"format": "pai-seal/1", "handoff_sha256": self.sha}, f)
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": []}, f)

    def approve(self, sha=None):
        with open(os.path.join(self.cp, "DECISION.json"), "w") as f:
            json.dump({"format": "pai-decision/1", "decision": "approved", "handoff_sha256": sha or self.sha}, f)

    def pai(self, *args):
        return subprocess.run([sys.executable, "-B", PAI, "worktree"] + list(args), capture_output=True, text=True,
                              env=self.env, cwd=self.tmp, stdin=subprocess.DEVNULL)

    def preview(self, *extra):
        return self.pai("preview", self.wt, "--title", "docs: note", "--path", "NOTE.md", "--github", "o/r",
                        "--handoff", self.cp, "--out", self.plan, *extra)

    def test_plan_records_task_queue_and_handoff_sha_and_a_filtered_body(self):
        p = self.preview()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("handoff     sealed %s (task PAI-R6-COR-030, sha256 %s)" % (os.path.realpath(self.cp), self.sha[:16]),
                      p.stdout)
        with open(self.plan) as f:
            plan = json.load(f)
        self.assertEqual((plan["task"], plan["queue_sha256"], plan["handoff_sha256"]), ("PAI-R6-COR-030", "c" * 64, self.sha))
        body = plan["draft"]["body"]
        self.assertIn("Handoff v2 and seal.", body)
        self.assertIn("| Validation | tests/ 153 OK | real run (Arch) |", body)
        self.assertIn("Items: done 1, prototype 0, blocked 0, owner-only 0, todo 0", body)
        self.assertIn(self.sha, body)
        self.assertNotIn("session_0123456789abcdef", body, "the privacy filter removed the session link")

    def test_require_approved_refuses_a_sealed_but_unapproved_handoff(self):
        p = self.preview("--require-approved-handoff")
        self.assertIn("REFUSED     handoff: ", p.stdout)
        self.assertIn("is sealed, not approved", p.stdout)
        self.assertFalse(os.path.exists(self.plan))
        self.approve(sha="0" * 64)  # a decision for other bytes does not count
        self.assertIn("not approved", self.preview("--require-approved-handoff").stdout)

    def test_publish_checks_the_approval_again(self):
        self.approve()
        p = self.preview("--require-approved-handoff")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(self.cp, "HANDOFF.md"), "a") as f:
            f.write("\nedited after approval\n")
        heads = git(self.origin, "for-each-ref")
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("REFUSED     the plan requires the approved handoff", p.stdout)
        self.assertIn("changed after sealing", p.stdout)
        self.assertEqual(git(self.origin, "for-each-ref"), heads, "nothing was pushed")
        self.assertEqual(git(self.wt, "log", "-1", "--format=%s"), git(self.repo, "log", "-1", "--format=%s"))

    def test_publish_goes_ahead_when_still_approved(self):
        self.approve()
        self.assertEqual(self.preview("--require-approved-handoff").returncode, 0)
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("opened", p.stdout)

    def test_require_without_a_handoff_is_refused(self):
        p = self.pai("preview", self.wt, "--title", "t", "--path", "NOTE.md", "--github", "o/r",
                     "--require-approved-handoff")
        self.assertIn("--require-approved-handoff needs --handoff DIR", p.stdout)


if __name__ == "__main__":
    unittest.main()
