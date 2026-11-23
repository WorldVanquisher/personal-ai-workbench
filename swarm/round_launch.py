"""pai round launch / next: start a lane's Claude Code session in tmux, and hand it text.

launch LANE [--write] [--paste-prompt]
  Opens the lane's tmux window (creating the round's session when needed) in the lane's
  worktree, running a login shell that starts
      claude --remote-control --name "<PROJECT> <ROUND> <LANE>"
  and keeps the login shell when Claude exits (so the window stays and `status` says exited).
  A window whose Claude is still running is never touched. A window left with only its shell
  is restarted in place (`respawn-pane`) only with --write.
  Folder trust: when the dialog shows, launch reports it and stops. It never answers it.

  --paste-prompt (off by default) pastes the lane's rendered prompt once Claude's input box is
  idle, as one bracketed paste (swarm/panes.py), then presses Enter after checking again. The
  steps are recorded in <work_dir>/round-dispatch.json, as in dispatch.py:
      sending -> typed -> sent, or unknown
  Any record for the same text is final: sent, typed, sending or unknown is never pasted again
  (an unknown one needs a person to look at the lane). Nothing is pasted while the trust dialog
  is on screen, or while Claude is working; the keys could answer the dialog or land mid-task.

next LANE [--send [--enter]]
  A short resume message built from the lane's newest RESTART.md: where it is, its sha, the
  next ID it names. Printed; --send pastes it with the same checks and records (Enter only with
  --enter).
"""
import hashlib
import json
import os
import re
import shlex
import time

import panes
import round as rnd
import round_status as rs

READY_POLL = 0.2
ENTER_GAP = 0.5
ID_RE = re.compile(r"\b(R\d+[A-Za-z0-9]*-[A-Z]{2,5}-\d{3})\b")


def claude_program():
    return os.environ.get("PAI_ROUND_CLAUDE") or "claude"


def login_shell():
    return os.environ.get("PAI_ROUND_SHELL") or os.environ.get("SHELL") or "/bin/bash"


def window_command(ln):
    sh = login_shell()
    inner = "%s --remote-control --name %s; exec %s -l" % (
        shlex.quote(claude_program()), shlex.quote(ln["session_name"]), shlex.quote(sh))
    return "%s -lc %s" % (shlex.quote(sh), shlex.quote(inner))


def tmux_for():
    import rc
    return rc.Tmux(os.environ.get("PAI_ROUND_TMUX_SOCKET") or None)


def lane_state(tmux, ln):
    procs = rs.processes()
    row = rs.lane_status(ln, tmux, procs)
    w = rs.window(tmux, ln["tmux_target"])
    return row, w


# -- records ---------------------------------------------------------------------------------
def record_path(ln):
    return os.path.join(ln["work_dir"], "round-dispatch.json")


def load_records(ln):
    try:
        with open(record_path(ln), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("entries"), dict) else {"entries": {}}
    except (OSError, ValueError):
        return {"entries": {}}


def save_record(ln, key, **entry):
    d = load_records(ln)
    if entry.get("state") is None:
        d["entries"].pop(key, None)
    else:
        d["entries"][key] = dict(d["entries"].get(key) or {}, **entry, at=rnd.now())
    rnd.write_atomic(record_path(ln), json.dumps(d, indent=1, sort_keys=True), 0o600)


def gate(row, queue_ok=False):
    """(ok, final, why): may text be pasted into this lane now? `final` means waiting will not help."""
    st = row["state"]
    if st == "no-window":
        return False, True, "the tmux window %s does not exist" % row.get("target")
    if st == "exited":
        return False, True, "no Claude runs in %s (%s)" % (row.get("window") or row.get("target"), row["why"])
    if st == "blocked-on-dialog":
        if row.get("dialog") == "trust":
            return False, True, ("the folder-trust dialog is on screen in %s. Answer it yourself; pai never does, and "
                                 "a paste would answer it" % (row.get("window") or row.get("target")))
        return False, True, "a Claude Code dialog (%s) is open in %s: keys would answer it. Answer it yourself first" % (
            row.get("dialog"), row.get("window") or row.get("target"))
    if st == "owner-draft":
        return False, True, ("the input box holds unsent text (%d characters), probably yours. pai never overwrites it "
                             "and never appends to it: send or clear it yourself" % row.get("draft_chars", 0))
    if not row.get("input"):
        return False, False, "Claude's input box is not on screen"
    if st in ("running", "queued"):
        return (True, False, "busy; Claude queues the message") if queue_ok else (
            False, False, "Claude is working (%s); add --queue-ok to let Claude queue it" % st)
    return True, False, "idle, input box empty"


def record_event(r, lane_name, event):
    """Append one event to the lane in ROUND.json (locked read-modify-write; last 200 kept)."""
    import fcntl
    path = r.get("_path")
    if not path:
        return
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        cur = rnd.load_round(path)
        for ln in cur["lanes"]:
            if ln["name"] == lane_name:
                ln["events"] = (ln.get("events") or [])[-199:] + [dict(event, at=rnd.now())]
        rnd.write_atomic(path, rnd.dump_round(cur))


BRACKETING = ("claude", "node", "codex")  # programs that turn bracketed paste on (Claude Code runs as claude or node)


def pane_command(tmux, pane_id):
    p = tmux.run("display-message", "-p", "-t", pane_id, "#{pane_current_command}")
    return p.stdout.strip() if p.returncode == 0 else ""


def bracketing(tmux, pane_id, row, allow_unbracketed=False):
    """How a multi-line paste is let through: (allow_unbracketed for panes.paste, how) or (False, why).
    tmux 3.8 reports #{bracket_paste_flag}; it is then trusted as is. Older tmux (3.6 on Ubuntu and
    WSL) cannot tell, so it is inferred from the program: #{pane_current_command} is claude, node or
    codex, or the round status found the lane's Claude process in the pane's process tree (a lane
    started by pai round launch runs claude under a login shell, so the pane command reads bash).
    Any other program is refused unless --allow-unbracketed."""
    meta = panes.info(tmux, pane_id)
    if meta is None:
        return False, "the pane is gone"
    if meta["bracketed"] is True:
        return allow_unbracketed, "reported by tmux"
    if meta["bracketed"] is False:
        return (True, "--allow-unbracketed (tmux reports bracketed paste off)") if allow_unbracketed else (
            False, "tmux reports that the program in the pane did not ask for bracketed paste")
    cmd = pane_command(tmux, pane_id)
    if cmd in BRACKETING:
        return True, "inferred from the command name (pane_current_command=%s; this tmux cannot report it)" % cmd
    if row.get("agent_pid"):
        return True, "inferred from the command name (Claude process pid %s in the pane; pane_current_command=%s; " \
                     "this tmux cannot report it)" % (row["agent_pid"], cmd or "?")
    if allow_unbracketed:
        return True, "--allow-unbracketed (pane_current_command=%s; this tmux cannot report it)" % (cmd or "?")
    return False, ("this tmux cannot tell whether %s in the pane asked for bracketed paste (tmux 3.8 can) and it is "
                   "not claude, node or codex; add --allow-unbracketed to paste anyway" % (cmd or "the program"))


def deliver(tmux, ln, text, kind, enter=True, wait=60.0, log=print, queue_ok=False, dry_run=False, r=None, again=False,
            allow_unbracketed=False):
    """Paste `text` once into the lane's Claude, then (enter=True) press Enter after checking again.
    Returns 0 sent (or a clean dry run), 1 refused / needs a person."""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = "%s:%s" % (kind, sha[:16])
    prev = load_records(ln)["entries"].get(key)
    if prev and not again:
        log("  NOT SENT: this %s was already %s at %s%s" % (kind, prev["state"], prev.get("at"),
            "; look at the lane (pai round status) and settle it by hand: an unknown paste is never repeated"
            if prev["state"] in ("unknown", "sending", "typed") else "; --again sends it once more"))
        return 1
    deadline = time.time() + max(0.0, wait)
    while True:
        row, w = lane_state(tmux, ln)
        ok, final, why = gate(row, queue_ok)
        if ok or final or time.time() >= deadline:
            break
        time.sleep(READY_POLL)
    if not ok:
        log("  NOT SENT: %s; nothing was pasted" % why)
        return 1
    unbr, how = bracketing(tmux, w["pane_id"], row, allow_unbracketed)
    if not unbr and how != "reported by tmux":
        log("  NOT SENT: %s; nothing was pasted" % how)
        return 1
    if dry_run:
        log("  dry run: would paste %d characters (sha256 %s) into %s (%s, Claude pid %s; bracketed paste %s)%s; nothing was sent" % (
            len(text), sha[:12], row.get("window"), why, row["agent_pid"], how, " then press Enter" if enter else ""))
        return 0
    pid = row["agent_pid"]
    save_record(ln, key, state="sending", pid=pid, kind=kind)
    try:
        res = panes.paste(tmux, w["pane_id"], text, trust=lambda scr: rs.dialog(scr) is not None, enter=False,
                          allow_unbracketed=unbr)
    except panes.Refused as e:
        save_record(ln, key, state=None)  # Refused: nothing reached the pane
        log("  NOT SENT: %s" % e)
        return 1
    except Exception as e:  # noqa: BLE001  the paste may or may not have arrived
        save_record(ln, key, state="unknown", pid=pid, why="%s: %s" % (type(e).__name__, e))
        if r:
            record_event(r, ln["name"], {"kind": kind, "sha256": sha, "chars": len(text), "state": "unknown"})
        log("  UNKNOWN: the paste failed half-way (%s); it will not be repeated. Look at the lane." % e)
        return 1
    save_record(ln, key, state="typed", pid=pid, chars=res["pasted"], bracketed=res["bracketed"], bracketed_by=how)
    if not enter:
        if r:
            record_event(r, ln["name"], {"kind": kind, "sha256": sha, "chars": len(text), "state": "typed", "bracketed_by": how})
        log("  typed (%d characters, no Enter): press Enter in the lane yourself" % res["pasted"])
        return 0
    time.sleep(ENTER_GAP)
    row2, w2 = lane_state(tmux, ln)
    if row2.get("dialog") or row2["agent_pid"] != pid or not w2 or w2["pane_id"] != w["pane_id"]:
        save_record(ln, key, state="unknown", pid=pid, why="before Enter: state %s, pid %s" % (row2["state"], row2["agent_pid"]))
        if r:
            record_event(r, ln["name"], {"kind": kind, "sha256": sha, "chars": len(text), "state": "unknown"})
        log("  UNKNOWN: the lane changed after the paste (%s); Enter was NOT pressed and nothing will be resent"
            % row2["state"])
        return 1
    p = tmux.run("send-keys", "-t", w["pane_id"], "Enter")
    if p.returncode != 0:
        save_record(ln, key, state="unknown", pid=pid, why="send-keys Enter failed")
        log("  UNKNOWN: Enter could not be sent; nothing will be resent")
        return 1
    save_record(ln, key, state="sent", pid=pid)
    if r:
        record_event(r, ln["name"], {"kind": kind, "sha256": sha, "chars": len(text), "state": "sent", "pid": pid,
                                     "queued": row["state"] in ("running", "queued"), "bracketed_by": how})
    log("  sent (%d characters, sha256 %s, bracketed paste %s, then Enter%s)" % (
        res["pasted"], sha[:12], how, "; Claude queues it" if row["state"] in ("running", "queued") else ""))
    return 0


# -- launch ----------------------------------------------------------------------------------
def launch(r, ln, write, paste_prompt, wait, log=print, tmux=None, allow_unbracketed=False):
    tmux = tmux or tmux_for()
    if not tmux.bin:
        raise rnd.RoundError("tmux is not installed")
    if not os.path.isdir(ln["worktree"]):
        raise rnd.RoundError("the worktree %s is missing; pai round new %s %s --write creates it" % (
            ln["worktree"], r["project"], r["round"]))
    sess, _, win = ln["tmux_target"].partition(":")
    row, w = lane_state(tmux, ln)
    cmd = window_command(ln)
    log("pai round launch %s%s" % (ln["name"], "" if write else "  (preview: add --write)"))
    log("  window   %s  in %s" % (ln["tmux_target"], ln["worktree"]))
    log("  command  %s" % cmd)
    if w and row["agent_pid"]:
        log("  already running: Claude pid %s (%s, state %s); nothing was started" % (row["agent_pid"], row["agent_name"], row["state"]))
        if row["state"] == "blocked-on-dialog":
            log("  %s: a dialog is waiting in %s; answer it yourself" % ("TRUST" if row.get("dialog") == "trust" else "DIALOG", row["window"]))
            return 1
        if paste_prompt and write:
            return paste_lane_prompt(tmux, ln, wait, log, allow_unbracketed)
        return 0
    action = "respawn the shell-only window" if w else ("new window" if tmux.has_session(sess) else "new session")
    log("  action   %s" % action)
    if not write:
        if paste_prompt:
            log("  then     paste %s once Claude is idle (bracketed, recorded, never twice)" % (ln.get("prompt_file") or "(no prompt file)"))
        return 0
    if w:
        p = tmux.run("respawn-pane", "-k", "-t", w["pane_id"], "-c", ln["worktree"], cmd)
    elif tmux.has_session(sess):
        p = tmux.run("new-window", "-d", "-t", "=" + sess, "-n", win, "-c", ln["worktree"], cmd)
    else:
        p = tmux.run("new-session", "-d", "-s", sess, "-n", win, "-c", ln["worktree"], cmd)
    if p.returncode != 0:
        raise rnd.RoundError("tmux could not start the window: %s" % p.stderr.strip())
    log("  started  %s" % ln["tmux_target"])
    deadline = time.time() + min(max(wait, 0.0), 15.0)
    while time.time() < deadline:
        row, _ = lane_state(tmux, ln)
        if row["state"] in ("blocked-on-dialog", "idle", "running", "owner-draft", "queued"):
            break
        time.sleep(READY_POLL)
    log("  state    %s (%s)" % (row["state"], row["why"]))
    if row["state"] == "blocked-on-dialog":
        log("  TRUST: answer the folder-trust dialog yourself in %s (tmux attach -t %s); pai never answers it. "
            "Then run pai round launch %s --paste-prompt --write if you want the prompt pasted."
            % (row["window"], sess, ln["name"]))
        return 1
    if paste_prompt:
        return paste_lane_prompt(tmux, ln, wait, log, allow_unbracketed)
    return 0


def paste_lane_prompt(tmux, ln, wait, log, allow_unbracketed=False):
    if not ln.get("prompt_file") or not os.path.isfile(ln["prompt_file"]):
        log("  NOT SENT: the lane has no rendered prompt file")
        return 1
    with open(ln["prompt_file"], encoding="utf-8") as f:
        text = f.read()
    if ln.get("prompt_sha256") and hashlib.sha256(text.encode("utf-8")).hexdigest() != ln["prompt_sha256"]:
        log("  NOT SENT: %s changed since pai round new rendered it (sha256 differs from ROUND.json)" % ln["prompt_file"])
        return 1
    return deliver(tmux, ln, text, "prompt", enter=True, wait=wait, log=log, allow_unbracketed=allow_unbracketed)


# -- next ------------------------------------------------------------------------------------
def latest_restart(ln):
    cp = rs.latest_checkpoint(ln.get("handoff_dir") or "")
    if cp and cp["restart"]:
        return cp["restart"], cp["name"]
    for p in (os.path.join(ln.get("work_dir") or "", "RESTART.md"),):
        if os.path.isfile(p):
            return p, None
    return None, None


def next_message(r, ln):
    path, cp = latest_restart(ln)
    if not path:
        raise rnd.RoundError("lane %s has no RESTART.md (looked in the newest cp-* of %s and in %s)" % (
            ln["name"], ln.get("handoff_dir"), ln.get("work_dir")))
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8", "replace")
    m = re.search(r"(?:下一个|next)[^\n]{0,40}?" + ID_RE.pattern, text, re.I) or ID_RE.search(text)
    nid = m.group(1) if m else None
    queue = os.path.join(ln.get("work_dir") or "", "queue")
    msg = "继续 %s %s 线%s：先完整读 %s（sha256 %s）%s，按里面的续跑开场白接着做%s。规则仍以本线最初的 prompt 为准。" % (
        r["round"], ln["name"], "（%s 之后）" % cp if cp else "", path, hashlib.sha256(data).hexdigest()[:12],
        "，再跑 pai queue status %s" % queue if os.path.isfile(queue) else "",
        "，下一个是 %s" % nid if nid else "")
    return msg, nid, path


def inject(r, ln, text, queue_ok=False, dry_run=False, again=False, enter=True, wait=10.0, log=print, tmux=None, kind="inject",
           allow_unbracketed=False):
    """pai round inject: one message into a lane, only when it is safe (see gate())."""
    if not text.strip():
        raise rnd.RoundError("nothing to inject: the text is empty")
    log("pai round inject %s: %d characters, sha256 %s%s" % (ln["name"], len(text), hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
                                                          " (dry run)" if dry_run else ""))
    return deliver(tmux or tmux_for(), ln, text, kind, enter=enter, wait=wait, log=log, queue_ok=queue_ok,
                   dry_run=dry_run, r=r, again=again, allow_unbracketed=allow_unbracketed)


def main(a, r, cfg, out):
    log = lambda s: print(s, file=out, flush=True)  # noqa: E731
    ln = rnd.lane(r, a.lane)
    if a.cmd == "launch":
        return launch(r, ln, a.write, a.paste_prompt, a.wait, log, allow_unbracketed=a.allow_unbracketed)
    if a.cmd == "inject":
        try:
            with open(a.file, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            raise rnd.RoundError("cannot read %s: %s" % (a.file, e))
        return inject(r, ln, text, a.queue_ok, a.dry_run, a.again, not a.no_enter, a.wait, log,
                      allow_unbracketed=a.allow_unbracketed)
    msg, nid, path = next_message(r, ln)
    print(msg, file=out)
    if not a.send:
        return 0
    return inject(r, ln, msg, a.queue_ok, False, False, not a.no_enter, 10.0, log, kind="next",
                  allow_unbracketed=a.allow_unbracketed)
