"""A pull request from an existing worktree, in two explicit steps.

    pai worktree preview PATH --path P [--path P ...] --title T [--body-file F] [--message-file F]
                         [--branch B] [--base main] [--remote origin] [--offline] [--json] [--out PLAN.json]
    pai worktree publish PLAN.json [--yes] [--draft | --ready] [--no-pr]

`preview` reads only: no file, index or ref changes (Git runs with optional locks off; only
`--out` writes, a new file). It shows the worktree, its repository and branch, the PR base
resolved on its own (the base's last fetched SHA and how far HEAD is from it), whether the
branch is already on the remote (a read-only `git ls-remote`) and any PR for this repository,
base and head (`gh pr list`), the approved paths apart from the staged paths and from every
unexpected change, cache files in the proposal, and the title, body and commit message as
drafts with the publication check applied. A detached HEAD or a different branch is refused.

`publish` changes nothing without --yes. It first checks that nothing drifted since the
preview (HEAD, the approved files' contents, the staged set, the remote's identity), then
stages only the approved literal paths, commits them with the reviewed message and the
repository's hooks (other staged changes stay staged and out of the commit), pushes the
branch without force, reads the remote's SHA back, and only then opens a PR (or reuses the
matching one) as --draft or --ready, which must be chosen. Each step is recorded in
PLAN.json.journal; a rerun after an interruption continues from the last completed step and
never commits, pushes or opens anything twice.

Approval is a hard gate. Before any push or PR step, on every run (the first one, a retry that
reads the journal, or one that adopts a commit an interrupted run made), the commit itself is
checked against the approved preview: its only parent is the previewed HEAD, it changes exactly
the approved paths, each approved path is exactly the previewed tree entry (type, mode and
content: a symbolic link is its target path, mode 120000), and its message is the reviewed one.
Directories, submodules and special files are refused at preview, before anything is committed. Anything else (a hook that added a file, a different staged version, an
edited message) stops publication with exit 1: nothing is pushed and no PR is opened, the local
commit stays as it is, and the mismatch is listed. A journal entry never stands in for this check.
"""
import hashlib
import json
import os
import re
import posixpath
import shlex
import stat as stat_mod
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import gitprep  # noqa: E402
import publish_check  # noqa: E402

PLAN_FORMAT = "pai-worktree-pr-plan"
CACHE_RE = re.compile(r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|node_modules|\.tox|\.venv|venv|"
                      r"dist|build|[^/]+\.egg-info|\.cache)(/|$)|\.py[co]$|(^|/)\.DS_Store$|(^|/)\.coverage$|"
                      r"(^|/)coverage\.xml$")


class PlanError(Exception):
    pass


def git_ro(wt, *args):
    """Read-only git (optional locks off, so not even the index stat cache is refreshed)."""
    try:
        p = gitprep.git(wt, *args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def status(wt):
    """{path: XY} for every change, untracked files included, renames by their new path."""
    out = git_ro(wt, "status", "--porcelain=v1", "-z", "--untracked-files=all") or ""
    res, parts, i = {}, out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            xy, path = entry[:2], entry[3:]
            res[path] = xy
            if xy[0] in "RC":
                i += 1  # the original path follows a rename or copy
        i += 1
    return res


KINDS = {"100644": "file", "100755": "executable file", "120000": "symbolic link", "160000": "submodule",
         "040000": "directory"}


def kind(mode):
    return "%s (%s)" % (mode, KINDS.get(mode, "unknown type")) if mode else "absent"


def _git_input(wt, args, data):
    """git with `data` (bytes) on stdin and the same clean environment as gitprep.git; stdout or None."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    try:
        p = subprocess.run([gitprep.git_bin(), "-C", wt] + list(args), input=data, capture_output=True, timeout=60,
                           env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


def _config_bool(wt, key, default=True):
    out = (git_ro(wt, "config", "--bool", key) or "").strip()
    return default if not out else out == "true"


def _index_mode(wt, path):
    out = git_ro(wt, "ls-files", "-s", "-z", "--", ":(literal)" + path) or ""
    for item in out.split("\0"):
        meta, _, name = item.partition("\t")
        if name == path and meta:
            return meta.split()[0]
    return None


def entry(wt, path):
    """What `git add` would record for `path` from the working tree, computed without writing
    anything: {"mode": "100644" | "100755" | "120000", "blob": object id}, None when the path is
    absent, or {"mode": None, "blob": None, "unsupported": WHAT} for a directory (or submodule) or a
    special file. A symbolic link is its target path, stored as mode 120000 (never the bytes of
    the file it points to); a file's executable bit counts only where core.fileMode is on, and
    where core.symlinks is off a link checked out as a file keeps the index's 120000, as in Git."""
    full = os.path.join(wt, path)
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if stat_mod.S_ISLNK(st.st_mode):
        out = _git_input(wt, ["hash-object", "--no-filters", "--stdin"], os.readlink(os.fsencode(full)))
        return {"mode": "120000", "blob": out.strip() if out else None}
    if not stat_mod.S_ISREG(st.st_mode):
        return {"mode": None, "blob": None,
                "unsupported": "a directory or a submodule" if stat_mod.S_ISDIR(st.st_mode) else "a special file"}
    out = git_ro(wt, "hash-object", "--no-filters", "--", path)
    mode = "100755" if st.st_mode & stat_mod.S_IXUSR else "100644"
    if not _config_bool(wt, "core.fileMode") or not _config_bool(wt, "core.symlinks"):
        was = _index_mode(wt, path)
        if was == "120000" and not _config_bool(wt, "core.symlinks"):
            mode = "120000"
        elif not _config_bool(wt, "core.fileMode"):
            mode = was if was in ("100644", "100755") else "100644"
    return {"mode": mode, "blob": out.strip() if out else None}


def blob(wt, path):
    """The working-tree content id of `path` as Git would store it unfiltered (a link: its target), None when absent."""
    return (entry(wt, path) or {}).get("blob")


def tree_entry(wt, sha, path):
    """{"mode", "blob"} of `path` in commit `sha` (the tree entry itself, not followed), None when absent."""
    d, name = posixpath.split(path)
    out = git_ro(wt, "ls-tree", "-z", "%s:%s" % (sha, d) if d else "%s^{tree}" % sha)
    for item in (out or "").split("\0"):
        meta, _, n = item.partition("\t")
        if n == name and meta:
            mode, _typ, oid = meta.split()
            return {"mode": mode, "blob": oid}
    return None


def remote_branch(wt, remote, branch, offline):
    """(sha or None, how it was learned)."""
    if offline:
        sha = gitprep.rev(wt, "refs/remotes/%s/%s" % (remote, branch))
        return sha, "local tracking ref (offline)"
    sha, err = gitprep.ls_remote(wt, remote, branch)
    if err and "has no branch" not in err:
        return None, "unknown: %s" % err
    return sha, "git ls-remote"


def existing_prs(slug, branch, base, offline):
    if offline or not slug:
        return None, "not asked (%s)" % ("offline" if offline else "not a GitHub remote")
    import rc
    try:
        prs = rc.prs_for(slug, branch)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
        return None, "could not ask GitHub: %s" % e
    return [p for p in prs if p.get("baseRefName") == base], "gh pr list"


def text_findings(title, body, message):
    found = []
    for field, text in (("PR title", title), ("PR body", body), ("commit message", message)):
        found += [(f["field"], "line %d: %s" % (f["line"], f["detail"])) for f in publish_check.check_text(field, text or "")]
    try:
        sys.path.insert(0, os.path.dirname(HERE))
        from pai import privacy
        for field, text in (("PR title", title), ("PR body", body), ("commit message", message)):
            for line, kind, _match in privacy.findings(text or ""):
                found.append((field, "line %d: %s (not shown)" % (line, kind)))
    except ImportError:
        pass
    return found


def preview(path, paths, title, body="", message=None, branch=None, base="main", remote="origin", offline=False,
            github=None):
    """Everything an owner needs before publishing; reads only."""
    wt = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    top = gitprep.toplevel(wt) if os.path.isdir(wt) else None
    if top != wt:
        raise PlanError("%s is not the top of a Git worktree" % wt)
    problems, warnings = [], []
    current = gitprep.current_branch(wt)
    head = gitprep.rev(wt, "HEAD")
    if current is None:
        problems.append("HEAD is detached: check out the branch the PR comes from")
    if branch and current and branch != current:
        problems.append("the worktree is on %s, not %s" % (current, branch))
    branch = branch or current
    url = gitprep.out(wt, "remote", "get-url", "--push", remote)
    if not url:
        raise PlanError("the repository has no remote named %s" % remote)
    slug = github or gitprep.github_slug(url)
    tracking = "refs/remotes/%s/%s" % (remote, base)
    base_sha = gitprep.rev(wt, tracking)
    base_info = {"branch": base, "tracking_ref": tracking, "last_fetched": base_sha,
                 "merge_base": (git_ro(wt, "merge-base", base_sha, "HEAD") or "").strip() or None if base_sha else None,
                 "ahead": gitprep.ahead(wt, base_sha) if base_sha else None,
                 "head_contains_base": bool(base_sha and head and gitprep.is_ancestor(wt, base_sha, head))}
    if not base_sha:
        problems.append("%s is not known here: fetch the base first (see `pai worktree refresh`)" % tracking)
    rsha, how = remote_branch(wt, remote, branch, offline) if branch else (None, "no branch")
    pushed = {"sha": rsha, "how": how, "state": "not on the remote" if not rsha else
              ("at HEAD" if rsha == head else ("behind HEAD" if gitprep.is_ancestor(wt, rsha, head or rsha) and rsha != head
                                               else "different from HEAD"))}
    if pushed["state"] == "different from HEAD":
        problems.append("%s on the remote is %s, which HEAD does not contain; nothing is forced" % (branch, rsha[:12]))
    st = status(wt)
    approved = []
    for p in paths:
        rel = os.path.normpath(p).replace(os.sep, "/")
        if os.path.isabs(p) or rel == "." or rel.startswith("../") or rel == ".." or rel.startswith(".git/") or rel == ".git":
            problems.append("approved path %s is not a path inside the worktree" % p)
            continue
        approved.append(rel)
    staged = sorted(p for p, xy in st.items() if xy[0] not in " ?")
    rows = []
    for p in approved:
        xy = st.get(p)
        e = entry(wt, p) or {}
        if e.get("unsupported"):
            problems.append("approved path %s is %s: publish commits regular files and symbolic links only; approve "
                            "the files inside it, or commit it yourself" % (p, e["unsupported"]))
        rows.append({"path": p, "status": xy or "unchanged", "blob": e.get("blob"), "mode": e.get("mode"),
                     "partly_staged": bool(xy and xy[0] not in " ?" and xy[1] not in " ")})
        if not xy:
            warnings.append("approved path %s has no change to commit" % p)
    unexpected = sorted(p for p in st if p not in approved)
    staged_unrelated = [p for p in staged if p not in approved]
    caches = sorted(p for p in approved + unexpected if CACHE_RE.search(p))
    for p in caches:
        (problems if p in approved else warnings).append("generated cache %s %s" % (
            p, "is in the approved paths: remove it from the proposal" if p in approved else "is present (not approved)"))
    for r in rows:
        if r["partly_staged"]:
            warnings.append("%s has staged and unstaged changes; the commit takes the working-tree file as previewed"
                            % r["path"])
    message = message or (title + ("\n\n" + body.strip() if body and body.strip() else ""))
    found = text_findings(title, body, message)
    for field, why in found:
        problems.append("%s: %s" % (field, why))
    prs, prs_how = existing_prs(slug, branch, base, offline) if branch else (None, "no branch")
    return {"format": PLAN_FORMAT, "version": 1, "worktree": wt, "repository": gitprep.common_dir(wt),
            "remote": {"name": remote, "push_url": gitprep.redact_url(url), "github": slug},
            "branch": branch, "head": head, "base": base_info, "pushed": pushed,
            "paths": {"approved": rows, "staged": staged, "staged_unrelated": staged_unrelated, "unexpected": unexpected},
            "caches": caches, "existing_prs": prs, "existing_prs_how": prs_how,
            "draft": {"title": title, "body": body, "message": message}, "problems": problems, "warnings": warnings}


def render(pv):
    b, pu, pa = pv["base"], pv["pushed"], pv["paths"]
    out = ["worktree    %s" % pv["worktree"], "repository  %s (remote %s %s)" % (
        pv["repository"], pv["remote"]["name"], pv["remote"]["push_url"]),
        "branch      %s at %s" % (pv["branch"] or "(detached)", (pv["head"] or "?")[:12]),
        "base        %s, last fetched %s; HEAD is %s commit(s) ahead%s" % (
            b["tracking_ref"], (b["last_fetched"] or "never")[:12], b["ahead"], "" if b["head_contains_base"] else
            " and does NOT contain it"),
        "remote      %s (%s)" % (pu["state"], pu["how"]),
        "existing PR %s" % (", ".join("%s %s" % (p["url"], p["state"]) for p in pv["existing_prs"])
                            if pv["existing_prs"] else "none" if pv["existing_prs"] == [] else pv["existing_prs_how"]),
        "approved    %s" % (", ".join("%s [%s]" % (r["path"], r["status"].strip() or "?") for r in pa["approved"]) or "none"),
        "staged      %s" % (", ".join(pa["staged"]) or "none"),
        "unexpected  %s" % (", ".join(pa["unexpected"]) or "none"),
        "", "title       %s" % publish_check.redact(pv["draft"]["title"]), "body:"]
    out += ["  " + l for l in publish_check.redact(pv["draft"]["body"] or "(empty)").splitlines()]
    out += ["commit message:"] + ["  " + l for l in publish_check.redact(pv["draft"]["message"]).splitlines()]
    out += ["WARNING     %s" % w for w in pv["warnings"]] + ["REFUSED     %s" % p for p in pv["problems"]]
    out.append("\nPREVIEW ONLY: no file, index or ref was changed.%s" % (
        " Fix the REFUSED lines first." if pv["problems"] else " `pai worktree publish PLAN --yes` would publish this."))
    return "\n".join(out)


def save(pv, out):
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(pv, f, indent=1)


def _journal(plan_path):
    p = plan_path + ".journal"
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"steps": {}}


def _save_journal(plan_path, j):
    p = plan_path + ".journal"
    tmp = p + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(j, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def drift(plan, now, journal):
    """What changed since the preview, other than this flow's own recorded commit."""
    out = []
    commit = journal["steps"].get("commit", {}).get("sha")
    if now["head"] != plan["head"] and now["head"] != commit:
        out.append("HEAD moved from %s to %s" % ((plan["head"] or "?")[:12], (now["head"] or "?")[:12]))
    if now["branch"] != plan["branch"]:
        out.append("the branch is now %s, not %s" % (now["branch"], plan["branch"]))
    if now["remote"]["push_url"] != plan["remote"]["push_url"]:
        out.append("the remote's push URL changed (%s)" % now["remote"]["push_url"])
    was = {r["path"]: r for r in plan["paths"]["approved"]}
    for r in now["paths"]["approved"]:
        before = was.get(r["path"]) or {}
        if r["blob"] != before.get("blob"):
            out.append("%s changed since the preview" % r["path"])
        elif "mode" in before and r.get("mode") != before["mode"]:  # plans made before modes were recorded lack it
            out.append("%s changed type or mode since the preview: %s, now %s" % (
                r["path"], kind(before["mode"]), kind(r.get("mode"))))
    if not commit and now["paths"]["staged_unrelated"] != plan["paths"]["staged_unrelated"]:
        out.append("other staged paths changed: %s" % ", ".join(now["paths"]["staged_unrelated"]))
    return out


def tidy(wt, text):
    """A commit message as `git commit -F` stores it (whitespace clean-up), for comparing."""
    try:
        p = subprocess.run([gitprep.git_bin(), "-C", wt, "stripspace"], input=text or "", capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return (text or "").strip()
    return p.stdout.strip() if p.returncode == 0 else (text or "").strip()


def same_message(plan, sha):
    wt = plan["worktree"]
    return tidy(wt, git_ro(wt, "log", "-1", "--format=%B", sha)) == tidy(wt, plan["draft"]["message"])


def changed_paths(wt, sha):
    out = git_ro(wt, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", sha) or ""
    return sorted(p for p in out.split("\0") if p)


def commit_mismatch(plan, sha):
    """Why commit `sha` is not exactly what the preview approved ([] when it is): its only parent
    must be the previewed HEAD, it must change exactly the approved paths with their previewed
    contents, and carry the reviewed message. Read from Git every time, never from the journal."""
    wt, out = plan["worktree"], []
    parents = (git_ro(wt, "rev-list", "--parents", "-n", "1", sha) or "").split()[1:]
    if parents != [plan["head"]]:
        out.append("its parent is %s, not the previewed HEAD %s" % (
            ", ".join(p[:12] for p in parents) or "none", (plan["head"] or "?")[:12]))
    rows = [r for r in plan["paths"]["approved"] if r["status"] != "unchanged"]
    todo = {r["path"] for r in rows}
    changed = set(changed_paths(wt, sha))
    extra, missing = sorted(changed - todo), sorted(todo - changed)
    if extra:
        out.append("it changes path(s) outside the approved preview: %s" % ", ".join(extra))
    if missing:
        out.append("it leaves out approved path(s): %s" % ", ".join(missing))
    for r in rows:
        if r["path"] in missing:
            continue
        got = tree_entry(wt, sha, r["path"]) or {}
        have, have_mode = got.get("blob"), got.get("mode")
        # the previewed mode; a plan made before modes were recorded: the working tree's, which drift()
        # has just shown to hold the previewed content
        want_mode = r["mode"] if "mode" in r else (entry(wt, r["path"]) or {}).get("mode")
        if have_mode != want_mode:
            out.append("%s in the commit is %s, not the previewed %s" % (r["path"], kind(have_mode), kind(want_mode)))
        if have == r["blob"]:
            continue
        if have and r["blob"] and want_mode != "120000" and have_mode == want_mode and blob(wt, r["path"]) == r["blob"]:
            # a clean filter (line endings, attributes) stores other bytes than the working file:
            # compare with what `git add` stores for the previewed file, which is still on disk
            if (git_ro(wt, "hash-object", "--", r["path"]) or "").strip() == have:
                continue
        out.append("%s in the commit is %s, not the previewed content %s" % (
            r["path"], (have or "absent")[:12], (r["blob"] or "absent")[:12]))
    if not same_message(plan, sha):
        out.append("its message is not the reviewed one")
    return out


def refuse_commit(plan, sha, why, log, how):
    wt = plan["worktree"]
    log("REFUSED     commit %s (%s) is not the commit the preview approved:" % (sha[:12], how))
    for w in why:
        log("              - %s" % w)
    q = shlex.quote(wt)
    log("            Nothing was pushed and no PR was opened; the commit stays in your local branch as it is.\n"
        "            Inspect it: git -C %s show --stat %s\n"
        "            To publish only the approved work, undo it yourself (for example git -C %s reset --soft %s), "
        "then preview again." % (q, sha[:12], q, (plan["head"] or "HEAD~1")[:12]))
    return 1


def adopt(plan, journal):
    """The journal entry for a commit this plan made when the run stopped before recording it
    (killed between `git commit` and the journal write): HEAD's only parent is the previewed
    HEAD and its message is the reviewed one. Else None. Adopting it authorizes nothing: the
    commit still has to pass commit_mismatch() before anything is pushed."""
    if journal["steps"].get("commit"):
        return None
    wt = plan["worktree"]
    head = gitprep.rev(wt, "HEAD")
    if not head or head == plan["head"]:
        return None
    parents = (git_ro(wt, "rev-list", "--parents", "-n", "1", head) or "").split()
    if parents[1:] == [plan["head"]] and same_message(plan, head):
        return {"sha": head, "paths": changed_paths(wt, head), "adopted": True}
    return None


def publish(plan_path, yes=False, draft=None, no_pr=False, log=print):
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    if plan.get("format") != PLAN_FORMAT:
        raise PlanError("%s is not a worktree PR plan" % plan_path)
    j = _journal(plan_path)
    wt, branch, remote = plan["worktree"], plan["branch"], plan["remote"]["name"]
    d = plan["draft"]
    adopted = adopt(plan, j)
    if adopted:
        why = commit_mismatch(plan, adopted["sha"])
        if why:
            return refuse_commit(plan, adopted["sha"], why, log, "made before an interruption")
        j["steps"]["commit"] = adopted
        if yes:
            _save_journal(plan_path, j)
        log("resumed     %s is this plan's commit (made before an interruption); it is not made again" % adopted["sha"][:12])
    now = preview(wt, [r["path"] for r in plan["paths"]["approved"]], d["title"], d["body"], d["message"], branch,
                  plan["base"]["branch"], remote, github=plan["remote"]["github"])
    blocking = now["problems"]
    moved = drift(plan, now, j)
    for line in moved:
        log("DRIFT       %s" % line)
    for p in blocking:
        log("REFUSED     %s" % p)
    if moved or blocking:
        log("\npai worktree publish: nothing was staged, committed, pushed or opened. Preview again.")
        return 1
    recorded = j["steps"].get("commit", {}).get("sha")
    if recorded and recorded == now["head"] and not adopted:  # an earlier run's commit: checked again, every time
        why = commit_mismatch(plan, recorded)
        if why:
            return refuse_commit(plan, recorded, why, log, "recorded in the journal by an earlier run")
    todo = [r["path"] for r in plan["paths"]["approved"] if r["status"] != "unchanged"]
    log("plan        %s -> %s %s (base %s)" % (wt, remote, branch, plan["base"]["branch"]))
    if not yes:
        log("would       commit %s; push %s without force; %s" % (", ".join(todo) or "nothing new", branch,
                                                                 "no PR" if no_pr else "open or reuse the PR"))
        log("\nNothing was done: add --yes to publish (and --draft or --ready for a new PR).")
        return 1
    if not no_pr and not plan["remote"]["github"]:
        log("REFUSED     %s is not a GitHub remote, so no PR can be opened: preview again with --github OWNER/REPO, "
            "or publish with --no-pr. Nothing was done." % plan["remote"]["push_url"])
        return 1
    if not no_pr and draft is None and not j["steps"].get("pr"):
        log("REFUSED     choose --draft or --ready: the PR's state is the owner's choice. Nothing was done.")
        return 1
    # 1. commit, once
    made_now = False
    if todo and not j["steps"].get("commit"):
        lit = dict(os.environ, GIT_LITERAL_PATHSPECS="1")
        st = status(wt)
        untracked = [p for p in todo if st.get(p) == "??"]
        if untracked:
            r = subprocess.run([gitprep.git_bin(), "-C", wt, "add", "--"] + untracked, env=lit, capture_output=True, text=True)
            if r.returncode:
                log("FAILED      git add: %s" % r.stderr.strip())
                return 1
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".msg", encoding="utf-8") as f:
            f.write(d["message"].rstrip("\n") + "\n")
            msg = f.name
        try:
            r = subprocess.run([gitprep.git_bin(), "-C", wt, "commit", "--only", "-F", msg, "--"] + todo, env=lit,
                               capture_output=True, text=True)
        finally:
            os.remove(msg)
        if r.returncode:
            if untracked:  # put the index back as it was: those files were untracked (they stay on disk)
                subprocess.run([gitprep.git_bin(), "-C", wt, "rm", "-q", "--cached", "--"] + untracked, env=lit,
                               capture_output=True, text=True)
            log("FAILED      git commit (hooks run): %s\nNothing was committed or pushed%s." % (
                (r.stderr.strip() or r.stdout.strip())[-600:], "; %s untracked again" % ", ".join(untracked) if untracked else ""))
            return 1
        sha = gitprep.rev(wt, "HEAD")
        changed = changed_paths(wt, sha)
        j["steps"]["commit"] = {"sha": sha, "paths": changed}
        _save_journal(plan_path, j)  # a record that this run committed (so it never commits again), not an approval
        made_now = True
        log("committed   %s (%s)" % (sha[:12], ", ".join(changed)))
    head = gitprep.rev(wt, "HEAD")
    # the gate: on every run, the commit about to be published is checked against the preview
    if head != plan["head"]:
        why = commit_mismatch(plan, head)
        if why:
            return refuse_commit(plan, head, why, log, "made by this run" if made_now else
                                 "recorded in the journal by an earlier run")
    # 2. push without force, then read the remote back
    rsha, _ = remote_branch(wt, remote, branch, False)
    if rsha != head:
        try:
            p = gitprep.git_net(wt, "push", remote, "%s:refs/heads/%s" % (head, branch), timeout=300)
        except subprocess.TimeoutExpired:
            log("FAILED      push timed out; rerun to continue (nothing is forced)")
            return 1
        if p.returncode:
            log("FAILED      push (never forced): %s" % gitprep.redact_url(p.stderr.strip())[-400:])
            return 1
        rsha, _ = remote_branch(wt, remote, branch, False)
    if rsha != head:
        log("REFUSED     the remote has %s for %s, not %s: no PR is opened" % ((rsha or "nothing")[:12], branch, head[:12]))
        return 1
    j["steps"]["push"] = {"sha": head}
    _save_journal(plan_path, j)
    log("pushed      %s %s = %s (read back from the remote)" % (remote, branch, head[:12]))
    if no_pr:
        log("\nNo PR was opened (--no-pr).")
        return 0
    # 3. PR: reuse a matching one, else open one, after the explicit choice
    slug, base = plan["remote"]["github"], plan["base"]["branch"]
    prs, how = existing_prs(slug, branch, base, False)
    if prs is None:
        log("FAILED      %s; the branch is pushed, rerun to open the PR" % how)
        return 1
    open_prs = [p for p in prs if p["state"] == "OPEN"]
    if open_prs:
        url = open_prs[0]["url"]
        log("reused      %s" % url)
    else:
        import rc
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".md", encoding="utf-8") as f:
            f.write(d["body"] or d["title"])
            body = f.name
        try:
            url = rc.gh("pr", "create", "--repo", slug, "--base", base, "--head", branch, "--title=" + d["title"],
                        "--body-file", body, *(["--draft"] if draft else [])).strip().splitlines()[-1]
        except (RuntimeError, IndexError, subprocess.TimeoutExpired) as e:
            log("FAILED      the branch is pushed but the PR was not opened (%s); rerun to continue" % e)
            return 1
        finally:
            os.remove(body)
        log("opened      %s (%s)" % (url, "draft" if draft else "ready for review"))
    j["steps"]["pr"] = {"url": url}
    _save_journal(plan_path, j)
    log("\nNothing was merged; merging stays with the owner.")
    return 0
