"""pai workbench defaults: one TOML format (PAI's own) in two places.

    global   $PAI_WORKBENCH_HOME/config.toml            defaults shared by every project
    project  $PAI_WORKBENCH_HOME/projects/NAME.toml     one checkout's defaults (written by `pai init`)

Precedence: explicit command-line flag > project file > global file > built-in default.
One-time actions (--yes, cleanup --apply, --stop-running, setup --install-missing) are never
defaults, and API keys never belong in these files: a key-like setting is rejected.

Updates are made line by line so the operator's comments and layout survive; an unset value
is written as a commented-out line. Reading needs tomllib (Python 3.11+, as `pai` itself).
"""
import os
import re

import agents

try:
    import tomllib
except ImportError:  # Python < 3.11: the workbench is run through `pai`, which needs 3.11
    tomllib = None

SECRET_RE = re.compile(r"(api[_-]?key|token|secret|password|passphrase|credential)", re.I)

# (table, key, kind, built-in default, comment). kind: str | int | float | bool | path | agent | list | checks
SCHEMA = [
    ("project", "name", "str", None, "project name (set by pai init)"),
    ("project", "repo", "path", None, "the checkout this project is (set by pai init)"),
    ("project", "common_dir", "path", None, "its Git directory (set by pai init; identifies it from any worktree)"),
    ("project", "github", "str", None, "OWNER/REPO for gh (pr open, next); from the remote URL when it is GitHub"),
    ("run", "default_agent", "agent", None, "agent for every lane when `pai run` has no --agent: %s"
     % ", ".join(agents.names())),
    ("run", "workers", "int", 1, "pai run --workers"),
    ("run", "remote", "str", "origin", "remote a new run fetches (pai run --remote)"),
    ("run", "base", "str", None, "base branch a new run starts from (pai run --base); unset: the remote's default"),
    ("run", "tasks", "path", None, "Obsidian folder of task notes; unset: no queue"),
    ("run", "worktree_root", "path", None, "lane worktrees live here, one per lane, reused across runs"),
    ("run", "tmux_socket", "str", None, "tmux socket name (or path) for this project's workers"),
    ("run", "wait", "float", 10.0, "seconds pai run/resume/next wait for lanes to start and take their task"),
    ("run", "dispatch", "bool", True, "send each lane its task when it is ready (pai run --no-dispatch turns it off once)"),
    ("review", "checks", "checks", None, "approved check commands, NAME = [argv]; only these ever run"),
    ("review", "required_checks", "list", None, "checks that always run before pr open"),
    ("review", "check_timeout_min", "int", 45, "minutes before a check is stopped"),
    ("review", "strip_trailers", "list", None, "trailer keys `pai pr draft` removes from PR text (case-insensitive)"),
    ("logs", "tail", "int", 60, "pai logs --tail"),
]
AGENT_KEYS = [("mode", "launch mode"), ("model", "model; unset: the agent's own default"),
              ("effort", "reasoning effort; unset: the agent's own default")]


class ConfigError(Exception):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("; ".join("%s: %s" % kv for kv in problems))


def home():
    h = os.environ.get("PAI_WORKBENCH_HOME") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.join("~", ".config"), "pai-workbench")
    return os.path.realpath(os.path.expanduser(h))


def global_path():
    return os.path.join(home(), "config.toml")


def project_path(name):
    return os.path.join(home(), "projects", name + ".toml")


def parse(text, where="config"):
    if tomllib is None:
        raise ConfigError([(where, "reading TOML needs Python 3.11 or newer (run it through `pai`)")])
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([(where, "not valid TOML: %s" % e)])


def load(path):
    """The parsed file, {} when it does not exist. Raises ConfigError when it is invalid."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    data = parse(text, path)
    problems = validate(data, scope="project" if os.sep + "projects" + os.sep in path else "global")
    if problems:
        raise ConfigError(problems)
    return data


def validate(data, scope="project"):
    """[(dotted key, problem)]: unknown keys, wrong types, unsupported agent values, secrets."""
    problems = []
    known = {(t, k): kind for t, k, kind, _, _ in SCHEMA}
    for table, body in data.items():
        if SECRET_RE.search(table):
            problems.append((table, "API keys and other credentials never belong in this file"))
            continue
        if table == "agents":
            if not isinstance(body, dict):
                problems.append(("agents", "must be a table of [agents.NAME] tables"))
                continue
            for name, ab in body.items():
                ad = agents.get(name)
                if not ad:
                    problems.append(("agents.%s" % name, "unknown agent (known: %s)" % ", ".join(agents.names())))
                    continue
                for k, v in (ab or {}).items():
                    key = "agents.%s.%s" % (name, k)
                    if SECRET_RE.search(k):
                        problems.append((key, "API keys and other credentials never belong in this file"))
                    elif k not in dict(AGENT_KEYS) or not isinstance(v, str):
                        problems.append((key, "unknown setting" if k not in dict(AGENT_KEYS) else "must be a string"))
                    else:
                        why = agent_value_problem(ad, k, v)
                        if why:
                            problems.append((key, why))
            continue
        if table == "project" and scope == "global":
            problems.append(("project", "project settings belong in a project file, not the global one"))
            continue
        if not isinstance(body, dict):
            problems.append((table, "unknown setting" if (table, "") not in known else "must be a table"))
            continue
        for k, v in body.items():
            key = "%s.%s" % (table, k)
            kind = known.get((table, k))
            if SECRET_RE.search(k):
                problems.append((key, "API keys and other credentials never belong in this file"))
            elif kind is None:
                problems.append((key, "unknown setting"))
            else:
                why = type_problem(kind, v)
                if why:
                    problems.append((key, why))
    rc_ = (data.get("review") or {})
    if isinstance(rc_.get("required_checks"), list) and isinstance(rc_.get("checks"), dict):
        for n in rc_["required_checks"]:
            if n not in rc_["checks"]:
                problems.append(("review.required_checks", "names %r, which review.checks does not define" % n))
    return problems


def type_problem(kind, v):
    if kind in ("str", "path"):
        return None if isinstance(v, str) and v else "must be a non-empty string"
    if kind == "agent":
        return None if v in agents.names() else "must be one of %s" % ", ".join(agents.names())
    if kind == "int":
        return None if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else "must be a whole number, 1 or more"
    if kind == "float":
        return None if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else "must be a number, 0 or more"
    if kind == "bool":
        return None if isinstance(v, bool) else "must be true or false"
    if kind == "list":
        return None if isinstance(v, list) and all(isinstance(x, str) for x in v) else "must be a list of strings"
    if kind == "checks":
        ok = isinstance(v, dict) and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", k) and isinstance(c, list) and c
                                         and all(isinstance(x, str) for x in c) for k, c in v.items())
        return None if ok else "must map a check name to a command argv, e.g. verify = [\"make\", \"verify\"]"
    return "unknown kind"


def agent_value_problem(ad, key, value):
    if key == "mode":
        return None if value in ad.modes else "%s has no launch mode %r (it supports: %s)" % (ad.name, value, ", ".join(ad.modes))
    if key == "model":
        return ad.model_problem(value)
    if key == "effort":
        return ad.effort_problem(value)
    return "unknown setting"


def get(data, table, key):
    return (data.get(table) or {}).get(key)


def effective(project, global_, cli=None, agent_names=None):
    """{dotted key: (value, source)} with source built-in | global | project | command line."""
    cli = cli or {}
    res = {}
    for table, key, _, default, _ in SCHEMA:
        dk = "%s.%s" % (table, key)
        val, src = default, "built-in"
        if get(global_, table, key) is not None:
            val, src = get(global_, table, key), "global"
        if get(project, table, key) is not None:
            val, src = get(project, table, key), "project"
        if cli.get(dk) is not None:
            val, src = cli[dk], "command line"
        res[dk] = (val, src)
    for name in agent_names or agents.names():
        for key, _ in AGENT_KEYS:
            dk = "agents.%s.%s" % (name, key)
            val, src = (agents.get(name).default_mode(), "built-in") if key == "mode" else (None, "built-in (agent's own default)")
            for layer, label in ((global_, "global"), (project, "project")):
                v = ((layer.get("agents") or {}).get(name) or {}).get(key)
                if v is not None:
                    val, src = v, label
            if cli.get(dk) is not None:
                val, src = cli[dk], "command line"
            res[dk] = (val, src)
    return res


# ---------------------------------------------------------------- writing, comments kept

def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    if isinstance(v, list):
        return "[%s]" % ", ".join(toml_value(x) for x in v)
    raise TypeError(v)


def template(values, scope="project"):
    """A complete commented file: every supported default, grouped by command; unset values
    are commented-out examples."""
    out = ["# pai workbench %s defaults. Edit: `pai config edit%s`; check: `pai config check%s`." % (
        scope, " --global" if scope == "global" else "", " --global" if scope == "global" else ""),
        "# Precedence: command-line flag > project file > global file > built-in default.",
        "# One-time actions (--yes, cleanup --apply, --stop-running, setup --install-missing) are never",
        "# defaults. API keys never belong here.", ""]
    examples = {"run.default_agent": "claude", "run.base": "main", "run.tasks": "/path/to/Vault/Tasks",
                "run.worktree_root": "/path/to/lanes", "run.tmux_socket": "pai-myproject", "project.github": "OWNER/REPO",
                "review.required_checks": ["verify"]}
    titles = {"project": "the checkout", "run": "pai run / resume / next", "review": "pai pr check / open",
              "logs": "pai logs"}
    for table in ("project", "run", "review", "logs"):
        if table == "project" and scope == "global":
            continue
        out.append("[%s]   # %s" % (table, titles[table]))
        for t, key, kind, default, comment in SCHEMA:
            if t != table:
                continue
            v = (values.get(table) or {}).get(key)
            if kind == "checks":
                continue
            if v is not None:
                out.append("%s = %s   # %s" % (key, toml_value(v), comment))
            else:
                ex = examples.get("%s.%s" % (table, key), default)
                out.append("# %s = %s   # %s%s" % (key, toml_value(ex) if ex is not None else '""', comment,
                                                    "" if default is None else " (built-in: %s)" % toml_value(default)))
        out.append("")
        if table == "review":
            out.append("[review.checks]   # NAME = [command argv]; only these ever run; task notes cannot add commands")
            checks = (values.get("review") or {}).get("checks") or {}
            out += ["%s = %s" % (k, toml_value(v)) for k, v in checks.items()] or ['# verify = ["make", "verify"]']
            out.append("")
    for name in agents.names():
        ad = agents.get(name)
        cur = (values.get("agents") or {}).get(name) or {}
        out.append("[agents.%s]   # used only for %s lanes; nothing carries over between agents" % (name, name))
        for key, comment in AGENT_KEYS:
            choices = {"mode": ", ".join(ad.modes), "effort": ", ".join(ad.efforts) or "not supported"}.get(key)
            text = "%s%s" % (comment, " (%s)" % choices if choices else "")
            if cur.get(key) is not None:
                out.append("%s = %s   # %s" % (key, toml_value(cur[key]), text))
            else:
                ex = {"mode": ad.default_mode(), "model": ad.example_model, "effort": (ad.efforts or ("",))[-2 if len(
                    ad.efforts) > 1 else 0]}[key]
                out.append("# %s = %s   # %s" % (key, toml_value(ex), text))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def set_values(text, updates):
    """Apply {(table, key): value or None} to TOML text line by line, keeping every other line
    (comments included). None comments the setting out."""
    lines = text.split("\n")
    for (table, key), value in updates.items():
        header = re.compile(r"^\s*\[\s*%s\s*\]" % re.escape(table))
        start = next((i for i, l in enumerate(lines) if header.match(l)), None)
        if start is None:
            lines += ["", "[%s]" % table]
            start = len(lines) - 1
        end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
        live = re.compile(r"^\s*%s\s*=" % re.escape(key))
        dead = re.compile(r"^\s*#\s*%s\s*=" % re.escape(key))
        idx = next((i for i in range(start + 1, end) if live.match(lines[i])), None)
        if idx is None:
            idx = next((i for i in range(start + 1, end) if dead.match(lines[i])), None)
        comment = ""
        if idx is not None and "   # " in lines[idx]:
            comment = "   # " + lines[idx].split("   # ", 1)[1]
        new = ("# %s = \"\"" % key if value is None else "%s = %s" % (key, toml_value(value))) + comment
        if value is None and idx is not None and dead.match(lines[idx]):
            continue
        if idx is None:
            j = end
            while j > start + 1 and not lines[j - 1].strip():
                j -= 1
            lines.insert(j, new)
        else:
            lines[idx] = new
    return "\n".join(lines)


def write(path, text, scope="project"):
    """Validate, then replace atomically. Raises ConfigError and leaves the file as it was."""
    problems = validate(parse(text, path), scope)
    if problems:
        raise ConfigError(problems)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def flat(data):
    """The project dict the workbench uses, from a parsed project file."""
    p, r, rv = data.get("project") or {}, data.get("run") or {}, data.get("review") or {}
    review = {k: v for k, v in rv.items() if k in ("checks", "required_checks", "check_timeout_min")}
    return {"name": p.get("name"), "repo": p.get("repo"), "common_dir": p.get("common_dir"), "github": p.get("github"),
            "default_agent": r.get("default_agent"), "workers": r.get("workers"), "remote": r.get("remote"),
            "base_branch": r.get("base"), "tasks": r.get("tasks"), "worktree_root": r.get("worktree_root"),
            "tmux_socket": r.get("tmux_socket"), "wait": r.get("wait"), "dispatch": r.get("dispatch"),
            "review": review if review.get("checks") else {}, "agents": data.get("agents") or {},
            "logs_tail": (data.get("logs") or {}).get("tail"), "strip_trailers": rv.get("strip_trailers")}
