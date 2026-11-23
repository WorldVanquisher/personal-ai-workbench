"""pai round status: one line per lane, from tmux, /proc and files only (no model is asked).

For each lane of ROUND.json:
  session   the Claude Code session name the lane was started with (from the process's own
            command line, `--name ...`), checked against ROUND.json's session_name
  window    the tmux window (session:window), and whether it exists
  state     running | idle | blocked-on-dialog | owner-draft | queued | exited | no-window
              no-window     the tmux window is not there
              exited        the pane is dead, or no Claude process runs under the pane
              blocked-on-dialog  a Claude Code dialog holds the screen: the folder-trust dialog
                            (see TRUST below), "Teach auto mode about your environment?", a
                            permission prompt, or another "Enter to confirm" dialog; pai never answers it
              owner-draft   typed, unsent text in the input box (dim placeholder or suggestion
                            text does not count); pai never overwrites or appends to it
              queued        Claude shows "Press up to edit queued messages"
              running       Claude's footer says "esc to interrupt" (it is working)
              idle          Claude runs and its footer does not say so
  worktree  clean | N changed | missing
  checkpoint  the newest cp-* folder in the lane's handoff folder, and whether RESTART.md is there
  queue     the Totals of the lane's work queue (<work_dir>/queue), when there is one

The trust dialog is recognised by its own wording (TRUST_MARKERS, every Claude Code version seen
so far) together with its "No, exit" option, and only while Claude's normal input footer is NOT
on screen: a prompt or transcript that merely quotes "Yes, I trust this folder" (a worker's own
instructions do) is not a trust dialog.
"""
import json
import os
import re
import time

import gitprep

# Wording of Claude Code's first-use folder-trust dialog, lower case. Sources: swarm/agents.py
# (owner-observed on 2.1.323) and the dialog text quoted by the R6 owner ("Quick safety check",
# "Yes, I trust this folder"). Older builds asked "Do you trust the files in this folder?" with
# "Yes, proceed".
TRUST_MARKERS = ("quick safety check", "is this a project you created or one you trust",
                 "yes, i trust this folder", "do you trust the files in this folder", "yes, proceed")
TRUST_OPTIONS = ("no, exit",)
# Claude's normal input footer: while any of these is on the last lines, no dialog is up.
MAIN_UI = ("esc to interrupt", "? for shortcuts", "shift+tab to cycle", "auto mode on", "accept edits on",
           "bypass permissions on", "plan mode on")
RUNNING = ("esc to interrupt",)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-_]")
CHECKPOINT_EVERY = 10  # the worker prompts seal a checkpoint about every 10 items
STATES = ("running", "idle", "blocked-on-dialog", "owner-draft", "queued", "exited", "no-window")


def flat(text):
    return " ".join(ANSI_RE.sub("", text or "").lower().split())


def lines_of(screen):
    return [l for l in ANSI_RE.sub("", screen or "").splitlines() if l.strip()]


def footer(screen, n=4):
    return flat("\n".join(lines_of(screen)[-n:]))


def trust_prompt(screen):
    """True when the visible screen shows Claude Code's folder-trust dialog."""
    tail = flat("\n".join(lines_of(screen)[-30:]))
    if not any(m in tail for m in TRUST_MARKERS) or not any(o in tail for o in TRUST_OPTIONS):
        return False
    return not any(m in footer(screen) for m in MAIN_UI)


DIALOG_MARKERS = (("teach auto mode about your environment", "teach-auto-mode"),
                  ("do you want to proceed", "permission"), ("do you want to make this edit", "permission"),
                  ("do you want to create", "permission"))
DIALOG_FOOTERS = ("enter to confirm", "esc to cancel", "esc to exit")
QUEUED = ("press up to edit queued messages",)
BORDER_RE = re.compile(r"^[\s\u2500-\u257f]*\u2500{20,}")
DIM_RE = re.compile(r"\x1b\[(?:[0-9;]*;)?2m")


def input_box(screen):
    """Claude's input box at the bottom of the screen: {"text", "dim", "placeholder"} or None
    when there is none (a dialog replaced it). `screen` may carry colours (capture-pane -e):
    dim text in the box is Claude's own placeholder or prompt suggestion, not something typed."""
    raw = (screen or "").split("\n")
    rows = [l for l in raw if l.strip()]
    tail = rows[-10:]
    for i in range(len(tail) - 1, -1, -1):
        plain = ANSI_RE.sub("", tail[i])
        if plain.lstrip().startswith("\u276f") and i > 0 and BORDER_RE.match(ANSI_RE.sub("", tail[i - 1])):
            parts = [tail[i]]
            for nxt in tail[i + 1:]:
                if BORDER_RE.match(ANSI_RE.sub("", nxt)):
                    break
                parts.append(nxt)
            first = ANSI_RE.sub("", parts[0]).lstrip()[1:]
            text = "\n".join([first] + [ANSI_RE.sub("", p) for p in parts[1:]]).replace("\u00a0", " ").strip()
            after = tail[i].split("\u276f", 1)[1] if "\u276f" in tail[i] else ""
            dim = bool(text) and bool(DIM_RE.search(after.split(text.split()[0])[0] if text.split() else after))
            placeholder = text.startswith('Try "')
            return {"text": text, "dim": dim, "placeholder": placeholder}
    return None


def draft(screen):
    """The owner's unsent text in the input box, or None (empty, placeholder, suggestion, no box)."""
    box = input_box(screen)
    if not box or not box["text"] or box["dim"] or box["placeholder"]:
        return None
    return box["text"]


def dialog(screen):
    """The kind of Claude Code dialog that holds the screen (trust, teach-auto-mode, permission,
    other), or None. A dialog replaces the input box, so text quoted in a transcript above an
    input box is never a dialog."""
    if trust_prompt(screen):
        return "trust"
    if input_box(screen) is not None:
        return None
    tail = flat("\n".join(lines_of(screen)[-25:]))
    for marker, kind in DIALOG_MARKERS:
        if marker in tail:
            return kind
    if any(m in footer(screen, 6) for m in DIALOG_FOOTERS) and not any(m in footer(screen) for m in MAIN_UI):
        return "other"
    return None


def classify(screen, pane_dead=False, agent=True):
    """(state, why) from the screen text (with or without colours) and process evidence.
    Order: exited, blocked-on-dialog, owner-draft, queued, running, idle."""
    if pane_dead:
        return "exited", "the pane's process has exited"
    if not agent:
        return "exited", "no Claude process under the pane (only its shell)"
    kind = dialog(screen)
    if kind:
        return "blocked-on-dialog", ("the folder-trust dialog is on screen; answer it yourself (pai never does)"
                                     if kind == "trust" else "a Claude Code dialog (%s) waits for an answer" % kind)
    text = draft(screen)
    if text:
        return "owner-draft", "unsent text in the input box (%d characters); pai never overwrites or appends to it" % len(text)
    if any(m in flat("\n".join(lines_of(screen)[-12:])) for m in QUEUED):
        return "queued", "messages are queued for Claude (Press up to edit queued messages)"
    if any(m in footer(screen) for m in RUNNING):
        return "running", "the footer says esc to interrupt"
    return "idle", "Claude runs; its footer does not say it is working"


def busy(screen):
    return any(m in footer(screen) for m in RUNNING)


# -- /proc -----------------------------------------------------------------------------------
def processes(proc="/proc"):
    """{pid: {"ppid", "comm", "argv", "cwd"}} for every process this user can read."""
    out = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        base = os.path.join(proc, n)
        try:
            with open(os.path.join(base, "stat"), encoding="utf-8", errors="replace") as f:
                stat = f.read()
            with open(os.path.join(base, "cmdline"), "rb") as f:
                argv = [x.decode("utf-8", "replace") for x in f.read().split(b"\0") if x]
        except OSError:
            continue
        r = stat.rfind(")")
        comm = stat[stat.find("(") + 1:r]
        fields = stat[r + 2:].split()
        try:
            cwd = os.readlink(os.path.join(base, "cwd"))
        except OSError:
            cwd = None
        out[int(n)] = {"ppid": int(fields[1]) if len(fields) > 1 else 0, "comm": comm, "argv": argv, "cwd": cwd,
                       "start": int(fields[19]) if len(fields) > 19 and fields[19].isdigit() else None}
    return out


def descendants(procs, root):
    kids = {}
    for pid, p in procs.items():
        kids.setdefault(p["ppid"], []).append(pid)
    out, stack, seen = [], [root], set()
    while stack:
        pid = stack.pop()
        for k in kids.get(pid, []):
            if k not in seen:
                seen.add(k)
                out.append(k)
                stack.append(k)
    return sorted(out)


def is_claude(p):
    if p["comm"] == "claude":
        return True
    return any(os.path.basename(a) == "claude" for a in p["argv"][:2])


def session_name(argv):
    for i, x in enumerate(argv):
        if x in ("--name", "-n") and i + 1 < len(argv):
            return argv[i + 1]
        if x.startswith("--name="):
            return x[7:]
    return None


def busy_in(path, procs=None, proc="/proc", skip=()):
    """[(pid, short command)] of processes whose working folder is inside `path`."""
    procs = processes(proc) if procs is None else procs
    want = gitprep.canon(path)
    out = []
    for pid, p in sorted(procs.items()):
        if pid in skip or not p["cwd"]:
            continue
        c = p["cwd"].removesuffix(" (deleted)")
        if gitprep.under(c, want):
            out.append((pid, " ".join(p["argv"])[:40] or p["comm"]))
    return out


# -- tmux ------------------------------------------------------------------------------------
def claude_record(pid, claude_dir=None):
    """Claude Code's own session record for this process ($CLAUDE_CONFIG_DIR/sessions/<pid>.json,
    2.1.32x): status, version, name, Remote Control bridge. Only fields pai shows; the session
    and bridge ids themselves are not kept."""
    d = claude_dir or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        with open(os.path.join(d, "sessions", "%d.json" % pid), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(rec, dict) or rec.get("pid") != pid:
        return None
    return {"status": rec.get("status"), "waiting_for": rec.get("waitingFor"), "version": rec.get("version"),
            "name": rec.get("name"), "rc": bool(rec.get("bridgeSessionId")), "updated_ms": rec.get("updatedAt")}


def screen_flags(screen):
    """Extra facts from the visible screen: fresh (the start screen, no prompt given yet) and
    update (Claude says an update waits for a restart)."""
    t = flat(screen)
    return {"fresh": "claude code v" in t and 'try "' in t and "esc to interrupt" not in t,
            "update": "restart to update" in t}


def window(tmux, target):
    """The pane of `session:window` (exact names): {"pane_id", "pane_pid", "pane_dead", ...} or None."""
    sess, _, win = target.partition(":")
    if not tmux.bin:
        return None
    p = tmux.run("list-panes", "-s", "-t", "=" + sess, "-F",
                 "#{window_name}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{pane_pid}\t#{pane_dead}\t#{pane_current_path}\t#{window_activity}")
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        f = line.split("\t")
        if len(f) == 8 and f[0] == win and f[2] in ("0", "1"):
            return {"window": "%s:%s" % (sess, f[1]), "pane_id": f[3], "pane_pid": int(f[4]) if f[4].isdigit() else None,
                    "pane_dead": f[5] == "1", "path": f[6], "activity": int(f[7]) if f[7].isdigit() else None}
    return None


def capture(tmux, pane_id, colours=True):
    """The visible screen; with colours (-e) so dim placeholder text can be told from typed text."""
    p = tmux.run(*(["capture-pane", "-p", "-J"] + (["-e"] if colours else []) + ["-t", pane_id]))
    return p.stdout if p.returncode == 0 else ""


# -- files -----------------------------------------------------------------------------------
def latest_checkpoint(handoff_dir):
    """{"name", "path", "handoff", "restart", "sums"} of the newest cp-* folder, or None."""
    try:
        cps = [e for e in os.scandir(handoff_dir) if e.is_dir() and (e.name.startswith(("cp-", "final-")) or e.name == "final")]
    except OSError:
        return None
    if not cps:
        return None
    e = max(cps, key=lambda e: (e.stat().st_mtime, e.name))
    has = lambda n: os.path.isfile(os.path.join(e.path, n))  # noqa: E731
    restart = os.path.join(e.path, "RESTART.md") if has("RESTART.md") else None
    return {"name": e.name, "path": e.path, "handoff": has("HANDOFF.md"), "next_pr": has("NEXT-PR.md"),
            "restart": restart, "sums": has("SHA256SUMS")}


def queue_totals(work_dir):
    path = os.path.join(work_dir, "queue")
    if not os.path.isfile(path):
        return None
    try:
        from pai import workqueue
        doc = workqueue.load(path)
        evs, _ = workqueue.events(path)
        s = workqueue.summary(doc, workqueue.state(doc, evs), evs)
        last = max((i for i, e in enumerate(evs) if e.get("event") == "checkpoint"), default=-1)
        since = {e.get("id") for e in evs[last + 1:] if e.get("event") == "state" and e.get("state") in ("done", "prototype")}
        return dict(s["counts"], next=(s["next"] or {}).get("id"), since_checkpoint=len(since),
                    checkpointed=last >= 0, doing_ids=s["doing"], blocked_items=s["blocked"], owner_only_items=s["owner_only"])
    except Exception:  # noqa: BLE001  an unreadable queue is reported, not fatal
        return _queue_totals_fallback(path)


def _queue_totals_fallback(path):
    """Replay FILE.log directly (format pai-queue/1): last state event per id wins."""
    try:
        with open(path, encoding="utf-8") as f:
            q = json.load(f)
        ids = [it["id"] for it in q.get("items", [])]
    except (OSError, ValueError, KeyError, TypeError):
        return {"error": "unreadable queue"}
    last = {}
    try:
        with open(path + ".log", encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("event") == "state" and ev.get("id") in ids:
                    last[ev["id"]] = ev.get("state", "todo")
    except OSError:
        pass
    counts = {s: 0 for s in ("todo", "doing", "done", "prototype", "blocked", "owner-only")}
    for i in ids:
        counts[last.get(i, "todo")] = counts.get(last.get(i, "todo"), 0) + 1
    return counts


def totals_text(c):
    if not c:
        return "-"
    if "error" in c:
        return c["error"]
    return "done %d, prototype %d, blocked %d, owner-only %d, doing %d, todo %d" % tuple(
        c.get(k, 0) for k in ("done", "prototype", "blocked", "owner-only", "doing", "todo"))


# -- status ----------------------------------------------------------------------------------
def lane_status(ln, tmux, procs):
    row = {"lane": ln["name"], "target": ln.get("tmux_target"), "session_name": ln.get("session_name"),
           "window": None, "state": "no-window", "why": "no tmux window %s" % ln.get("tmux_target"),
           "agent_pid": None, "agent_name": None, "name_ok": None}
    w = window(tmux, ln["tmux_target"]) if ln.get("tmux_target") else None
    if w:
        row["window"] = w["window"]
        # the pane's own process too: `tmux new-window CMD` may exec CMD in place of the shell
        tree = [w["pane_pid"]] + descendants(procs, w["pane_pid"]) if w["pane_pid"] else []
        agents = [pid for pid in tree if pid in procs and is_claude(procs[pid])]
        if agents:
            row["agent_pid"] = agents[0]
            row["agent_name"] = session_name(procs[agents[0]]["argv"])
            row["name_ok"] = row["agent_name"] == ln.get("session_name") if row["agent_name"] else None
        screen = "" if w["pane_dead"] else capture(tmux, w["pane_id"])
        row["state"], row["why"] = classify(screen, w["pane_dead"], bool(agents))
        row["dialog"] = dialog(screen) if agents and not w["pane_dead"] else None
        row["busy"] = busy(screen) if agents else False
        row["draft_chars"] = len(draft(screen) or "") if agents else 0
        row["input"] = input_box(screen) is not None if agents else False
        row.update(screen_flags(screen) if agents else {"fresh": False, "update": False})
        if row["state"] != "idle":
            row["fresh"] = False
        elif row["fresh"]:
            row["why"] = "the start screen: Claude runs but has had no prompt yet"
        row["quiet_seconds"] = max(0, int(time.time()) - w["activity"]) if w.get("activity") else None
        row["record"] = claude_record(agents[0]) if agents else None
    wt = ln.get("worktree")
    if wt and os.path.isdir(wt):
        d = gitprep.dirty(wt)
        row["worktree"] = "clean" if not d else "%d changed" % len(d)
        row["changed"] = len(d)
    else:
        row["worktree"], row["changed"] = "missing", None
    cp = latest_checkpoint(ln.get("handoff_dir") or "")
    row["checkpoint"] = cp
    row["queue"] = queue_totals(ln.get("work_dir") or "")
    return row


def status(r, tmux=None, proc="/proc"):
    if tmux is None:
        import rc
        tmux = rc.Tmux(os.environ.get("PAI_ROUND_TMUX_SOCKET") or None)
    procs = processes(proc)
    return [lane_status(ln, tmux, procs) for ln in r["lanes"]]


def as_json(r, rows):
    return {"format": "pai-round-status", "version": 1, "round": r["round"], "project": r["project"],
            "base_sha": r["base_sha"], "lanes": rows}


def render(r, rows):
    out = ["round %s (%s)  base %s  %d lane(s)" % (r["round"], r["project"], r["base_sha"][:12], len(rows))]
    for x in rows:
        cp = x["checkpoint"]
        out.append("  %-10s %-13s %-16s %-11s cp %-8s queue %s" % (
            x["lane"], x["state"], x["window"] or x["target"] or "-", x["worktree"],
            (cp["name"] + ("+R" if cp["restart"] else "")) if cp else "-", totals_text(x["queue"])))
        flags = [f for f, on in (("fresh: no prompt yet", x.get("fresh")), ("update waiting", x.get("update")),
                                 ("RC on", (x.get("record") or {}).get("rc"))) if on]
        if x.get("record") and x["record"].get("status") and x["record"]["status"] != {"running": "busy"}.get(x["state"], x["state"]):
            flags.append("Claude's record: %s" % x["record"]["status"])
        q = x.get("queue") or {}
        if q.get("since_checkpoint", 0) >= CHECKPOINT_EVERY:
            flags.append("checkpoint overdue: %d item(s) finished since the %s" % (
                q["since_checkpoint"], "last checkpoint" if q.get("checkpointed") else "start (no checkpoint yet)"))
        if x.get("quiet_seconds") and x["quiet_seconds"] >= 1800:
            flags.append("no screen change for %d min" % (x["quiet_seconds"] // 60))
        if flags:
            out.append("             " + "; ".join(flags))
        if x["agent_name"] and x["name_ok"] is False:
            out.append("             session name %r, expected %r" % (x["agent_name"], x["session_name"]))
        if x["state"] == "blocked-on-dialog":
            out.append("             %s: answer it yourself in %s (pai never answers it)" % (
                "TRUST" if x.get("dialog") == "trust" else "DIALOG %s" % x.get("dialog"), x["window"] or x["target"]))
        if x["state"] == "owner-draft":
            out.append("             DRAFT: unsent text in the input box; send or clear it yourself")
    return "\n".join(out)
