"""pai round close: the acceptance checklist, then reclaim worktrees (r6-worktrees.sh's rules).

    pai round close [ROUND.json] [--only-re RE] [--all-worktrees] [--write]

1. Checklist, per lane, informational: session state, worktree clean, commits on the branch
   beyond the base, newest checkpoint with HANDOFF.md / NEXT-PR.md / SHA256SUMS, queue items
   still todo or doing. It decides nothing; it is what the owner reads before closing.
2. Candidates: this round's lane worktrees (with --all-worktrees: every linked worktree of the
   round's repository, as r6-worktrees.sh cleaned old ones). Then:
     - never the main checkout;
     - --only-re: only paths matching the regular expression are candidates, the rest are kept;
     - a worktree of another OPEN round (its ROUND.json in the same Agent Inbox) is protected;
     - a missing folder: its record is pruned (`git worktree prune`);
     - uncommitted or untracked changes: kept and listed (never --force);
     - a process whose working folder is inside: kept and named;
     - otherwise `git worktree remove` (no --force). Branches are never deleted, so every commit
       stays reachable.
3. With --write, once none of this round's worktrees is left, ROUND.json is archived:
   ROUND.json gets status closed and a copy goes to archive/ROUND-<time>.json.
Without --write nothing changes (exit 0, or 1 when something would be kept).
"""
import json
import os
import re

import gitprep
import round as rnd
import round_status as rs


def other_open_rounds(r):
    """{worktree path: round id} of every other open round under the same Agent Inbox."""
    inbox = os.path.dirname(r["state_dir"])
    out = {}
    try:
        names = os.listdir(inbox)
    except OSError:
        return out
    for n in names:
        p = os.path.join(inbox, n, "ROUND.json")
        if n == r["round"] or not os.path.isfile(p):
            continue
        try:
            o = rnd.load_round(p)
        except rnd.RoundError:
            continue
        if o.get("status", "open") == "open":
            for ln in o["lanes"]:
                out[gitprep.canon(ln["worktree"])] = o["round"]
    return out


def checklist(r, rows):
    out = []
    for ln, x in zip(r["lanes"], rows):
        cp = x.get("checkpoint") or {}
        q = x.get("queue") or {}
        ahead = gitprep.ahead(r["repo"], r["base_sha"], "refs/heads/" + ln["branch"]) if gitprep.branch_sha(r["repo"], ln["branch"]) else None
        items = [("会话已退出或空闲", x["state"] in ("exited", "no-window", "idle"), x["state"]),
                 ("worktree 干净", x["worktree"] == "clean", x["worktree"]),
                 ("分支上有提交", bool(ahead), "%s 个" % ahead if ahead is not None else "无分支"),
                 ("最新 checkpoint 有 HANDOFF.md", bool(cp.get("handoff")), cp.get("name") or "-"),
                 ("有 NEXT-PR.md", bool(cp.get("next_pr")), ""),
                 ("有 SHA256SUMS", bool(cp.get("sums")), ""),
                 ("queue 没有 todo/doing", bool(q) and not q.get("todo") and not q.get("doing"),
                  rs.totals_text(q) if q else "没有 queue")]
        out.append((ln["name"], items))
    return out


def plan(r, only_re=None, all_worktrees=False):
    repo = r["repo"]
    main, _ = __import__("refresh").main_checkout(repo)
    if not main:
        raise rnd.RoundError("the round's repository %s is gone or not a Git repository" % repo)
    rx = re.compile(only_re) if only_re else None
    mine = {gitprep.canon(ln["worktree"]): ln for ln in r["lanes"]}
    protected = other_open_rounds(r)
    procs = rs.processes()
    me = {os.getpid(), os.getppid()}
    res = []
    for w in gitprep.worktree_list(repo):
        path = w["path"]
        item = {"path": path, "branch": w["branch"], "lane": (mine.get(path) or {}).get("name")}
        if path == gitprep.canon(main):
            continue
        if not all_worktrees and path not in mine:
            continue
        if rx and not rx.search(path):
            res.append(dict(item, action="keep", why="not matched by --only-re"))
        elif path in protected:
            res.append(dict(item, action="keep", why="protected: a worktree of open round %s" % protected[path]))
        elif not os.path.isdir(path) or w["prunable"]:
            res.append(dict(item, action="prune", why="folder is gone; only its record is pruned"))
        else:
            dirty = gitprep.dirty(path)
            busy = rs.busy_in(path, procs, skip=me)
            if dirty:
                res.append(dict(item, action="keep", why="%d uncommitted/untracked: %s" % (
                    len(dirty), ", ".join(d[3:] for d in dirty[:5]) + (" …" if len(dirty) > 5 else ""))))
            elif busy:
                res.append(dict(item, action="keep", why="in use by %s" % ", ".join("%d(%s)" % b for b in busy[:3])))
            else:
                res.append(dict(item, action="remove", why="clean and idle; branch %s is kept" % (w["branch"] or "(detached)")))
    return res


def apply(r, items, log):
    removed = 0
    for it in items:
        if it["action"] != "remove":
            continue
        p = gitprep.git(r["repo"], "worktree", "remove", it["path"])  # never --force
        if p.returncode == 0:
            it["done"] = True
            removed += 1
            log("  removed      %s  [%s kept]" % (it["path"], it["branch"]))
        else:
            it["action"], it["why"] = "keep", "git worktree remove refused: %s" % p.stderr.strip()[-200:]
            log("  kept         %s  (%s)" % (it["path"], it["why"]))
    if any(it["action"] == "prune" for it in items):
        gitprep.git(r["repo"], "worktree", "prune")
        log("  pruned       records of missing folders")
    left = [ln["name"] for ln in r["lanes"] if os.path.isdir(ln["worktree"])]
    if left:
        log("  ROUND.json stays open: %d lane worktree(s) remain (%s)" % (len(left), ", ".join(left)))
        return removed, None
    stamp = rnd.now()
    r["status"], r["closed"] = "closed", stamp
    archive = os.path.join(r["state_dir"], "archive", "ROUND-%s.json" % stamp.replace(":", ""))
    rnd.write_atomic(archive, rnd.dump_round(r))
    rnd.write_atomic(r["_path"], rnd.dump_round(r))
    log("  archived     %s (ROUND.json status: closed)" % archive)
    return removed, archive


def main(a, r, out):
    log = lambda s: print(s, file=out, flush=True)  # noqa: E731
    rows = rs.status(r)
    log("pai round close %s%s" % (r["round"], "" if a.write else "  (preview: nothing is changed; add --write)"))
    log("== 验收清单（只是给人看的，不替人决定）")
    for name, items in checklist(r, rows):
        log("  %s" % name)
        for label, ok, detail in items:
            log("    [%s] %s%s" % ("x" if ok else " ", label, "  (%s)" % detail if detail else ""))
    try:
        items = plan(r, a.only_re, a.all_worktrees)
    except re.error as e:
        raise rnd.RoundError("--only-re: %s" % e)
    log("== worktree")
    for it in items:
        log("  %-12s %s  [%s]  %s" % ({"remove": "remove" if a.write else "would remove"}.get(it["action"], it["action"]),
                                      it["path"], it["branch"] or "detached", it["why"]))
    if not items:
        log("  (no candidate worktrees)")
    if a.write:
        apply(r, items, log)
    kept = [it for it in items if it["action"] == "keep"]
    log("  summary: %s %d, kept %d, pruned %d; branches are never deleted" % (
        "removed" if a.write else "would remove", sum(1 for it in items if it["action"] == "remove" or it.get("done")),
        len(kept), sum(1 for it in items if it["action"] == "prune")))
    return 1 if kept else 0
