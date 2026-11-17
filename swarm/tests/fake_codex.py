#!/usr/bin/env python3
"""Stand-in for the Codex CLI in the launcher tests. It never talks to anything.

Records each start in $FAKE_RC_DIR/starts.jsonl (agent "codex"), may show a first-use
folder-trust prompt (lanes under "codex_trust" in behavior.json; text modelled on Codex
0.167.0's; "yes" trusts, kept in $FAKE_RC_DIR/codex-trust.json), then shows an idle footer
("? for shortcuts  100% context left") and logs each line it reads to received.jsonl.
`--help` and `login status` answer like the real CLI.
"""
import json
import os
import signal
import sys
import time

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
if lane in behavior.get("codex_trust", []) and os.getcwd() not in trusted:
    print("Trust this folder? Codex can read, edit, and run files here, subject to your permission settings. "
          "Folder settings can run code automatically, even without a model request. Continue only if you trust "
          "these files.")
    sys.stdout.flush()
    if sys.stdin.readline().strip().lower() != "yes":
        sys.exit(1)
    trusted.append(os.getcwd())
    json.dump(trusted, open(trust_file, "w"))
    sys.stdout.write("\x1b[H\x1b[2J")
print("fake codex ready: %s" % " ".join(args))
print("? for shortcuts   100% context left")
sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        time.sleep(0.2)
        continue
    with open(os.path.join(ctl, "received.jsonl"), "a") as f:
        f.write(json.dumps({"lane": lane, "agent": "codex", "pid": pid, "text": line.rstrip("\n"), "time": time.time()}) + "\n")
    print("received: %s" % line.strip())
    print("? for shortcuts   100% context left")
    sys.stdout.flush()
