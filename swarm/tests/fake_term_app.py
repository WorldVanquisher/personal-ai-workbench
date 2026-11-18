#!/usr/bin/env python3
"""A terminal program for paste and typing tests (panes.py, pai web, pai paste): raw mode, and every
byte it receives is appended to $FAKE_APP_LOG exactly as it arrived.

  FAKE_APP_BRACKETED=1   turn bracketed paste on (ESC [ ? 2004 h), as Claude Code, Codex and bash do
  FAKE_APP_TRUST_AFTER=1 after the first input, show a Claude-style folder-trust prompt
  FAKE_APP_COLOUR=1      draw a coloured line (red, bold) for screen-capture tests
It never answers anything; it only records."""
import os
import sys
import tty

log = os.environ["FAKE_APP_LOG"]
fd = sys.stdin.fileno()
tty.setraw(fd)
if os.environ.get("FAKE_APP_BRACKETED") == "1":
    os.write(1, b"\x1b[?2004h")
if os.environ.get("FAKE_APP_COLOUR") == "1":
    os.write(1, b"\x1b[1;31mred bold\x1b[0m plain\r\n")
os.write(1, b"fake app ready\r\n> ")
trust = os.environ.get("FAKE_APP_TRUST_AFTER") == "1"
while True:
    data = os.read(fd, 65536)
    if not data:
        break
    with open(log, "ab") as f:
        f.write(data)
    if trust:
        os.write(1, b"\r\nQuick safety check: Is this a project you created or one you trust?\r\n"
                    b" > No, exit\r\n   Yes, I trust this folder\r\n")
        trust = False
