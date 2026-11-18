#!/usr/bin/env python3
"""A pass-through `tmux` for dispatch tests (PAI_SWARM_TMUX points here). Every command runs on
the real tmux (FAKE_TMUX_REAL, or the first other `tmux` on PATH) unchanged, except where
$FAKE_RC_DIR/tmux.json asks for a fault on a lane's pane. It never answers a trust prompt.

Each load-buffer, paste-buffer and send-keys is logged to $FAKE_RC_DIR/tmux.log as one JSON
line: {"cmd", "lane", "keys", "time"}. Faults, keyed by lane (the pane's window name):

  drop_enter         [lane]          `send-keys ... Enter` is accepted (exit 0) but never sent:
                                     the agent shows the pasted text, nothing is submitted
  kill_parent_after  {lane: "paste"|"enter"}
                                     after that keystroke reached the pane, SIGKILL the calling
                                     pai process: interrupted before it records the result
  trust_after_paste  [lane]          after the paste, ask the fake agent to show its trust prompt
                                     now ($FAKE_RC_DIR/trust-now-<lane>) and wait until it has
  slow_paste         seconds         sleep before each paste (widens races for lock tests)
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.realpath(__file__)


def real_tmux():
    if os.environ.get("FAKE_TMUX_REAL"):
        return os.environ["FAKE_TMUX_REAL"]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, "tmux")
        if os.path.isfile(p) and os.access(p, os.X_OK) and os.path.realpath(p) != HERE:
            return p
    return shutil.which("tmux") or "tmux"


def split(argv):
    """(global options, command, command arguments)."""
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in ("-S", "-L", "-f") else 1
    return argv[:i], (argv[i] if i < len(argv) else ""), argv[i + 1:]


def target(args):
    return args[args.index("-t") + 1] if "-t" in args and args.index("-t") + 1 < len(args) else None


def main():
    argv = sys.argv[1:]
    tmux = real_tmux()
    glob, cmd, args = split(argv)
    ctl = os.environ.get("FAKE_RC_DIR")
    if not ctl or cmd not in ("load-buffer", "paste-buffer", "send-keys"):
        os.execv(tmux, [tmux] + argv)
    try:
        with open(os.path.join(ctl, "tmux.json")) as f:
            faults = json.load(f)
    except (OSError, ValueError):
        faults = {}
    lane = None
    pane = target(args)
    if pane:
        p = subprocess.run([tmux] + glob + ["display-message", "-p", "-t", pane, "#{window_name}"],
                           capture_output=True, text=True)
        lane = p.stdout.strip() or None
    keys = [a for a in args if a not in ("-t", pane)] if cmd == "send-keys" else []
    with open(os.path.join(ctl, "tmux.log"), "a") as f:
        f.write(json.dumps({"cmd": cmd, "lane": lane, "keys": keys, "time": time.time()}) + "\n")
    if cmd == "paste-buffer" and faults.get("slow_paste"):
        time.sleep(float(faults["slow_paste"]))
    if cmd == "send-keys" and keys == ["Enter"] and lane in faults.get("drop_enter", []):
        return 0
    p = subprocess.run([tmux] + argv)
    if p.returncode != 0:
        return p.returncode
    if cmd == "paste-buffer" and lane in faults.get("trust_after_paste", []):
        open(os.path.join(ctl, "trust-now-%s" % lane), "w").close()
        end = time.time() + 10
        while time.time() < end and not os.path.exists(os.path.join(ctl, "trust-shown-%s" % lane)):
            time.sleep(0.05)
        time.sleep(0.2)  # let the prompt reach the screen
    step = {"paste-buffer": "paste", "send-keys": "enter" if keys == ["Enter"] else None}.get(cmd)
    if step and (faults.get("kill_parent_after") or {}).get(lane) == step:
        os.kill(os.getppid(), signal.SIGKILL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
