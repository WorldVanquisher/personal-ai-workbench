"""Run state for people and programs: durable identifiers, one lifecycle name per lane derived
from the evidence pai already records, and a run summary that says plainly when only some lanes
got their task. Read-only: nothing here writes a record.

Identifiers (stable across pai restarts; computed from fields every record since v0.3 has, so
older records get them without being rewritten):

  run   PROJECT/RUN_ID       RUN_ID is unique within a project (`pai run` never reuses one).
                             A manifest outside a workbench project is just RUN_ID.
  task  RUN_ID/LANE/NOTE     the same text as the dispatch tag `[pai RUN_ID/LANE/NOTE]` that the
                             agent's transcript must show; a lane without a task note is
                             RUN_ID/LANE/#GENERATION (`pai next` raises the generation).
  record                     the task record's own `task_id` (the run id for the first batch),
                             kept as it is.

Lifecycle, per lane. Recorded evidence (the dispatch record, then the task record) is read first;
what tmux and the agent show now refines it. Each name comes with `why` (the evidence, in one
sentence) and `next` (what a person can do, as commands):

  queued               a task note is assigned and not sent yet: the agent is starting, its input
                       box is not ready yet, or dispatch is off for the run
  waiting-for-trust    the agent's folder-trust prompt is on screen; only a person answers it
  sent                 Enter was pressed less than PAI_DISPATCH_CONFIRM_SECONDS ago; pai waits for
                       the agent's own transcript to record the prompt
  running              the task was delivered and the agent is busy with it
  delivered            the task was delivered and the agent's input box is ready again (its turn
                       ended; read its handoff)
  idle                 no task note for this lane; the agent runs and waits for input
  not-accepting-input  the agent process is alive but has not accepted input: a dialog is waiting,
                       or no ready input box for longer than the start-up grace (120 s)
  exited               the agent process ended (tmux kept the pane): a crash when its status is
                       not 0. Different from not-accepting-input: there is no process at all
  stopped              the lane has no window (closed, or the tmux server is gone)
  unknown              the evidence conflicts or is missing: an unknown dispatch (nothing resends
                       it on its own), two windows with the lane's name, a window that is not the
                       one this run started, or a window without the agent
  not-started          the run lists the lane but no task was ever prepared for it
  handed-off           the lane's session was stopped and its task handed to a continuation
                       (`pai recover --continue`): goal, completed steps, evidence and open decisions
                       are in the task note; it is never restarted or resent here

`terminal` marks the names that do not change until a person acts (or that are final for pai):
waiting-for-trust, delivered, idle, not-accepting-input, exited, stopped, unknown, not-started,
handed-off.
"""
import os

SCHEMA = "pai_status_v1"
ORDER = ("queued", "waiting-for-trust", "sent", "running", "delivered", "idle", "not-accepting-input", "exited",
         "stopped", "unknown", "not-started", "handed-off")
TERMINAL = {"waiting-for-trust", "delivered", "idle", "not-accepting-input", "exited", "stopped", "unknown",
            "not-started", "handed-off"}
ATTENTION = {"waiting-for-trust", "not-accepting-input", "exited", "stopped", "unknown"}
STARTING_GRACE = 120  # seconds, as rc.STARTING_GRACE


def project_of(m):
    """The workbench project a manifest belongs to (runs/PROJECT/RUN_ID/manifest.json), or None."""
    run_dir = os.path.dirname(m.path)
    proj_dir = os.path.dirname(run_dir)
    return os.path.basename(proj_dir) if os.path.basename(os.path.dirname(proj_dir)) == "runs" else None


def run_key(m):
    p = project_of(m)
    return "%s/%s" % (p, m.run_id) if p else m.run_id


def task_key(run_id, lane, note, generation=1):
    return "%s/%s/%s" % (run_id, lane, note if note else "#%s" % (generation or 1))


def ids(m, row):
    return {"run": run_key(m), "task": task_key(m.run_id, row["lane"], row.get("note"), row.get("generation")),
            "record": row.get("task_id"), "generation": row.get("generation", 1)}


def _r(m, lane):
    return " --run %s" % m.run_id if m is not None else ""


def lifecycle(row, m=None):
    """{'state', 'why', 'terminal', 'attention', 'delivered', 'next': [command, ...]} for one
    status row (rc.lane_status). Pure: it reads only the row."""
    lane, proc, disp = row["lane"], row.get("process") or {}, row.get("dispatch") or {}
    pstate, dstate = proc.get("state"), disp.get("state")
    r = _r(m, lane)
    delivered = dstate == "delivered"
    note = row.get("note")
    after = " (task %s was delivered first: %s)" % (note, disp.get("evidence")) if delivered else (
        " (task %s was not delivered: dispatch %s)" % (note, dstate) if note else "")

    def res(state, why, nxt=()):
        return {"state": state, "why": why, "terminal": state in TERMINAL, "attention": state in ATTENTION,
                "delivered": delivered, "next": list(nxt)}

    h = row.get("handed_off")
    if h:  # recorded first: whatever tmux shows now, this session is over and its task lives on elsewhere
        return res("handed-off", "the session was stopped and its task handed off at %s (head %s on %s)%s" % (
            h.get("at"), (h.get("head") or "?")[:12], h.get("branch") or row.get("branch"),
            "; continued in %s" % h["continued_by"] if h.get("continued_by") else ""),
            [] if h.get("continued_by") else ["pai run --continue %s   # a fresh session continues it" % h.get("note")])

    if pstate in ("ambiguous", "mismatched"):
        return res("unknown", "%s: %s%s" % (pstate, proc.get("detail") or "the lane's window is not certain", after),
                   ["close the extra or foreign window yourself (`tmux%s list-windows`), then `pai resume%s`" % (
                       "" if not m or not m.socket else " -S %s" % m.socket if os.sep in (m.socket or "") else
                       " -L %s" % m.socket, r)])
    if dstate == "unknown":
        return res("unknown", disp.get("cause") or disp.get("detail") or "the dispatch is unknown", disp.get("reconcile") or [])
    if pstate == "exited":
        code = str(proc.get("exit") or "?")
        kind = "crashed" if code not in ("0",) else "exited normally"
        return res("exited", "the agent process %s (tmux reports exit status %s; the pane is kept)%s" % (kind, code, after),
                   ["pai logs %s%s --tail 40   # its last screen" % (lane, r),
                    "pai recover%s   # preview: restart it in the same worktree%s" % (r, "" if delivered else
                                                                                      " and deliver the task")])
    if pstate == "no-window":
        return res("stopped", "the lane has no tmux window (it was closed, or the tmux server is gone)%s" % after,
                   ["pai recover%s   # preview what a resume would start" % r])
    if pstate == "no-claude":
        return res("unknown", "the lane's window runs %s, not the agent%s" % (
            (proc.get("detail") or "something else").replace("pane runs ", ""), after),
            ["pai attach %s%s   # look at it" % (lane, r)])
    if pstate != "running":
        return res("unknown", "no process evidence for the lane (%s)%s" % (pstate or "none", after))
    phase = row.get("phase")
    inp = row.get("input") or {}
    if phase == "waiting-for-trust":
        return res("waiting-for-trust", "%s; the task waits until a person answers it (pai never does)" % (
            (row.get("trust") or {}).get("evidence") or "the folder-trust prompt is on screen"),
            ["pai trust%s   # opens each prompt in this terminal; answering is yours" % r,
             "pai attach %s%s" % (lane, r)])
    if delivered:
        if inp.get("ready"):
            return res("delivered", "%s; the agent is idle again (%s)" % (disp.get("evidence"), inp.get("evidence")),
                       ["pai logs %s%s --tail 40   # its answer and handoff" % (lane, r)])
        return res("running", "%s; the agent is busy with it (%s)" % (disp.get("evidence"), inp.get("evidence")),
                   ["pai logs %s%s --follow" % (lane, r)])
    if dstate == "sent":
        return res("sent", disp.get("detail") or "Enter was pressed; waiting for the agent's transcript", ["pai status%s" % r])
    age = proc.get("age")
    waiting_dialog = "waiting for" in (inp.get("evidence") or "")
    stuck = not inp.get("ready") and phase == "running" and (waiting_dialog or (isinstance(age, (int, float))
                                                                                 and age >= STARTING_GRACE))
    if stuck:
        return res("not-accepting-input", "the agent process (pid %s) is alive but its input is not ready: %s%s%s" % (
            proc.get("pid"), inp.get("evidence"), "" if waiting_dialog else " for %ds since it started" % int(age),
            after), ["pai attach %s%s   # see what it shows; answer or close the dialog yourself" % (lane, r)])
    if dstate == "none":
        return res("idle", "no task note is assigned to this lane; the agent %s" % (
            "is ready for input" if inp.get("ready") else "is starting (%s)" % inp.get("evidence")),
            ["pai paste %s FILE%s   # give it text yourself" % (lane, r)])
    if dstate == "pending":
        why = disp.get("detail") or "not sent yet"
        if phase == "starting":
            why += "; the agent is starting"
        elif not inp.get("ready"):
            why += "; its input box is not ready yet (%s)" % inp.get("evidence")
        elif m is not None and not m.dispatch:
            pass
        else:
            why += "; the agent is ready, so the next `pai resume` (or the running `pai run`) sends it"
        return res("queued", why, ["pai resume%s   # deliver it once the agent is ready" % r] if m is None or m.dispatch
                   else ["pai paste %s %s%s" % (lane, note or "FILE", r)])
    return res("unknown", "dispatch state %r is not one pai knows%s" % (dstate, after))


def summary(m, rows):
    """Run-level view: how many lanes, which got their task, which did not (and why, by
    lifecycle name), which were never started. delivery: complete | partial | none | no-tasks."""
    started = {r["lane"]: r for r in rows}
    with_task = [r for r in rows if r.get("note")]
    delivered = [r["lane"] for r in with_task if (r.get("lifecycle") or {}).get("delivered")]
    not_delivered = {r["lane"]: (r.get("lifecycle") or {}).get("state") for r in with_task
                     if not (r.get("lifecycle") or {}).get("delivered")}
    not_started = [w.lane for w in m.workers if w.lane not in started]
    if not with_task:
        delivery = "no-tasks"
    elif not not_delivered and not not_started:
        delivery = "complete"
    elif delivered:
        delivery = "partial"
    else:
        delivery = "none"
    counts = {}
    for r in rows:
        s = (r.get("lifecycle") or {}).get("state")
        counts[s] = counts.get(s, 0) + 1
    for lane in not_started:
        counts["not-started"] = counts.get("not-started", 0) + 1
    return {"lanes": len(m.workers), "started": len(rows), "with_task": len(with_task), "delivered": delivered,
            "not_delivered": not_delivered, "not_started": not_started, "delivery": delivery,
            "states": {k: counts[k] for k in ORDER if k in counts},
            "attention": sorted([r["lane"] for r in rows if (r.get("lifecycle") or {}).get("attention")] + not_started)}


def summary_line(s):
    parts = ["%d %s" % (n, k) for k, n in s["states"].items()]
    text = "%d lane(s): %s" % (s["lanes"], ", ".join(parts) or "none started")
    if s["delivery"] == "partial":
        text += "; PARTIAL delivery: %d of %d task(s) delivered (%s), not delivered: %s" % (
            len(s["delivered"]), s["with_task"] + len(s["not_started"]), ", ".join(s["delivered"]),
            ", ".join("%s (%s)" % kv for kv in sorted(s["not_delivered"].items())) +
            ("" if not s["not_started"] else "%s%s (not-started)" % (", " if s["not_delivered"] else "",
                                                                      ", ".join(s["not_started"]))))
    elif s["delivery"] == "complete":
        text += "; every task delivered"
    elif s["delivery"] == "none":
        text += "; no task delivered yet"
    return text


def annotate(m, rows):
    """Add ids and lifecycle to every row (in place); return the run summary."""
    for r in rows:
        r["ids"] = ids(m, r)
        r["lifecycle"] = lifecycle(r, m)
    return summary(m, rows)


def wrap(text, width, indent):
    import textwrap
    return textwrap.wrap(text, width=max(20, width), initial_indent=indent, subsequent_indent=indent,
                         break_long_words=True, break_on_hyphens=False) or [indent]


def brief(m, rows, s, width, out):
    """The narrow-terminal view: one line per lane, then why and the first next step wrapped to
    `width` (long lane names, paths and evidence wrap instead of running off the screen)."""
    for line in wrap("run %s: %s" % (run_key(m), summary_line(s)), width, ""):
        out(line)
    for r in rows:
        lc = r["lifecycle"]
        p = r.get("process") or {}
        out("")
        for line in wrap("%s  %s%s" % (r["lane"], lc["state"].upper() if lc["attention"] else lc["state"],
                                       "  pid %s" % p["pid"] if p.get("pid") else ""), width, ""):
            out(line)
        for line in wrap(lc["why"], width, "  "):
            out(line)
        if lc["next"]:
            for line in wrap("next: " + lc["next"][0], width, "  "):
                out(line)
    for lane in s["not_started"]:
        out("")
        out("%s  not-started" % lane)


# ---------------------------------------------------------------- inspect: evidence -> lane

def sources(m, row):
    """[{'kind', 'state', 'detail', 'source'}]: every piece of evidence behind the lane's
    lifecycle, each with the file or command it was read from."""
    import dispatch
    import gitprep
    import rc
    lane, note, proc = row["lane"], row.get("note"), row.get("process") or {}
    tm = row.get("tmux") or {}
    cdir = gitprep.common_dir(m.repo) or m.repo
    sock = "tmux -S %s" % m.socket if m.socket and os.sep in m.socket else "tmux -L %s" % m.socket if m.socket else "tmux"
    pid = proc.get("pid")
    rows = [{"kind": "run", "state": run_key(m), "detail": "manifest of run %s" % m.run_id, "source": m.path},
            {"kind": "task", "state": row["ids"]["task"], "detail": "task record %s, generation %s, branch %s, base %s" % (
                row.get("task_id"), row.get("generation", 1), row.get("branch"), (row.get("base_sha") or "?")[:12]),
             "source": gitprep.record_path(cdir, row.get("task_id"))}]
    if note:
        rows.append({"kind": "note", "state": note, "detail": "the task note the agent is told to read",
                     "source": os.path.join(m.tasks_dir, note + ".md") if m.tasks_dir else note})
    rows.append({"kind": "process", "state": proc.get("state"), "detail": (
        "pid %s, %ss old" % (pid, proc.get("age")) if pid else "exit status %s" % proc.get("exit")
        if proc.get("state") == "exited" else proc.get("detail") or proc.get("state")) + (
        "; window %s pane %s" % (tm.get("window_id"), tm.get("pane_id")) if tm.get("window_id") else ""),
        "source": "%s list-windows -t =%s; ps -ww -o args= -p %s" % (sock, m.session, pid or "PID")
        if pid else "%s list-windows -t =%s" % (sock, m.session)})
    rows.append({"kind": "trust", "state": (row.get("trust") or {}).get("state"),
                 "detail": (row.get("trust") or {}).get("evidence"),
                 "source": "the lane's visible screen (`pai logs %s --run %s`)" % (lane, m.run_id)})
    agent = (row.get("agent") or {}).get("name")
    inp = row.get("input") or {}
    if agent == "claude" and pid:
        src = os.path.join(m.sessions_dir(), "%s.json" % pid)
    else:
        src = "the lane's visible screen"
    rows.append({"kind": "input", "state": "ready" if inp.get("ready") else "not ready", "detail": inp.get("evidence"),
                 "source": src})
    disp = row.get("dispatch") or {}
    e = (dispatch.load(m).get("entries") or {}).get(dispatch.key(lane, note)) if note else None
    detail = disp.get("cause") or disp.get("detail")
    if e:
        detail += " (attempt %s%s%s)" % (e.get("attempt"), ", sent %s" % e["sent_at"] if e.get("sent_at") else "",
                                         ", to pid %s" % e["pid"] if e.get("pid") else "")
    rows.append({"kind": "dispatch", "state": disp.get("state"), "detail": detail,
                 "source": "%s entry %s" % (dispatch.path(m), dispatch.key(lane, note)) if note else dispatch.path(m)})
    if disp.get("evidence"):
        if agent == "claude":
            slug = "".join(c if c.isascii() and c.isalnum() else "-" for c in os.path.realpath(row["worktree"]))
            where = os.path.join(m.claude_dir(), "projects", slug) + os.sep
        elif agent == "codex":
            where = os.path.join(m.codex_home(), "sessions") + os.sep
        else:
            where = "the operator's record"
        rows.append({"kind": "delivery", "state": "delivered", "detail": disp["evidence"], "source": where})
    import pastes
    w = next((x for x in m.workers if x.lane == lane), None)
    for pe in pastes.entries(m, lane) if w else []:  # text pasted by hand into this lane's agent
        st, why = pastes.state(m, w, pe, proc)
        rows.append({"kind": "paste", "state": st, "detail": "%s: %s" % (pastes.describe(pe), why),
                     "source": pastes.path(m)})
    rows.append({"kind": "window", "state": "recorded" if tm.get("window_id") else "none",
                 "detail": "which tmux server, window and pane this run started the lane in",
                 "source": rc.own_path(m)})
    wt = row.get("worktree")
    rows.append({"kind": "worktree", "state": "present" if "head" in row else "missing",
                 "detail": ("on %s, HEAD %s, %s commit(s) ahead of base, %s uncommitted path(s)" % (
                     row.get("on_branch") or "a detached HEAD", (row.get("head") or "?")[:12], row.get("ahead"),
                     row.get("uncommitted"))) if "head" in row else "the worktree directory does not exist",
                 "source": wt})
    return rows


def inspect_lines(m, row, width=100):
    lc = row["lifecycle"]
    out = ["lane %s   task %s   lifecycle %s" % (row["lane"], row["ids"]["task"], lc["state"])]
    out += wrap("why: " + lc["why"], width, "  ")
    out.append("evidence (what each part rests on, and where it was read)")
    for s in sources(m, row):
        out += wrap("%-9s %s: %s" % (s["kind"], s["state"], s["detail"]), width, "  ")
        out += wrap("from %s" % s["source"], width, "            ")
    if lc["next"]:
        out.append("next")
        for n in lc["next"]:
            out += wrap(n, width, "  ")
    return out
