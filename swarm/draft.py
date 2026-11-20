"""`pai pr draft LANE`: the owner-review draft of one lane, rendered on this machine only.

It shows the owner everything needed to decide, in one Markdown page: the branch and base, the
proposed diff (commits and diffstat), the test evidence recorded for exactly this commit by
`pai pr check`, the PR title and description as a draft, what the metadata filter removed from
that text, an owner checklist, and the commands the owner would run to publish. It publishes
nothing: no push, no `gh`, no fetch or other network call, and no write to the Git index, refs
or worktree (Git runs read-only, with GIT_OPTIONAL_LOCKS=0 so not even `git status` refreshes
the index). The only file it can write is --out FILE, which must not exist.

The metadata filter (on by default; --keep-trailers shows the text unfiltered):
  - trailer lines whose key is configured in `review.strip_trailers` (case-insensitive), e.g.
    ["Assisted-by", "Generated-by"], whatever their value;
  - `Co-authored-by:` trailers that name Claude or Anthropic (the publication check's rule; a
    human co-author, including a person called Claude, is kept), `Claude-Session:` trailers and
    the "Generated with Claude Code" footer;
  - agent session links and text that looks like a credential (pai/privacy.py).
Every removal is listed with its line before the text, so the owner sees what the filter did.
Only whole trailer lines are removed: a sentence that mentions a trailer or a tool is kept.
"""
import os
import re
import subprocess

import gitprep
import publish_check
import rc
import review

try:
    from pai import privacy
except ImportError:  # the workbench started without the pai package on sys.path (its own tests)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pai import privacy

TRAILER = re.compile(r"^\s*([A-Za-z][A-Za-z0-9-]*)\s*:\s*(.*?)\s*$")

TEMPLATE = """# Owner review: {title}

Draft for the owner. Nothing here was pushed, published or merged; pai did not run Git commands
that change anything, and did not contact GitHub.

| | |
| --- | --- |
| run | `{run}` |
| lane / task | `{lane}` / `{task}` |
| branch | `{branch}` |
| base | `{remote}/{base_branch}` @ `{base}` |
| head | `{head}` ({ahead} commit(s) ahead of base) |
| worktree | `{worktree}` |

## Proposed diff

{commits}

```
{diffstat}
```

Full diff: `git -C {worktree_q} diff {base_short}..{head_short}`

## Test evidence

{evidence}

## PR text (draft, not published)

**Title:** {pr_title}

{pr_body}

## Removed by the metadata filter

{removed}

## Owner checklist

- [ ] The diff does only what the task note asks, and nothing outside its paths.
- [ ] The checks above passed at exactly this head (or I ran them myself).
- [ ] The PR text is accurate and fit to publish; authorship is right.
- [ ] I publish it myself, if at all (below). Merging is mine too, after CI.

## Commands for the owner (not run by pai)

```sh
{commands}
```
"""


def git_ro(cwd, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    p = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=60)
    return p.stdout if p.returncode == 0 else ""


def strip(text, keys=()):
    """(text without filtered trailer lines and private text, removals), each removal with the
    line number of the original text."""
    keys = {k.lower() for k in keys or ()}
    out, removals = [], []
    for n, line in enumerate((text or "").split("\n"), 1):
        m = TRAILER.match(line)
        why = None
        if m and m.group(1).lower() in keys:
            why = "configured trailer %s" % m.group(1)
        elif m and m.group(1).lower() == "co-authored-by" and publish_check.ai_identity(m.group(2)):
            why = "Co-authored-by names Claude/Anthropic"
        elif publish_check.SESSION_KEY.match(line):
            why = "Claude-Session trailer"
        elif publish_check.FOOTER.match(line):
            why = "\"Generated with Claude Code\" footer"
        if why:
            removals.append({"line": n, "kind": why, "sample": privacy.masked(line.strip())})
            continue
        clean, more = privacy.scrub(line)
        removals += [dict(r, line=n) for r in more]
        out.append(clean)
    return "\n".join(out), removals


def render(m, lane, keys=(), keep=False, title=None, body_file=None):
    """(markdown, facts) for one lane. Read-only."""
    ctx = review.Ctx(m, lane)
    wt = ctx.wt
    head = gitprep.rev(wt, "HEAD") or ""
    last = ctx.load()
    data = last if (last.get("snapshot") or {}).get("head") == head and head else None
    pr_title, pr_body = review.pr_text(ctx, data, title, body_file)
    removals = []
    if not keep:
        pr_body, removals = strip(pr_body, keys)
        pr_title, t_rem = strip(pr_title, keys)
        removals = [dict(r, field="PR title") for r in t_rem] + [dict(r, field="PR body") for r in removals]
    log = git_ro(wt, "log", "--no-show-signature", "--format=%h%x09%an%x09%s", "%s..HEAD" % ctx.base).splitlines()
    commits = "\n".join("- `%s` %s: %s" % tuple(publish_check.redact(x) for x in (l.split("\t") + ["", ""])[:3])
                        for l in log[:50]) or "- (no commit ahead of the base yet)"
    stat = git_ro(wt, "diff", "--stat", "%s..HEAD" % ctx.base).rstrip() or "(no change)"
    if data:
        rows = ["| check | result | exit | seconds |", "| --- | --- | --- | --- |"] + [
            "| `%s` | %s | %s | %s |" % (c["name"], "passed" if c["passed"] else "FAILED", c["exit"], c.get("seconds"))
            for c in data.get("checks") or []]
        evidence = "Recorded by `pai pr check` at `%s` for this head (snapshot sha256 `%s`):\n\n%s" % (
            data.get("time"), data["snapshot"]["sha256"][:16], "\n".join(rows))
    else:
        evidence = ("None recorded for this head. `pai pr check %s --run %s` runs the approved checks and records them "
                    "(it changes no code and publishes nothing)." % (lane, m.run_id))
    try:
        found, _ = publish_check.check(wt, ranges=["%s..HEAD" % ctx.base]) if head else ([], [])
    except publish_check.CannotInspect:
        found = []
    removed = "\n".join("- %s line %d: %s (`%s`)" % (r["field"], r["line"], r["kind"], r["sample"]) for r in removals) \
        or "- nothing" + (" (filter off: --keep-trailers)" if keep else "")
    if found:
        removed += ("\n\nThe commits themselves carry %d item(s) the publication check rejects (they stay in history "
                    "unless you rewrite it yourself): %s" % (len(found), "; ".join(
                        "%s line %s: %s" % (f["field"], f["line"], f["rule"]) for f in found)))
    remote, branch = ctx.rec["repo"]["remote"], ctx.branch
    q = lambda s: "'" + str(s).replace("'", "'\\''") + "'"  # noqa: E731
    try:
        slug = rc.slug_of(m, ctx.rec)
    except RuntimeError:
        slug = "OWNER/REPO"
    commands = "\n".join([
        "git -C %s log --format=fuller %s..HEAD   # read the history first" % (q(wt), ctx.base[:12]),
        "git -C %s push %s %s                     # publishing is yours" % (q(wt), remote, branch),
        "gh pr create --repo %s --base %s --head %s --title %s --body-file PR-BODY.md" % (
            slug, ctx.rec["repo"]["base_branch"], branch, q(pr_title.strip()))])
    facts = {"run": m.run_id, "lane": lane, "task": "%s/%s/%s" % (m.run_id, lane, ctx.rl.get("note") or "#1"),
             "branch": branch, "base": ctx.base, "head": head, "removals": removals, "checks_recorded": bool(data),
             "pr_title": pr_title, "pr_body": pr_body}
    md = TEMPLATE.format(title=pr_title.strip(), run=m.run_id, lane=lane, task=facts["task"], branch=branch,
                         remote=remote, base_branch=ctx.rec["repo"]["base_branch"], base=ctx.base[:12],
                         head=head[:12] or "?", ahead=gitprep.ahead(wt, ctx.base), worktree=wt, worktree_q=q(wt),
                         base_short=ctx.base[:12], head_short=head[:12] or "HEAD", commits=commits, diffstat=stat,
                         evidence=evidence, pr_title=pr_title.strip(), pr_body=pr_body.strip(), removed=removed,
                         commands=commands)
    return md, facts


def main(m, a, keys):
    try:
        md, facts = render(m, a.lane, keys, a.keep_trailers, a.title, a.body_file)
    except review.Blocked as e:
        print("pai pr draft: %s. Nothing was written." % e)
        return 2
    print("FILTER    %s" % ("off (--keep-trailers)" if a.keep_trailers else "%d removal(s) from the PR text%s" % (
        len(facts["removals"]), "" if not facts["removals"] else ":")))
    for r in facts["removals"]:
        print("          %s line %d: %s (%s)" % (r["field"], r["line"], r["kind"], r["sample"]))
    if not a.out:
        print("DRAFT (not published, nothing written)\n")
        print(md.rstrip("\n"))
        print("\nTo save it: the same command with --out FILE. Publishing stays with you (commands above).")
        return 0
    try:
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        print("pai pr draft: %s exists; nothing was written (choose another --out)." % a.out)
        return 1
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print("wrote %s (a local draft for the owner; nothing was published)" % a.out)
    return 0
