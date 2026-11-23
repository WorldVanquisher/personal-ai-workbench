#!/usr/bin/env python3
"""A stand-in `claude` for the pai round launch tests (copied to a folder as `claude`, so its
process name is claude). It never talks to anything.

$FAKE_ROUND_DIR/behavior.json: {"trust": true} shows a 2.1-style folder-trust dialog first (a
line "1" trusts, anything else exits); {"busy_after_submit": seconds} keeps the footer at
"esc to interrupt" that long after a prompt is submitted; {"dialog": true} shows a "Teach auto
mode about your environment?" dialog first (a line "2" closes it); {"draft": TEXT} shows TEXT typed
(not dim) in the input box; {"busy": true} stays working: a submitted prompt is queued (logged
with "queued": true) and the screen shows "Press up to edit queued messages".

Logs, one JSON line each, in $FAKE_ROUND_DIR:
  starts.jsonl    {"argv", "cwd", "pid"}
  received.jsonl  {"text", "bracketed"}: a submitted prompt (Enter outside a paste)
  pasted.jsonl    {"text"}: each bracketed paste as it arrives
"""
import json
import os
import sys
import termios
import time
import tty

d = os.environ["FAKE_ROUND_DIR"]
try:
    with open(os.path.join(d, "behavior.json")) as f:
        behavior = json.load(f)
except (OSError, ValueError):
    behavior = {}


def log(name, obj):
    with open(os.path.join(d, name), "a") as f:
        f.write(json.dumps(obj) + "\n")


def out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


log("starts.jsonl", {"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid()})
TRUST = ("\x1b[2J\x1b[H Quick safety check: Is this a project you created or one you trust?\r\n\r\n"
         " Claude Code'll be able to read, edit, and execute files here.\r\n\r\n"
         " ❯ 1. Yes, I trust this folder\r\n   2. No, exit\r\n\r\n Enter to confirm · Esc to cancel\r\n")
IDLE = ("\x1b[2J\x1b[H Claude Code (fake)\r\n\r\n" + "─" * 40 + "\r\n❯ \r\n" + "─" * 40 +
        "\r\n  ⏵⏵ auto mode on (shift+tab to cycle)\r\n")
BUSY = IDLE.replace("(shift+tab to cycle)", "(shift+tab to cycle) · esc to interrupt")
TEACH = ("\x1b[2J\x1b[H Teach auto mode about your environment?\r\n\r\n ❯ 1. Yes, set it up\r\n   2. Not now\r\n\r\n"
         " Enter to confirm · Esc to cancel\r\n")
if behavior.get("draft"):
    IDLE = IDLE.replace("❯ \r\n", "❯ " + behavior["draft"] + "\r\n")
    BUSY = BUSY.replace("❯ \r\n", "❯ " + behavior["draft"] + "\r\n")
QUEUED = BUSY.replace("\r\n─", "\r\n  ⎿  Press up to edit queued messages\r\n─", 1)

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    if behavior.get("trust"):
        out(TRUST)
        line = b""
        while not line.endswith(b"\r"):
            line += os.read(fd, 1)
        if line.strip() != b"1":
            sys.exit(0)
    if behavior.get("dialog"):
        out(TEACH)
        line = b""
        while not line.endswith(b"\r"):
            line += os.read(fd, 1)
    out("\x1b[?2004h")  # ask for bracketed paste, as Claude Code does
    out(BUSY if behavior.get("busy") else IDLE)
    buf, pasting, pasted = b"", False, False
    while True:
        b = os.read(fd, 4096)
        if not b:
            break
        buf += b
        while True:
            if pasting:
                i = buf.find(b"\x1b[201~")
                if i < 0:
                    break
                text = buf[:i].decode("utf-8", "replace")
                log("pasted.jsonl", {"text": text})
                pending = text
                pasting, pasted, buf = False, True, buf[i + 6:]
                continue
            i = buf.find(b"\x1b[200~")
            j = buf.find(b"\r")
            if i >= 0 and (j < 0 or i < j):
                pending_before = buf[:i]
                pasting, buf = True, buf[i + 6:]
                continue
            if j >= 0:
                q = bool(behavior.get("busy"))
                if pasted:
                    log("received.jsonl", {"text": pending, "bracketed": True, "queued": q})
                else:
                    log("received.jsonl", {"text": buf[:j].decode("utf-8", "replace"), "bracketed": False, "queued": q})
                pasted, buf = False, buf[j + 1:]
                if q:
                    out(QUEUED)
                    continue
                out(BUSY)
                time.sleep(float(behavior.get("busy_after_submit", 0.5)))
                out(IDLE)
                continue
            break
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
