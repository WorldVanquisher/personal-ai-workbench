"""Command registry (R6-COR-049; R7 limitation L5, hot-file conflicts).

A feature module describes its subcommands once, as a Feature: `add(sub, parsers)` adds its
argparse parsers (`sub` is the top-level subparsers object; `parsers` names the existing ones a
feature may extend, such as "handoff" and "handoff new"), and `run(a, load_project)` runs the
command when it is the feature's, returning an exit code, or returns None when it is not. pai/cli.py
then needs one `commands.use(...)` line per feature, plus the two calls below, instead of a parser
block and a dispatch branch per command, so lanes adding commands do not edit the same lines.
Standard library only.
"""

FEATURES = []


class Feature:
    def __init__(self, name, add, run):
        self.name, self.add, self.run = name, add, run

    def __repr__(self):
        return "Feature(%r)" % self.name


def use(feature):
    """Register FEATURE once (registering the same name again replaces it, so a re-import is harmless)."""
    FEATURES[:] = [f for f in FEATURES if f.name != feature.name] + [feature]
    return feature


def add_all(sub, parsers):
    """Add every registered feature's parsers, in registration order."""
    for f in FEATURES:
        f.add(sub, parsers)


def dispatch(a, load_project):
    """The exit code of the feature that owns the parsed command, or None when none does."""
    for f in FEATURES:
        code = f.run(a, load_project)
        if code is not None:
            return code
    return None


# ---------------------------------------------------------------- `pai doctor` checks of the core lane
# pai/doctor.py calls doctor_checks() once per configured project (one line, R6-COR-051), so the docs
# lane can edit doctor.py's wording without touching the lines these checks live on.

DOCTOR_CHECKS = []


def doctor_check(fn):
    """Register fn(report, key, project): it calls report.ok / warn / problem / info."""
    DOCTOR_CHECKS[:] = [f for f in DOCTOR_CHECKS if f.__qualname__ != fn.__qualname__ or f.__module__ != fn.__module__]
    DOCTOR_CHECKS.append(fn)
    return fn


def doctor_checks(report, key, project):
    from pai import handoff  # noqa: F401  (importing registers its checks)
    for fn in DOCTOR_CHECKS:
        fn(report, key, project)


# ---------------------------------------------------------------- pai context --worktree (R6-COR-061)

LOCAL_FILE = "CLAUDE.local.md"


def worktree_of(project, path):
    """The real path of PATH when it is the project's repository or one of its linked worktrees (read from
    the worktree's `.git` file; no git command is run), else ValueError."""
    import os
    wt = os.path.realpath(os.path.expanduser(path))
    repo = os.path.realpath(project.repo)
    if wt == repo:
        return wt
    dot = os.path.join(wt, ".git")
    if not os.path.isfile(dot):
        raise ValueError("%s is not a linked worktree (no .git file)" % wt)
    with open(dot, encoding="utf-8", errors="replace") as f:
        line = f.readline().strip()
    gitdir = line[len("gitdir:"):].strip() if line.startswith("gitdir:") else ""
    gitdir = os.path.realpath(os.path.join(wt, gitdir))
    if not gitdir.startswith(os.path.join(repo, ".git", "worktrees") + os.sep):
        raise ValueError("%s is not a worktree of %s" % (wt, repo))
    return wt


def worktree_project(project, path):
    """PROJECT with its instruction files read from the worktree at PATH: the lane's L2 first, from
    CLAUDE.local.md when that file is there (`pai worktree init --target claude-local`), then the
    project's own instruction files (AGENTS.md, ...) as they are in that worktree, not in the main checkout."""
    import dataclasses
    import os
    from pai import config
    wt = worktree_of(project, path)
    names = ([LOCAL_FILE] if os.path.isfile(os.path.join(wt, LOCAL_FILE)) else []) + \
        [n for n in project.instructions if n != LOCAL_FILE]
    # the managed section's own heading (swarm/agentsmd.py): the lane's L2 is always required context
    always = list(project.instruction_always) + [h for h in ("this worktree (pai)",) if h not in project.instruction_always]
    p = dataclasses.replace(project, repo=wt, instructions=names, instruction_always=always)
    p.sources = config.gather_sources(p)
    return p


def _context_add(sub, parsers):
    parsers["context"].add_argument("--worktree", metavar="PATH", help="read the instructions (the lane's L2: "
                                    "CLAUDE.local.md, then AGENTS.md ...) from this worktree of the project")


def _context_run(a, load_project):
    if a.cmd != "context" or not getattr(a, "worktree", None):
        return None
    import sys
    from pai import config, context
    if a.budget is not None and not 2000 <= a.budget <= 200000:
        raise config.ConfigError("--budget must be between 2000 and 200000")
    try:
        project = worktree_project(load_project(), a.worktree)
    except ValueError as e:
        raise config.ConfigError(str(e))
    sys.stdout.write(context.build(project, a.task, a.budget, handoffs=a.handoffs))
    return 0


CONTEXT_WORKTREE = Feature("context-worktree", _context_add, _context_run)


# ---------------------------------------------------------------- pai secrets where (R6-COR-069)

SECRETS = (
    # (name, what it is for, where it belongs)
    ("TYPESAFE_API_KEY", "JEV (optional judgments)", "the environment, or ~/.config/pai/jev.env (mode 600, sourced "
     "with `set -a; . ~/.config/pai/jev.env; set +a`), or the credential store `pai init` offers"),
    ("ANTHROPIC_API_KEY", "real Claude Code workers (or your `claude` login)", "the environment of the shell that "
     "starts the worker, or `claude` login; never a pai file, note or manifest"),
    ("ANTHROPIC_CUSTOM_HEADERS", "an organization key not scoped to a workspace (anthropic-workspace-id: ...)",
     "the environment of that shell (tools/real_agent_smoke.sh says when it is needed)"),
    ("GH_TOKEN", "GitHub for `pai worktree publish` / `pai pr` (or `gh auth login`)", "gh's own login, or the environment"),
)
SECRET_FILES = (("~/.config/pai/jev.env", "TYPESAFE_API_KEY"),)


def secrets_where(env=None, home=None):
    """[{name, purpose, where, env, file, file_mode, problem}] for every secret pai knows: whether it is set
    and where it should live. Values are never read into the result: only presence, and file modes."""
    import os
    import stat as st
    env = os.environ if env is None else env
    rows = []
    for name, purpose, where in SECRETS:
        row = {"name": name, "purpose": purpose, "where": where, "env": bool(env.get(name)), "file": None,
               "file_mode": None, "problem": None}
        for path, key in SECRET_FILES:
            if key != name:
                continue
            full = os.path.expanduser(path) if home is None else os.path.join(home, path[2:])
            if os.path.isfile(full):
                mode = st.S_IMODE(os.stat(full).st_mode)
                row.update(file=path, file_mode="%03o" % mode)
                if mode & 0o077:
                    row["problem"] = "%s is readable or writable by others (mode %03o): chmod 600 %s" % (path, mode, path)
        rows.append(row)
    return rows


def _secrets_add(sub, parsers):
    s = sub.add_parser("secrets", help="where each key pai uses belongs, and whether it is set (never its value)")
    s.add_argument("scmd", choices=("where",))
    s.add_argument("--json", action="store_true")


def _secrets_run(a, load_project):
    if a.cmd != "secrets":
        return None
    import json
    rows = secrets_where()
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            have = "set in the environment" if r["env"] else "not in the environment"
            if r["file"]:
                have += "; %s exists (mode %s)" % (r["file"], r["file_mode"])
            print("%-25s %s" % (r["name"], have))
            print("%-25s for: %s" % ("", r["purpose"]))
            print("%-25s belongs in: %s" % ("", r["where"]))
            if r["problem"]:
                print("%-25s PROBLEM: %s" % ("", r["problem"]))
    return 1 if any(r["problem"] for r in rows) else 0


SECRETS_WHERE = Feature("secrets", _secrets_add, _secrets_run)


def _doctor_secrets(report, key, project):
    for r in secrets_where():
        if r["problem"]:
            report.warn("%s: %s" % (key, r["problem"]))


doctor_check(_doctor_secrets)
