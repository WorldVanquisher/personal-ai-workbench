"""Task dispatch after folder trust (PAI-008 follow-up), end to end with fakes only: fake Claude
and Codex on PATH, an isolated tmux server on its own -S socket, isolated pai homes and a
per-test CODEX_HOME and CLAUDE_CONFIG_DIR. A pass-through tmux (fake_tmux.py, through the
existing PAI_SWARM_TMUX override) logs every keystroke pai sends and injects faults: a dropped
Enter, a trust prompt that appears right after the paste, a slow paste, or the pai process
killed right after a keystroke.

Where a test answers a trust prompt or presses Enter in a pane, it plays the operator with
tmux send-keys; product code never does either.

Evidence kinds checked separately: process (pid), trust (prompt on screen), input (ready or
not, and why), dispatch (pending/sent/delivered/unknown and its evidence) and rc."""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, PAI, needs_311, wrapper, lines, alive  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class Base(WB):
    def setUp(self):
        super().setUp()
        for n in ("a", "b", "c"):
            self.note("task-%s" % n, priority=9 - ord(n) + ord("a"))
        wrapper(self.bin, "tmux-fault", "fake_tmux.py")
        self.env.update(PAI_SWARM_TMUX=os.path.join(self.bin, "tmux-fault"), FAKE_TMUX_REAL=shutil.which("tmux"))
        self.init()

    # -- fixtures
    def faults(self, **kw):
        with open(os.path.join(self.ctl, "tmux.json"), "w") as f:
            json.dump(kw, f)

    def keys(self, lane, cmd):
        return [r for r in lines(os.path.join(self.ctl, "tmux.log")) if r["lane"] == lane and r["cmd"] == cmd]

    def pastes(self, lane):
        return len(self.keys(lane, "paste-buffer"))

    def enters(self, lane):
        return len([r for r in self.keys(lane, "send-keys") if r["keys"] == ["Enter"]])

    def capture(self, lane):
        return self.tmux("capture-pane", "-p", "-J", "-S", "-", "-t", self.pane(lane)).stdout

    def operator(self, lane, *keys):
        """A person at the lane's window (never pai)."""
        self.tmux("send-keys", "-t", self.pane(lane), *keys)

    def answer_trust(self, lane):
        self.operator(lane, "C-u")  # clear whatever sits on the input line first
        self.operator(lane, "yes", "Enter")

    def run_id(self):
        return json.loads(self.pai("status", "--json").stdout)["run_id"]

    def tag(self, lane):
        return "[pai %s/%s/task-%s]" % (self.run_id(), lane, lane)

    def rows(self, **env):
        p = self.pai("status", "--json", env=env or None)
        return {l["lane"]: l for l in json.loads(p.stdout)["lanes"]}, p.returncode

    def wait_rows(self, pred, timeout=20):
        end = time.time() + timeout
        while True:
            rows, code = self.rows()
            if pred(rows) or time.time() > end:
                return rows, code
            time.sleep(0.3)


@helpers.needs_tmux
@needs_311
class AfterTrust(Base):
    def test_claude_gets_its_task_on_resume_after_trust_when_its_input_box_shows_no_hint(self):
        # The live smoke: trust answered, RC verified, `pai resume` left the lane pending. Claude
        # Code shows "? for shortcuts" only while its footer has nothing else to show.
        self.behavior(trust=["a"], no_hint=["a"])
        p = self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "4")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        rows, _ = self.rows()
        self.assertEqual((rows["a"]["phase"], rows["a"]["dispatch"]["state"]), ("waiting-for-trust", "pending"))
        self.assertEqual(self.pastes("a"), 0, "nothing is typed into a trust prompt")
        self.answer_trust("a")
        rows, _ = self.wait_rows(lambda r: r["a"]["rc"]["state"] == "verified")
        self.assertEqual(rows["a"]["rc"]["state"], "verified")
        p = self.pai("resume", "--wait", "12")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out)
        got = self.received("a")
        self.assertEqual([r["text"].split("]")[0] + "]" for r in got], [self.tag("a")])
        rows, code = self.rows()
        self.assertEqual(code, 0)
        d = rows["a"]["dispatch"]
        self.assertEqual(d["state"], "delivered", d)
        self.assertIn("transcript", d.get("evidence", ""), "delivered rests on Claude's own transcript, not the tag")
        rows, _ = self.wait_rows(lambda r: r["a"]["input"]["ready"])  # Claude is busy for a moment after the prompt
        self.assertEqual(rows["a"]["input"]["ready"], True)
        self.assertIn("idle", rows["a"]["input"]["evidence"])
        self.assertEqual(rows["a"]["trust"]["state"], "not-shown")
        for _ in range(2):
            self.pai("resume", "--wait", "2")
        self.assertEqual((self.pastes("a"), len(self.received("a"))), (1, 1), "repeated resumes resend nothing")

    def test_codex_trust_prompt_after_the_first_frame_gets_no_keystroke(self):
        # Codex drew its idle footer first and asked for trust 2 s later.
        self.behavior(codex_trust_late={"a": 2.0})
        p = self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "6")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        rows, _ = self.rows()
        self.assertEqual(rows["a"]["phase"], "waiting-for-trust", rows["a"])
        self.assertEqual(rows["a"]["trust"]["state"], "prompt-visible")
        self.assertEqual(rows["a"]["dispatch"]["state"], "pending", rows["a"]["dispatch"])
        self.assertEqual((self.pastes("a"), self.enters("a")), (0, 0), "no keystroke reached the trust prompt")
        self.assertNotIn("[pai ", self.capture("a"))
        self.answer_trust("a")
        p = self.pai("resume", "--wait", "12")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows, _ = self.rows()
        self.assertEqual(rows["a"]["dispatch"]["state"], "delivered", rows["a"]["dispatch"])
        self.assertIn("rollout", rows["a"]["dispatch"]["evidence"])
        self.assertEqual(len(self.received("a")), 1)

    def test_a_trust_prompt_that_appears_after_the_paste_never_gets_enter(self):
        self.behavior(codex_trust_on_signal=["a"])
        self.faults(trust_after_paste=["a"])
        p = self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "8")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual((self.pastes("a"), self.enters("a")), (1, 0), "Enter is never sent while trust is asked")
        rows, _ = self.rows()
        d = rows["a"]["dispatch"]
        self.assertEqual(d["state"], "unknown", d)
        self.assertIn("trust", d["detail"])
        self.assertTrue(any("--redispatch" in s for s in d["reconcile"]), d)
        self.assertTrue(alive(rows["a"]["process"]["pid"]), "the agent still waits at its trust prompt")
        self.assertEqual(self.received(), [])
        self.pai("resume", "--wait", "3")
        self.assertEqual(self.pastes("a"), 1, "unknown is never resent on its own")
        # The operator answers the prompt (which also clears the pasted line), then redispatches.
        self.faults()
        self.answer_trust("a")
        p = self.pai("resume", "--lane", "a", "--redispatch")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows, _ = self.wait_rows(lambda r: r["a"]["dispatch"]["state"] == "delivered")
        self.assertEqual(rows["a"]["dispatch"]["state"], "delivered")
        self.assertEqual((self.pastes("a"), len(self.received("a"))), (2, 1))


@helpers.needs_tmux
@needs_311
class Submission(Base):
    def test_a_tag_echoed_without_submission_is_not_delivered_and_the_operator_can_reconcile_it(self):
        env = {"PAI_DISPATCH_CONFIRM_SECONDS": "2"}
        self.env.update(env)
        self.faults(drop_enter=["a", "b"])
        p = self.pai("run", "--workers", "2", "--agent", "claude", "--agent", "codex", "--yes", "--wait", "8")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        for lane in "ab":
            self.assertEqual((self.pastes(lane), self.enters(lane)), (1, 1))
            self.assertIn(self.tag(lane), self.capture(lane), "the pasted tag is on screen ...")
        self.assertEqual(self.received(), [], "... but nothing was submitted")
        time.sleep(2.5)
        rows, code = self.rows()
        self.assertEqual(code, 1)
        for lane in "ab":
            d = rows[lane]["dispatch"]
            self.assertEqual(d["state"], "unknown", d)
            text = " ".join(d["reconcile"])
            for step in ("pai logs %s" % lane, "--mark-delivered", "--redispatch", "Enter"):
                self.assertIn(step, text)
        self.pai("resume", "--wait", "3")
        self.assertEqual((self.pastes("a"), self.pastes("b")), (1, 1), "unknown is never resent on its own")
        # Reconciliation: the line is still in each input box, so the operator presses Enter there.
        for lane in "ab":
            self.operator(lane, "Enter")
        rows, code = self.wait_rows(lambda r: all(r[l]["dispatch"]["state"] == "delivered" for l in "ab"))
        self.assertEqual({l: rows[l]["dispatch"]["state"] for l in "ab"}, {"a": "delivered", "b": "delivered"})
        self.assertIn("transcript", rows["a"]["dispatch"]["evidence"])
        self.assertIn("rollout", rows["b"]["dispatch"]["evidence"])
        self.pai("resume", "--wait", "2")
        self.assertEqual([len(self.received(l)) for l in "ab"], [1, 1])
        self.assertEqual((self.pastes("a"), self.pastes("b")), (1, 1))

    def test_killed_after_enter_before_recording_it_resumes_from_evidence_without_resending(self):
        self.faults(kill_parent_after={"a": "enter"})
        p = self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "8")
        self.assertEqual(p.returncode, -9, p.stdout + p.stderr)
        self.faults()
        self.assertEqual((self.pastes("a"), self.enters("a")), (1, 1))
        self.assertEqual(len(self.wait_for(lambda: self.received("a"))), 1, "the agent got the line")
        rows, _ = self.wait_rows(lambda r: r["a"]["dispatch"]["state"] == "delivered")
        self.assertEqual(rows["a"]["dispatch"]["state"], "delivered", rows["a"]["dispatch"])
        self.assertIn("rollout", rows["a"]["dispatch"]["evidence"])
        for _ in range(2):
            self.pai("resume", "--wait", "2")
        self.assertEqual((self.pastes("a"), len(self.received("a"))), (1, 1))

    def test_killed_after_paste_before_enter_is_unknown_never_resent_and_reconcilable(self):
        self.faults(kill_parent_after={"a": "paste"})
        p = self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "8")
        self.assertEqual(p.returncode, -9, p.stdout + p.stderr)
        self.faults()
        self.assertEqual((self.pastes("a"), self.enters("a")), (1, 0))
        rows, code = self.rows()
        d = rows["a"]["dispatch"]
        self.assertEqual((d["state"], code), ("unknown", 1), d)
        self.assertTrue(d.get("reconcile"), d)
        for _ in range(2):
            self.pai("resume", "--wait", "2")
        self.assertEqual((self.pastes("a"), self.received()), (1, []), "never resent on its own")
        self.operator("a", "Enter")  # the line sat in the input box: the operator submits it
        rows, _ = self.wait_rows(lambda r: r["a"]["dispatch"]["state"] == "delivered")
        self.assertEqual(rows["a"]["dispatch"]["state"], "delivered", rows["a"]["dispatch"])
        self.assertEqual(len(self.received("a")), 1)


@helpers.needs_tmux
@needs_311
class Concurrency(Base):
    def test_concurrent_resume_and_resolution_wait_for_the_lane_lock(self):
        self.behavior(codex_trust=["a"])
        self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "3")
        self.answer_trust("a")
        self.faults(slow_paste=4)
        first = subprocess.Popen([sys.executable, "-B", PAI, "resume", "--wait", "15"], cwd=self.repo, env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)
        try:
            self.assertTrue(self.wait_for(lambda: self.pastes("a") == 1, timeout=20), "the first resume is pasting")
            second = self.pai("resume", "--wait", "2")
            self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
            self.assertIn("another", second.stdout + second.stderr)
            mark = self.pai("resume", "--lane", "a", "--mark-delivered")
            self.assertEqual(mark.returncode, 1, mark.stdout + mark.stderr)
            self.assertIn("another", mark.stdout + mark.stderr)
            out, _ = first.communicate(timeout=60)
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate()
        self.assertEqual(first.returncode, 0, out)
        rows, _ = self.rows()
        d = rows["a"]["dispatch"]
        self.assertEqual(d["state"], "delivered", d)
        self.assertNotIn("operator", d.get("evidence", ""))
        self.assertEqual((self.pastes("a"), len(self.received("a"))), (1, 1))


@helpers.needs_tmux
@needs_311
class Policy(Base):
    def test_stale_remote_control_evidence_neither_blocks_nor_is_upgraded_by_local_dispatch(self):
        # Claude's record was last touched an hour ago (RC inconclusive), its footer shows no hint,
        # and its local input box is idle: the task goes through the local pane; RC stays unverified.
        self.behavior(stale_record=["a"], no_hint=["a"])
        p = self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "10")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        rows, code = self.rows()
        self.assertEqual(code, 1, "not every lane is ready: RC is not verified")
        self.assertEqual(rows["a"]["rc"]["state"], "unverified")
        self.assertIn("last updated", rows["a"]["rc"]["evidence"])
        d = rows["a"]["dispatch"]
        self.assertEqual(d["state"], "delivered", d)
        self.assertIn("transcript", d["evidence"])
        self.assertEqual(len(self.received("a")), 1)

    def test_a_ready_lane_does_not_wait_for_lanes_at_a_trust_prompt(self):
        self.behavior(trust=["a"], codex_trust_late={"c": 2.0})
        p = self.pai("run", "--workers", "3", "--agent", "claude", "--agent", "codex", "--agent", "codex",
                     "--yes", "--wait", "10")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        rows, _ = self.rows()
        self.assertEqual({l: rows[l]["dispatch"]["state"] for l in "abc"},
                         {"a": "pending", "b": "delivered", "c": "pending"})
        self.assertEqual((self.pastes("a"), self.pastes("c")), (0, 0))
        self.assertEqual([r["lane"] for r in self.received()], ["b"])
        self.answer_trust("a")
        self.answer_trust("c")
        p = self.pai("resume", "--wait", "15")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(sorted(r["lane"] for r in self.received()), ["a", "b", "c"])
        self.assertEqual([self.pastes(l) for l in "abc"], [1, 1, 1])


@helpers.needs_tmux
@needs_311
class Legacy(Base):
    def dispatch_file(self):
        import hashlib
        cdir = helpers.git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        d = os.path.join(self.home, "dispatch")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "%s-%s.json" % (hashlib.sha256(os.path.realpath(cdir).encode()).hexdigest()[:12],
                                               self.run_id()))

    def test_records_from_an_earlier_pai_are_never_resent_and_need_evidence_to_count_as_delivered(self):
        self.behavior(codex_trust=["a"])
        self.pai("run", "--workers", "2", "--agent", "codex", "--yes", "--no-dispatch", "--wait", "3")
        # --no-dispatch is recorded in the manifest; an earlier run with dispatch on is the case here
        man = [os.path.join(r, f) for r, _, fs in os.walk(os.path.join(self.home, "workbench", "runs")) for f in fs
               if f == "manifest.json"][0]
        with open(man) as f:
            m = json.load(f)
        m["dispatch"] = True
        with open(man, "w") as f:
            json.dump(m, f)
        rows, _ = self.rows()
        tag_b = self.tag("b")
        # lane b's agent really got its line earlier (an operator typed it here), lane a's never did
        self.tmux("send-keys", "-t", self.pane("b"), "-l", tag_b + " Your task is the note (legacy).")
        self.tmux("send-keys", "-t", self.pane("b"), "Enter")
        self.assertTrue(self.wait_for(lambda: self.received("b")))
        # what an earlier pai wrote: delivered because the tag was visible; no evidence fields
        old = "2026-01-01T00:00:00Z"
        entries = {"%s/task-%s" % (l, l): {"state": "delivered", "lane": l, "task": "task-%s" % l,
                                           "pid": rows[l]["process"]["pid"], "attempt": 1, "started_at": old,
                                           "sent_at": old, "delivered_at": old} for l in "ab"}
        with open(self.dispatch_file(), "w") as f:
            json.dump({"schema": "pai_dispatch_v1", "entries": entries}, f)
        rows, _ = self.rows()
        self.assertEqual(rows["a"]["dispatch"]["state"], "unknown", rows["a"]["dispatch"])
        self.assertIn("earlier pai", rows["a"]["dispatch"]["detail"])
        self.assertEqual(rows["b"]["dispatch"]["state"], "delivered", rows["b"]["dispatch"])
        self.assertIn("rollout", rows["b"]["dispatch"]["evidence"])
        self.answer_trust("a")
        self.pai("resume", "--wait", "5")
        self.assertEqual((self.pastes("a"), self.pastes("b")), (0, 0), "an earlier record is never resent")
        self.assert_ok(self.pai("resume", "--lane", "a", "--mark-delivered"))
        rows, _ = self.rows()
        self.assertEqual(rows["a"]["dispatch"]["state"], "delivered")
        self.assertIn("operator", rows["a"]["dispatch"]["evidence"])


class Units(unittest.TestCase):
    """The rules underneath, without tmux: readiness, record identity, submission evidence,
    the message, and how a stored entry reads."""

    def setUp(self):
        sys.path.insert(0, helpers.SWARM)
        import tempfile
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pai-dispatch-unit-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.wt = os.path.join(self.tmp, "work tree")
        os.makedirs(self.wt)

    def test_readiness_never_comes_from_a_trust_screen_and_claude_trusts_its_own_record(self):
        import agents
        claude, codex = agents.get("claude"), agents.get("codex")
        trust = "Quick safety check: Is this a project you created or one you trust?\n> No, exit\n? for shortcuts"
        P = lambda **r: agents.Probe(self.wt, 1, r or None)  # noqa: E731
        self.assertEqual(claude.input_state(trust, P(status="idle"))[0], False)
        self.assertIn("trust", claude.input_state(trust, P(status="idle"))[1])
        ok, why = claude.input_state("> \n  * 1 background notice", P(status="idle", waitingFor=None))
        self.assertEqual((ok, "idle" in why), (True, True), "no hint on screen, but Claude's record says idle")
        self.assertEqual(claude.input_state("? for shortcuts", P(status="busy"))[0], False, "the record wins")
        ok, why = claude.input_state("? for shortcuts", P(status="waiting", waitingFor="permission prompt"))
        self.assertEqual((ok, "permission prompt" in why), (False, True))
        self.assertEqual(claude.input_state("? for shortcuts", P())[0], True, "no record: the screen decides")
        self.assertEqual(claude.input_state("> working", P())[0], False)
        self.assertEqual(claude.enter_blocked("> [pai x] text", P(status="idle")), None)
        self.assertIn("busy", claude.enter_blocked("> [pai x] text", P(status="busy")))
        self.assertIn("trust", claude.enter_blocked(trust, P(status="idle")))
        self.assertEqual(codex.input_state("? for shortcuts   100% context left")[0], True)
        self.assertEqual(codex.input_state("Trust this folder? Codex can read, edit, and run files here, "
                                           "subject to ...  100% context left")[0], False)
        self.assertEqual(codex.enter_blocked("> [pai x] text  100% context left"), None)

    def test_a_record_counts_only_for_exactly_this_process_and_worktree(self):
        import gitprep
        import rc
        import wtlock
        sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(sessions)
        pid, start, wt = os.getpid(), wtlock.proc_start(os.getpid()), gitprep.canon(self.wt)

        def rec(**kw):
            with open(os.path.join(sessions, "%d.json" % pid), "w") as f:
                json.dump(dict({"pid": pid, "procStart": start, "cwd": wt, "status": "idle"}, **kw), f)
            return rc.own_record(pid, wt, sessions)
        self.assertEqual(rec()["status"], "idle")
        self.assertIsNone(rec(procStart="1"), "an earlier process with the same pid")
        self.assertIsNone(rec(cwd=self.tmp), "another directory")
        self.assertIsNone(rec(pid=pid + 1))
        self.assertIsNone(rc.own_record(pid + 1, wt, sessions))

    def write(self, path, rows):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    def test_claude_evidence_is_a_user_prompt_with_the_tag_in_its_own_transcript_after_the_attempt(self):
        import agents
        claude, tag, since = agents.get("claude"), "[pai r1/a/task-a]", time.time()
        cfg = os.path.join(self.tmp, "claude")
        slug = "".join(c if c.isascii() and c.isalnum() else "-" for c in self.wt)
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int(t % 1 * 1000)  # noqa: E731
        u = lambda text, t, **kw: dict({"type": "user", "timestamp": iso(t), "cwd": self.wt,  # noqa: E731
                                        "message": {"role": "user", "content": text}}, **kw)
        path = os.path.join(cfg, "projects", "elsewhere", "sid-1.jsonl")
        rows = ["not json", {"type": "assistant", "timestamp": iso(since + 1),
                             "message": {"role": "assistant", "content": [{"type": "text", "text": tag}]}},
                u([{"type": "tool_result", "content": tag}], since + 1),  # the tag in a file the agent read
                u(tag + " old attempt", since - 60),
                u(tag + " wrong dir", since + 1, cwd=self.tmp)]
        self.write(path, rows)
        probe = agents.Probe(self.wt, 1, {"sessionId": "sid-1"}, cfg)
        self.assertIsNone(claude.submitted(probe, tag, since))
        self.write(path, rows + [u([{"type": "text", "text": tag + " Your task ..."}], since + 0.5)])
        self.assertIn("transcript", claude.submitted(probe, tag, since))
        # the process is gone (no record): its worktree's transcript directory is searched instead
        self.assertIsNone(claude.submitted(agents.Probe(self.wt, None, None, cfg), tag, since))
        self.write(os.path.join(cfg, "projects", slug, "sid-2.jsonl"), [u(tag + " Your task ...", since + 1)])
        self.assertIn("transcript", claude.submitted(agents.Probe(self.wt, None, None, cfg), tag, since))
        self.assertIsNone(claude.submitted(agents.Probe(self.wt, None, None, cfg), "[pai r1/b/task-b]", since))

    def test_codex_evidence_is_a_user_message_with_the_tag_in_a_rollout_for_this_worktree(self):
        import agents
        codex, tag, since = agents.get("codex"), "[pai r1/b/task-b]", time.time()
        home = os.path.join(self.tmp, "codex")
        day = os.path.join(home, "sessions", time.strftime("%Y/%m/%d", time.gmtime()))
        iso = time.strftime("%Y-%m-%dT%H:%M:%S.999Z", time.gmtime(since + 1))
        msg = lambda role, kind="input_text", text=tag: {"timestamp": iso, "type": "response_item", "payload": {  # noqa: E731
            "type": "message", "role": role, "content": [{"type": kind, "text": text + " Your task ..."}]}}
        meta = lambda cwd: {"timestamp": iso, "type": "session_meta", "payload": {"cwd": cwd}}  # noqa: E731
        probe = agents.Probe(self.wt, None, None, None, home)
        self.write(os.path.join(day, "rollout-1.jsonl"), [meta(self.tmp), msg("user")])  # another directory
        self.write(os.path.join(day, "rollout-2.jsonl"), [meta(self.wt), msg("assistant", "output_text"),
                                                          msg("developer"), "{broken"])
        self.assertIsNone(codex.submitted(probe, tag, since))
        self.assertIsNone(codex.submitted(probe, tag, since + 120), "a prompt from before the attempt")
        self.write(os.path.join(day, "rollout-2.jsonl"), [meta(self.wt), msg("assistant", "output_text"), msg("user")])
        self.assertIn("rollout", codex.submitted(probe, tag, since))
        self.assertIsNone(codex.submitted(agents.Probe(self.wt, None, None, None, os.path.join(self.tmp, "none")),
                                          tag, since))

    def test_timestamps_from_either_agent_parse(self):
        import agents
        self.assertEqual(agents.epoch("1970-01-01T00:00:10Z"), 10.0)
        self.assertAlmostEqual(agents.epoch("1970-01-01T00:00:10.5Z"), 10.5)
        self.assertAlmostEqual(agents.epoch("1970-01-01T01:00:10.123456789+01:00"), 10.123456, places=5)
        for bad in (None, "", "yesterday", 12):
            self.assertIsNone(agents.epoch(bad))

    def fake(self, entry, proc_pid=None, dispatch_on=True):
        import types
        m = types.SimpleNamespace(run_id="r1", dispatch=dispatch_on, tasks_dir=self.tmp)
        w = types.SimpleNamespace(lane="a")
        rl = {"note": "task-a", "branch": "pai/r1/a"}
        proc = {"state": "running", "pid": proc_pid} if proc_pid else {"state": "exited"}
        d = {"schema": "pai_dispatch_v1", "entries": {"a/task-a": entry} if entry else {}}
        return m, w, rl, proc, d

    def test_the_message_is_one_line_whatever_the_note_or_its_name_holds(self):
        import dispatch
        import types
        m, w, rl, _, _ = self.fake(None)
        rl["note"] = "evil\nname\x1b[2J x"
        text = dispatch.message(m, w, {}, rl, types.SimpleNamespace(title="Title\r\nyes\n"))
        self.assertNotRegex(text, r"[\x00-\x1f\x7f  ]")
        self.assertTrue(text.startswith("[pai r1/a/evil name"))

    def test_how_stored_entries_read(self):
        import dispatch
        now, old = time.time(), "2026-01-01T00:00:00Z"
        me, start = os.getpid(), None
        import wtlock
        start = wtlock.proc_start(me)
        base = {"lane": "a", "task": "task-a", "pid": me, "pid_start": start, "attempt": 1}
        no = lambda e: None  # noqa: E731
        yes = lambda e: "the transcript says so"  # noqa: E731
        cases = [
            (None, no, "pending", None),
            (dict(base, state="delivered", delivered_at=old, sent_at=old), no, "unknown", "earlier pai"),
            (dict(base, state="delivered", delivered_at=old, sent_at=old), yes, "delivered", "transcript says so"),
            (dict(base, state="delivered", marked_by_operator=True, delivered_at=old), no, "delivered", "operator"),
            (dict(base, state="sending", started_ts=now), no, "unknown", "before tmux confirmed the paste"),
            (dict(base, state="typed", started_ts=now), no, "unknown", "pasted"),
            (dict(base, state="typed", started_ts=now), yes, "delivered", "transcript says so"),
            (dict(base, state="sent", started_ts=now, sent_ts=now), no, "sent", "waiting"),
            (dict(base, state="sent", started_ts=now - 99, sent_ts=now - 99, sent_at=old), no, "unknown", "no such prompt"),
            (dict(base, state="unknown", started_ts=now, why="trust appeared"), no, "unknown", "trust appeared"),
            (dict(base, state="sent", started_ts=now, sent_ts=now, pid=me + 1), no, "unknown", "ended or changed"),
        ]
        for entry, ev, want, needle in cases:
            m, w, rl, proc, d = self.fake(entry, proc_pid=me)
            st = dispatch.state(m, w, rl, proc, ev, d)
            self.assertEqual(st["state"], want, (entry, st))
            if needle:
                self.assertIn(needle, st["detail"] + st.get("evidence", ""), (entry, st))
            if want == "unknown":
                self.assertTrue(any("--redispatch" in s for s in st["reconcile"]))
                self.assertIn("--mark-delivered", st["detail"])
        m, w, rl, proc, d = self.fake(None, dispatch_on=False)
        self.assertIn("dispatch is off", dispatch.state(m, w, rl, proc, no, d)["detail"])


if __name__ == "__main__":
    unittest.main()
