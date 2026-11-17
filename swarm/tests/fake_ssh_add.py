#!/usr/bin/env python3
"""Fake `ssh-add`: only `-l` is expected. Exit 2 without an agent (SSH_AUTH_SOCK unset or its
file missing), 1 when the agent file is empty, 0 with keys. Any other use is logged and fails:
pai must never add keys or ask for a passphrase itself."""
import json
import os
import sys

with open(os.environ["FAKE_SSH_LOG"] + ".add", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:] != ["-l"]:
    sys.exit(3)
sock = os.environ.get("SSH_AUTH_SOCK")
if not sock or not os.path.isfile(sock):
    print("Could not open a connection to your authentication agent.", file=sys.stderr)
    sys.exit(2)
keys = [l.strip() for l in open(sock) if l.strip()]
if not keys:
    print("The agent has no identities.")
    sys.exit(1)
for k in keys:
    print("256 SHA256:fake%s %s (ED25519)" % (abs(hash(k)) % 10000, k))
sys.exit(0)
