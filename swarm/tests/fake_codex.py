#!/usr/bin/env python3
"""Stand-in for the Codex CLI in the launcher tests. It never talks to anything.

Records each start in $FAKE_RC_DIR/starts.jsonl (agent "codex"), may show a first-use
folder-trust prompt (lanes under "codex_trust" in behavior.json; text modelled on Codex
0.167.0's; "yes" trusts, kept in $FAKE_RC_DIR/codex-trust.json), then shows an idle footer
("? for shortcuts  100% context left") and logs each line it reads to received.jsonl.
`--help` and `login status` answer like the real CLI.

Two late prompts model a trust prompt that is not on the first frame: "codex_trust_late"
({lane: seconds}) shows the idle footer first and the prompt that many seconds later;
"codex_trust_on_signal" ([lane]) shows it when $FAKE_RC_DIR/trust-now-<lane> appears (the
fake tmux creates it right after a paste) and then writes trust-shown-<lane>. A late prompt
behaves like a selection: typed text and Enter are ignored (the terminal still echoes them);
only the line "yes" (trust) or "no" (exit) answers it.

With $CODEX_HOME set, a submitted line is also recorded the way Codex 0.167.0 records a
session on disk (observed on the development host, structure only): one rollout file
$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<uuid>.jsonl whose lines are
{"timestamp", "type", "payload"}: session_meta (with cwd), then per prompt a response_item
user message (content input_text) and an event_msg task_started. Without $CODEX_HOME nothing
is written there, so a test can never touch a real ~/.codex.
"""
import datetime
import json
import os
import select
import signal
import sys
import time
import uuid

args = sys.argv[1:]
if args[:1] == ["--help"]:
    print("Codex CLI\n\nUsage: codex [OPTIONS] [PROMPT]\n  -m, --model <MODEL>")
    sys.exit(0)
if args[:2] == ["login", "status"]:
    if os.environ.get("FAKE_CODEX_LOGGED_OUT"):
        print("Not logged in")
        sys.exit(1)
    print("Logged in using ChatGPT")
    sys.exit(0)
lane = os.environ.get("PAI_RC_LANE", "?")
ctl = os.environ["FAKE_RC_DIR"]
try:
    behavior = json.load(open(os.path.join(ctl, "behavior.json")))
except (OSError, ValueError):
    behavior = {}
pid = os.getpid()
with open(os.path.join(ctl, "starts.jsonl"), "a") as f:
    f.write(json.dumps({"lane": lane, "agent": "codex", "pid": pid, "cwd": os.getcwd(), "argv": args,
                        "ssh_auth_sock": os.environ.get("SSH_AUTH_SOCK"), "time": time.time(),
                        "env": {k: v for k, v in os.environ.items() if k.startswith(("PAI_TASK_", "PAI_RC_"))}}) + "\n")
if lane in behavior.get("codex_exit", {}):
    sys.exit(int(behavior["codex_exit"][lane]))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
trust_file = os.path.join(ctl, "codex-trust.json")
try:
    trusted = json.load(open(trust_file))
except (OSError, ValueError):
    trusted = []
TRUST_TEXT = ("Trust this folder? Codex can read, edit, and run files here, subject to your permission settings. "
              "Folder settings can run code automatically, even without a model request. Continue only if you trust "
              "these files.")
if lane in behavior.get("codex_trust", []) and os.getcwd() not in trusted:
    print(TRUST_TEXT)
    sys.stdout.flush()
    if sys.stdin.readline().strip().lower() != "yes":
        sys.exit(1)
    trusted.append(os.getcwd())
    json.dump(trusted, open(trust_file, "w"))
    sys.stdout.write("\x1b[H\x1b[2J")


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


rollout = None
if os.environ.get("CODEX_HOME"):
    now = datetime.datetime.now(datetime.timezone.utc)
    d = os.path.join(os.environ["CODEX_HOME"], "sessions", now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"))
    os.makedirs(d, exist_ok=True)
    rollout = os.path.join(d, "rollout-%s-%s.jsonl" % (now.strftime("%Y-%m-%dT%H-%M-%S"), uuid.uuid4()))


def record(kind, payload):
    if rollout:
        with open(rollout, "a") as f:
            f.write(json.dumps({"timestamp": stamp(), "type": kind, "payload": payload}) + "\n")


def footer():
    print("? for shortcuts   100% context left")
    sys.stdout.flush()


def late_trust():
    """A selection prompt: only "yes" or "no" answers it; anything else typed is ignored."""
    sys.stdout.write("\x1b[H\x1b[2J")
    print(TRUST_TEXT)
    print("> 1. Yes, continue\n  2. No, quit")
    sys.stdout.flush()
    open(os.path.join(ctl, "trust-shown-%s" % lane), "w").close()
    while True:
        answer = sys.stdin.readline()
        if not answer:
            time.sleep(0.2)
            continue
        answer = answer.strip().lower()
        if answer == "no":
            sys.exit(1)
        if answer == "yes":
            trusted.append(os.getcwd())
            json.dump(trusted, open(trust_file, "w"))
            sys.stdout.write("\x1b[H\x1b[2J")
            return


late = os.getcwd() not in trusted and (
    lane in behavior.get("codex_trust_late", {}) or lane in behavior.get("codex_trust_on_signal", []))
print("fake codex ready: %s" % " ".join(args))
footer()
if late and lane in behavior.get("codex_trust_late", {}):
    time.sleep(float(behavior["codex_trust_late"][lane]))
    late_trust()
    late = False
    footer()
record("session_meta", {"id": str(uuid.uuid4()), "cwd": os.getcwd(), "cli_version": "0.167.0-fake"})
while True:
    if late and os.path.exists(os.path.join(ctl, "trust-now-%s" % lane)):
        late_trust()
        late = False
        footer()
    if not select.select([sys.stdin], [], [], 0.1)[0]:
        continue
    line = sys.stdin.readline()
    if not line:
        time.sleep(0.2)
        continue
    record("response_item", {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": line.rstrip("\n")}]})
    record("event_msg", {"type": "task_started", "turn_id": str(uuid.uuid4())})
    with open(os.path.join(ctl, "received.jsonl"), "a") as f:
        f.write(json.dumps({"lane": lane, "agent": "codex", "pid": pid, "text": line.rstrip("\n"), "time": time.time()}) + "\n")
    print("received: %s" % line.strip())
    footer()
