#!/usr/bin/env python3
"""Stand-in for `claude --remote-control` in the launcher tests. It never talks to anything.

Appends one JSON line per start to $FAKE_RC_DIR/starts.jsonl (lane, pid, cwd, argv, the
branch and HEAD it found, the PAI_TASK_* variables), writes a Claude-style session record
to $CLAUDE_CONFIG_DIR/sessions/<pid>.json, then waits until it is killed.
$FAKE_RC_DIR/behavior.json may list lanes under "exit" (exit code, at once), "no_bridge"
(running, but no Remote Control bridge session) and "notice" (prints a disconnect notice).
"""
import json
import os
import signal
import subprocess
import sys
import time

lane = os.environ.get("PAI_RC_LANE", "?")
ctl = os.environ["FAKE_RC_DIR"]
try:
    behavior = json.load(open(os.path.join(ctl, "behavior.json")))
except (OSError, ValueError):
    behavior = {}


def git(*args):
    p = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    return p.stdout.strip()


pid = os.getpid()
with open(os.path.join(ctl, "starts.jsonl"), "a") as f:
    f.write(json.dumps({"lane": lane, "pid": pid, "cwd": os.getcwd(), "argv": sys.argv[1:],
                        "branch": git("symbolic-ref", "--short", "HEAD"), "head": git("rev-parse", "HEAD"),
                        "env": {k: v for k, v in os.environ.items() if k.startswith(("PAI_TASK_", "PAI_RC_"))}}) + "\n")
if lane in behavior.get("exit", {}):
    print("fake claude: exiting at once")
    sys.exit(int(behavior["exit"][lane]))

sessions = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "sessions")
os.makedirs(sessions, exist_ok=True)
stat = open("/proc/%d/stat" % pid).read()
record = {"pid": pid, "cwd": os.getcwd(), "kind": "interactive", "status": "idle",
          "procStart": stat[stat.rindex(")") + 2:].split()[19], "updatedAt": int(time.time() * 1000),
          "bridgeSessionId": None if lane in behavior.get("no_bridge", []) else "fake-bridge-%d" % pid}
with open(os.path.join(sessions, "%d.json" % pid), "w") as f:
    json.dump(record, f)
print("fake claude ready: %s" % " ".join(sys.argv[1:]))
if lane in behavior.get("notice", []):
    print("● Remote Control disconnected — this session was ended from another device (fake)")
sys.stdout.flush()
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
while True:
    time.sleep(0.2)
