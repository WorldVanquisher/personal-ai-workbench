"""Post-merge refresh: the exact owner-run commands that bring a repository's work onto the
freshly merged base, and the check that a worktree really contains it.

    pai worktree list [--repo PATH] [--json]
    pai worktree refresh [--repo PATH] [--remote R] [--base B] [--worktree PATH | --new PATH --branch NAME]
                         [--shell bash|powershell|both] [--json]
    pai worktree verify PATH --base SHA [--json]

`refresh` only reads and prints. It fetches nothing (a fetch is the owner's, and a fetch alone
only updates refs), creates nothing and never resets, stashes, forces or overwrites. Its
commands are, in order: an explicit fetch of exactly the remote's base branch into its
tracking ref; then either a fresh worktree from the fetched SHA (the default, and the only path
for a dirty, detached or squash-merged worktree, which is left exactly as it is) or, for a
clean worktree on a branch whose HEAD the fetched base already contains, a fast-forward that
refuses by itself otherwise; then `pai worktree verify`, which is what a dispatch must rely
on: the worktree's actual HEAD contains the fetched base.
"""
import json
import os
import re
import shlex
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import gitprep  # noqa: E402


class RefreshError(Exception):
    pass


def main_checkout(path):
    """The repository's main checkout for any path inside it or one of its worktrees."""
    cdir = gitprep.common_dir(path)
    if not cdir:
        return None, None
    main = os.path.dirname(cdir) if os.path.basename(cdir) == ".git" else None
    if main and gitprep.common_dir(main) == cdir:
        return main, cdir
    return gitprep.toplevel(path), cdir


def worktrees(repo):
    """Every worktree of the repository: path, branch (None when detached), HEAD, uncommitted
    paths, Git operation in progress. Read-only (optional locks off)."""
    res = []
    for w in gitprep.worktree_list(repo):
        item = {"path": w["path"], "branch": w["branch"], "head": w["head"], "detached": w["branch"] is None,
                "prunable": w["prunable"], "exists": os.path.isdir(w["path"])}
        if item["exists"] and not w["prunable"]:
            d = gitprep.dirty(w["path"])
            item.update(uncommitted=len(d), uncommitted_paths=[x[3:] for x in d[:20]], in_progress=gitprep.in_progress(w["path"]))
        else:
            item.update(uncommitted=None, uncommitted_paths=[], in_progress=[])
        res.append(item)
    return res


def identity(repo, remote):
    url = gitprep.out(repo, "config", "--get", "remote.%s.url" % remote)
    return {"remote": remote, "url": gitprep.redact_url(url) if url else None,
            "github": gitprep.github_slug(url) if url else None}


def describe(repo=None, remote="origin", base="main"):
    main, cdir = main_checkout(os.path.abspath(repo or os.getcwd()))
    if not main:
        raise RefreshError("%s is not inside a Git repository" % os.path.abspath(repo or os.getcwd()))
    ident = identity(main, remote)
    if not ident["url"]:
        raise RefreshError("the repository has no remote named %s" % remote)
    if not gitprep.valid_branch(base):
        raise RefreshError("base branch %r is not a valid branch name" % base)
    tracking = "refs/remotes/%s/%s" % (remote, base)
    last = gitprep.rev(main, tracking)
    wts = worktrees(main)
    for w in wts:
        w["contains_last_fetched_base"] = bool(last and w["head"] and gitprep.is_ancestor(main, last, w["head"]))
        # commits the worktree has that the fetched base does not: unpublished work (or a refused publish) stays visible
        w["ahead_of_base"] = gitprep.ahead(main, last, w["head"]) if last and w["head"] else None
    return {"repository": dict(ident, path=main, common_dir=cdir), "base": {"branch": base, "tracking_ref": tracking,
            "last_fetched": last}, "worktrees": wts}


def _q(path, shell):
    if shell == "powershell":
        return "'%s'" % path.replace("'", "''")
    return shlex.quote(path)


def plan(repo=None, remote="origin", base="main", worktree=None, new=None, branch=None, today=None):
    """The refresh plan for one repository: what is known now, the recommendation and the
    owner-run commands. Reads only."""
    info = describe(repo, remote, base)
    main = info["repository"]["path"]
    day = today or time.strftime("%Y%m%d")
    rec = {"kind": None, "reasons": []}
    target = None
    if worktree:
        wt = os.path.realpath(os.path.abspath(worktree))
        target = next((w for w in info["worktrees"] if w["path"] == wt), None)
        if not target:
            raise RefreshError("%s is not a worktree of %s" % (wt, main))
        if target["detached"]:
            rec["reasons"].append("its HEAD is detached")
        if target["uncommitted"]:
            rec["reasons"].append("it has %d uncommitted path(s), which stay exactly as they are" % target["uncommitted"])
        if target["in_progress"]:
            rec["reasons"].append("a Git operation is in progress there (%s)" % ", ".join(target["in_progress"]))
        if not rec["reasons"]:
            rec["kind"] = "fast-forward"
            rec["path"], rec["branch"] = wt, target["branch"]
            rec["reasons"].append("clean and on branch %s: it can move forward only if the fetched base contains its "
                                  "HEAD (a squash-merged branch does not; the command then refuses and changes nothing)"
                                  % target["branch"])
    if rec["kind"] is None:
        new_path = os.path.realpath(os.path.abspath(os.path.expanduser(new))) if new else os.path.join(
            os.path.dirname(main), "%s-%s" % (os.path.basename(main), day))
        new_branch = branch or "postmerge/%s" % day
        if not gitprep.valid_branch(new_branch):
            raise RefreshError("branch name %r is not valid" % new_branch)
        if gitprep.branch_sha(main, new_branch):
            raise RefreshError("branch %s already exists; choose another --branch" % new_branch)
        if os.path.lexists(new_path):
            raise RefreshError("%s already exists; choose another --new path (nothing there is touched)" % new_path)
        rec.update(kind="new-worktree", path=new_path, branch=new_branch)
        if not rec["reasons"]:
            rec["reasons"].append("a fresh worktree never touches an existing one")
    info["recommendation"] = rec
    info["commands"] = {s: commands(info, s) for s in ("bash", "powershell")}
    return info


def commands(info, shell):
    """The owner-run commands, in order, for bash or PowerShell. Every value pai puts into them
    (paths, the remote, the base and the new branch, which Git allows to contain `$`, quotes,
    backticks, `|` or `&`) is quoted for that shell; only $BASE, set by the commands themselves,
    is expanded."""
    main, r = info["repository"]["path"], info["recommendation"]
    remote, base = info["repository"]["remote"], info["base"]["branch"]
    refspec = "+refs/heads/%s:refs/remotes/%s/%s" % (base, remote, base)
    tracking = "%s/%s" % (remote, base)
    q = lambda p: _q(p, shell)  # noqa: E731
    if shell == "powershell":
        out = ["git -C %s fetch %s %s" % (q(main), q(remote), q(refspec)),
               "$BASE = git -C %s rev-parse %s; %s + $BASE" % (q(main), q(tracking), q("fetched %s = " % tracking))]
        if r["kind"] == "new-worktree":
            out.append("git -C %s worktree add -b %s %s $BASE" % (q(main), q(r["branch"]), q(r["path"])))
        else:
            out.append("if ((git -C %s status --porcelain) -eq $null) { git -C %s merge --ff-only $BASE } else "
                       "{ 'not clean: nothing was changed' }" % (q(r["path"]), q(r["path"])))
        out.append("pai worktree verify %s --base $BASE" % q(r["path"]))
        return out
    out = ["git -C %s fetch %s %s" % (q(main), q(remote), q(refspec)),
           "BASE=$(git -C %s rev-parse %s) && echo %s\"$BASE\"" % (q(main), q(tracking), q("fetched %s = " % tracking))]
    if r["kind"] == "new-worktree":
        out.append("git -C %s worktree add -b %s %s \"$BASE\"" % (q(main), q(r["branch"]), q(r["path"])))
    else:
        out.append("test -z \"$(git -C %s status --porcelain)\" && git -C %s merge --ff-only \"$BASE\"" % (
            q(r["path"]), q(r["path"])))
    out.append("pai worktree verify %s --base \"$BASE\"" % q(r["path"]))
    return out


def verify(path, base):
    """Does the worktree at `path` contain `base` (a SHA or a ref)? The check a dispatch relies on."""
    wt = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    res = {"path": wt, "base": None, "head": None, "branch": None, "contains_base": False, "uncommitted": None,
           "problems": []}
    if not os.path.isdir(wt) or not gitprep.toplevel(wt):
        res["problems"].append("%s is not a Git worktree" % wt)
        return res
    sha = gitprep.rev(wt, base)
    head = gitprep.rev(wt, "HEAD")
    res.update(base=sha, head=head, branch=gitprep.current_branch(wt), uncommitted=len(gitprep.dirty(wt)))
    if not sha:
        res["problems"].append("%s is not a commit in this repository (was the fetch run?)" % base)
    elif not head:
        res["problems"].append("the worktree has no HEAD commit")
    elif not gitprep.is_ancestor(wt, sha, head):
        res["problems"].append("HEAD %s does not contain the base %s: the fetch updated refs, not this worktree"
                               % (head[:12], sha[:12]))
    else:
        res["contains_base"] = True
    if res["branch"] is None and head:
        res["problems"].append("HEAD is detached")
    return res


def render_list(info):
    r = info["repository"]
    lines = ["repository  %s" % r["path"], "remote      %s %s%s" % (r["remote"], r["url"], " (github.com/%s)" % r["github"]
                                                                     if r["github"] else ""),
             "base        %s/%s last fetched here: %s" % (r["remote"], info["base"]["branch"],
                                                          info["base"]["last_fetched"] or "never")]
    for w in info["worktrees"]:
        state = "missing" if not w["exists"] else ("%d uncommitted" % w["uncommitted"] if w["uncommitted"] else "clean")
        lines.append("worktree    %s\n            branch %s, HEAD %s, %s%s%s" % (
            w["path"], w["branch"] or "(detached)", (w["head"] or "?")[:12], state,
            ", contains the last fetched base" if w.get("contains_last_fetched_base") else "",
            ", %d commit(s) not in the fetched base" % w["ahead_of_base"] if w.get("ahead_of_base") else ""))
    return "\n".join(lines)


def render_plan(info, shells=("bash", "powershell")):
    r = info["recommendation"]
    lines = [render_list(info), "",
             "plan        %s: %s" % (r["kind"], r["path"]) + (" (new branch %s)" % r["branch"] if r["kind"] == "new-worktree"
                                                              else ""),
             "            " + "; ".join(r["reasons"]),
             "",
             "Nothing was fetched or changed. Run these yourself, in order (the fetch updates refs only; the last",
             "command checks that the worktree itself contains the fetched base before any dispatch):"]
    for s in shells:
        lines.append("\n%s:" % ("Bash" if s == "bash" else "PowerShell"))
        lines += ["  " + c for c in info["commands"][s]]
    return "\n".join(lines)


def parser():
    """The `pai worktree` command line (also read by tools/command_inventory.py)."""
    import argparse
    ap = argparse.ArgumentParser(prog="pai worktree", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="wcmd", required=True)
    ls = sub.add_parser("list", help="every worktree: repository, branch, HEAD, uncommitted paths")
    ls.add_argument("--repo")
    ls.add_argument("--remote", default="origin")
    ls.add_argument("--base", default="main")
    ls.add_argument("--json", action="store_true")
    rf = sub.add_parser("refresh", help="print the owner-run fetch and worktree commands after a merge (reads only)")
    rf.add_argument("--repo")
    rf.add_argument("--remote", default="origin")
    rf.add_argument("--base", default="main")
    g = rf.add_mutually_exclusive_group()
    g.add_argument("--worktree", help="move this clean worktree forward instead (only if the base contains it)")
    g.add_argument("--new", help="path for the fresh worktree (default: next to the main checkout)")
    rf.add_argument("--branch", help="branch for the fresh worktree (default postmerge/YYYYMMDD)")
    rf.add_argument("--shell", choices=("bash", "powershell", "both"), default="both")
    rf.add_argument("--json", action="store_true")
    vf = sub.add_parser("verify", help="check that a worktree's HEAD contains the fetched base")
    vf.add_argument("path")
    vf.add_argument("--base", required=True, help="the fetched base SHA (or a ref such as origin/main)")
    vf.add_argument("--json", action="store_true")
    pv = sub.add_parser("preview", help="read-only PR preview of an existing worktree (and --out PLAN.json)")
    pv.add_argument("path")
    pv.add_argument("--path", dest="paths", action="append", default=[], metavar="FILE",
                    help="an approved path, relative to the worktree (repeatable; literal, no globs)")
    pv.add_argument("--title", required=True)
    pv.add_argument("--body-file")
    pv.add_argument("--message-file", help="commit message (default: title, blank line, body)")
    pv.add_argument("--branch", help="the branch the PR comes from (default: the worktree's; must match)")
    pv.add_argument("--base", default="main")
    pv.add_argument("--remote", default="origin")
    pv.add_argument("--github", metavar="OWNER/REPO", help="when the remote URL is not a GitHub URL")
    pv.add_argument("--offline", action="store_true", help="no ls-remote and no gh: use the last fetched refs only")
    pv.add_argument("--json", action="store_true")
    pv.add_argument("--out", help="write the plan here (a new file) for `pai worktree publish`")
    pb = sub.add_parser("publish", help="commit the approved paths, push without force, open or reuse the PR")
    pb.add_argument("plan")
    pb.add_argument("--yes", action="store_true", help="do it (without this nothing is done)")
    st = pb.add_mutually_exclusive_group()
    st.add_argument("--draft", dest="draft", action="store_const", const=True)
    st.add_argument("--ready", dest="draft", action="store_const", const=False)
    pb.add_argument("--no-pr", action="store_true", help="stop after the push")
    pb.add_argument("--expect-plan", metavar="SHA256", help="the reviewed plan's sha256 (the preview prints it; at least "
                    "12 hex digits): refuse any other plan file")
    it = sub.add_parser("init", help="this worktree's AGENTS.md section: goal, scope, checks, owner rules (preview; --write)")
    it.add_argument("path")
    it.add_argument("--goal", required=True)
    it.add_argument("--scope", action="append", default=[], metavar="DIR", help="directory the worker may change (repeatable)")
    it.add_argument("--owner")
    it.add_argument("--lane")
    it.add_argument("--check", action="append", default=[], metavar="NAME=COMMAND")
    it.add_argument("--file", default="AGENTS.md", help="instruction file name at the worktree root (default AGENTS.md)")
    it.add_argument("--profile", help="a rule profile exported earlier")
    it.add_argument("--jev", action="store_true", help="let JEV score the rule library for this goal (optional)")
    it.add_argument("--accept", action="append", default=[], metavar="RULE", help="add a proposed rule (repeatable)")
    it.add_argument("--reserve", action="append", default=[], choices=("implementation", "testing", "design"),
                    help="keep this kind of decision with the owner (repeatable; by default the worker owns all three)")
    it.add_argument("--export-profile", metavar="FILE", help="write the accepted rules and checks as a profile (with --write)")
    it.add_argument("--write", action="store_true")
    it.add_argument("--json", action="store_true")
    wb = sub.add_parser("web", help="the worktrees in the browser: instruction files, refresh state, split plan, PR preview")
    wb.add_argument("--repo", default=".")
    wb.add_argument("--remote", default="origin")
    wb.add_argument("--base", default="main")
    wb.add_argument("--queues", help="folder of the worker queues (`pai plan accept --queues`), to show their states")
    wb.add_argument("--port", type=int, default=0)
    wb.add_argument("--no-browser", action="store_true")
    return ap


def main(argv=None):
    ap = parser()
    a = ap.parse_args(argv)
    try:
        if a.wcmd == "web":
            if a.port and not 1024 <= a.port <= 65535:
                print("pai worktree web: --port must be between 1024 and 65535", file=sys.stderr)
                return 2
            import wtweb
            return wtweb.main(a.repo, a.queues, a.port, a.no_browser, remote=a.remote, base=a.base)
        if a.wcmd == "init":
            import agentsmd
            try:
                checks = []
                for c in a.check:
                    name, sep, cmd = c.partition("=")
                    if not sep or not name.strip() or not cmd.strip():
                        raise agentsmd.InitError("--check looks like NAME=COMMAND, not %r" % c)
                    checks.append((name.strip(), cmd.strip()))
                p = agentsmd.plan(a.path, a.goal, a.scope, a.owner, a.lane, checks, a.file, a.jev, a.accept, a.profile,
                                  reserve=a.reserve)
                if a.json:
                    print(json.dumps({k: v for k, v in p.items() if k != "new"}, indent=1))
                else:
                    print(agentsmd.render(p))
                if a.export_profile:
                    text, removed = agentsmd.profile(p, checks)
                    if a.write:
                        fd = os.open(a.export_profile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(text + "\n")
                        print("profile     %s written%s" % (a.export_profile, " (removed: %s)" % ", ".join(removed) if removed else ""))
                    else:
                        print("profile     would write %s:\n%s" % (a.export_profile, text))
                if a.write:
                    wrote = agentsmd.write(p)
                    print("\n%s %s" % ("wrote" if wrote else "unchanged:", p["target"]))
                elif not a.json:
                    print("\nPREVIEW ONLY: nothing was written. Add --write to write %s." % p["file"])
                return 0
            except (agentsmd.InitError, OSError, ValueError) as e:
                print("pai worktree init: %s" % e, file=sys.stderr)
                return 2
        if a.wcmd in ("preview", "publish"):
            import wtpr
            try:
                if a.wcmd == "publish":
                    return wtpr.publish(a.plan, a.yes, a.draft, a.no_pr, expect=a.expect_plan)
                def read(p):
                    if not p:
                        return ""
                    with open(p, encoding="utf-8") as f:
                        return f.read()
                info = wtpr.preview(a.path, a.paths, a.title, read(a.body_file), read(a.message_file) or None,
                                    a.branch, a.base, a.remote, a.offline, a.github)
            except wtpr.PlanError as e:
                print("pai worktree: %s" % e, file=sys.stderr)
                return 2
            print(json.dumps(info, indent=1) if a.json else wtpr.render(info))
            if a.out and not info["problems"]:
                wtpr.save(info, a.out)
                if not a.json:
                    dig = wtpr.plan_digest(a.out)[:16]
                    print("plan        %s (sha256 %s; review it, then: pai worktree publish %s --expect-plan %s --yes "
                          "--draft|--ready)" % (a.out, dig, shlex.quote(a.out), dig))
            return 1 if info["problems"] else 0
        if a.wcmd == "list":
            info = describe(a.repo, a.remote, a.base)
            print(json.dumps(info, indent=1) if a.json else render_list(info))
            return 0
        if a.wcmd == "refresh":
            info = plan(a.repo, a.remote, a.base, a.worktree, a.new, a.branch)
            shells = ("bash", "powershell") if a.shell == "both" else (a.shell,)
            print(json.dumps(info, indent=1) if a.json else render_plan(info, shells))
            return 0
        res = verify(a.path, a.base)
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            print("worktree    %s\nbranch      %s\nHEAD        %s\nbase        %s\nresult      %s" % (
                res["path"], res["branch"] or "(detached)", res["head"], res["base"],
                "contains the base" if res["contains_base"] else "NOT READY"))
            for p in res["problems"]:
                print("PROBLEM     %s" % p)
            if res["uncommitted"]:
                print("note        %d uncommitted path(s), left as they are" % res["uncommitted"])
        return 0 if res["contains_base"] and not res["problems"] else 1
    except RefreshError as e:
        print("pai worktree: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
