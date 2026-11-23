"""Run state (1.0 readiness 011-020): durable identifiers, one lifecycle name per lane derived
from recorded evidence first, structured status with a schema version, a narrow-terminal view,
`pai inspect`, crash versus an agent that accepts no input, partial delivery, and state
reconstructed from old and new synthetic records without rewriting them.

The pure part needs nothing; the end-to-end part uses the fake Claude and Codex on an isolated
tmux server, as the dispatch tests do."""
import hashlib
import json
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import dispatch  # noqa: E402
import runstate  # noqa: E402


def manifest(run_id="20261119-120000", lanes=("a", "b"), dispatch_on=True, project="demo"):
    path = os.path.join(os.sep, "state", "workbench", "runs", project, run_id, "manifest.json")
    return types.SimpleNamespace(path=path, run_id=run_id, dispatch=dispatch_on, socket=None,
                                 workers=[types.SimpleNamespace(lane=l) for l in lanes])


def row(lane="a", note="task-a", proc=None, phase="running", ready=True, disp=None, generation=1, trust=None,
        why="Claude's session record for this process says idle (no dialog waiting)"):
    proc = proc if proc is not None else {"state": "running", "pid": 4242, "age": 5}
    return {"lane": lane, "note": note, "task_id": "20261119-120000", "generation": generation, "process": proc,
            "phase": phase, "input": {"ready": ready, "evidence": why},
            "trust": trust or {"state": "not-shown", "evidence": "no folder-trust prompt on the visible screen"},
            "dispatch": disp or {"state": "pending", "detail": "task %s not sent yet" % note}}


DELIVERED = {"state": "delivered", "detail": "task task-a delivered",
             "evidence": "Claude's own transcript for this worktree records the prompt (user message at 2026-11-19T12:00:01.000Z)"}


class Lifecycle(unittest.TestCase):
    def state(self, r, m=None):
        return runstate.lifecycle(r, m or manifest())

    def test_identifiers_come_from_recorded_fields_only(self):
        m = manifest()
        self.assertEqual(runstate.run_key(m), "demo/20261119-120000")
        self.assertEqual(runstate.ids(m, row())["task"], "20261119-120000/a/task-a")
        self.assertEqual(runstate.ids(m, row(note=None, generation=2))["task"], "20261119-120000/a/#2")
        self.assertIn(runstate.ids(m, row())["task"], dispatch.tag(m, "a", "task-a"), "the task id is the dispatch tag")
        bare = types.SimpleNamespace(path="/elsewhere/manifest.json", run_id="run1")
        self.assertEqual(runstate.run_key(bare), "run1", "a manifest outside a project is its run id")

    def test_each_lifecycle_name_and_why(self):
        cases = [
            (row(), "queued", "the agent is ready"),
            (row(phase="starting", ready=False, why="the pane shows nothing yet"), "queued", "starting"),
            (row(phase="waiting-for-trust", ready=False, trust={"state": "prompt-visible", "evidence":
                                                                "Claude's folder-trust prompt is on the visible screen"}),
             "waiting-for-trust", "a person answers it"),
            (row(disp={"state": "sent", "detail": "task task-a: Enter pressed 2s ago; waiting"}), "sent", "Enter pressed"),
            (row(ready=False, why="Claude's session record for this process says busy", disp=DELIVERED), "running", "busy"),
            (row(disp=DELIVERED), "delivered", "transcript"),
            (row(note=None, disp={"state": "none", "detail": "no task note"}), "idle", "no task note"),
        ]
        for r, want, needle in cases:
            lc = self.state(r)
            self.assertEqual(lc["state"], want, (r, lc))
            self.assertIn(needle, lc["why"])
            self.assertEqual(lc["terminal"], want in runstate.TERMINAL)

    def test_a_crash_is_not_an_agent_that_accepts_no_input(self):
        crash = self.state(row(proc={"state": "exited", "exit": "3"}))
        self.assertEqual(crash["state"], "exited")
        self.assertIn("crashed (tmux reports exit status 3", crash["why"])
        self.assertIn("was not delivered", crash["why"])
        clean = self.state(row(proc={"state": "exited", "exit": "0"}, disp=DELIVERED))
        self.assertIn("exited normally", clean["why"])
        self.assertTrue(clean["delivered"], "delivery recorded before the exit is kept")
        dialog = self.state(row(ready=False, why="Claude's session record for this process says waiting (waiting for "
                                                 "permission prompt (fake))"))
        self.assertEqual(dialog["state"], "not-accepting-input")
        self.assertIn("(pid 4242) is alive", dialog["why"])
        old = self.state(row(proc={"state": "running", "pid": 7, "age": 600}, ready=False,
                             why="the screen shows no idle input box"))
        self.assertEqual(old["state"], "not-accepting-input")
        self.assertIn("for 600s since it started", old["why"])
        young = self.state(row(proc={"state": "running", "pid": 7, "age": 3}, ready=False,
                               why="the screen shows no idle input box"))
        self.assertEqual(young["state"], "queued", "within the start-up grace it is still starting")

    def test_unknown_and_stopped(self):
        unk = self.state(row(disp={"state": "unknown", "cause": "Enter was pressed but no transcript", "detail": "x",
                                   "reconcile": ["look", "mark", "redispatch"]}))
        self.assertEqual((unk["state"], unk["next"]), ("unknown", ["look", "mark", "redispatch"]))
        amb = self.state(row(proc={"state": "ambiguous", "detail": "2 windows are named a"}))
        self.assertEqual(amb["state"], "unknown")
        self.assertIn("2 windows", amb["why"])
        gone = self.state(row(proc={"state": "no-window"}, disp=DELIVERED))
        self.assertEqual(gone["state"], "stopped")
        self.assertTrue(gone["delivered"])

    def test_partial_delivery_is_explicit(self):
        m = manifest(lanes=("a", "b", "c"))
        rows = [row("a", "task-a", disp=DELIVERED), row("b", "task-b", phase="waiting-for-trust", ready=False)]
        s = runstate.annotate(m, rows)
        self.assertEqual(s["delivery"], "partial")
        self.assertEqual(s["delivered"], ["a"])
        self.assertEqual(s["not_delivered"], {"b": "waiting-for-trust"})
        self.assertEqual(s["not_started"], ["c"])
        self.assertEqual(s["attention"], ["b", "c"])
        line = runstate.summary_line(s)
        self.assertIn("PARTIAL delivery: 1 of 3 task(s) delivered (a), not delivered: b (waiting-for-trust), c "
                      "(not-started)", line)
        self.assertEqual(runstate.annotate(manifest(lanes=("a",)), [row(disp=DELIVERED)])["delivery"], "complete")
        self.assertEqual(runstate.annotate(manifest(lanes=("a",)), [row()])["delivery"], "none")

    def test_the_brief_view_fits_a_narrow_window_with_long_names(self):
        m = manifest(run_id="20261119-120000-2", lanes=("a",), project="a-very-long-project-name-for-wrapping")
        r = row(note="a-very-long-task-note-name-that-goes-on", disp={"state": "unknown", "cause": "Enter was pressed for "
                "task a-very-long-task-note-name-that-goes-on at 2026-11-19T12:00:00Z, but after 25s the transcript shows "
                "no such prompt", "detail": "x", "reconcile": ["look at the lane: `pai logs a --run 20261119-120000-2 "
                                                                "--tail 40`"]})
        s = runstate.annotate(m, [r])
        out = []
        runstate.brief(m, [r], s, 40, out.append)
        self.assertTrue(out)
        self.assertTrue(all(len(line) <= 40 for line in out), [l for l in out if len(l) > 40])
        self.assertIn("a  UNKNOWN  pid 4242", out)


class Reconstruct(unittest.TestCase):
    """dispatch.state + lifecycle on synthetic records written the old way and the new way."""

    def setUp(self):
        self.m = manifest()
        self.w = types.SimpleNamespace(lane="a")
        self.rl = {"note": "task-a", "branch": "pai/r/a", "state": "started"}  # an old lane record: no agent fields

    def derive(self, entry, proc, evidence=None):
        d = {"schema": dispatch.SCHEMA, "entries": {"a/task-a": entry} if entry else {}}
        before = json.dumps(d, sort_keys=True)
        disp = dispatch.state(self.m, self.w, self.rl, proc, (lambda e: evidence) if evidence else None, d)
        self.assertEqual(json.dumps(d, sort_keys=True), before, "deriving a state never rewrites the record")
        return runstate.lifecycle(row(disp=disp, proc=proc), self.m)

    def test_old_delivered_by_visible_tag_is_unknown_until_there_is_evidence(self):
        old = {"state": "delivered", "lane": "a", "task": "task-a", "delivered_at": "2026-11-01T10:00:00Z", "pid": 9}
        lc = self.derive(old, {"state": "running", "pid": 9, "age": 50})
        self.assertEqual(lc["state"], "unknown")
        self.assertIn("tag was visible", lc["why"])
        lc = self.derive(old, {"state": "running", "pid": 9, "age": 50}, evidence="Claude's own transcript records it")
        self.assertEqual(lc["state"], "delivered")

    def test_new_records_keep_delivery_after_the_process_is_gone(self):
        new = {"state": "delivered", "attempt": 1, "pid": 9, "started_ts": time.time() - 60,
               "evidence": "Claude's own transcript for this worktree records the prompt (user message at T)"}
        lc = self.derive(new, {"state": "no-window"})
        self.assertEqual((lc["state"], lc["delivered"]), ("stopped", True))
        marked = {"state": "delivered", "marked_by_operator": True, "delivered_at": "2026-11-19T12:00:00Z"}
        self.assertTrue(self.derive(marked, {"state": "exited", "exit": "1"})["delivered"])

    def test_interrupted_attempts_are_unknown_not_resent(self):
        for st in ("sending", "typed"):
            e = {"state": st, "attempt": 1, "pid": 9, "pid_start": None, "started_ts": time.time()}
            lc = self.derive(e, {"state": "running", "pid": 9, "age": 50})
            self.assertEqual(lc["state"], "unknown", st)
            self.assertTrue(any("--redispatch" in n for n in lc["next"]))

    def test_no_entry_is_queued(self):
        self.assertEqual(self.derive(None, {"state": "running", "pid": 9, "age": 2})["state"], "queued")


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
class EndToEnd(WB):
    def setUp(self):
        super().setUp()
        self.note("task-a", priority=9)
        self.note("task-b", priority=8)
        self.env["PAI_DISPATCH_SETTLE"] = helpers.FAKE_SETTLE
        self.init()

    def json_status(self, *extra):
        p = self.pai("status", "--json", *extra)
        return json.loads(p.stdout), p.returncode

    def test_status_inspect_and_reconstruction(self):
        self.behavior(trust=["b"])
        p = self.pai("run", "--workers", "2", "--agent", "claude", "--yes", "--wait", "8")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)  # lane b waits for trust
        self.assertIn("summary   2 lane(s)", p.stdout)
        d, code = self.json_status()
        self.assertEqual(d["schema"], "pai_status_v1")
        self.assertTrue(d["run"].endswith("/" + d["run_id"]))
        lanes = {l["lane"]: l for l in d["lanes"]}
        self.assertEqual(lanes["b"]["lifecycle"]["state"], "waiting-for-trust")
        self.assertIn(lanes["a"]["lifecycle"]["state"], ("delivered", "running"))
        self.assertEqual(lanes["a"]["ids"]["task"], "%s/a/task-a" % d["run_id"])
        self.assertEqual(d["summary"]["delivery"], "partial")
        self.assertEqual(d["summary"]["not_delivered"], {"b": "waiting-for-trust"})
        for k in ("run_id", "session", "socket", "ok", "lanes"):
            self.assertIn(k, d, "fields of the unversioned output are kept")

        p = self.pai("status", env={"COLUMNS": "40"})
        lines = p.stdout.rstrip("\n").split("\n")
        self.assertTrue(all(len(l) <= 40 for l in lines), [l for l in lines if len(l) > 40])
        self.assertIn("b  WAITING-FOR-TRUST", [l.split("  pid")[0] for l in lines])
        self.assertIn("PARTIAL", p.stdout)

        p = self.pai("inspect", "a")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for kind in ("run", "task", "note", "process", "trust", "input", "dispatch", "delivery", "window", "worktree"):
            self.assertIn("  %-9s " % kind, p.stdout)
        p = self.pai("inspect", "a", "--json")
        ev = {e["kind"]: e for e in json.loads(p.stdout)["evidence"]}
        for kind in ("run", "task", "note", "input", "window"):
            self.assertTrue(os.path.isfile(ev[kind]["source"].split(" entry ")[0]), (kind, ev[kind]))
        self.assertTrue(os.path.isfile(ev["dispatch"]["source"].split(" entry ")[0]))
        self.assertTrue(os.path.isdir(ev["delivery"]["source"]), ev["delivery"])
        p = self.pai("inspect", "b")
        self.assertEqual(p.returncode, 1, "a lane that needs a person exits 1")
        self.assertIn("pai trust", p.stdout)

        # The tmux server goes away: state is reconstructed from the records alone.
        state = self.env.get("PAI_SWARM_HOME")
        self.tmux("kill-server")
        before = digest(state)
        d, code = self.json_status()
        lanes = {l["lane"]: l for l in d["lanes"]}
        self.assertEqual(lanes["a"]["lifecycle"]["state"], "stopped")
        self.assertTrue(lanes["a"]["lifecycle"]["delivered"], "delivery evidence was recorded and survives")
        self.assertEqual(lanes["b"]["lifecycle"]["state"], "stopped")
        self.assertFalse(lanes["b"]["lifecycle"]["delivered"])
        self.pai("inspect", "a")
        self.pai("status")
        self.assertEqual(digest(state), before, "status and inspect never rewrite a record")

        # The same dispatch record written the old way (delivered because the tag was visible).
        dpath = next(os.path.join(r, f) for r, _, fs in os.walk(os.path.join(state, "dispatch")) for f in fs
                     if f.endswith(".json"))
        with open(dpath) as f:
            rec = json.load(f)
        entry = rec["entries"]["a/task-a"]
        rec["entries"]["a/task-a"] = {k: entry[k] for k in ("state", "lane", "task", "pid", "delivered_at")}
        with open(dpath, "w") as f:
            json.dump(rec, f)
        before = digest(state)
        d, _ = self.json_status()
        a = {l["lane"]: l for l in d["lanes"]}["a"]
        self.assertEqual((a["lifecycle"]["state"], a["lifecycle"]["delivered"]), ("stopped", True),
                         "Claude's transcript is still there: it is the evidence, not the old record's word")
        self.assertIn("transcript", a["dispatch"]["evidence"])
        os.rename(os.path.join(self.cfg, "projects"), os.path.join(self.cfg, "projects.moved"))
        d, _ = self.json_status()
        a = {l["lane"]: l for l in d["lanes"]}["a"]
        self.assertEqual(a["lifecycle"]["state"], "unknown")
        self.assertIn("tag was visible", a["lifecycle"]["why"])
        self.assertEqual(digest(state), before, "an old record is read as it is")

    def test_crash_and_dialog(self):
        self.behavior(exit={"a": 3}, dialog=["b"])
        p = self.pai("run", "--workers", "2", "--agent", "claude", "--yes", "--wait", "4")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        d, _ = self.json_status()
        lanes = {l["lane"]: l for l in d["lanes"]}
        self.assertEqual(lanes["a"]["lifecycle"]["state"], "exited")
        self.assertIn("crashed (tmux reports exit status 3", lanes["a"]["lifecycle"]["why"])
        self.assertEqual(lanes["b"]["lifecycle"]["state"], "not-accepting-input")
        self.assertIn("waiting for permission prompt", lanes["b"]["lifecycle"]["why"])
        self.assertEqual(d["summary"]["delivery"], "none")
        self.assertEqual(self.received(), [], "nothing is typed into an agent that accepts no input")


if __name__ == "__main__":
    unittest.main()
