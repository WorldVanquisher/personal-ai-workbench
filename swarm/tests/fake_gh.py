#!/usr/bin/env python3
"""Fake `gh` for the tests. State is $FAKE_GH_STATE (JSON: {"prs": [...], "unauth": bool});
every call is appended to $FAKE_GH_STATE.log. `pr create` records an OPEN PR whose head is
whatever $FAKE_GH_REMOTE (a local bare repository) has for the branch. Anything this
workflow must never call (`pr merge`, `api`, ...) exits 2."""
import json
import os
import subprocess
import sys

path = os.environ["FAKE_GH_STATE"]
args = sys.argv[1:]
with open(path + ".log", "a") as log:
    log.write(json.dumps(args) + "\n")
try:
    state = json.load(open(path))
except (OSError, ValueError):
    state = {}
state.setdefault("prs", [])


def opt(name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a[len(name) + 1:]
    return None


if args[:2] == ["auth", "status"]:
    sys.exit(1 if state.get("unauth") else 0)
if args[:1] == ["--version"]:
    print("gh version 2.99.0 (fake)")
    sys.exit(0)
if args[:2] == ["pr", "list"]:
    fields = (opt("--json") or "number,url,state").split(",")
    print(json.dumps([{k: p.get(k) for k in fields} for p in state["prs"] if p["headRefName"] == opt("--head")]))
    sys.exit(0)
if args[:2] == ["pr", "create"]:
    if state.get("fail_create"):
        print("fake gh: injected pr create failure", file=sys.stderr)
        sys.exit(1)
    head = opt("--head")
    out = subprocess.run(["git", "ls-remote", os.environ["FAKE_GH_REMOTE"], "refs/heads/" + head],
                         capture_output=True, text=True, cwd=os.sep).stdout  # not the caller's directory
    if not out:
        print("fake gh: head branch is not on the remote", file=sys.stderr)
        sys.exit(1)
    n = len(state["prs"]) + 1
    pr = {"number": n, "url": "https://github.example/%s/pull/%d" % (opt("--repo"), n), "state": "OPEN",
          "mergedAt": None, "baseRefName": opt("--base"), "headRefName": head, "headRefOid": out.split("\t")[0],
          "title": opt("--title"), "body": open(opt("--body-file")).read()}
    state["prs"].append(pr)
    json.dump(state, open(path, "w"), indent=1)
    print(pr["url"])
    sys.exit(0)
print("fake gh: unsupported call %r" % args, file=sys.stderr)
sys.exit(2)
