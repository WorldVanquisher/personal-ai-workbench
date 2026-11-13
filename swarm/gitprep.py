"""Task preparation shared by the headless runner and the Remote Control launcher.

Every start is either a NEW task or a RESUME, decided by whether a task record exists
for (repository, task id):

  new     validate everything -> lock every worktree -> `git fetch <remote>
          +refs/heads/<base>:refs/remotes/<remote>/<base>` once -> pin the fetched commit
          for the whole batch -> create one new branch per lane from it (a new worktree, or
          `git switch -c` in an existing clean one). A failed fetch stops here.
  resume  no fetch. The recorded branches and the recorded base are used even if the
          remote base branch has moved; existing edits are left exactly as they are.

Nothing here resets, cleans, stashes, forces or deletes. A dirty worktree, a Git operation
in progress, an existing branch, or a branch or worktree that belongs to another task is a
refusal, reported before any lane is switched. The record (under `<home>/tasks/`) is written
before the first lane is touched and updated after each one, so a start that stops partway
resumes from where it stopped, on the same pinned base.

`plan()` is read-only (its only network use is `git ls-remote`). Standard library only.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

import wtlock

SCHEMA = "pai_swarm_task_v1"
TASK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
LANE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,23}")
IN_PROGRESS = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
               ("rebase-merge", "rebase"), ("rebase-apply", "rebase or am"), ("BISECT_LOG", "bisect"),
               ("index.lock", "another git process (index.lock)"))


class Blocked(Exception):
    """Preparation refused or failed. Nothing was started."""

    def __init__(self, problems, plan=None):
        self.problems, self.plan = list(problems), plan
        super().__init__("; ".join(self.problems))


# ---------------------------------------------------------------- git helpers

def git_bin():
    return os.environ.get("PAI_SWARM_GIT") or shutil.which("git") or "git"


def git(cwd, *args, timeout=60):
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return subprocess.run([git_bin(), "-C", cwd] + list(args), capture_output=True, text=True,
                          timeout=timeout, env=env, stdin=subprocess.DEVNULL)


def out(cwd, *args):
    try:
        p = git(cwd, *args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def redact_url(url):
    return re.sub(r"(://)[^/@\s]+@", r"\1", url or "")


def canon(path):
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def under(path, parent):
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def common_dir(path):
    if not os.path.isdir(path):
        return None
    d = out(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return canon(d) if d else None


def toplevel(path):
    d = out(path, "rev-parse", "--path-format=absolute", "--show-toplevel") if os.path.isdir(path) else None
    return canon(d) if d else None


def rev(repo, name):
    return out(repo, "rev-parse", "--verify", "--quiet", name + "^{commit}")


def branch_sha(repo, branch):
    return rev(repo, "refs/heads/" + branch)


def current_branch(worktree):
    return out(worktree, "symbolic-ref", "--quiet", "--short", "HEAD")


def is_ancestor(repo, a, b):
    try:
        return git(repo, "merge-base", "--is-ancestor", a, b).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ahead(repo, base, head="HEAD"):
    n = out(repo, "rev-list", "--count", "%s..%s" % (base, head))
    return int(n) if n and n.isdigit() else None


def valid_branch(name):
    """A plain branch name. Checked as refs/heads/NAME from /, so neither a repository nor the
    current directory takes part (`--branch` set up the repository of the current directory,
    failing in a broken worktree, and expanded `@{-1}` to whatever branch was checked out last)."""
    if not name or name.startswith("-") or name != name.strip() or name == "HEAD":
        return False
    try:
        return git(os.sep, "check-ref-format", "refs/heads/" + name).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def worktree_list(repo):
    res, cur = [], None
    for line in (out(repo, "worktree", "list", "--porcelain") or "").splitlines():
        if line.startswith("worktree "):
            cur = {"path": canon(line[9:]), "branch": None, "head": None, "prunable": False}
            res.append(cur)
        elif cur is not None and line.startswith("HEAD "):
            cur["head"] = line[5:]
        elif cur is not None and line.startswith("branch refs/heads/"):
            cur["branch"] = line[len("branch refs/heads/"):]
        elif cur is not None and line.startswith("prunable"):
            cur["prunable"] = True
    return res


def dirty(worktree):
    """Changed, staged or untracked paths (ignored files do not count)."""
    try:  # not out(): stripping would eat the leading status column of the first line
        p = git(worktree, "status", "--porcelain=v1", "--untracked-files=normal")
    except (OSError, subprocess.TimeoutExpired):
        return ["?? (git status failed)"]
    return [l for l in p.stdout.splitlines() if l] if p.returncode == 0 else ["?? (git status failed)"]


def in_progress(worktree):
    gd = out(worktree, "rev-parse", "--absolute-git-dir")
    if not gd:
        return ["unreadable git directory"]
    return [label for name, label in IN_PROGRESS if os.path.lexists(os.path.join(gd, name))]


def github_slug(url):
    m = re.match(r"^(?:https://(?:[^/@]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
                 r"([^/]+/[^/]+?)(?:\.git)?/?$", url or "")
    return m.group(1).lower() if m else None


def identity_problem(url, github=None, expect_url=None):
    """The remote must be the expected repository: an exact URL when one is given (mirrors,
    non-GitHub hosts), otherwise the named GitHub repository."""
    if expect_url:
        if url.rstrip("/") != expect_url.rstrip("/"):
            return "remote URL %s is not the expected %s" % (redact_url(url), redact_url(expect_url))
    elif github and github_slug(url) != github.lower():
        return "remote URL %s is not github.com/%s" % (redact_url(url), github)
    return None


def ls_remote(repo, remote, base_branch, timeout=30):
    """(sha, None) for the remote branch head, or (None, reason). Read-only."""
    try:
        p = git(repo, "ls-remote", remote, "refs/heads/" + base_branch, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timed out after %ds" % timeout
    if p.returncode != 0:
        return None, redact_url(p.stderr.strip()[-300:]) or "exit %d" % p.returncode
    sha = p.stdout.split("\t")[0].strip()
    return (sha, None) if re.fullmatch(r"[0-9a-f]{40,64}", sha) else (None, "the remote has no branch " + base_branch)


def fetch_base(repo, remote, base_branch, timeout=180):
    """Fetch one branch with an explicit refspec that updates the tracking ref (also after
    the remote was rewritten), and return (fetched commit, tracking ref before)."""
    ref = "refs/remotes/%s/%s" % (remote, base_branch)
    before = rev(repo, ref)
    try:
        p = git(repo, "fetch", "--no-tags", "--no-recurse-submodules", remote,
                "+refs/heads/%s:%s" % (base_branch, ref), timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Blocked(["git fetch %s timed out after %ds; no worker was started" % (remote, timeout)])
    if p.returncode != 0:
        raise Blocked(["git fetch %s %s failed (exit %d): %s. No worker was started."
                       % (remote, base_branch, p.returncode, redact_url(p.stderr.strip()[-400:]))])
    sha = rev(repo, ref)
    if not sha:
        raise Blocked(["after the fetch %s does not name a commit; no worker was started" % ref])
    return sha, before


def verify_ready(worktree, branch, base_sha, exact=False):
    """The start gate: problems that forbid starting a worker in this worktree."""
    if not os.path.isdir(worktree):
        return ["worktree %s does not exist" % worktree]
    cur, head = current_branch(worktree), rev(worktree, "HEAD")
    problems = []
    if cur != branch:
        problems.append("worktree is on %s, not the task branch %s" % (cur or "a detached HEAD", branch))
    if not head:
        problems.append("worktree HEAD is not a commit")
    elif exact and head != base_sha:
        problems.append("new branch is at %s, not the pinned base %s" % (head[:12], base_sha[:12]))
    elif not is_ancestor(worktree, base_sha, head):
        problems.append("pinned base %s is not an ancestor of %s" % (base_sha[:12], branch))
    ops = in_progress(worktree)
    if ops:
        problems.append("Git operation in progress: " + ", ".join(ops))
    return problems


# ---------------------------------------------------------------- task records

def record_path(cdir, task_id):
    return os.path.join(wtlock.home(), "tasks",
                        "%s-%s.json" % (hashlib.sha256(cdir.encode()).hexdigest()[:12], task_id))


def load_record(cdir, task_id):
    try:
        with open(record_path(cdir, task_id), encoding="utf-8") as f:
            rec = json.load(f)
    except FileNotFoundError:
        return None
    if rec.get("schema") != SCHEMA or rec.get("task_id") != task_id:
        raise ValueError("task record %s is not a %s record" % (record_path(cdir, task_id), SCHEMA))
    return rec


def save_record(rec, create=False):
    p = record_path(rec["repo"]["common_dir"], rec["task_id"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = json.dumps(rec, indent=1, sort_keys=True) + "\n"
    if create:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        return
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, p)


def all_records():
    d, res = os.path.join(wtlock.home(), "tasks"), []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if fn.endswith(".json"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    rec = json.load(f)
                if rec.get("schema") == SCHEMA:
                    res.append(rec)
            except (OSError, ValueError):
                continue
    return res


def describe(rec, lane=None):
    """One line for status output and handoff evidence: remote, base, branch, worktree."""
    r = rec["repo"]
    s = "Git task %s: remote %s (%s), base %s @ %s fetched %s" % (
        rec["task_id"], r["remote"], r["remote_url"], r["base_branch"], rec["base_sha"], rec["fetched_at"])
    if lane:
        s += ", task branch %s, worktree %s" % (rec["lanes"][lane]["branch"], rec["lanes"][lane]["worktree"])
    return s


# ---------------------------------------------------------------- plan

class Lane:
    def __init__(self, lane, branch, worktree, extra=None):
        self.lane, self.branch, self.worktree = lane, branch, canon(worktree)
        self.extra = dict(extra or {})  # recorded with the lane (assigned note, branch stem, ...)
        self.action, self.problems, self.notes = None, [], []
        self.cur_branch = self.head = self.lock = None
        self.dirty = 0


class Spec:
    def __init__(self, task_id, mode, repo, lanes, remote="origin", base_branch="main",
                 github=None, url=None, worktree_root=None, want=None, meta=None, lock_owner=None):
        self.task_id, self.mode, self.repo, self.lanes = task_id, mode, canon(repo), lanes
        self.remote, self.base_branch, self.github, self.url = remote, base_branch, github, url
        self.worktree_root = canon(worktree_root) if worktree_root else None
        self.want = want  # None, "new" or "resume": what the caller expects this start to be
        self.meta = dict(meta or {})  # recorded with the task (RC run id and generation)
        self.lock_owner = lock_owner or task_id  # an RC run keeps one lock per lane across its tasks


class Plan:
    def __init__(self, spec):
        self.spec, self.problems, self.path, self.record = spec, [], None, None
        self.common_dir = self.remote_url = self.tracking_ref = None
        self.local_tracking = self.remote_tip = self.remote_error = self.base_sha = None

    @property
    def blocked(self):
        return bool(self.problems or any(l.problems for l in self.spec.lanes))

    def all_problems(self):
        return self.problems + ["lane %s: %s" % (l.lane, p) for l in self.spec.lanes for p in l.problems]


def _inspect(spec, pl, ln, registered, prepare):
    """Look at the lane's worktree. With prepare=True it is about to be created or switched
    to a new branch, so it must be absent or clean; otherwise it is kept as it is."""
    kind = "rc" if spec.mode == "rc" else "headless"
    ln.lock = wtlock.read(ln.worktree)
    if ln.lock and not wtlock.stale(ln.lock) and not (
            kind == "rc" and wtlock.owned(ln.lock, kind, spec.lock_owner, ln.lane)):
        ln.problems.append("worktree is locked by %s" % wtlock.describe(ln.lock))
    reg = registered.get(ln.worktree)
    if not os.path.lexists(ln.worktree):
        if reg:
            ln.problems.append("Git lists %s as a worktree but the directory is gone; run "
                               "`git worktree prune` yourself, then start again" % ln.worktree)
        elif not prepare:
            ln.problems.append("recorded worktree %s is missing" % ln.worktree)
        else:
            inside = [p for p in registered if under(ln.worktree, p)]
            if inside:
                ln.problems.append("%s would be created inside the worktree %s" % (ln.worktree, inside[0]))
            ln.action = "add"
        return
    if not os.path.isdir(ln.worktree) or not reg or common_dir(ln.worktree) != pl.common_dir:
        ln.problems.append("%s exists but is not a worktree of this repository; it is left untouched"
                           % ln.worktree)
        return
    ln.cur_branch, ln.head = current_branch(ln.worktree), rev(ln.worktree, "HEAD")
    ops = in_progress(ln.worktree)
    if ops:
        ln.problems.append("Git operation in progress (%s); finish or abort it yourself" % ", ".join(ops))
    d = dirty(ln.worktree)
    ln.dirty = len(d)
    if prepare:
        if d:
            ln.problems.append("worktree is dirty (%d changed or untracked path(s), first: %s); commit or "
                               "move that work yourself. Nothing is reset, cleaned or stashed."
                               % (len(d), d[0][3:]))
        ln.action = "switch"


def plan(spec, probe=True):
    """Read-only. Decides new or resume and what each lane needs; collects every refusal."""
    pl = Plan(spec)
    P = pl.problems
    if not TASK_RE.fullmatch(spec.task_id or ""):
        P.append("task id %r must match %s" % (spec.task_id, TASK_RE.pattern))
        return pl
    pl.common_dir = common_dir(spec.repo)
    if not pl.common_dir:
        P.append("not a git repository: %s" % spec.repo)
        return pl
    for what, name in (("remote", spec.remote), ("base branch", spec.base_branch)):
        if not valid_branch(name) or "/" in (name if what == "remote" else ""):
            P.append("%s name %r is not valid" % (what, name))
    if P:
        return pl
    url = out(spec.repo, "config", "--get", "remote.%s.url" % spec.remote)
    if not url:
        P.append("the repository has no remote named %s" % spec.remote)
    else:
        pl.remote_url = redact_url(url)
        ident = identity_problem(url, spec.github, spec.url)
        if ident:
            P.append("repository identity: " + ident)
    pl.tracking_ref = "refs/remotes/%s/%s" % (spec.remote, spec.base_branch)
    pl.local_tracking = rev(spec.repo, pl.tracking_ref)
    try:
        pl.record = load_record(pl.common_dir, spec.task_id)
    except (OSError, ValueError) as e:
        P.append("unreadable task record: %s" % e)
        return pl
    rec = pl.record
    pl.path = "resume" if rec else "new"
    if spec.want and spec.want != pl.path:
        P.append("task %s has no record here, so there is nothing to resume" % spec.task_id
                 if spec.want == "resume" else
                 "task %s was already started (base %s); resume it, or choose a new task id"
                 % (spec.task_id, rec["base_sha"][:12]))

    # Names, roots and duplicate assignments, before anything is looked at on disk.
    if not spec.lanes:
        P.append("no lane to prepare")
    for attr, label in (("lane", "lane name"), ("branch", "branch"), ("worktree", "worktree")):
        seen = {}
        for ln in spec.lanes:
            seen.setdefault(getattr(ln, attr), []).append(ln.lane)
        for value, lanes in seen.items():
            if len(lanes) > 1:
                P.append("duplicate %s %s (lanes %s)" % (label, value, ", ".join(lanes)))
    registered = {w["path"]: w for w in worktree_list(spec.repo)}
    for ln in spec.lanes:
        if not LANE_RE.fullmatch(ln.lane or ""):
            ln.problems.append("lane name must match %s" % LANE_RE.pattern)
        if not valid_branch(ln.branch):
            ln.problems.append("branch name %r is not valid" % ln.branch)
        if spec.worktree_root and not (under(ln.worktree, spec.worktree_root) and ln.worktree != spec.worktree_root):
            ln.problems.append("worktree %s is outside the worktree root %s" % (ln.worktree, spec.worktree_root))
        if under(ln.worktree, pl.common_dir):
            ln.problems.append("worktree %s is inside the repository's .git directory" % ln.worktree)
        for other in spec.lanes:
            if other is not ln and under(ln.worktree, other.worktree) and ln.worktree != other.worktree:
                ln.problems.append("worktree is nested inside lane %s" % other.lane)
    if pl.blocked:
        return pl

    if rec:
        pl.base_sha = rec["base_sha"]
        if rec.get("mode") != spec.mode:
            P.append("task %s was started as %s, not %s" % (spec.task_id, rec.get("mode"), spec.mode))
        r = rec["repo"]
        if (r["remote"], r["base_branch"], r["remote_url"]) != (spec.remote, spec.base_branch, pl.remote_url):
            P.append("the task was recorded against %s %s (%s); the repository or manifest now says %s %s (%s)"
                     % (r["remote"], r["base_branch"], r["remote_url"], spec.remote, spec.base_branch, pl.remote_url))
        want = {l.lane: (l.branch, l.worktree) for l in spec.lanes}
        have = {k: (v["branch"], v["worktree"]) for k, v in rec["lanes"].items() if v["state"] != "retired"}
        if want != have:
            P.append("lanes, branches or worktrees differ from the recorded task %s; a changed assignment "
                     "is a new task: use a new task id" % spec.task_id)
        if not rev(spec.repo, pl.base_sha):
            P.append("recorded base %s is no longer in the repository" % pl.base_sha)
        if P:
            return pl
    others = [o for o in all_records() if o["repo"]["common_dir"] == pl.common_dir and o["task_id"] != spec.task_id]
    for ln in spec.lanes:
        state = rec["lanes"][ln.lane]["state"] if rec else "planned"
        tip = branch_sha(spec.repo, ln.branch)
        owner = next((o["task_id"] for o in others if any(v["branch"] == ln.branch for v in o["lanes"].values())), None)
        if state in ("prepared", "started"):
            _inspect(spec, pl, ln, registered, prepare=False)
            if not ln.problems:
                if ln.cur_branch != ln.branch:
                    ln.problems.append(
                        "worktree is on %s, not the recorded task branch %s; if that is a mistake, switch it "
                        "back yourself: git -C %s switch %s" % (ln.cur_branch or "a detached HEAD", ln.branch,
                                                                 ln.worktree, ln.branch))
                elif not is_ancestor(ln.worktree, pl.base_sha, "HEAD"):
                    ln.problems.append("recorded base %s is not an ancestor of %s (its history was rewritten)"
                                       % (pl.base_sha[:12], ln.branch))
                ln.action = "keep"
                if ln.dirty:
                    ln.notes.append("%d uncommitted path(s) kept as they are" % ln.dirty)
        elif tip is None:
            if owner:
                ln.problems.append("branch %s is assigned to task %s" % (ln.branch, owner))
            _inspect(spec, pl, ln, registered, prepare=True)
        elif state == "preparing" and tip == pl.base_sha:
            # A previous start stopped between creating this branch and recording it.
            on_it = os.path.isdir(ln.worktree) and current_branch(ln.worktree) == ln.branch
            _inspect(spec, pl, ln, registered, prepare=not on_it)
            ln.action = {"add": "attach", "switch": "checkout"}.get(ln.action, "confirm")
        else:
            ln.problems.append(
                "branch %s already exists (at %s)%s; a new task needs a new branch and an existing one is "
                "never reused" % (ln.branch, tip[:12], " and belongs to task %s" % owner if owner else ""))
    if pl.path == "new" and probe and pl.remote_url and not pl.blocked:
        pl.remote_tip, pl.remote_error = ls_remote(spec.repo, spec.remote, spec.base_branch)
        if pl.remote_error:
            P.append("remote %s is not reachable for %s (%s); the fetch would fail and no worker would start"
                     % (spec.remote, spec.base_branch, pl.remote_error))
    return pl


# ---------------------------------------------------------------- apply

def _prepare_lane(spec, rec, ln):
    rl, base = rec["lanes"][ln.lane], rec["base_sha"]
    cmds = {"add": (spec.repo, ["worktree", "add", "-b", ln.branch, ln.worktree, base]),
            # --no-overwrite-ignore: without it git silently replaces an ignored file that the
            # target commit tracks, although `git status` calls the worktree clean.
            "switch": (ln.worktree, ["switch", "--no-overwrite-ignore", "-c", ln.branch, base]),
            "attach": (spec.repo, ["worktree", "add", ln.worktree, ln.branch]),
            "checkout": (ln.worktree, ["switch", "--no-overwrite-ignore", ln.branch])}
    if ln.action in cmds:
        rl["state"] = "preparing"
        save_record(rec)
        cwd, args = cmds[ln.action]
        try:
            p = git(cwd, *args, timeout=300)
            err = p.stderr.strip()[-400:] if p.returncode else None
        except (OSError, subprocess.TimeoutExpired) as e:
            err = str(e)
        if err is not None:
            hint = (" Move those files out of the worktree yourself; nothing was overwritten."
                    if "would be overwritten" in err else "")
            raise Blocked(["lane %s: `git %s` failed: %s.%s The task record keeps what was done; run the "
                           "same command again to resume." % (ln.lane, " ".join(args), err, hint)])
    problems = verify_ready(ln.worktree, ln.branch, base, exact=ln.action != "keep")
    if problems:
        raise Blocked(["lane %s: %s" % (ln.lane, p) for p in problems])
    if rl["state"] in ("planned", "preparing"):
        rl["state"], rl["prepared_at"] = "prepared", now()
        save_record(rec)


def apply(spec, expect_base=None, fetch_timeout=180, log=lambda s: None):
    """Validate, lock, fetch (new tasks only), pin, and put every lane on its task branch.
    Returns (plan, record). Raises Blocked; in that case no worker may be started."""
    kind = "rc" if spec.mode == "rc" else "headless"
    with wtlock.mutex("prepare", blocking=False) as got:
        if not got:
            raise Blocked(["another pai-swarm start is preparing worktrees right now; retry when it has finished"])
        pl = plan(spec, probe=False)
        if pl.blocked:
            raise Blocked(pl.all_problems(), pl)
        rec, fresh = pl.record, []
        try:
            for ln in spec.lanes:  # every lock before any lane is switched
                mine = wtlock.owned(wtlock.read(ln.worktree), kind, spec.lock_owner, ln.lane)
                wtlock.acquire(ln.worktree, kind, spec.lock_owner, ln.lane)
                if not mine:
                    fresh.append(ln)
            if rec is None:
                base, before = fetch_base(spec.repo, spec.remote, spec.base_branch, fetch_timeout)
                if expect_base and base != expect_base:
                    raise Blocked(["%s/%s is %s after the fetch, not the expected %s; no worker was started"
                                   % (spec.remote, spec.base_branch, base, expect_base)])
                log("fetched %s %s: %s%s" % (spec.remote, spec.base_branch, base, "" if before == base else
                                            " (local tracking ref was %s)" % (before or "absent")))
                rec = {"schema": SCHEMA, "task_id": spec.task_id, "mode": spec.mode, "created": now(),
                       "repo": {"common_dir": pl.common_dir, "remote": spec.remote, "remote_url": pl.remote_url,
                                "base_branch": spec.base_branch, "tracking_ref": pl.tracking_ref},
                       "base_sha": base, "fetched_at": now(), "tracking_before": before, "meta": spec.meta,
                       "lanes": {l.lane: dict(l.extra, branch=l.branch, worktree=l.worktree, state="planned")
                                 for l in spec.lanes}}
                save_record(rec, create=True)
            elif expect_base and rec["base_sha"] != expect_base:
                raise Blocked(["task %s is pinned to %s, not the expected %s" % (spec.task_id, rec["base_sha"], expect_base)])
            pl.record, pl.base_sha = rec, rec["base_sha"]
            for ln in spec.lanes:
                _prepare_lane(spec, rec, ln)
        except (Blocked, wtlock.LockHeld) as e:
            # Give back locks taken by this attempt unless a record now owns the lanes (an RC
            # task that stopped partway keeps its worktrees until it is resumed or released).
            if kind == "headless" or pl.record is None:
                for ln in fresh:
                    try:
                        wtlock.release(ln.worktree, kind, spec.lock_owner, ln.lane)
                    except wtlock.LockHeld:
                        pass  # the lock stays; `pai_swarm.py tasks` shows it
            if isinstance(e, wtlock.LockHeld):
                raise Blocked([str(e)], pl)
            e.plan = e.plan or pl
            raise
        return pl, rec


def release_headless(spec):
    """Release the run's locks. Returns the lanes whose lock is kept because a process the
    run started is still alive in the worktree."""
    kept = []
    for ln in spec.lanes:
        if not wtlock.release(ln.worktree, "headless", spec.lock_owner, ln.lane) and \
                wtlock.owned(wtlock.read(ln.worktree), "headless", spec.lock_owner, ln.lane):
            kept.append(ln)
    return kept


def render_plan(pl):
    """Human-readable plan, shared by `rc plan`, `rc up` refusals and `run --dry-run`."""
    s, lines = pl.spec, []
    lines.append("task      %s (%s%s)" % (s.task_id, s.mode, ", " + pl.path.upper() if pl.path else ""))
    lines.append("repo      %s" % (pl.common_dir or s.repo))
    lines.append("remote    %s %s" % (s.remote, pl.remote_url or "(none)"))
    if pl.path == "resume":
        r = pl.record
        lines.append("base      %s @ %s (recorded %s; no fetch on resume)" % (s.base_branch, r["base_sha"], r["fetched_at"]))
        if pl.local_tracking and pl.local_tracking != r["base_sha"]:
            lines.append("          %s is now %s; the task stays on its recorded base" % (pl.tracking_ref, pl.local_tracking[:12]))
    elif pl.path == "new":
        if pl.remote_tip:
            lines.append("base      %s @ %s on the remote now; the start fetches and pins what it gets" % (s.base_branch, pl.remote_tip))
            if pl.local_tracking != pl.remote_tip:
                lines.append("          local %s is %s (stale); it is not used" % (pl.tracking_ref, (pl.local_tracking or "absent")[:12]))
        else:
            lines.append("base      %s: fetched and pinned at start (local %s is %s)" % (
                s.base_branch, pl.tracking_ref, (pl.local_tracking or "absent")[:12]))
    for ln in s.lanes:
        verb = {"add": "create worktree + new branch", "switch": "switch to new branch (from %s)" % (ln.cur_branch or "detached HEAD"),
                "attach": "create worktree on its branch", "checkout": "switch to its branch",
                "confirm": "confirm branch", "keep": "keep"}.get(ln.action, "-")
        lines.append("lane      %-14s %s" % (ln.lane, "REFUSED" if ln.problems else verb))
        lines.append("            branch   %s" % ln.branch)
        lines.append("            worktree %s" % ln.worktree)
        for n in ln.notes:
            lines.append("            note     %s" % n)
        for p in ln.problems:
            lines.append("            REFUSED  %s" % p)
    for p in pl.problems:
        lines.append("REFUSED   %s" % p)
    return "\n".join(lines)
