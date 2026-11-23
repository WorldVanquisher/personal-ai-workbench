"""pai round prplan: a lane's NEXT-PR.md as a `pai worktree publish` preview plan (wtpr).

    pai round prplan LANE [--commit N] [--next-pr FILE] [--offline] [--out PLAN.json]

NEXT-PR.md (written by the worker in its newest checkpoint) holds the branch, the commits in
order (a message in backticks, then a `git add PATH ...` line) and a PR body draft. A wtpr plan
is one commit, so this previews commit N (default 1) of the lane's worktree with exactly that
commit's paths (a folder stands for the files under it), its message, the PR title (the first
commit's subject) and the body draft.

The plan gets the fields that tie it to the round, as in the fusion design (§5.4):
  task             <ROUND>/<LANE>#<N>
  round, lane, commit, commits
  next_pr          path and sha256 of the NEXT-PR.md it came from
  handoff_sha256   the sha256 of the same checkpoint's HANDOFF.md (null when missing)
  queue_sha256     the sha256 of the lane's queue log (null when there is none)
It reads only; --out writes a new plan file (never overwrites, mode 0600). Publishing stays the
owner's: `pai worktree publish PLAN.json --expect-plan SHA --yes --draft|--ready`.
"""
import hashlib
import json
import os
import re

import round as rnd
import round_status as rs
import wtpr

COMMIT_RE = re.compile(r"^\s*(\d+)\.\s+`([^`]+)`")
ADD_RE = re.compile(r"^\s*git add\s+(.+)$")
BRANCH_RE = re.compile(r"(?:分支|branch)\s*[：:]\s*`?([A-Za-z0-9._/-]+)`?", re.I)


def parse(text):
    """{"branch", "commits": [{"n", "message", "paths"}], "body"} from NEXT-PR.md."""
    out = {"branch": None, "commits": [], "body": ""}
    m = BRANCH_RE.search(text)
    if m:
        out["branch"] = m.group(1)
    lines = text.splitlines()
    cur = None
    for line in lines:
        c = COMMIT_RE.match(line)
        if c:
            cur = {"n": int(c.group(1)), "message": c.group(2).strip(), "paths": []}
            out["commits"].append(cur)
            continue
        a = ADD_RE.match(line)
        if a and cur is not None:
            cur["paths"] += a.group(1).split()
    body, on = [], False
    for line in lines:
        if line.startswith("## "):
            on = "正文" in line or "body" in line.lower()
            continue
        if on:
            body.append(line)
    out["body"] = "\n".join(body).strip()
    return out


def expand(worktree, paths):
    res = []
    for p in paths:
        full = os.path.join(worktree, p)
        if os.path.isdir(full) and not os.path.islink(full):
            for d, dirs, files in os.walk(full):
                dirs[:] = sorted(x for x in dirs if x != "__pycache__")
                for f in sorted(files):
                    res.append(os.path.relpath(os.path.join(d, f), worktree).replace(os.sep, "/"))
        else:
            res.append(p)
    return res


def sha_file(path):
    return rnd.sha256_file(path) if path and os.path.isfile(path) else None


def plan(r, ln, n=1, next_pr=None, offline=False):
    if not next_pr:
        cp = rs.latest_checkpoint(ln.get("handoff_dir") or "")
        if not cp or not cp.get("next_pr"):
            raise rnd.RoundError("lane %s has no NEXT-PR.md in its newest checkpoint" % ln["name"])
        next_pr = os.path.join(cp["path"], "NEXT-PR.md")
    with open(next_pr, encoding="utf-8") as f:
        text = f.read()
    d = parse(text)
    if not d["commits"]:
        raise rnd.RoundError("%s lists no commits (lines like 1. `message` then git add PATH ...)" % next_pr)
    if not 1 <= n <= len(d["commits"]):
        raise rnd.RoundError("--commit %d: %s has %d commit(s)" % (n, next_pr, len(d["commits"])))
    c = d["commits"][n - 1]
    if d["branch"] and d["branch"] != ln["branch"]:
        raise rnd.RoundError("NEXT-PR.md names branch %s, but lane %s is on %s" % (d["branch"], ln["name"], ln["branch"]))
    paths = expand(ln["worktree"], c["paths"])
    try:
        pv = wtpr.preview(ln["worktree"], paths, d["commits"][0]["message"], d["body"], c["message"], ln["branch"],
                          r.get("base_branch") or "main", "origin", offline)
    except wtpr.PlanError as e:
        raise rnd.RoundError("wtpr: %s" % e)
    cpdir = os.path.dirname(next_pr)
    qlog = os.path.join(ln.get("work_dir") or "", "queue.log")
    pv.update({"task": "%s/%s#%d" % (r["round"], ln["name"], n), "round": r["round"], "lane": ln["name"], "commit": n,
               "commits": len(d["commits"]), "next_pr": {"path": next_pr, "sha256": rnd.sha256_bytes(text.encode("utf-8"))},
               "handoff_sha256": sha_file(os.path.join(cpdir, "HANDOFF.md")), "queue_sha256": sha_file(qlog)})
    return pv


def main(a, r, out):
    ln = rnd.lane(r, a.lane)
    pv = plan(r, ln, a.commit, a.next_pr, a.offline)
    print(wtpr.render(pv), file=out)
    print("  task %s (commit %d of %d)  next_pr %s  handoff %s  queue %s" % (
        pv["task"], pv["commit"], pv["commits"], pv["next_pr"]["sha256"][:12], (pv["handoff_sha256"] or "-")[:12],
        (pv["queue_sha256"] or "-")[:12]), file=out)
    if a.out:
        if os.path.exists(a.out):
            raise rnd.RoundError("%s exists; a plan file is never overwritten" % a.out)
        data = json.dumps(pv, indent=1, sort_keys=True).encode("utf-8")
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        print("  plan written: %s (sha256 %s). Publishing is the owner's: pai worktree publish %s --expect-plan %s --yes "
              "--draft" % (a.out, hashlib.sha256(data).hexdigest(), a.out, hashlib.sha256(data).hexdigest()[:16]), file=out)
    return 1 if pv.get("problems") else 0
