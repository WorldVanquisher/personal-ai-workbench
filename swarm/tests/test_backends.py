"""Backend capability contract (1.0 readiness 061-068): one capability representation that doctor
and run planning share, a refusal with a repair hint before anything starts, Claude and Codex
readiness evidence kept apart, synthetic fixtures for a missing transcript, a changed footer and
a late trust prompt, a test-only backend the command line can never select, and older worker
configuration that keeps working."""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import agents  # noqa: E402
import rc  # noqa: E402
import fake_backend  # noqa: E402


class Contract(unittest.TestCase):
    def test_every_backend_states_launch_readiness_trust_input_and_transcript(self):
        for name in agents.names():
            caps = agents.get(name).capabilities()
            self.assertEqual(caps["schema"], "pai_backend_capabilities_v1")
            for k in ("launch", "readiness", "trust", "input", "transcript", "login"):
                self.assertIn(k, caps, name)
            self.assertEqual(caps["trust"]["answered_by"], "a person, never pai")
            json.dumps(caps)  # machine-readable as it is
        c, x = agents.get("claude").capabilities(), agents.get("codex").capabilities()
        self.assertEqual((c["readiness"]["source"], x["readiness"]["source"]), ("session-record", "screen"))
        self.assertEqual((c["launch"]["remote_control_modes"], x["launch"]["remote_control_modes"]), (["rc"], []))
        self.assertTrue(c["transcript"]["delivery_evidence"] and x["transcript"]["delivery_evidence"])
        self.assertEqual(agents.get("claude").gaps(), [])
        self.assertEqual(agents.get("codex").gaps(), ["no Remote Control mode",
                                                       "input readiness from the screen only (no session record)"])

    def test_readiness_evidence_is_kept_apart(self):
        claude, codex = agents.get("claude"), agents.get("codex")
        screen = "? for shortcuts"
        busy = agents.Probe("/w", 7, {"pid": 7, "status": "busy"}, "/cfg", None)
        self.assertEqual(claude.input_state(screen, busy)[0], False, "Claude: its own record wins over the screen")
        self.assertIn("session record", claude.input_state(screen, busy)[1])
        self.assertEqual(codex.input_state(screen, busy)[0], True, "Codex never reads a Claude-style record")
        self.assertIn("screen", codex.input_state(screen, busy)[1])
        self.assertIsNone(codex.submitted(agents.Probe("/w", 7, None, "/cfg", None), "[pai x]", 0),
                          "Codex looks only in CODEX_HOME, never in Claude's config folder")

    def test_a_test_only_backend_and_the_refusal_before_spawn(self):
        self.assertNotIn("echo-fake", agents.names())
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        data = {"schema": rc.SCHEMA, "run_id": "r1", "repository": {"path": tmp}, "worktree_root": os.path.join(tmp, "w"),
                "dispatch": True, "agents": {"echo-fake": {"command": [shutil.which("cat")]}},
                "workers": [{"lane": "a", "agent": "echo-fake"}]}
        with self.assertRaises(rc.ManifestError):
            rc.Manifest(os.path.join(tmp, "m.json"), data=data)  # not registered: unknown agent
        with fake_backend.registered() as echo:
            self.assertIn("echo-fake", agents.names())
            self.assertIn("no transcript pai can read: a dispatched task could never be shown delivered", echo.gaps())
            m = rc.Manifest(os.path.join(tmp, "m.json"), data=data)
            problems = rc.agent_problems(m)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("keeps no transcript pai can read", problems[0])
            self.assertIn("Repair: `pai run --no-dispatch`", problems[0])
            self.assertIn("(claude, codex)", problems[0])
            m2 = rc.Manifest(os.path.join(tmp, "m.json"), data=dict(data, dispatch=False))
            self.assertEqual(rc.agent_problems(m2), [], "without dispatch nothing needs a transcript")
        self.assertNotIn("echo-fake", agents.names(), "registered for the with-block only")
        with self.assertRaises(ValueError):
            agents.unregister("claude")

    def test_older_worker_configuration_still_works(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        # a 0.2-style manifest: the fixed Claude launch line, no agent fields at all
        old = {"schema": rc.SCHEMA, "run_id": "old1", "repository": {"path": tmp}, "worktree_root": os.path.join(tmp, "w"),
               "command": [shutil.which("cat"), "--remote-control", "--name", "{name}"], "workers": [{"lane": "a"}]}
        m = rc.Manifest(os.path.join(tmp, "m.json"), data=old)
        w = m.workers[0]
        self.assertEqual((w.agent, w.mode), ("claude", "rc"))
        self.assertEqual(w.adapter.capabilities()["backend"], "claude")
        self.assertEqual(rc.agent_problems(m), [], "a wrapper command is not probed, and the capabilities fit")
        self.assertEqual(rc.agent_mismatch(w, {"branch": "b", "note": "n"}), [], "a lane record without agent fields "
                                                                                   "is a Claude lane")


@helpers.needs_tmux
@needs_311
class Fixtures(WB):
    def setUp(self):
        super().setUp()
        self.note("task-a", priority=9)
        self.env.update(PAI_DISPATCH_SETTLE=helpers.FAKE_SETTLE, PAI_DISPATCH_CONFIRM_SECONDS="2")
        self.init()

    def lanes(self):
        return {l["lane"]: l for l in json.loads(self.pai("status", "--json").stdout)["lanes"]}

    def test_missing_transcript_is_unknown_never_delivered(self):
        self.behavior(no_transcript=["a"])
        self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "6")
        self.assertEqual(len(self.received("a")), 1, "the prompt arrived")
        self.assertTrue(self.wait_for(lambda: self.lanes()["a"]["dispatch"]["state"] == "unknown", 10))
        lane = self.lanes()["a"]
        self.assertEqual(lane["lifecycle"]["state"], "unknown")
        self.assertIn("transcript shows no such prompt", lane["lifecycle"]["why"])
        self.pai("resume", "--wait", "3")
        self.assertEqual(len(self.received("a")), 1, "an unknown dispatch is never resent on its own")

    def test_a_changed_footer_is_not_guessed_ready(self):
        self.behavior(codex_footer={"a": "codex 9.9 | type a message (a footer pai has never seen)"})
        p = self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "3")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        lane = self.lanes()["a"]
        self.assertEqual((lane["input"]["ready"], lane["dispatch"]["state"]), (False, "pending"))
        self.assertEqual(lane["lifecycle"]["state"], "queued")
        self.assertIn("input box is not ready", lane["lifecycle"]["why"])
        self.assertEqual(self.received("a"), [], "nothing typed into an input box pai cannot recognise")

    def test_doctor_json_carries_capabilities_and_gaps(self):
        p = self.pai("doctor", "--json", "--agent", "codex", "--agent", "claude")
        rows = {r["tool"]: r for r in json.loads(p.stdout)}
        self.assertEqual(rows["backend codex"]["gaps"], agents.get("codex").gaps())
        self.assertEqual(rows["backend claude"]["capabilities"], agents.get("claude").capabilities())
        self.assertEqual({r["need"] for r in rows.values()} <= {"essential", "optional", "info"}, True)
        self.assertEqual(rows["git"]["need"], "essential")
        p = self.pai("run", "--dry-run", "--workers", "1", "--agent", "codex")
        self.assertIn("evidence: readiness from screen; delivery from $CODEX_HOME/sessions", p.stdout)
        p = self.pai("run", "--dry-run", "--workers", "1", "--agent", "echo-fake")
        self.assertEqual(p.returncode, 2, "the test-only backend is never selectable from the command line")
        self.assertIn("unknown agent", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
