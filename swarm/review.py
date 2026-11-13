"""Review checks and owner-opened pull requests for RC worker lanes.

    pai_swarm.py pr check MANIFEST --lane LANE   run the approved checks at HEAD and record the results
    pai_swarm.py pr plan  MANIFEST --lane LANE   read-only: what `pr open` would publish, and every blocker
    pai_swarm.py pr open  MANIFEST --lane LANE   run the checks, then push the task branch and open the PR

Checks are named in the manifest (`review.checks`: name -> argv) and are the only commands
that run. `review.required_checks` always run; a task note may add more by name with
`checks: [name]`, and can neither remove a required one nor supply a command: text from a
note is never executed. The repository's own instruction files outrank a task note.

`pr open` stops before the first push unless: the worktree is on its recorded task branch
with the pinned base, it is clean, every check passed at exactly this commit, and the
publication check found no Claude attribution or session link in the commits, the PR title
and the exact PR body (opt in per run with --allow-ai-attribution). It never forces a push
and never merges. A rerun reuses the branch and PR it already made, and stops on anything
that does not match. Jev (--jev-verify) is recorded as a second opinion only.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

import gitprep
import jev
import notes
import publish_check
import rc
import wtlock

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")

TEMPLATE = """---
type: pai-swarm-review-template
---
# Project review template

How a task in this folder is accepted. Copy the fields into each task note's front matter.

```yaml
checks: [unit]            # extra approved checks, by name; required checks always run
acceptance:               # what the owner looks at before merging; judged by a person
  - The page still works offline
pr_title: "feat: short imperative title"   # optional; default is the note's heading
```

Rules:

- The repository's own instructions (AGENTS.md, CLAUDE.md, CONTRIBUTING.md) come first. If
  a note disagrees with them, the repository wins and the note is corrected.
- A check name maps to a command approved in the run manifest (`review.checks`). Unknown
  names block the review. Nothing written in a note is ever run as a command.
- `pr check` writes the actual results under a `## Review` heading: each command with its
  exit code, the commit and its author, and the snapshot digest the results belong to.
- Text under a `## PR` heading becomes the pull request description. Without it the
  description is the title and the check table. Write it for the public.
- No Claude co-author trailers, "Generated with Claude Code" footers or session links in
  commits or PR text. `pr open` refuses them unless you opt in for that one run.
- Jev, when enabled, is a second opinion. It can ask for a closer look; it cannot pass a
  failed check.
- Merging is done by the owner, in GitHub, after CI.
"""


def log(msg=""):
    print(msg, flush=True)


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- context

class Ctx:
    def __init__(self, m, lane):
        self.m, self.w = m, m.worker(lane)
        cdir = gitprep.common_dir(m.repo)
        cur = rc.current_lanes(cdir, m.run_id) if cdir else {}
        if lane not in cur:
            raise Blocked("lane %s has no started task in run %s (see `rc status`)" % (lane, m.run_id))
        self.rec, self.rl = cur[lane]
        self.branch, self.base, self.wt = self.rl["branch"], self.rec["base_sha"], self.w.worktree
        path = os.path.join(m.tasks_dir, self.rl["note"] + ".md") if m.tasks_dir and self.rl.get("note") else None
        self.note = notes.Task(path) if path and os.path.isfile(path) else None
        self.dir = os.path.join(wtlock.home(), "reviews")
        self.stem = os.path.join(self.dir, "%s--%s" % (self.rec["task_id"], lane))

    def load(self):
        try:
            with open(self.stem + ".json", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, data):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.stem + ".json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self.stem + ".json")


def wanted_checks(ctx):
    """(names, problems). Required checks first; a note adds names but never commands."""
    approved = ctx.m.review.get("checks") or {}
    required = list(ctx.m.review.get("required_checks") or [])
    extra = ctx.note.meta.get("checks") if ctx.note else None
    extra = [extra] if isinstance(extra, str) else list(extra or [])
    names, problems = [], []
    for n in required + [str(x) for x in extra]:
        argv = approved.get(n)
        if not (isinstance(argv, list) and argv and all(isinstance(x, str) for x in argv)):
            problems.append("check %r is not an approved command in the manifest (review.checks); note text is "
                            "never run as a command" % n)
        elif n not in names:
            names.append(n)
    if not names and not problems:
        problems.append("no approved check is configured (manifest review.required_checks); nothing may be "
                        "published unchecked")
    return names, problems


def snapshot(ctx):
    wt = ctx.wt
    head = gitprep.rev(wt, "HEAD")
    listing = gitprep.git(wt, "ls-tree", "-r", "--full-tree", "HEAD").stdout
    fields = (gitprep.out(wt, "show", "-s", "--no-show-signature", "--format=%an <%ae>%n%aI%n%cn <%ce>%n%s", "HEAD") or "").split("\n")
    fields += [""] * (4 - len(fields))
    return {"head": head, "tree": gitprep.out(wt, "rev-parse", "HEAD^{tree}"),
            "sha256": hashlib.sha256(listing.encode()).hexdigest(), "ahead": gitprep.ahead(wt, ctx.base),
            "commit": {"sha": head, "author": fields[0], "author_date": fields[1], "committer": fields[2], "subject": fields[3]}}


def ready_problems(ctx):
    """Why this lane's commit cannot be reviewed or published right now."""
    problems = gitprep.verify_ready(ctx.wt, ctx.branch, ctx.base)
    if problems:
        return problems
    d = gitprep.dirty(ctx.wt)
    if d:
        problems.append("worktree has %d uncommitted path(s) (first: %s); results are recorded against a commit, "
                        "so commit them yourself first. Nothing was changed." % (len(d), d[0][3:]))
    if not gitprep.ahead(ctx.wt, ctx.base):
        problems.append("branch %s has no commit ahead of its base %s: there is nothing to publish" % (ctx.branch, ctx.base[:12]))
    return problems


def run_checks(ctx, names):
    approved, results = ctx.m.review["checks"], []
    timeout = float(ctx.m.review.get("check_timeout_min") or 30) * 60
    os.makedirs(ctx.dir, exist_ok=True)
    for n in names:
        argv, started = approved[n], time.time()
        try:
            p = subprocess.run(argv, cwd=ctx.wt, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, timeout=timeout)
            code, output = p.returncode, p.stdout
        except subprocess.TimeoutExpired as e:
            code, output = "timeout", e.stdout or b""
        except OSError as e:
            code, output = "not run", str(e).encode()
        logf = "%s.%s.log" % (ctx.stem, n)
        with open(logf, "wb") as f:
            f.write(output)
        results.append({"name": n, "argv": argv, "exit": code, "passed": code == 0,
                        "seconds": round(time.time() - started, 1), "output_sha256": hashlib.sha256(output).hexdigest(),
                        "log": logf})
        log("check %-12s %s (exit %s, %.1fs)  %s" % (n, "PASSED" if code == 0 else "FAILED", code,
                                                     results[-1]["seconds"], " ".join(argv)))
    return results


def jev_review(ctx, data):
    """Optional second opinion on the recorded results. Deterministic checks stay the verdict."""
    report = "STATUS: %s\nFILES: %s\nCHECKS: %s" % (
        "DONE" if data["checks_passed"] else "PARTIAL",
        (gitprep.out(ctx.wt, "diff", "--name-only", ctx.base, "HEAD") or "").replace("\n", ", ")[:800],
        "; ".join("%s %s (exit %s)" % (c["name"], "passed" if c["passed"] else "failed", c["exit"]) for c in data["checks"]))
    sig = jev.signal("verify", ctx.note.title if ctx.note else ctx.branch, ctx.note.body if ctx.note else "", report)
    if sig["state"] != "ok":
        sig["verdict"] = "unavailable (%s: %s)" % (sig["state"], sig["detail"])
    elif (sig["done"] >= 0.5) == data["checks_passed"]:
        sig["verdict"] = "agrees with the checks (done %.2f)" % sig["done"]
    elif data["checks_passed"]:
        sig["verdict"] = "DISAGREES: checks passed but Jev doubts the task is done (%.2f); owner review advised" % sig["done"]
    else:
        sig["verdict"] = "DISAGREES: Jev says done (%.2f) but checks failed; the checks decide" % sig["done"]
    return sig


def do_checks(ctx, names, use_jev):
    snap = snapshot(ctx)
    results = run_checks(ctx, names)
    after = gitprep.rev(ctx.wt, "HEAD")
    # Tracked files a check rewrote (a formatter, a code generator): the results then describe
    # that edited tree, not the commit that would be published.
    p = gitprep.git(ctx.wt, "status", "--porcelain=v1", "--untracked-files=no")
    changed = [l[3:] for l in p.stdout.splitlines() if l] if p.returncode == 0 else ["(git status failed)"]
    data = {"task_id": ctx.rec["task_id"], "lane": ctx.w.lane, "branch": ctx.branch, "base_sha": ctx.base,
            "remote": ctx.rec["repo"]["remote"], "worktree": ctx.wt, "time": gitprep.now(), "snapshot": snap,
            "checks": results, "checks_passed": bool(results) and all(c["passed"] for c in results)
            and after == snap["head"] and not changed}
    if after != snap["head"]:
        data["note"] = "HEAD moved while the checks ran (%s -> %s); the results count as failed" % (snap["head"][:12], (after or "?")[:12])
    elif changed:
        data["note"] = ("the checks changed tracked files: %s; the results describe that edited tree, not commit %s, "
                        "so they count as failed. Nothing was reverted." % (", ".join(changed[:5]), snap["head"][:12]))
    if data.get("note"):
        log("checks       %s" % data["note"])
    if use_jev:
        data["jev"] = jev_review(ctx, data)
        log("jev          %s (optional signal, not a gate)" % data["jev"]["verdict"])
    prev = ctx.load()
    if prev.get("pr"):
        data["pr"] = prev["pr"]
    ctx.save(data)
    write_note(ctx, data)
    return data


def write_note(ctx, data):
    if not ctx.note:
        return
    snap, c = data["snapshot"], data["snapshot"]["commit"]
    lines = ["## Review %s · checks %s" % (data["time"], "passed" if data["checks_passed"] else "FAILED"),
             "- commit `%s` by %s on %s: %s" % (c["sha"], c["author"], c["author_date"], c["subject"]),
             "- snapshot sha256 `%s` (tree `%s`), %s commit(s) ahead of base `%s`" % (snap["sha256"], snap["tree"], snap["ahead"], data["base_sha"]),
             "- remote %s, task branch `%s`, worktree `%s`" % (data["remote"], data["branch"], data["worktree"])]
    for r in data["checks"]:
        lines.append("- check `%s`: %s (exit %s, %ss) `%s`" % (r["name"], "PASSED" if r["passed"] else "FAILED",
                                                              r["exit"], r["seconds"], " ".join(r["argv"])))
    if data.get("note"):
        lines.append("- %s" % data["note"])
    meta = {"review": "checks-passed" if data["checks_passed"] else "checks-failed", "review_commit": c["sha"],
            "snapshot_sha256": snap["sha256"]}
    if data.get("jev"):
        lines.append("- Jev (second opinion, not a gate): %s" % data["jev"]["verdict"])
        meta["jev_review"] = data["jev"]["verdict"].split(" ")[0].rstrip(":").lower()
    acc = ctx.note.meta.get("acceptance")
    for item in ([acc] if isinstance(acc, str) else list(acc or [])):
        lines.append("- [ ] acceptance (owner judges): %s" % item)
    ctx.note.update(meta=meta, append="\n".join(lines))


# ---------------------------------------------------------------- publication text and remote state

def pr_text(ctx, data, title=None, body_file=None):
    title = title or (ctx.note and (ctx.note.meta.get("pr_title") or ctx.note.title)) or ctx.branch
    if body_file:
        with open(body_file, encoding="utf-8") as f:
            return str(title), f.read()
    section = []
    if ctx.note:
        grab = False
        for line in ctx.note.body.splitlines():
            if line.startswith("## "):
                grab = line.strip().lower() == "## pr"
                continue
            if grab:
                section.append(line)
    body = "\n".join(section).strip() or str(title)
    checks = (data or {}).get("checks") or []
    if checks:
        body += "\n\n## Checks\n\n| check | result |\n| --- | --- |\n" + "\n".join(
            "| `%s` | %s (exit %s) |" % (c["name"], "passed" if c["passed"] else "FAILED", c["exit"]) for c in checks)
        body += "\n\nRun at `%s`." % data["snapshot"]["head"]
    return str(title), body + "\n"


def remote_state(ctx, head):
    """(action, blocker) for the branch on the remote: push it, reuse it, or stop."""
    sha, err = gitprep.ls_remote(ctx.wt, ctx.rec["repo"]["remote"], ctx.branch)
    if err and "has no branch" not in err:
        return None, "cannot read the remote: %s" % err
    if not sha:
        return "push", None
    if sha == head:
        return "reuse", None
    return None, ("%s already exists on the remote at %s, which is not this commit. Nothing is forced: inspect it, "
                  "or continue on a new branch." % (ctx.branch, sha[:12]))


def pr_state(ctx, slug, head):
    """(action, pr, blocker): create a PR, reuse the matching open one, or stop."""
    prs = rc.prs_for(slug, ctx.branch)
    if not prs:
        return "create", None, None
    if len(prs) > 1:
        return None, None, "more than one PR exists for %s" % ctx.branch
    p = prs[0]
    if p["state"] == "MERGED":
        return None, p, "PR %s is already merged; `rc next` prepares the next task" % p["url"]
    if p["state"] != "OPEN":
        return None, p, "PR %s is %s; reopen it yourself or continue on a new branch" % (p["url"], p["state"])
    if p["baseRefName"] != ctx.rec["repo"]["base_branch"]:
        return None, p, "PR %s targets %s, not %s" % (p["url"], p["baseRefName"], ctx.rec["repo"]["base_branch"])
    if p["headRefOid"] != head:
        return None, p, "PR %s has head %s, not this commit %s" % (p["url"], p["headRefOid"][:12], head[:12])
    return "reuse", p, None


def publication_findings(ctx, title, body):
    """Findings in the actual commits, the PR title and the PR body text."""
    found, inspected = publish_check.check(ctx.wt, ranges=["%s..HEAD" % ctx.base], pr_title=title)
    return found + publish_check.check_text("PR body", body), inspected + ["PR body"]


# ---------------------------------------------------------------- commands

def describe(ctx):
    log("lane      %s   task %s%s" % (ctx.w.lane, ctx.rec["task_id"], "   note %s" % ctx.note.name if ctx.note else ""))
    log("remote    %s %s" % (ctx.rec["repo"]["remote"], ctx.rec["repo"]["remote_url"]))
    log("base      %s @ %s" % (ctx.rec["repo"]["base_branch"], ctx.base))
    log("branch    %s" % ctx.branch)
    log("worktree  %s" % ctx.wt)
    found = [f for f in INSTRUCTION_FILES if os.path.isfile(os.path.join(ctx.wt, f))]
    log("rules     repository instructions take precedence over the task note%s"
        % (": " + ", ".join(found) if found else " (none of %s found)" % ", ".join(INSTRUCTION_FILES)))


def cmd_check(ctx, a):
    describe(ctx)
    names, problems = wanted_checks(ctx)
    problems += ready_problems(ctx)
    if problems:
        for p in problems:
            log("BLOCKED   %s" % p)
        return 1
    data = do_checks(ctx, names, a.jev_verify)
    log("\nreview    checks %s at %s (snapshot sha256 %s)" % (
        "passed" if data["checks_passed"] else "FAILED", data["snapshot"]["head"][:12], data["snapshot"]["sha256"][:16]))
    return 0 if data["checks_passed"] else 1


def cmd_plan(ctx, a):
    describe(ctx)
    blockers = []
    names, problems = wanted_checks(ctx)
    blockers += problems + ready_problems(ctx)
    head = gitprep.rev(ctx.wt, "HEAD") or ""
    last = ctx.load()
    log("commits   %s ahead of base:" % gitprep.ahead(ctx.wt, ctx.base))
    for line in (gitprep.out(ctx.wt, "log", "--no-show-signature", "--format=%h %an: %s", "%s..HEAD" % ctx.base) or "").splitlines()[:20]:
        log("            %s" % publish_check.redact(line))
    for n in names:
        prev = next((c for c in last.get("checks", []) if c["name"] == n), None) if (last.get("snapshot") or {}).get("head") == head else None
        log("check     %-12s %s   last result for this commit: %s" % (
            n, " ".join(ctx.m.review["checks"][n]),
            "none (`pr open` runs it)" if not prev else ("passed" if prev["passed"] else "FAILED (exit %s)" % prev["exit"])))
    title, body = pr_text(ctx, last if (last.get("snapshot") or {}).get("head") == head else None, a.title, a.body_file)
    log("PR title  %s" % publish_check.redact(title))
    if not any("worktree is on" in b or "does not exist" in b for b in blockers):
        try:
            found, _ = publication_findings(ctx, title, body)
            publish_check.report(found, ["commits %s..HEAD" % ctx.base[:12], "PR title", "PR body"], a.allow_ai_attribution)
            if found and not a.allow_ai_attribution:
                blockers.append("publication check rejected %d item(s)" % len(found))
        except publish_check.CannotInspect as e:
            blockers.append("publication check could not inspect: %s" % e)
        action, why = remote_state(ctx, head)
        log("remote    %s" % ({"push": "branch is not on the remote: `pr open` would push it (never forced)",
                               "reuse": "branch is already on the remote at this commit: reused"}.get(action) or "STOP"))
        if why:
            blockers.append(why)
        try:
            paction, pr, why = pr_state(ctx, rc.slug_of(ctx.m, ctx.rec), head)
            log("PR        %s" % ({"create": "none yet: `pr open` would open one against %s" % ctx.rec["repo"]["base_branch"],
                                   "reuse": "open and matching: reused (%s)" % (pr or {}).get("url")}.get(paction) or "STOP"))
            if why:
                blockers.append(why)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            blockers.append("cannot ask GitHub about existing PRs: %s" % e)
    for b in blockers:
        log("BLOCKED   %s" % b)
    log("\nPLAN ONLY: no check was run, nothing was pushed or opened.%s" % (
        "" if blockers else " `pr open` would run the checks and publish if they pass."))
    return 1 if blockers else 0


def cmd_open(ctx, a):
    describe(ctx)
    names, problems = wanted_checks(ctx)
    problems += ready_problems(ctx)
    head = gitprep.rev(ctx.wt, "HEAD") or ""
    slug = action = paction = pr = None
    if not problems:
        try:
            slug = rc.slug_of(ctx.m, ctx.rec)
            rc.gh("auth", "status")
            action, why = remote_state(ctx, head)
            paction, pr, why2 = pr_state(ctx, slug, head)
            problems += [w for w in (why, why2) if w]
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            problems.append("GitHub is not ready (%s); run `pai_swarm.py doctor`" % e)
    if problems:
        for p in problems:
            log("BLOCKED   %s" % p)
        log("\npr open: nothing was run, pushed or opened.")
        return 1
    data = do_checks(ctx, names, a.jev_verify)
    if not data["checks_passed"]:
        log("\npr open: BLOCKED, checks failed at %s. Nothing was pushed or opened." % head[:12])
        return 1
    title, body = pr_text(ctx, data, a.title, a.body_file)
    body_path = ctx.stem + ".pr-body.md"
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(body)
    try:  # the exact file that goes to GitHub, the exact commits that would be pushed
        found, inspected = publish_check.check(ctx.wt, ranges=["%s..%s" % (ctx.base, head)], pr_title=title,
                                               pr_bodies=[body_path])
    except publish_check.CannotInspect as e:
        log("\npr open: BLOCKED, the publication check could not inspect (%s). Nothing was pushed or opened." % e)
        return 1
    publish_check.report(found, inspected, a.allow_ai_attribution)
    if found and not a.allow_ai_attribution:
        log("\npr open: BLOCKED by the publication check. Nothing was pushed or opened.")
        return 1
    remote = ctx.rec["repo"]["remote"]
    if action == "push":
        p = gitprep.git(ctx.wt, "push", remote, "%s:refs/heads/%s" % (head, ctx.branch), timeout=300)
        if p.returncode != 0:
            log("pr open: push failed (%s). Nothing was forced; rerun to continue." % gitprep.redact_url(p.stderr.strip()[-300:]))
            return 1
        log("pushed    %s %s -> %s" % (head[:12], ctx.branch, remote))
    else:
        log("reused    %s is already on %s at %s" % (ctx.branch, remote, head[:12]))
    if paction == "create":
        try:
            url = rc.gh("pr", "create", "--repo", slug, "--base", ctx.rec["repo"]["base_branch"], "--head", ctx.branch,
                        "--title=" + title, "--body-file", body_path).strip().splitlines()[-1]
        except (RuntimeError, IndexError, subprocess.TimeoutExpired) as e:
            log("pr open: the branch is pushed but the PR was not opened (%s). Rerun: the branch is reused." % e)
            return 1
        log("opened    %s" % url)
    else:
        url = pr["url"]
        log("reused    %s" % url)
    data["pr"] = {"url": url, "head": head, "title": title, "time": gitprep.now()}
    ctx.save(data)
    if ctx.note:
        ctx.note.update(meta={"status": "review", "pr": url, "pr_state": "open"})
    log("\nNothing was merged. Merge it yourself after CI. If you squash-merge in GitHub's UI, check the "
        "message GitHub proposes first (see RC.md, \"Squash message\"), then `rc next` after the merge.")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(prog="pai_swarm.py pr", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("check", "run the approved checks and record the results"),
                        ("plan", "read-only preview of `pr open`"), ("open", "checks, then push and open the PR")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("manifest")
        p.add_argument("--lane", required=True)
        p.add_argument("--tmux-socket", help=argparse.SUPPRESS)
        if name != "plan":
            p.add_argument("--jev-verify", action="store_true", help="also record Jev's opinion (needs TYPESAFE_API_KEY)")
        if name != "check":
            p.add_argument("--title", help="PR title (default: the note's pr_title, else its heading)")
            p.add_argument("--body-file", help="PR description file (default: the note's `## PR` section)")
            p.add_argument("--allow-ai-attribution", action="store_true",
                           help="deliberate opt-in for this run: publish despite Claude attribution findings")
    a = ap.parse_args(argv)
    try:
        if getattr(a, "body_file", None) and not os.path.isfile(a.body_file):
            raise Blocked("--body-file %s is not a file" % a.body_file)
        ctx = Ctx(rc.Manifest(a.manifest), a.lane)
        return {"check": cmd_check, "plan": cmd_plan, "open": cmd_open}[a.cmd](ctx, a)
    except rc.ManifestError as e:
        print("pr: %s" % e, file=sys.stderr)
        return 2
    except Blocked as e:
        print("pr: %s" % e, file=sys.stderr)
        return 1
