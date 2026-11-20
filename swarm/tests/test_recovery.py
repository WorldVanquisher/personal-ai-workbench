"""`pai recover` (1.0 readiness 021-030), end to end with the fake Claude and the fake Codex on an
isolated tmux server: a preview that changes nothing, actions that name lane, task and evidence,
no resend of an ambiguous task without an explicit choice, a task delivered before pai stopped
(transcript evidence), recorded decisions with before- and after-state, an interrupted recovery,
two recoveries at once, and a trust prompt kept as a human action."""
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, PAI, needs_311, lines  # noqa: E402


def digest(*dirs):
    h = hashlib.sha256()
    for d in dirs:
        for root, ds, fs in os.walk(d):
            ds.sort()
            for fn in sorted(fs):
                if fn.endswith(".mutex"):
                    continue
                p = os.path.join(root, fn)
                with open(p, "rb") as f:
                    h.update(p.encode() + b"\0" + f.read())
    return h.hexdigest()


@helpers.needs_tmux
@needs_311
class Recover(WB):
    def setUp(self):
        super().setUp()
        self.note("task-a", priority=9)
        self.note("task-b", priority=8)
        self.env["PAI_DISPATCH_SETTLE"] = "0.5"
        self.init()

    # -- helpers
    def lanes(self):
        p = self.pai("status", "--json")
        d = json.loads(p.stdout)
        return {l["lane"]: l for l in d["lanes"]}, d

    def preview(self, *extra):
        p = self.pai("recover", "--json", *extra)
        self.assertIn(p.returncode, (0, 2), p.stdout + p.stderr)
        d = json.loads(p.stdout)
        return {x["lane"]: x for x in d["lanes"]}, d

    def log_entries(self):
        d = os.path.join(self.home, "recovery")
        fs = [os.path.join(d, f) for f in os.listdir(d)] if os.path.isdir(d) else []
        return [e for f in fs for e in lines(f)]

    def dispatch_file(self):
        d = os.path.join(self.home, "dispatch")
        return next(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))

    def edit_entry(self, lane, fn):
        path = self.dispatch_file()
        with open(path) as f:
            rec = json.load(f)
        rec["entries"]["%s/task-%s" % (lane, lane)] = fn(rec["entries"]["%s/task-%s" % (lane, lane)])
        with open(path, "w") as f:
            json.dump(rec, f)

    def starts(self, lane):
        return [s for s in lines(os.path.join(self.ctl, "starts.jsonl")) if s["lane"] == lane]

    def crashed_run(self, agent="claude", behavior=None):
        self.behavior(**(behavior or {"exit_once": {"a": 3}}))
        p = self.pai("run", "--workers", "1", "--agent", agent, "--yes", "--wait", "3")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        rows, _ = self.lanes()
        self.assertEqual(rows["a"]["lifecycle"]["state"], "exited", rows["a"]["lifecycle"])
        return rows

    def recover_crash(self, agent):
        self.crashed_run(agent, {"exit_once": {"a": 3}} if agent == "claude" else {"codex_exit_once": {"a": 3}})
        before = digest(self.home, self.tasks, self.root)
        panes = self.tmux("list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{pane_dead}").stdout
        p = self.pai("recover")
        out = p.stdout
        self.assertEqual(p.returncode, 0, out + p.stderr)
        self.assertIn("RECOVERY PREVIEW", out)
        self.assertIn("nothing has changed", out)
        self.assertIn("task %s/a/task-a" % json.loads(self.pai("status", "--json").stdout)["run_id"], out)
        self.assertIn("now exited", out)
        self.assertIn("crashed (tmux reports exit status 3", out)
        self.assertIn("action    restart [AUTO]", out)
        plans, d = self.preview()
        self.assertEqual(plans["a"]["action"], "restart")
        self.assertFalse(d["changed"])
        self.assertEqual(digest(self.home, self.tasks, self.root), before, "the preview wrote nothing")
        self.assertEqual(self.tmux("list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{pane_dead}").stdout, panes)
        self.assertEqual(len(self.starts("a")), 1, "the preview started nothing")

        p = self.pai("recover", "--apply", "--wait", "15")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("DONE     lane a  restart: exited ->", p.stdout)
        rows, d = self.lanes()
        self.assertTrue(rows["a"]["lifecycle"]["delivered"], rows["a"]["lifecycle"])
        self.assertIn("transcript" if agent == "claude" else "rollout", rows["a"]["dispatch"]["evidence"])
        self.assertEqual(len(self.received("a")), 1)
        self.assertEqual(len(self.starts("a")), 2, "one crash, one restart")
        begin, end = self.log_entries()
        self.assertEqual((begin["phase"], begin["action"], begin["by"], begin["before"]["lifecycle"]),
                         ("begin", "restart", "default", "exited"))
        self.assertEqual(begin["before"]["exit"], "3")
        self.assertEqual((end["phase"], end["id"], end["result"]), ("end", begin["id"], "done"))
        self.assertIn(end["after"]["lifecycle"], ("delivered", "running"))
        for e in (begin, end):
            self.assertRegex(e["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z$")
            self.assertEqual(e["schema"], "pai_recovery_v1")
        p = self.pai("recover", "--history")
        self.assertIn("begin  lane a  restart (by default)  before: exited", p.stdout)
        p = self.pai("recover", "--apply")
        self.assertEqual(p.returncode, 0)
        self.assertIn("Nothing for pai to do on its own.", p.stdout)
        self.assertEqual(len(self.received("a")), 1, "a second recovery resends nothing")

    def test_claude_crash_preview_then_recovery(self):
        self.recover_crash("claude")

    def test_codex_crash_preview_then_recovery(self):
        self.recover_crash("codex")

    def test_an_unknown_dispatch_waits_for_an_explicit_choice(self):
        self.behavior()
        self.assert_ok(self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "8"))
        self.assertEqual(len(self.received("a")), 1)
        # pai stopped after the paste, before it recorded Enter; the transcript is not there either
        self.edit_entry("a", lambda e: dict({k: e[k] for k in ("lane", "task", "pid", "pid_start", "attempt",
                                                                "started_at", "started_ts", "tag", "pane_id")},
                                            state="typed"))
        os.rename(os.path.join(self.cfg, "projects"), os.path.join(self.cfg, "projects.moved"))
        plans, _ = self.preview()
        self.assertEqual(plans["a"]["action"], "choose")
        self.assertEqual(len(plans["a"]["choices"]), 3)
        p = self.pai("recover", "--apply")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("action    choose [CHOOSE]", p.stdout)
        self.assertIn("--choose a=redispatch", p.stdout)
        self.assertEqual(len(self.received("a")), 1, "nothing is resent without a choice")
        self.assertEqual(self.log_entries(), [], "no decision was taken, so none is recorded")
        p = self.pai("recover", "--choose", "a=redispatch", "--apply", "--wait", "10")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.received("a")), 2, "sent once more, because the operator chose it")
        begin = [e for e in self.log_entries() if e["phase"] == "begin"]
        self.assertEqual([(e["action"], e["by"], e["before"]["lifecycle"]) for e in begin],
                         [("redispatch", "operator", "unknown")])
        p = self.pai("recover", "--choose", "a=redispatch")
        self.assertEqual(p.returncode, 2, "redispatch applies only to an unknown dispatch")
        self.assertIn("REFUSED", p.stdout)

    def test_mark_delivered_records_and_sends_nothing(self):
        self.behavior()
        self.assert_ok(self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "8"))
        self.edit_entry("a", lambda e: dict(e, state="unknown", why="synthetic: Enter may not have arrived",
                                            evidence=None))
        os.rename(os.path.join(self.cfg, "projects"), os.path.join(self.cfg, "projects.moved"))
        p = self.pai("recover", "--choose", "a=mark-delivered", "--apply")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows, _ = self.lanes()
        self.assertIn("recorded by the operator", rows["a"]["dispatch"]["evidence"])
        self.assertEqual(len(self.received("a")), 1)

    def test_a_task_delivered_before_pai_stopped_is_recorded_not_resent(self):
        self.behavior()
        self.assert_ok(self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "8"))
        # pai stopped right after Enter, before it saw the transcript; the transcript has the prompt
        self.edit_entry("a", lambda e: dict({k: v for k, v in e.items() if k not in ("evidence", "delivered_at")},
                                            state="sent", sent_ts=e["started_ts"], sent_at=e["started_at"]))
        plans, _ = self.preview()
        self.assertEqual(plans["a"]["action"], "record-delivered")
        self.assertIn("transcript", plans["a"]["would"])
        p = self.pai("recover", "--apply")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(self.dispatch_file()) as f:
            e = json.load(f)["entries"]["a/task-a"]
        self.assertEqual(e["state"], "delivered")
        self.assertIn("transcript", e["evidence"])
        self.assertEqual(len(self.received("a")), 1, "nothing was resent")

    def test_trust_is_a_human_action_with_its_folder(self):
        self.behavior(trust=["a"])
        self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "3")
        plans, _ = self.preview()
        self.assertEqual((plans["a"]["action"], plans["a"]["human"]), ("trust", True))
        rows, _ = self.lanes()
        self.assertIn(rows["a"]["worktree"], plans["a"]["would"])
        self.assertIn("repository %s" % self.repo, plans["a"]["would"])
        p = self.pai("recover", "--apply")
        self.assertEqual(p.returncode, 0)
        self.assertIn("[HUMAN]", p.stdout)
        self.assertIn("Lanes marked HUMAN or CHOOSE need you.", p.stdout)
        self.assertEqual(self.received(), [])
        self.assertIn("Is this a project you created", self.tmux("capture-pane", "-p", "-t", self.pane("a")).stdout,
                      "the prompt is still there, unanswered")

    def test_an_interrupted_recovery_keeps_its_before_state(self):
        self.crashed_run()
        env = dict(self.env, PAI_DISPATCH_SETTLE="8")  # a long settle window: time to interrupt it
        p = subprocess.Popen([sys.executable, "-B", PAI, "recover", "--apply", "--wait", "20"], cwd=self.repo, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            self.assertTrue(self.wait_for(lambda: [e for e in self.log_entries() if e["phase"] == "begin"], 20))
            self.assertTrue(self.wait_for(lambda: len(self.starts("a")) == 2, 10), "the restart happened")
            p.send_signal(signal.SIGKILL)
        finally:
            p.wait(30)
            p.stdout.close()
        self.assertEqual(self.received("a"), [], "killed inside the settle window: nothing was typed")
        out = self.pai("recover").stdout
        self.assertIn("INTERRUPTED  an earlier recovery of lane a (restart, chosen by default", out)
        self.assertIn("before it: exited", out)
        self.assertIn("INTERRUPTED: restart of lane a", self.pai("recover", "--history").stdout)
        p = self.pai("recover", "--apply", "--wait", "15")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.received("a")), 1)
        ends = [e for e in self.log_entries() if e["phase"] == "end"]
        self.assertEqual([e["action"] for e in ends], ["deliver", "restart"], "the second recovery delivered the queued "
                         "task, then settled the interrupted restart")
        self.assertTrue(ends[1]["result"].startswith("superseded by "), ends[1])
        self.assertNotIn("INTERRUPTED", self.pai("recover", "--history").stdout)

    def test_two_recoveries_of_the_same_lane(self):
        self.crashed_run()
        sys.path.insert(0, helpers.SWARM)
        import rc
        with mock.patch.dict(os.environ, {"PAI_SWARM_HOME": self.home}):
            run_dir = os.path.join(self.home, "workbench", "runs")
            path = next(os.path.join(r, "manifest.json") for r, _, fs in os.walk(run_dir) if "manifest.json" in fs)
            m = rc.Manifest(path)
            with rc.session_mutex(m) as got:
                self.assertTrue(got)
                p = self.pai("recover", "--apply")
        self.assertEqual(p.returncode, 1)
        self.assertIn("another pai command", p.stdout)
        self.assertEqual(len(self.starts("a")), 1, "refused: nothing started")
        self.assertEqual(self.log_entries(), [])
        procs = [subprocess.Popen([sys.executable, "-B", PAI, "recover", "--apply", "--wait", "10"], cwd=self.repo,
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  text=True) for _ in range(2)]
        outs = []
        for q in procs:
            outs.append(q.communicate(timeout=120)[0])
        self.assertEqual(len(self.starts("a")), 2, "exactly one restart: %s" % outs)
        self.assertEqual(len(self.received("a")), 1, "the task arrived once")
        acted = [o for o in outs if "APPLYING" in o]
        self.assertEqual(len(acted), 1, outs)


if __name__ == "__main__":
    unittest.main()
