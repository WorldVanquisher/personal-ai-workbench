#!/usr/bin/env python3
"""Stand-in for `claude` in the launcher tests. It never talks to anything.

Appends one JSON line per start to $FAKE_RC_DIR/starts.jsonl (lane, pid, cwd, argv, the
branch and HEAD it found, the PAI_TASK_* variables, SSH_AUTH_SOCK, start time). With
--remote-control it writes a Claude-style session record to
$CLAUDE_CONFIG_DIR/sessions/<pid>.json; then it shows an idle input box ("? for shortcuts")
and reads lines from its terminal, logging each to $FAKE_RC_DIR/received.jsonl.

$FAKE_RC_DIR/behavior.json may list lanes under "exit" (exit code, at once), "no_bridge"
(running, but no Remote Control bridge session), "notice" (prints a disconnect notice) and
"trust" (a first-use folder-trust prompt, modelled on Claude Code 2.1.323's, until the
folder is recorded as trusted in this fake's own $CLAUDE_CONFIG_DIR/fake-trust.json; the
answer is a line: "yes" trusts, anything else exits like "No, exit").
`--help`, `auth status` and `remote-control --help` answer like Claude Code does.
"""
import json
import os
import signal
import subprocess
import sys
import time

args = sys.argv[1:]
if args[:1] == ["--help"]:
    print("Usage: claude [options] [command] [prompt]\n  --model <model>\n  -n, --name <name>")
    if not os.environ.get("FAKE_CLAUDE_NO_RC"):
        print("  --remote-control [name]   Start an interactive session with Remote Control")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    if os.environ.get("FAKE_CLAUDE_LOGGED_OUT"):
        print(json.dumps({"loggedIn": False}))
        sys.exit(1)
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    sys.exit(0)
if args[:2] == ["remote-control", "--help"]:
    sys.exit(0)

lane = os.environ.get("PAI_RC_LANE", "?")
ctl = os.environ["FAKE_RC_DIR"]
try:
    behavior = json.load(open(os.path.join(ctl, "behavior.json")))
except (OSError, ValueError):
    behavior = {}


def git(*a):
    p = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    return p.stdout.strip()


pid = os.getpid()
with open(os.path.join(ctl, "starts.jsonl"), "a") as f:
    f.write(json.dumps({"lane": lane, "agent": "claude", "pid": pid, "cwd": os.getcwd(), "argv": args,
                        "branch": git("symbolic-ref", "--short", "HEAD"), "head": git("rev-parse", "HEAD"),
                        "ssh_auth_sock": os.environ.get("SSH_AUTH_SOCK"), "time": time.time(),
                        "env": {k: v for k, v in os.environ.items() if k.startswith(("PAI_TASK_", "PAI_RC_"))}}) + "\n")
if lane in behavior.get("exit", {}):
    print("fake claude: exiting at once")
    sys.exit(int(behavior["exit"][lane]))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))

cfg = os.environ["CLAUDE_CONFIG_DIR"]
trust_file = os.path.join(cfg, "fake-trust.json")
try:
    trusted = json.load(open(trust_file))
except (OSError, ValueError):
    trusted = []
if lane in behavior.get("trust", []) and os.getcwd() not in trusted:
    print("Quick safety check: Is this a project you created or one you trust? (Like your own code, a well-known "
          "open source project, or work from your team). If not, take a moment to review what's in this folder first.")
    print("Claude Code'll be able to read, edit, and execute files here.\n")
    print(" > No, exit\n   Yes, I trust this folder\n\n Enter to confirm . Esc to cancel")
    sys.stdout.flush()
    answer = sys.stdin.readline().strip().lower()
    if answer != "yes":
        sys.exit(1)
    trusted.append(os.getcwd())
    json.dump(trusted, open(trust_file, "w"))
    sys.stdout.write("\x1b[H\x1b[2J")  # the dialog is gone from the screen, as in the real UI

if "--remote-control" in args:
    sessions = os.path.join(cfg, "sessions")
    os.makedirs(sessions, exist_ok=True)
    stat = open("/proc/%d/stat" % pid).read()
    record = {"pid": pid, "cwd": os.getcwd(), "kind": "interactive", "status": "idle",
              "procStart": stat[stat.rindex(")") + 2:].split()[19], "updatedAt": int(time.time() * 1000),
              "bridgeSessionId": None if lane in behavior.get("no_bridge", []) else "fake-bridge-%d" % pid}
    with open(os.path.join(sessions, "%d.json" % pid), "w") as f:
        json.dump(record, f)
print("fake claude ready: %s" % " ".join(args))
if lane in behavior.get("notice", []):
    print("● Remote Control disconnected — this session was ended from another device (fake)")
print("? for shortcuts")
sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        time.sleep(0.2)
        continue
    with open(os.path.join(ctl, "received.jsonl"), "a") as f:
        f.write(json.dumps({"lane": lane, "agent": "claude", "pid": pid, "text": line.rstrip("\n"), "time": time.time()}) + "\n")
    print("received: %s" % line.strip())
    print("? for shortcuts")
    sys.stdout.flush()
