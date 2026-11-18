#!/usr/bin/env python3
"""Stand-in for `claude` in the launcher tests. It never talks to anything.

Appends one JSON line per start to $FAKE_RC_DIR/starts.jsonl (lane, pid, cwd, argv, the
branch and HEAD it found, the PAI_TASK_* variables, SSH_AUTH_SOCK, start time). Once past any
trust prompt it writes a Claude-style session record to $CLAUDE_CONFIG_DIR/sessions/<pid>.json
(modelled on Claude Code 2.1.326's: pid, procStart, cwd, status idle|busy, statusUpdatedAt,
updatedAt; a Remote Control bridgeSessionId only with --remote-control), shows an idle input
box ("? for shortcuts") and reads lines from its terminal. A line is a submitted prompt: it is
logged to $FAKE_RC_DIR/received.jsonl, appended to the session's transcript
$CLAUDE_CONFIG_DIR/projects/<cwd, non-alphanumerics as ->/<sessionId>.jsonl as a
{"type": "user", "message": {"role": "user", "content": TEXT}} entry, and the record says
`busy` until the fake is idle again. Text pasted without Enter is only echoed by the
terminal: nothing is submitted or recorded.

$FAKE_RC_DIR/behavior.json may list lanes under "exit" (exit code, at once), "no_bridge"
(running, but no Remote Control bridge session), "notice" (prints a disconnect notice) and
"trust" (a first-use folder-trust prompt, modelled on Claude Code 2.1.323's, until the
folder is recorded as trusted in this fake's own $CLAUDE_CONFIG_DIR/fake-trust.json; the
answer is a line: "yes" trusts, anything else exits like "No, exit"), "no_hint" (the idle
input box without the "? for shortcuts" hint: Claude Code shows that hint only while its
footer has nothing else to show) and "stale_record" (the record's updatedAt is an hour old).
`--help`, `auth status` and `remote-control --help` answer like Claude Code does.
"""
import json
import os
import signal
import datetime
import subprocess
import sys
import time
import uuid

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

sessions = os.path.join(cfg, "sessions")
os.makedirs(sessions, exist_ok=True)
stat = open("/proc/%d/stat" % pid).read()
session_id = str(uuid.uuid4())
transcript = os.path.join(cfg, "projects", "".join(c if c.isascii() and c.isalnum() else "-" for c in os.getcwd()), session_id + ".jsonl")
record = {"pid": pid, "cwd": os.getcwd(), "kind": "interactive", "status": "idle", "sessionId": session_id,
          "procStart": stat[stat.rindex(")") + 2:].split()[19], "updatedAt": int(time.time() * 1000),
          "statusUpdatedAt": int(time.time() * 1000), "waitingFor": None,
          "bridgeSessionId": None if lane in behavior.get("no_bridge", []) or "--remote-control" not in args
          else "fake-bridge-%d" % pid}
if lane in behavior.get("stale_record", []):
    record["updatedAt"] -= 3600 * 1000


def save_record(**kw):
    record.update(kw)
    tmp = os.path.join(sessions, "%d.json.tmp" % pid)
    with open(tmp, "w") as f:
        json.dump(record, f)
    os.replace(tmp, os.path.join(sessions, "%d.json" % pid))


def idle_box():
    print("? for shortcuts" if lane not in behavior.get("no_hint", []) else
          "  * 1 background notice  (the footer has something else to show, so no shortcuts hint)")
    sys.stdout.flush()


save_record()
print("fake claude ready: %s" % " ".join(args))
if lane in behavior.get("notice", []):
    print("\u25cf Remote Control disconnected \u2014 this session was ended from another device (fake)")
idle_box()
while True:
    line = sys.stdin.readline()
    if not line:
        time.sleep(0.2)
        continue
    save_record(status="busy", statusUpdatedAt=int(time.time() * 1000))
    os.makedirs(os.path.dirname(transcript), exist_ok=True)
    with open(transcript, "a") as f:
        f.write(json.dumps({"type": "user", "sessionId": session_id, "cwd": os.getcwd(),
                            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                            "message": {"role": "user", "content": line.rstrip("\n")}}) + "\n")
    with open(os.path.join(ctl, "received.jsonl"), "a") as f:
        f.write(json.dumps({"lane": lane, "agent": "claude", "pid": pid, "text": line.rstrip("\n"), "time": time.time()}) + "\n")
    print("received: %s" % line.strip())
    sys.stdout.flush()
    time.sleep(float(behavior.get("busy_seconds", 0.5)))
    save_record(status="idle", statusUpdatedAt=int(time.time() * 1000))
    idle_box()
