#!/usr/bin/env python3
"""Fake `ssh` for the SSH preflight tests; it reaches no network.

The "agent" is the file named by SSH_AUTH_SOCK: one loaded key name per line. A connection is
accepted when that file lists $FAKE_SSH_ACCEPT; then the git command runs locally against the
path in the URL (a local bare repository), so fetch, ls-remote and push really work.
Every call is logged to $FAKE_SSH_LOG with whether BatchMode=yes was given and whether a
terminal could be opened: a call that could have prompted for a passphrase is recorded and
fails, so a test can prove nothing ever prompted. `-G` prints an effective config with
$FAKE_SSH_KEYFILE as the identity file. $FAKE_SSH_HOSTKEY_FAIL simulates an unknown host key.
"""
import json
import os
import sys

args = sys.argv[1:]
try:
    fd = os.open("/dev/tty", os.O_RDWR)
    os.close(fd)
    tty = True
except OSError:
    tty = False
opts, rest, i = [], [], 0
while i < len(args):
    a = args[i]
    if a in ("-o", "-p", "-l", "-i", "-F", "-E") and i + 1 < len(args):
        opts.append((a, args[i + 1]))
        i += 2
    elif a.startswith("-") and not rest:
        opts.append((a, None))
        i += 1
    else:
        rest.append(a)
        i += 1
batch = ("-o", "BatchMode=yes") in opts
with open(os.environ["FAKE_SSH_LOG"], "a") as f:
    f.write(json.dumps({"argv": args, "batch": batch, "tty": tty, "sock": os.environ.get("SSH_AUTH_SOCK")}) + "\n")
if ("-G", None) in opts:
    print("user git\nhostname %s\nport 22\nidentityfile %s" % (rest[0] if rest else "?", os.environ.get("FAKE_SSH_KEYFILE", "")))
    sys.exit(0)
if not batch and tty:
    print("fake ssh: would have asked for a passphrase on the terminal", file=sys.stderr)
    sys.exit(255)
host = rest[0] if rest else "?"
if os.environ.get("FAKE_SSH_HOSTKEY_FAIL"):
    print("Host key verification failed.", file=sys.stderr)
    sys.exit(255)
sock = os.environ.get("SSH_AUTH_SOCK")
keys = []
if sock and os.path.isfile(sock):
    keys = [l.strip() for l in open(sock) if l.strip()]
if os.environ.get("FAKE_SSH_ACCEPT") not in keys:
    print("%s: Permission denied (publickey)." % host, file=sys.stderr)
    sys.exit(255)
cmd = " ".join(rest[1:])
if cmd.startswith("git-"):
    cmd = "git " + cmd[4:]
os.execvp("sh", ["sh", "-c", cmd])
