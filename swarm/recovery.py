"""`pai recover`: a preview of what recovering an interrupted run would do, lane by lane, before
anything changes; `--apply` carries out the automatic actions and the ones you chose, and records
every decision.

Per lane the preview names the lane, its task (RUN_ID/LANE/NOTE), its lifecycle (runstate.py) and
the evidence behind it, then one action:

  restart            the agent exited or its window is gone: start it again in the same worktree
                     and branch (no fetch, no new branch); a task that was not delivered is then
                     delivered as usual (dispatch.py: settle, paste, Enter, transcript evidence)
  deliver            the task was never sent and the agent is ready: send it
  record-delivered   the agent's own transcript already records the prompt (for example, it was
                     delivered just before pai stopped): record that; nothing is resent
  mark-delivered     ONLY when you choose it (--choose LANE=mark-delivered) for an unknown dispatch
  redispatch         ONLY when you choose it (--choose LANE=redispatch) for an unknown dispatch
  trust              the agent asks whether to trust a folder: a human action (`pai trust`); pai
                     never answers it. The folder and its repository are named
  look               the agent is alive but accepts no input, or its window is not certain: a human
                     action (`pai attach`, close a window yourself)
  wait               a dispatch is in flight (Enter pressed, transcript awaited)
  none               nothing to recover (delivered, running, idle)
  leave              you chose to leave the lane alone (--choose LANE=leave)

An unknown dispatch is never resent and never marked without your explicit choice. With
--apply, pai holds the run's session mutex for the whole recovery, so a second `pai recover`,
`pai resume` or `pai run` on the same run is refused instead of acting on the same lane.

Decisions are appended to $PAI_SWARM_HOME/recovery/<repo key>-<run>.jsonl, one JSON object per
line (schema pai_recovery_v1): a `begin` line with the timestamp, the lane, task, chosen action,
who chose it (default or operator) and the before-state, written (and fsync'ed) before anything
is done; then an `end` line with the after-state and the result. A `begin` without an `end`
(pai was interrupted) is shown by the next preview with its recorded before-state.
"""
import datetime
import hashlib
import json
import os
import sys
import uuid

import dispatch
import gitprep
import rc
import runstate
import wtlock

SCHEMA = "pai_recovery_v1"
AUTOMATIC = {"restart", "deliver", "record-delivered"}
CHOSEN_ONLY = {"mark-delivered", "redispatch", "leave"}
HUMAN = {"trust", "look"}


def log(msg=""):
    print(msg, flush=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def log_path(m):
    cdir = gitprep.common_dir(m.repo) or m.repo
    return os.path.join(wtlock.home(), "recovery", "%s-%s.jsonl" % (hashlib.sha256(cdir.encode()).hexdigest()[:12], m.run_id))


def read_log(m):
    try:
        with open(log_path(m), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    res = []
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue  # a line cut by an interruption
        if isinstance(d, dict) and d.get("schema") == SCHEMA:
            res.append(d)
    return res


def unfinished(m):
    """`begin` records without an `end`: recoveries that were interrupted."""
    entries = read_log(m)
    ended = {e.get("id") for e in entries if e.get("phase") == "end"}
    return [e for e in entries if e.get("phase") == "begin" and e.get("id") not in ended]


def close_superseded(m, lane, by, after):
    """After a recovery of `lane` completed, end every earlier interrupted one of that lane: the
    lane was checked again under the lock and recovered, so the interruption is settled (the
    history keeps both records)."""
    for e in unfinished(m):
        if e.get("lane") == lane and e.get("id") != by:
            _append(m, {"phase": "end", "id": e["id"], "lane": lane, "task": e.get("task"), "action": e.get("action"),
                        "result": "superseded by %s, which checked the lane again and completed" % by, "after": after})


def _append(m, d):
    p = log_path(m)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    d = dict(d, schema=SCHEMA, ts=now(), run=runstate.run_key(m))
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())  # the before-state must survive pai dying right after it
    return d


def snapshot(r):
    """The parts of a status row that say what state the lane is in (kept in the log)."""
    p = r.get("process") or {}
    return {"lifecycle": r["lifecycle"]["state"], "why": r["lifecycle"]["why"], "delivered": r["lifecycle"]["delivered"],
            "process": p.get("state"), "pid": p.get("pid"), "exit": p.get("exit"),
            "dispatch": (r.get("dispatch") or {}).get("state")}


def plan_lane(m, r, choice=None):
    """{'lane', 'task', 'lifecycle', 'action', 'by', 'why', 'would', 'human', 'choices'} for one row."""
    lc, disp, proc = r["lifecycle"], r.get("dispatch") or {}, r.get("process") or {}
    state, lane = lc["state"], r["lane"]
    rr = " --run %s" % m.run_id
    base = {"lane": lane, "task": r["ids"]["task"], "lifecycle": state, "why": lc["why"], "worktree": r.get("worktree"),
            "choices": [], "human": False}

    def act(action, would, by="default", **kw):
        return dict(base, action=action, would=would, by=by, **kw)

    if r.get("handed_off"):
        return act("none", "handed off; nothing to recover in this session (%s)" % lc["why"])
    unknown_choices = ["--choose %s=mark-delivered   (the agent is working on it: record it, send nothing)" % lane,
                       "--choose %s=redispatch       (nothing arrived: send it once more)" % lane,
                       "--choose %s=leave            (decide later)" % lane]
    if choice == "leave":
        return act("leave", "nothing; you chose to leave lane %s alone" % lane, by="operator")
    if choice in ("mark-delivered", "redispatch"):
        if disp.get("state") != "unknown" and not (choice == "mark-delivered" and disp.get("state") == "sent"):
            return act("refused", "--choose %s=%s applies to an unknown dispatch; this lane's dispatch is %s" % (
                lane, choice, disp.get("state")), by="operator")
        if choice == "redispatch" and state in ("exited", "stopped"):
            return act("refused", "the agent is not running; restart it first (`pai recover --apply%s --lane %s`), "
                                  "then choose" % (rr, lane), by="operator")
        return act(choice, {"mark-delivered": "record task %s as delivered by you; nothing is sent" % r["ids"]["task"],
                            "redispatch": "send task %s once more (attempt %s)" % (
                                r["ids"]["task"], "n+1")}[choice], by="operator")
    if choice == "restart" and state not in ("exited", "stopped"):
        return act("refused", "--choose %s=restart: the agent is %s, not exited or stopped" % (lane, state), by="operator")
    if state in ("exited", "stopped"):
        task = ("task %s was delivered before (%s): it is not resent" % (r["ids"]["task"], disp.get("evidence"))
                if lc["delivered"] else "then deliver task %s once it is ready (unless the dispatch is unknown)" %
                r["ids"]["task"] if r.get("note") else "no task note")
        res = act("restart", "start %s again in %s on branch %s (%s); %s" % (
            (r.get("agent") or {}).get("name"), r.get("worktree"), r.get("branch"),
            "its window is gone" if state == "stopped" else "it exited with status %s" % proc.get("exit"), task),
            by="operator" if choice == "restart" else "default")
        if disp.get("state") == "unknown":
            res["choices"] = unknown_choices
            res["would"] += "; its dispatch is unknown, so after the restart you choose: mark-delivered or redispatch"
        return res
    if disp.get("upgrade"):
        return act("record-delivered", "record task %s as delivered: %s (nothing is resent)" % (
            r["ids"]["task"], disp["upgrade"]["evidence"]))
    if state == "unknown" and disp.get("state") == "unknown":
        return act("choose", "nothing until you choose (an unknown dispatch is never resent on its own)",
                   choices=unknown_choices)
    if state == "unknown":
        return act("look", lc["why"], human=True, choices=lc["next"])
    if state == "waiting-for-trust":
        return act("trust", "%s asks whether to trust %s (repository %s). Only you answer it: `pai trust%s`, then "
                            "`pai recover%s` again" % ((r.get("agent") or {}).get("name"), r.get("worktree"), m.repo, rr, rr),
                   human=True)
    if state == "not-accepting-input":
        return act("look", "%s; look at it with `pai attach %s%s`" % (lc["why"], lane, rr), human=True)
    if state == "queued":
        if not m.dispatch:
            return act("none", "dispatch is off for this run; give the task yourself")
        return act("deliver", "send task %s when the agent has been ready for %ss" % (
            r["ids"]["task"], dispatch.settle_seconds()))
    if state == "sent":
        return act("wait", "Enter was pressed; the transcript evidence is awaited")
    return act("none", "nothing to recover (%s)" % state)


def build(m, rows, choices):
    return [plan_lane(m, r, choices.get(r["lane"])) for r in rows]


def show(m, plans, summary, interrupted, out=log):
    out("RECOVERY PREVIEW for run %s (nothing has changed)" % runstate.run_key(m))
    out("  %s" % runstate.summary_line(summary))
    for e in interrupted:
        out("INTERRUPTED  an earlier recovery of lane %s (%s, chosen by %s, at %s) did not finish." % (
            e.get("lane"), e.get("action"), e.get("by"), e.get("ts")))
        out("             before it: %s (%s)" % (e["before"].get("lifecycle"), e["before"].get("why")))
        out("             The state below is what the evidence says now.")
    for p in plans:
        out("")
        out("lane %s   task %s   now %s" % (p["lane"], p["task"], p["lifecycle"]))
        out("  evidence  %s" % p["why"])
        tag = {"restart": "AUTO", "deliver": "AUTO", "record-delivered": "AUTO", "mark-delivered": "CHOSEN",
               "redispatch": "CHOSEN", "leave": "CHOSEN", "trust": "HUMAN", "look": "HUMAN", "choose": "CHOOSE",
               "refused": "REFUSED"}.get(p["action"], "")
        out("  action    %s%s: %s" % (p["action"], " [%s]" % tag if tag else "", p["would"]))
        for c in p["choices"]:
            out("            %s" % c)
    for lane in summary["not_started"]:
        out("")
        out("lane %s   not-started: no task was prepared for it; `pai resume --run %s` prepares nothing new either "
            "(a changed set of lanes is a new run)" % (lane, m.run_id))
    todo = [p for p in plans if p["action"] in AUTOMATIC | {"mark-delivered", "redispatch"}]
    out("")
    if todo:
        out("`pai recover --run %s --apply` would do: %s. Lanes marked HUMAN or CHOOSE are left alone." % (
            m.run_id, ", ".join("%s %s" % (p["action"], p["lane"]) for p in todo)))
    else:
        out("Nothing for pai to do on its own.%s" % (" Lanes marked HUMAN or CHOOSE need you." if any(
            p["action"] in ("trust", "look", "choose") for p in plans) else ""))


def collect(m):
    rows, cur = rc.collect_status(m, rc.Tmux(m.socket))
    summary = runstate.annotate(m, rows)
    return rows, cur, summary


def apply(m, a, choices, only):
    """Under the session mutex: re-read the evidence (another command may have acted since the
    preview), record each decision, act, record the result."""
    rows, cur, summary = collect(m)
    rows = [r for r in rows if not only or r["lane"] in only]
    plans = build(m, rows, choices)
    show(m, plans, summary, unfinished(m))
    todo = [p for p in plans if p["action"] in AUTOMATIC | {"mark-delivered", "redispatch"}]
    if not todo:
        return 0 if not any(p["action"] == "refused" for p in plans) else 2
    by_lane = {r["lane"]: r for r in rows}
    begun = {}
    for p in todo:
        begun[p["lane"]] = _append(m, {"phase": "begin", "id": str(uuid.uuid4()), "lane": p["lane"], "task": p["task"],
                                       "action": p["action"], "by": p["by"], "would": p["would"],
                                       "before": snapshot(by_lane[p["lane"]]), "worktree": p["worktree"]})
    log("\nAPPLYING (recorded in %s)" % log_path(m))
    tmux = rc.Tmux(m.socket)
    failed = 0
    restart = [p["lane"] for p in todo if p["action"] == "restart"]
    if restart:
        specs, problems = rc.build_specs(m, "resume")
        if problems:
            for x in problems:
                log("REFUSED   %s" % x)
            failed += len(restart)
        else:
            started = []
            for spec in specs:
                try:
                    pl, rec = gitprep.apply(spec, log=log)  # resume: verifies branch and lock, no fetch
                except gitprep.Blocked as e:
                    for x in e.problems:
                        log("REFUSED   %s" % x)
                    failed += 1
                    continue
                started += [(m.worker(l.lane), rec, rec["lanes"][l.lane]) for l in spec.lanes if l.lane in restart]
            failed += rc.ensure_workers(m, tmux, started)
    for p in todo:
        if p["action"] == "mark-delivered":
            w = m.worker(p["lane"])
            rec, rl = cur[p["lane"]]
            proc = rc.lane_process(m, tmux, w)
            st = dispatch.state(m, w, rl, proc, rc.evidence_fn(m, w, rl, rc.probe(m, w, proc)))
            if st["state"] in ("unknown", "sent"):
                dispatch.mark_delivered(m, w, rl, proc)
            else:
                log("NOT MARKED lane %s: its dispatch is %s now" % (p["lane"], st["state"]))
    send = {p["lane"] for p in todo if p["action"] in ("restart", "deliver", "redispatch", "record-delivered")}
    force = {p["lane"] for p in todo if p["action"] == "redispatch"}
    if send and m.dispatch:
        rc.watch_startup(m, tmux, a.wait, dispatch=True, lanes=send, force=force)
    rows2, _, summary2 = collect(m)
    after = {r["lane"]: r for r in rows2}
    log("")
    for p in todo:
        r = after.get(p["lane"])
        snap = snapshot(r) if r else {"lifecycle": "not-started"}
        ok = {"restart": snap.get("process") == "running", "deliver": snap.get("delivered"),
              "record-delivered": snap.get("delivered"), "mark-delivered": snap.get("delivered"),
              "redispatch": snap.get("dispatch") in ("delivered", "sent")}[p["action"]]
        failed += 0 if ok else 1
        _append(m, {"phase": "end", "id": begun[p["lane"]]["id"], "lane": p["lane"], "task": p["task"],
                    "action": p["action"], "result": "done" if ok else "not-done", "after": snap})
        if ok:
            close_superseded(m, p["lane"], begun[p["lane"]]["id"], snap)
        log("%-8s lane %s  %s: %s -> %s (%s)" % ("DONE" if ok else "NOT DONE", p["lane"], p["action"],
                                                 p.get("lifecycle"), snap.get("lifecycle"), snap.get("why", "")))
    log("\nrun %s: %s" % (runstate.run_key(m), runstate.summary_line(summary2)))
    return 0 if not failed else 1


def history(m, out=log):
    entries = read_log(m)
    if not entries:
        out("No recovery has been recorded for run %s (%s)." % (m.run_id, log_path(m)))
        return 0
    for e in entries:
        if e.get("phase") == "begin":
            out("%s  begin  lane %s  %s (by %s)  before: %s" % (e["ts"], e["lane"], e["action"], e["by"],
                                                               e["before"].get("lifecycle")))
        else:
            out("%s  end    lane %s  %s: %s  after: %s" % (e["ts"], e["lane"], e["action"], e.get("result"),
                                                         (e.get("after") or {}).get("lifecycle")))
    for e in unfinished(m):
        out("INTERRUPTED: %s of lane %s, begun %s, has no end record" % (e["action"], e["lane"], e["ts"]))
    return 0


def parse_choices(items, lanes):
    res = {}
    allowed = {"mark-delivered", "redispatch", "leave", "restart"}
    for item in items or []:
        lane, sep, action = item.partition("=")
        if not sep or lane not in lanes or action not in allowed:
            raise ValueError("--choose takes LANE=ACTION with a lane of this run (%s) and one of %s, not %r" % (
                ", ".join(lanes), ", ".join(sorted(allowed)), item))
        res[lane] = action
    return res


def main(m, a):
    lanes = [w.lane for w in m.workers]
    try:
        choices = parse_choices(a.choose, lanes)
    except ValueError as e:
        log("pai recover: %s. Nothing was changed." % e)
        return 2
    only = set(a.lane or [])
    for lane in only:
        if lane not in lanes:
            log("pai recover: run %s has no lane %s (lanes: %s). Nothing was changed." % (m.run_id, lane, ", ".join(lanes)))
            return 2
    if getattr(a, "continue_lane", None):
        import continuation
        if a.continue_lane not in lanes:
            log("pai recover: run %s has no lane %s (lanes: %s). Nothing was changed." % (m.run_id, a.continue_lane,
                                                                                         ", ".join(lanes)))
            return 2
        choice = choices.get(a.continue_lane)
        if choice not in (None, "mark-delivered", "leave"):
            log("pai recover --continue: --choose %s=%s does not apply; a continuation takes mark-delivered or leave."
                % (a.continue_lane, choice))
            return 2
        return continuation.capture(m, a.continue_lane, write=a.write, choice=choice)
    if a.history:
        return history(m)
    if not a.apply:
        rows, _, summary = collect(m)
        rows = [r for r in rows if not only or r["lane"] in only]
        plans = build(m, rows, choices)
        if a.json:
            json.dump({"schema": "pai_recovery_preview_v1", "run": runstate.run_key(m), "changed": False,
                       "summary": summary, "interrupted": unfinished(m), "lanes": plans}, sys.stdout, indent=1,
                      ensure_ascii=False)
            print()
        else:
            show(m, plans, summary, unfinished(m))
        return 0 if not any(p["action"] == "refused" for p in plans) else 2
    with rc.session_mutex(m) as got:
        if not got:
            log("pai recover: another pai command (run, resume, recover, next, cleanup) is changing run %s right now; "
                "nothing was changed. Run `pai recover --run %s` again when it has finished." % (m.run_id, m.run_id))
            return 1
        return apply(m, a, choices, only)
