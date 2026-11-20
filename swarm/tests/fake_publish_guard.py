#!/usr/bin/env python3
"""Stand-in `git` and `gh` for review-flow tests: anything that would publish or reach a network
fails loudly and is logged to $FAKE_PUBLISH_LOG, so a test can prove the flow never tried.

As `gh` (argv[0] ends in gh): every call is refused (exit 99) and logged.
As `git`: push, send-pack, fetch, pull, clone, ls-remote, remote update and request-pull are
refused (exit 99) and logged; anything else runs the real git ($FAKE_REAL_GIT) unchanged.
"""
import json
import os
import sys
import time

# the wrapper's own name (git or gh): runpy replaces sys.argv[0] with this file's path
orig = getattr(sys, "orig_argv", sys.argv)
name = os.path.basename(next((x for x in orig[1:] if not x.startswith("-")), sys.argv[0]))
args = sys.argv[1:]
NETWORK = {"push", "send-pack", "fetch", "pull", "clone", "ls-remote", "request-pull", "fetch-pack"}


def refuse(what):
    with open(os.environ["FAKE_PUBLISH_LOG"], "a") as f:
        f.write(json.dumps({"tool": name, "argv": args, "refused": what, "time": time.time()}) + "\n")
    sys.stderr.write("fake %s: refused %s: the review flow must not publish or use the network\n" % (name, what))
    sys.exit(99)


if name == "gh":
    refuse("gh %s" % " ".join(args[:2]))
i = 0
while i < len(args) and args[i].startswith("-"):  # global options: -C DIR, -c k=v, --no-pager, ...
    i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
sub = args[i] if i < len(args) else ""
if sub in NETWORK or (sub == "remote" and args[i + 1:i + 2] == ["update"]):
    refuse("git %s" % sub)
os.execv(os.environ["FAKE_REAL_GIT"], [os.environ["FAKE_REAL_GIT"]] + args)
