"""Task dispatch: give a lane's assigned task note to its agent once the agent's input box is
ready, at most once per run, lane and task, and report it delivered only on evidence that the
agent took it as a prompt.

The message is one line: a tag, the task note's path and title, and the lane's branch. The
note itself stays where it is. It reaches the agent through tmux only: `load-buffer` from a
file this module writes (mode 0600), `paste-buffer -p` (a bracketed paste when the agent asks
for one) and one Enter. No shell is involved, so nothing in a note is interpreted, and every
control character (newlines included) is replaced by a space.

Evidence, kept apart (`pai status` shows each one):
  process   the lane's agent process; an attempt is tied to its pid and start time
  trust     whether the agent's folder-trust prompt is on the visible screen. A trust prompt is
            never answered, and no keystroke is sent while it is visible
  input     whether the idle input box is ready (agents.Adapter.input_state): for Claude, its
            own session record for exactly this process saying idle; otherwise the screen
  dispatch  the states below; `delivered` needs submission evidence
  rc        Remote Control (rc.py). Dispatch goes through the local pane: RC never gates it, and
            a dispatch never changes what RC evidence says

Sending (the caller holds the run's session mutex, so two pai commands never send at once):
  1. the lane has been input-ready, with no trust prompt on screen, for SETTLE seconds
     (PAI_DISPATCH_SETTLE, default 3): a prompt that appears after the first frame is seen first
  2. `sending` is recorded; the screen is read again; the line is pasted
  3. `typed` is recorded; after a short pause the screen is read again. If the trust prompt is
     there now, or the process changed, Enter is NOT sent and the dispatch is `unknown`
  4. Enter; `sent` is recorded
  5. `delivered` once the agent's own transcript records a user prompt that contains the
     line's tag, at or after the attempt started (agents.Adapter.submitted). The tag being
     visible in the pane proves nothing: it may still sit in the input box

State per run, lane and task, in <home>/dispatch/<repo key>-<run>.json (schema pai_dispatch_v1;
records written by earlier versions are read as they are):
  sending, typed, sent, delivered, unknown   as above; an entry also keeps its attempt number,
             the pid it was sent to, timestamps and `evidence` (or `why`)
Shown states: none, pending, sent (Enter pressed less than CONFIRM seconds ago,
PAI_DISPATCH_CONFIRM_SECONDS, default 20; evidence awaited), delivered, unknown. Anything
ambiguous is `unknown`, with the steps to reconcile it (`reconcile`); nothing is ever resent
on its own. An entry an earlier pai marked delivered because its tag was visible counts as
delivered only with evidence now, or once the operator marks it.
"""
import hashlib
import json
import os
import re
import time

import agents
import gitprep
import wtlock

SCHEMA = "pai_dispatch_v1"
PASTE_ENTER_GAP = 0.5  # seconds between the paste and the check that precedes Enter


def _env_seconds(name, default):
    try:
        return max(0.0, float(os.environ.get(name) or default))
    except ValueError:
        return default


def settle_seconds():
    return _env_seconds("PAI_DISPATCH_SETTLE", 3.0)


def confirm_seconds():
    return _env_seconds("PAI_DISPATCH_CONFIRM_SECONDS", 20.0)


def path(m):
    cdir = gitprep.common_dir(m.repo) or m.repo
    return os.path.join(wtlock.home(), "dispatch", "%s-%s.json" % (hashlib.sha256(cdir.encode()).hexdigest()[:12], m.run_id))


def load(m):
    try:
        with open(path(m), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("schema") == SCHEMA and isinstance(d.get("entries"), dict) \
            else {"schema": SCHEMA, "entries": {}}
    except (OSError, ValueError):
        return {"schema": SCHEMA, "entries": {}}


def save(m, d):
    p = path(m)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())  # the record of a keystroke must not be lost if pai dies right after it
    os.replace(tmp, p)


def key(lane, task):
    return "%s/%s" % (lane, task)


def tag(m, lane, task):
    return "[pai %s/%s/%s]" % (m.run_id, lane, task)


def _clean(text, limit=None):
    text = re.sub(r"[\x00-\x1f\x7f\x85  ]+", " ", text or "")
    text = " ".join(text.split())
    return text[:limit] if limit else text


def message(m, w, rec, rl, note):
    note_path = os.path.join(m.tasks_dir, rl["note"] + ".md") if m.tasks_dir else rl["note"]
    title = _clean(note.title if note else rl["note"], 160)
    return _clean("%s Your task is the note %s (\"%s\"). Read the whole note and follow this repository's own "
                  "instructions first. Work only in this worktree, on branch %s. Do not push or open a pull request: "
                  "the operator reviews and publishes." % (tag(m, w.lane, rl["note"]), note_path, title, rl["branch"]))


def entry(m, w, rl, d=None):
    if not rl.get("note"):
        return None
    return ((d or load(m)).get("entries") or {}).get(key(w.lane, rl["note"]))


def same_process(e, proc):
    return proc.get("state") == "running" and e.get("pid") == proc.get("pid") and (
        e.get("pid_start") is None or str(e["pid_start"]) == str(wtlock.proc_start(proc["pid"])))


def since(e):
    """When the attempt started (seconds since the epoch): evidence must not be older."""
    for k in ("started_ts", "sent_ts"):
        if isinstance(e.get(k), (int, float)):
            return float(e[k])
    for k in ("started_at", "sent_at", "delivered_at"):
        t = agents.epoch(e.get(k))
        if t is not None:
            return t
    return None


def reconcile(m, lane):
    """What the operator does about an `unknown` dispatch: look, then decide. The commands name
    this run, so they work wherever more than one run is active."""
    r = " --run %s" % m.run_id
    return ["look at the lane: `pai logs %s%s --tail 40` (or `pai attach %s%s`)" % (lane, r, lane, r),
            "the agent is working on the task: `pai resume%s --lane %s --mark-delivered`" % (r, lane),
            "the line still sits in the input box: press Enter there yourself (the next `pai status` sees the "
            "prompt), or clear it and run `pai resume%s --lane %s --redispatch`" % (r, lane),
            "nothing arrived (input box empty, agent idle): `pai resume%s --lane %s --redispatch`" % (r, lane)]


def _unknown(m, w, detail):
    return {"state": "unknown", "reconcile": reconcile(m, w.lane), "cause": detail,
            "detail": "%s. Nothing resends it: look first (`pai logs %s --run %s`), then `pai resume --run %s --lane %s "
                      "--mark-delivered` or `--redispatch`" % (detail, w.lane, m.run_id, m.run_id, w.lane)}


def _delivered(rl, e, evidence, upgrade=False):
    res = {"state": "delivered", "detail": "task %s delivered" % rl["note"], "evidence": evidence}
    if upgrade:
        res["upgrade"] = {"evidence": evidence, "attempt": e.get("attempt")}
    return res


def state(m, w, rl, proc, evidence=None, d=None):
    """{'state': none|pending|sent|delivered|unknown, 'detail', 'evidence'?, 'reconcile'?} for the
    lane's current task. Read-only. `evidence(entry)` returns submission evidence or None."""
    if not rl.get("note"):
        return {"state": "none", "detail": "no task note is assigned to this lane"}
    e = entry(m, w, rl, d)
    if not e:
        return {"state": "pending", "detail": "task %s not sent yet%s" % (
            rl["note"], "" if m.dispatch else " (dispatch is off for this run; give it to the agent yourself)")}
    st = e.get("state")
    if st == "delivered" and (e.get("evidence") or e.get("marked_by_operator")):
        return _delivered(rl, e, e.get("evidence") or "recorded by the operator (`pai resume --mark-delivered`) at %s"
                          % e.get("delivered_at"))
    ev = evidence(e) if evidence and since(e) is not None else None
    if ev:
        return _delivered(rl, e, ev, upgrade=True)
    if st == "delivered":
        return _unknown(m, w, "an earlier pai recorded task %s as delivered at %s because its tag was visible in the "
                              "pane, which does not prove it was submitted, and the agent's transcript shows no such "
                              "prompt now" % (rl["note"], e.get("delivered_at")))
    if st == "unknown":
        return _unknown(m, w, e.get("why") or "dispatch of task %s is unknown" % rl["note"])
    if not same_process(e, proc):
        return _unknown(m, w, "the agent process (pid %s) ended or changed while task %s was %s, and its transcript "
                              "shows no such prompt; it is not known whether it arrived" % (e.get("pid"), rl["note"], st))
    if st == "sending":
        return _unknown(m, w, "a dispatch of task %s stopped before tmux confirmed the paste; the agent's transcript "
                              "shows no such prompt" % rl["note"])
    if st == "typed":
        return _unknown(m, w, e.get("why") or "task %s was pasted into the input box, but pai stopped before it "
                                              "pressed (or recorded) Enter; the transcript shows no such prompt" % rl["note"])
    sent = e.get("sent_ts") if isinstance(e.get("sent_ts"), (int, float)) else since(e)
    age = time.time() - (sent or 0)
    if st == "sent" and age < confirm_seconds():
        return {"state": "sent", "detail": "task %s: Enter pressed %ds ago; waiting for the agent's transcript to record "
                "the prompt" % (rl["note"], int(age))}
    return _unknown(m, w, "Enter was pressed for task %s at %s, but after %ds the agent's transcript shows no such "
                          "prompt: the line may still sit in the input box" % (rl["note"], e.get("sent_at"), int(age)))


def record_delivered(m, w, rl, st):
    """Persist evidence found by state() (`upgrade`), so it stays true after the process or its
    transcript is gone. The caller holds the session mutex."""
    if not st.get("upgrade"):
        return
    d = load(m)
    e = (d.get("entries") or {}).get(key(w.lane, rl["note"]))
    if e is None or (e.get("state") == "delivered" and e.get("evidence")):
        return
    e.update(state="delivered", delivered_at=gitprep.now(), evidence=st["upgrade"]["evidence"])
    save(m, d)


def deliver(m, tmux, w, rec, rl, proc, note, capture, input_state, enter_blocked, evidence, alive, force=False):
    """Send the lane's task if (and only if) it was never sent, or `force` (the operator's
    --redispatch of an `unknown` dispatch), and the agent is ready now. The caller has seen the
    lane ready for SETTLE seconds and holds the session mutex. capture() is the visible screen,
    input_state(screen) is (ready, why), enter_blocked(screen) is why Enter must not follow the
    paste (or None), evidence(entry) is submission evidence or None, alive() is whether the
    attempt's process is still the lane's process. Returns the resulting state."""
    d = load(m)
    entries = d.setdefault("entries", {})
    k = key(w.lane, rl["note"])
    cur = state(m, w, rl, proc, evidence, d)
    if cur["state"] == "delivered":
        record_delivered(m, w, rl, cur)
        return cur
    if cur["state"] not in ("pending", "unknown") or (cur["state"] == "unknown" and not force):
        return cur
    if proc.get("state") != "running":
        return cur
    ready, why = input_state(capture())
    if not ready:
        return dict(cur, detail="%s; not sent: %s" % (cur["detail"], why))
    prev = entries.get(k)
    attempt = (prev or {}).get("attempt", 0) + 1
    e = {"state": "sending", "lane": w.lane, "task": rl["note"], "task_id": rec["task_id"], "pid": proc["pid"],
         "pid_start": wtlock.proc_start(proc["pid"]), "pane_id": proc["window"]["pane_id"],
         "window_id": proc["window"]["window_id"], "attempt": attempt, "started_at": gitprep.now(),
         "started_ts": time.time(), "tag": tag(m, w.lane, rl["note"])}
    if prev:
        e["previous"] = (prev.get("previous") or []) + [{x: prev.get(x) for x in (
            "attempt", "state", "pid", "started_at", "sent_at", "why") if prev.get(x) is not None}]
    entries[k] = e
    save(m, d)

    def undo(reason):
        if prev:
            entries[k] = prev
        else:
            del entries[k]
        save(m, d)
        return dict(state(m, w, rl, proc, None, d), detail="nothing was sent: %s" % reason)

    text = message(m, w, rec, rl, note)
    buf = "pai-%s-%s" % (m.run_id, w.lane)
    fpath = os.path.join(wtlock.home(), "dispatch", "%s.%d.msg" % (buf, os.getpid()))
    fd = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        p1 = tmux.run("load-buffer", "-b", buf, fpath)
    finally:
        os.remove(fpath)
    if p1.returncode != 0:
        return undo("tmux could not load the task: %s" % (p1.stderr.strip() or "error"))
    ready, why = input_state(capture())  # the last look before the first keystroke
    if not ready or not alive():
        tmux.run("delete-buffer", "-b", buf)
        return undo(why if not ready else "the agent process changed")
    p2 = tmux.run("paste-buffer", "-d", "-p", "-b", buf, "-t", e["pane_id"])
    if p2.returncode != 0:
        return undo("tmux could not paste the task: %s" % (p2.stderr.strip() or "error"))
    e.update(state="typed", typed_at=gitprep.now(), typed_ts=time.time())
    save(m, d)
    time.sleep(PASTE_ENTER_GAP)
    blocked = enter_blocked(capture()) if alive() else "the agent process changed"
    if blocked:
        e.update(state="unknown", why="task %s was pasted, but %s before Enter, so Enter was NOT sent; the line may "
                                      "sit in the input box or in a prompt" % (rl["note"], blocked))
        save(m, d)
        return _unknown(m, w, e["why"])
    p3 = tmux.run("send-keys", "-t", e["pane_id"], "Enter")
    if p3.returncode != 0:
        e.update(state="unknown", why="task %s was pasted but Enter failed (%s)" % (rl["note"], p3.stderr.strip()))
        save(m, d)
        return _unknown(m, w, e["why"])
    e.update(state="sent", sent_at=gitprep.now(), sent_ts=time.time())
    save(m, d)
    end = time.time() + 2.0
    while True:
        st = state(m, w, rl, proc, evidence, d)
        if st["state"] == "delivered" or time.time() > end:
            record_delivered(m, w, rl, st)
            return st
        time.sleep(0.2)


def mark_delivered(m, w, rl, proc):
    """The operator's --mark-delivered. The caller holds the session mutex."""
    d = load(m)
    k = key(w.lane, rl["note"])
    e = d.setdefault("entries", {}).setdefault(k, {"lane": w.lane, "task": rl["note"], "attempt": 0})
    e.update(state="delivered", delivered_at=gitprep.now(), marked_by_operator=True,
             evidence="recorded by the operator (`pai resume --mark-delivered`) at %s" % gitprep.now(),
             pid=proc.get("pid"), pid_start=wtlock.proc_start(proc["pid"]) if proc.get("pid") else None)
    save(m, d)
