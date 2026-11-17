"""Task dispatch: give a lane's assigned task note to its agent once the agent's input box is
ready, exactly once per run, lane and task.

The message is one line: a tag, the task note's path and title, and the lane's branch. The
note itself stays where it is. It reaches the agent through tmux only: `load-buffer` from a
file this module writes (mode 0600), `paste-buffer -p` (a bracketed paste when the agent asks
for one) and one Enter. No shell is involved, so nothing in a note is interpreted.

Before any keystroke the lane is checked again: its process is the one recorded, it is not
showing a folder-trust prompt, and its idle input box is visible. A trust prompt is never
answered.

State per run, lane and task, in <home>/dispatch/<repo key>-<run>.json:
  sending    written before the first keystroke
  sent       tmux accepted the paste and the Enter
  delivered  the message's tag is visible in the lane's pane afterwards
  unknown    the process ended or changed while `sending` or `sent`, or Enter failed after the
             paste: whether the agent got the task is not known. Nothing resends it; the
             operator decides (`pai resume --lane L --redispatch`, or `--mark-delivered`).
"""
import hashlib
import json
import os
import re
import time

import gitprep
import wtlock

SCHEMA = "pai_dispatch_v1"


def path(m):
    cdir = gitprep.common_dir(m.repo) or m.repo
    return os.path.join(wtlock.home(), "dispatch", "%s-%s.json" % (hashlib.sha256(cdir.encode()).hexdigest()[:12], m.run_id))


def load(m):
    try:
        with open(path(m), encoding="utf-8") as f:
            d = json.load(f)
        return d if d.get("schema") == SCHEMA else {"schema": SCHEMA, "entries": {}}
    except (OSError, ValueError):
        return {"schema": SCHEMA, "entries": {}}


def save(m, d):
    p = path(m)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def key(lane, task):
    return "%s/%s" % (lane, task)


def tag(m, lane, task):
    return "[pai %s/%s/%s]" % (m.run_id, lane, task)


def _clean(text, limit=160):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text or "")
    return " ".join(text.split())[:limit]


def message(m, w, rec, rl, note):
    note_path = os.path.join(m.tasks_dir, rl["note"] + ".md") if m.tasks_dir else rl["note"]
    title = _clean(note.title if note else rl["note"])
    return ("%s Your task is the note %s (\"%s\"). Read the whole note and follow this repository's own "
            "instructions first. Work only in this worktree, on branch %s. Do not push or open a pull request: "
            "the operator reviews and publishes." % (tag(m, w.lane, rl["note"]), note_path, title, rl["branch"]))


def entry(m, w, rl, d=None):
    if not rl.get("note"):
        return None
    return ((d or load(m)).get("entries") or {}).get(key(w.lane, rl["note"]))


def same_process(e, proc):
    return proc.get("state") == "running" and e.get("pid") == proc.get("pid") and (
        e.get("pid_start") is None or str(e["pid_start"]) == str(wtlock.proc_start(proc["pid"])))


def state(m, w, rl, proc, history, d=None):
    """{'state': none|pending|sending|sent|delivered|unknown, 'detail'} for the lane's current
    task. Read-only."""
    if not rl.get("note"):
        return {"state": "none", "detail": "no task note is assigned to this lane"}
    e = entry(m, w, rl, d)
    if not e:
        return {"state": "pending", "detail": "task %s not sent yet%s" % (
            rl["note"], "" if m.dispatch else " (dispatch is off for this run; give it to the agent yourself)")}
    t = tag(m, w.lane, rl["note"])
    if e["state"] == "delivered":
        return {"state": "delivered", "detail": "task %s delivered %s (tag seen in the pane)" % (rl["note"], e.get("delivered_at"))}
    if e["state"] == "unknown":
        return {"state": "unknown", "detail": e.get("why") or "dispatch unknown"}
    if not same_process(e, proc):
        return {"state": "unknown", "detail": "the agent process (pid %s) ended or changed while task %s was %s; it is "
                "not known whether it arrived. Nothing resends it: check the pane (`pai logs %s`), then "
                "`pai resume --lane %s --redispatch` or `--mark-delivered`" % (e.get("pid"), rl["note"], e["state"],
                                                                             w.lane, w.lane)}
    if t in history:
        return {"state": "delivered", "detail": "task %s: tag visible in the pane" % rl["note"]}
    if e["state"] == "sending":
        return {"state": "unknown", "detail": "a dispatch of %s stopped before tmux confirmed it; the pane shows no tag. "
                "Check the pane, then --redispatch or --mark-delivered" % rl["note"]}
    return {"state": "sent", "detail": "task %s sent %s; its tag is not visible in the pane yet" % (rl["note"], e.get("sent_at"))}


def deliver(m, tmux, w, rec, rl, proc, note, screen_fn, history_fn, wait=5.0, force=False):
    """Send the lane's task if (and only if) the agent is ready and it was never sent. Returns
    the resulting state dict. `force` is the operator's --redispatch for a dispatch whose
    outcome is unknown."""
    d = load(m)
    entries = d.setdefault("entries", {})
    k = key(w.lane, rl["note"])
    cur = state(m, w, rl, proc, history_fn(), d)
    if cur["state"] in ("delivered", "sent") or (cur["state"] in ("unknown", "sending") and not force):
        if cur["state"] == "delivered" and entries.get(k, {}).get("state") != "delivered":
            entries[k].update(state="delivered", delivered_at=gitprep.now())
            save(m, d)
        return cur
    screen = screen_fn()
    if proc.get("state") != "running" or w.adapter.waiting_for_trust(screen) or not w.adapter.ready(screen):
        return cur
    attempt = (entries.get(k) or {}).get("attempt", 0) + 1
    e = {"state": "sending", "lane": w.lane, "task": rl["note"], "task_id": rec["task_id"], "pid": proc["pid"],
         "pid_start": wtlock.proc_start(proc["pid"]), "pane_id": proc["window"]["pane_id"],
         "window_id": proc["window"]["window_id"], "attempt": attempt, "started_at": gitprep.now()}
    entries[k] = e
    save(m, d)
    text = message(m, w, rec, rl, note)
    buf = "pai-%s-%s" % (m.run_id, w.lane)
    fpath = os.path.join(wtlock.home(), "dispatch", "%s.%d.msg" % (buf, os.getpid()))
    fd = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        p1 = tmux.run("load-buffer", "-b", buf, fpath)
        p2 = tmux.run("paste-buffer", "-d", "-p", "-b", buf, "-t", e["pane_id"]) if p1.returncode == 0 else p1
    finally:
        os.remove(fpath)
    if p2.returncode != 0:
        del entries[k]  # nothing reached the pane
        save(m, d)
        return {"state": "pending", "detail": "tmux could not paste the task: %s" % (p2.stderr.strip() or "error")}
    p3 = tmux.run("send-keys", "-t", e["pane_id"], "Enter")
    if p3.returncode != 0:
        e.update(state="unknown", why="the task text was pasted but Enter failed (%s); check the pane" % p3.stderr.strip())
        save(m, d)
        return {"state": "unknown", "detail": e["why"]}
    e.update(state="sent", sent_at=gitprep.now())
    save(m, d)
    end = time.time() + wait
    t = tag(m, w.lane, rl["note"])
    while True:
        if t in history_fn():
            e.update(state="delivered", delivered_at=gitprep.now())
            save(m, d)
            return {"state": "delivered", "detail": "task %s delivered (tag visible in the pane)" % rl["note"]}
        if time.time() > end:
            return {"state": "sent", "detail": "task %s sent; its tag is not visible in the pane yet" % rl["note"]}
        time.sleep(0.2)


def mark_delivered(m, w, rl, proc):
    d = load(m)
    k = key(w.lane, rl["note"])
    e = d.setdefault("entries", {}).setdefault(k, {"lane": w.lane, "task": rl["note"], "attempt": 0})
    e.update(state="delivered", delivered_at=gitprep.now(), marked_by_operator=True,
             pid=proc.get("pid"), pid_start=wtlock.proc_start(proc["pid"]) if proc.get("pid") else None)
    save(m, d)
