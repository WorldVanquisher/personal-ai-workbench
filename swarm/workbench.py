"""pai workbench: the operator's command line over the swarm launcher.

    pai init       project defaults: default agent, remote/base, task notes, review checks
    pai doctor     system, tools, agent install/login/Remote Control, SSH access (read-only)
    pai setup      what is missing and how it would be installed (--install-missing runs it)
    pai run        start a batch: one tmux window, worktree, branch and agent per lane
    pai status     process, task dispatch and Remote Control evidence per lane
    pai attach     open a lane's window          pai logs     a lane's screen and scrollback
    pai trust      walk through every lane waiting for folder trust, from this terminal
    pai resume     restart missing workers and deliver undelivered tasks (no fetch)
    pai pr         plan | check | open a lane's pull request
    pai next       after a merged PR: the lane's next task on a new branch
    pai cleanup    preview, or --apply: close exited workers' windows

Everything below reuses rc.py (tmux, records, dispatch, cleanup), gitprep.py (fetch, pin,
branches, SSH preflight), wtlock.py (locks), review.py (checks, publication guard) and
doctor.py (platform and installs). Project defaults live in
$PAI_WORKBENCH_HOME (default ~/.config/pai-workbench); run state in $PAI_SWARM_HOME.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import string
import subprocess
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agents  # noqa: E402
import gitprep  # noqa: E402
import notes  # noqa: E402
import rc  # noqa: E402
import wbconfig  # noqa: E402
import wtlock  # noqa: E402

PROJECT_SCHEMA = "pai_workbench_project_v1"
LANES = list(string.ascii_lowercase)
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
REVIEW_FILES = ("AGENTS.md", "CLAUDE.md", ".claude", ".codex", ".mcp.json", ".cursor", ".github/workflows",
                ".pre-commit-config.yaml", ".husky", "lefthook.yml", "package.json", "Makefile", ".envrc",
                ".devcontainer", ".vscode/tasks.json")


class Stop(Exception):
    """A refusal: print the message, exit with the code; nothing further happens."""

    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def log(msg=""):
    print(msg, flush=True)


def interactive(a=None):
    return sys.stdin.isatty() and not getattr(a, "no_input", False)


def ask(question, default=None):
    sys.stderr.write("%s%s: " % (question, " [%s]" % default if default else ""))
    sys.stderr.flush()
    answer = input().strip()
    return answer or (default or "")


# ---------------------------------------------------------------- places

def config_home():
    return wbconfig.home()


def data_home():
    h = os.environ.get("PAI_WORKBENCH_DATA") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.join("~", ".local", "share"), "pai-workbench")
    return os.path.realpath(os.path.expanduser(h))


def runs_dir(project):
    return os.path.join(wtlock.home(), "workbench", "runs", project)


def _write_json(path, data, exclusive=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        return
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- repository and project

def enclosing_repo(path):
    """The main checkout of the repository that `path` is in (also from a linked worktree or
    a subdirectory), or None."""
    cdir = gitprep.common_dir(path)
    if not cdir:
        return None
    top = gitprep.toplevel(path)
    if os.path.basename(cdir) == ".git" and os.path.isdir(os.path.dirname(cdir)):
        main = os.path.dirname(cdir)
        if gitprep.common_dir(main) == cdir:
            return main
    return top


def projects(strict=True):
    """Every project file. strict=False (pai config) also returns files that do not validate,
    so they can be checked and fixed."""
    d = os.path.join(config_home(), "projects")
    res = []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if fn.endswith(".toml"):
            try:
                if strict:
                    raw = wbconfig.load(os.path.join(d, fn))
                else:
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        try:
                            raw = wbconfig.parse(f.read())
                        except wbconfig.ConfigError:
                            raw = {"project": {"name": fn[:-5]}}
            except wbconfig.ConfigError as e:
                raise Stop("pai: %s is invalid: %s. Fix it with `pai config edit --project %s`." % (
                    os.path.join(d, fn), e, fn[:-5]), 2)
            p = wbconfig.flat(raw)
            if p.get("name"):
                p["_raw"] = raw
                res.append(p)
    return res


def project_for(repo, strict=True):
    cdir = gitprep.common_dir(repo) if repo else None
    found = [p for p in projects(strict) if cdir and p.get("common_dir") == cdir]
    return found[0] if found else None


def project_path(name):
    return wbconfig.project_path(name)


def remotes(repo):
    return (gitprep.out(repo, "remote") or "").split()


def infer_remote(repo):
    rs = remotes(repo)
    return "origin" if "origin" in rs else (rs[0] if len(rs) == 1 else None)


def infer_base(repo, remote):
    """The remote's default branch as last recorded locally (refs/remotes/R/HEAD); no network."""
    ref = gitprep.out(repo, "symbolic-ref", "--quiet", "refs/remotes/%s/HEAD" % remote) if remote else None
    prefix = "refs/remotes/%s/" % remote
    return ref[len(prefix):] if ref and ref.startswith(prefix) else None


def remote_default_branch(url_or_remote, cwd):
    """The remote's default branch from `git ls-remote --symref` (read-only network), or None."""
    try:
        p = gitprep.git_net(cwd, "ls-remote", "--symref", url_or_remote, "HEAD", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^ref: refs/heads/(\S+)\tHEAD", p.stdout, re.M) if p.returncode == 0 else None
    return m.group(1) if m else None


def infer_checks(repo):
    """Review checks that can be read reliably from the repository: a Makefile `test` target,
    or package.json's `test` script. Never anything from a task note."""
    found = {}
    try:
        with open(os.path.join(repo, "Makefile"), encoding="utf-8") as f:
            if re.search(r"^test\s*:", f.read(), re.M):
                found["test"] = ["make", "test"]
    except OSError:
        pass
    if not found:
        pkg = _read_json(os.path.join(repo, "package.json")) or {}
        if isinstance(pkg.get("scripts"), dict) and pkg["scripts"].get("test"):
            found["test"] = ["npm", "test"]
    return found


def parse_check(item):
    name, sep, cmd = item.partition("=")
    if not sep or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", name.strip()) or not cmd.strip():
        raise Stop("a review check looks like NAME=COMMAND (for example test=make test), not %r" % item, 2)
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        raise Stop("check %s: %s" % (name, e), 2)
    return name.strip(), argv


def agent_table():
    rows = []
    for name in agents.names():
        ad = agents.get(name)
        path = shutil.which(ad.program)
        rows.append((name, path, "Remote Control: %s" % ("yes (mode rc)" if ad.rc_modes else "not supported")))
    return rows


# ---------------------------------------------------------------- pai init

def settings(proj=None):
    """Built-in < global < project, as the flat dict the workbench uses."""
    g = wbconfig.load(wbconfig.global_path())
    eff = wbconfig.effective(proj.get("_raw") if proj else {}, g)
    flat_ = wbconfig.flat({t: {k.split(".", 1)[1]: v for k, (v, _) in eff.items() if k.startswith(t + ".")}
                           for t in ("project", "run", "review", "logs")})
    flat_["agents"] = {}
    for name in agents.names():
        merged = dict((g.get("agents") or {}).get(name) or {})
        merged.update(((proj or {}).get("agents") or {}).get(name) or {})
        flat_["agents"][name] = merged
    if proj:
        for k in ("name", "repo", "common_dir", "github"):
            flat_[k] = proj.get(k)
    return flat_


def cmd_init(a):
    start = os.path.abspath(a.repo or os.getcwd())
    repo = enclosing_repo(start)
    if not repo:
        raise Stop("pai init: %s is not inside a Git repository. Run it in your checkout, or pass --repo PATH." % start, 2)
    cdir = gitprep.common_dir(repo)
    old = project_for(repo)
    name = a.name or (old or {}).get("name") or re.sub(r"[^a-z0-9-]+", "-", os.path.basename(repo).lower()).strip("-")[:32]
    if not NAME_RE.fullmatch(name or ""):
        raise Stop("pai init: project name %r: use lower-case letters, digits and -" % name, 2)
    path = wbconfig.project_path(name)
    if not old and os.path.exists(path):
        raise Stop("pai init: a project named %s already exists for another repository (%s); choose --name" % (name, path), 2)
    tty = interactive(a)
    if old and not tty and not a.update:
        raise Stop("pai init: %s is already configured (%s). Nothing was changed; pass --update to change only the "
                   "settings you give, or edit it with `pai config edit`." % (repo, path), 1)
    cur = old or {}
    upd = {}  # (table, key) -> value: only what this init decides
    log("repository  %s" % repo)
    if old:
        log("existing    %s (kept unless you change a value)" % path)
    table = agent_table()
    choice = a.default_agent
    if choice is None and tty:
        log("\nAgents pai can launch:")
        for i, (n, p_, rcinfo) in enumerate(table, 1):
            log("  %d) %-7s %-40s %s" % (i, n, ("installed: %s" % p_) if p_ else "not installed", rcinfo))
        log("  %d) not set yet (choose per run with --agent)" % (len(table) + 1))
        while choice is None:
            ans = ask("Default agent", cur.get("default_agent") or "not set yet").lower()
            if ans.isdigit() and 1 <= int(ans) <= len(table) + 1:
                ans = table[int(ans) - 1][0] if int(ans) <= len(table) else "none"
            if ans in ("none", "not set yet", "not set"):
                choice = "none"
            elif agents.get(ans):
                choice = ans
            else:
                log("  answer with a number or a name: %s, or none" % ", ".join(agents.names()))
    if choice is None and not old:
        raise Stop("pai init: not a terminal, so nothing was asked. Pass --default-agent (%s, or none)."
                   % ", ".join(agents.names()), 2)
    if choice is not None:
        if choice != "none" and not agents.get(choice):
            raise Stop("pai init: unknown agent %r (known: %s, or none)" % (choice, ", ".join(agents.names())), 2)
        upd[("run", "default_agent")] = None if choice == "none" else choice
    agent = choice if choice not in (None, "none") else cur.get("default_agent")
    if agent:  # model and effort belong to this agent only; another agent's settings are never reused
        ad, mine = agents.get(agent), dict((cur.get("agents") or {}).get(agent) or {})
        model, effort = a.model, a.effort
        if tty:
            model = model or ask("Default model for %s (Enter: %s's own default)" % (agent, agent), mine.get("model") or "") or None
            if ad.efforts:
                effort = effort or ask("Reasoning effort for %s (%s; Enter: %s's own default)" % (
                    agent, ", ".join(ad.efforts), agent), mine.get("effort") or "") or None
        for key, val in (("model", model), ("effort", effort)):
            if val is not None:
                why = wbconfig.agent_value_problem(ad, key, val)
                if why:
                    raise Stop("pai init: agents.%s.%s: %s. Nothing was saved." % (agent, key, why), 2)
                upd[("agents.%s" % agent, key)] = val
    elif a.model or a.effort:
        raise Stop("pai init: --model and --effort belong to an agent; give --default-agent too", 2)
    remote = a.remote or cur.get("remote") or infer_remote(repo)
    if tty:
        remote = ask("Remote", remote)
    if not remote or remote not in remotes(repo):
        raise Stop("pai init: remote %r is not a remote of %s (it has: %s). Pass --remote." % (
            remote, repo, ", ".join(remotes(repo)) or "none"), 2)
    base = a.base or cur.get("base_branch") or infer_base(repo, remote)
    if tty:
        base = ask("Base branch", base or "main")
    if not base:
        raise Stop("pai init: the base branch of %s is not recorded locally. Pass --base BRANCH." % remote, 2)
    url = gitprep.out(repo, "config", "--get", "remote.%s.url" % remote) or ""
    tasks = a.tasks if a.tasks is not None else cur.get("tasks")
    if tty:
        tasks = ask("Task notes folder (Obsidian; blank for none)", tasks or "") or None
    if tasks:
        tasks = os.path.realpath(os.path.expanduser(tasks))
        if not os.path.isdir(tasks):
            raise Stop("pai init: task notes folder %s does not exist" % tasks, 2)
    checks = dict((cur.get("review") or {}).get("checks") or {})
    if a.check:
        checks = dict(parse_check(c) for c in a.check)
    elif tty:
        suggested = checks or infer_checks(repo)
        shown = ", ".join("%s=%s" % (k, " ".join(v)) for k, v in suggested.items())
        ans = ask("Review checks, NAME=COMMAND separated by ';' (blank keeps; '-' for none)", shown or "-")
        checks = {} if ans.strip() == "-" else dict(parse_check(x.strip()) for x in ans.split(";") if x.strip())
    root = a.worktree_root or cur.get("worktree_root") or os.path.join(os.path.dirname(repo),
                                                                       os.path.basename(repo) + "-pai-lanes")
    upd.update({("project", "name"): name, ("project", "repo"): repo, ("project", "common_dir"): cdir,
                ("run", "remote"): remote, ("run", "base"): base, ("run", "tasks"): tasks or None,
                ("run", "worktree_root"): os.path.realpath(os.path.expanduser(root))})
    if not cur.get("github") and gitprep.github_slug(url):
        upd[("project", "github")] = gitprep.github_slug(url)
    if not cur.get("tmux_socket"):
        upd[("run", "tmux_socket")] = "pai-%s" % name
    if a.workers:
        upd[("run", "workers")] = a.workers
    elif not old:
        upd[("run", "workers")] = 2
    if a.check or tty:
        upd[("review", "required_checks")] = sorted(checks) or None
    if old:
        with open(path, encoding="utf-8") as f:
            text = wbconfig.set_values(f.read(), upd)
    else:
        values = {}
        for (t, k), v in upd.items():
            if v is not None:
                values.setdefault(t, {})[k] = v
        nested = {"agents": {t.split(".", 1)[1]: v for t, v in values.items() if t.startswith("agents.")}}
        nested.update({t: v for t, v in values.items() if not t.startswith("agents.")})
        text = wbconfig.template(nested)
    if a.check or tty:
        text = set_checks(text, checks)
    data = wbconfig.parse(text, path)
    log("\nproject     %s" % name)
    for line in summary_lines(data):
        log("  " + line)
    if tty and ask("Save to %s? [y/N]" % path, "n").lower() not in ("y", "yes"):
        log("Nothing was saved.")
        return 1
    try:
        wbconfig.write(path, text)
    except wbconfig.ConfigError as e:
        raise Stop("pai init: not saved: %s" % e, 2)
    log("saved       %s" % path)
    jev_setup(a, tty)
    log("\nNext: pai doctor, then pai run --dry-run (pai config edit changes any default)")
    return 0


def set_checks(text, checks):
    """Replace the [review.checks] table's entries (comments outside it are kept)."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^\s*\[\s*review\.checks\s*\]", l)), None)
    body = ["%s = %s" % (k, wbconfig.toml_value(v)) for k, v in checks.items()] or ['# verify = ["make", "verify"]']
    if start is None:
        return text.rstrip("\n") + "\n\n[review.checks]\n" + "\n".join(body) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    keep = [l for l in lines[start + 1:end] if l.strip().startswith("#") and "=" not in l]
    return "\n".join(lines[:start + 1] + keep + body + [""] + lines[end:])


def summary_lines(data):
    f = wbconfig.flat(data)
    out = ["%-14s %s" % (k, f.get(k) if f.get(k) is not None else "not set")
           for k in ("default_agent", "remote", "base_branch", "tasks", "worktree_root", "tmux_socket", "workers")]
    for name, ab in sorted(f["agents"].items()):
        out.append("%-14s %s" % ("agent " + name, ", ".join("%s %s" % kv for kv in sorted(ab.items())) or "agent defaults"))
    out.append("%-14s %s" % ("review checks", ", ".join("%s=%s" % (k, " ".join(v)) for k, v in
                                                       ((f.get("review") or {}).get("checks") or {}).items()) or "none"))
    return out


JEV_PROMPT = "Enter JEV API key (optional): "


def jev_setup(a, tty):
    """Optional, terminal only: one hidden prompt. Enter skips and keeps whatever is set up
    already. A key goes to the credential store when there is one, never to a pai file."""
    if not tty or getattr(a, "jev", "ask") == "skip":
        return
    import getpass
    import jev
    have, where = jev.api_key()
    try:
        key = getpass.getpass(JEV_PROMPT).strip()
    except (EOFError, KeyboardInterrupt):
        key = ""
    if not key:
        log("JEV: skipped. %s" % ("Your existing key (%s) is kept as it is." % {"env": "TYPESAFE_API_KEY"}.get(where, where)
                                  if have else "JEV stays off; PAI works without it. Set it up later with "
                                  "`pai init --update` in a terminal, or export TYPESAFE_API_KEY."))
        return
    stored = jev.store_put(key)
    if stored:
        log("JEV: key saved in your credential store (%s); pai reads it from there." % stored)
    else:
        log("JEV: no credential store here (secret-tool or the macOS Keychain), so the key was not saved anywhere. "
            "For later runs, put `export TYPESAFE_API_KEY=...` in a file only you can read (chmod 600) and "
            "source it from your shell profile; pai never writes it for you.")
    jev_check(key, assume_yes=False)


def jev_check(key, assume_yes):
    import jev
    log("\nJEV check: this sends ONE real request to %s with a fixed synthetic task (no repository content, no "
        "notes). It may be billed by TypeSafe." % os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai"))
    if not assume_yes:
        if not sys.stdin.isatty():
            log("Not a terminal: add --yes to send it. Nothing was sent.")
            return 2
        if ask("Send it? [y/N]", "n").lower() not in ("y", "yes"):
            log("Nothing was sent.")
            return 1
    res = jev.smoke(key)
    label = {"ok": "ok", "invalid-key": "INVALID KEY", "rate-limited": "RATE LIMITED", "timeout": "TIMEOUT",
             "unexpected": "UNEXPECTED REPLY", "error": "ERROR"}[res["state"]]
    log("JEV check: %s: %s" % (label, res["detail"].replace(key, "<key>")))
    if res["answers"]:
        log("           synthetic task: actionable %.2f, tier %s" % (res["answers"]["actionable"], res["answers"]["tier"]))
    log("           usage reported by the API: %s" % (json.dumps(res["usage"]) if res["usage"] else "none in the reply"))
    if res["state"] != "ok":
        log("           Not retried. PAI keeps working with JEV off; run `pai jev check` again later.")
    return 0 if res["state"] == "ok" else 1


def cmd_jev(a):
    import jev
    key, where = jev.api_key()
    if a.jev_cmd == "status":
        log("JEV: %s" % ("key available from %s" % {"env": "TYPESAFE_API_KEY"}.get(where, where) if key else
                         "not set up (optional): TYPESAFE_API_KEY is unset and the credential store has no key"))
        return 0
    if not key:
        log("JEV: no key (TYPESAFE_API_KEY or the credential store); nothing was sent.")
        return 1
    return jev_check(key, a.yes)


# ---------------------------------------------------------------- managed checkouts (remote --repo)

def is_url(s):
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", s or "")) or bool(re.match(r"^[^/:\s]+@[^/:\s]+:", s or ""))


def checkout_dest(url):
    path = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", url)
    path = re.sub(r"^[^@/]+@", "", path).replace(":", "/")
    path = re.sub(r"\.git/?$", "", path).strip("/")
    parts = [re.sub(r"[^A-Za-z0-9._-]+", "-", x) for x in path.split("/") if x not in ("", ".", "..")]
    return os.path.join(data_home(), "checkouts", *parts)


def checkouts():
    return _read_json(os.path.join(data_home(), "checkouts.json")) or {}


def save_checkouts(d):
    _write_json(os.path.join(data_home(), "checkouts.json"), d)


def review_list(repo):
    """Files in a checkout that can make an agent or tool run code or take instructions."""
    found = []
    for rel in REVIEW_FILES:
        if os.path.lexists(os.path.join(repo, rel)):
            found.append(rel + ("/" if os.path.isdir(os.path.join(repo, rel)) else ""))
    hooks = gitprep.out(repo, "config", "--get", "core.hooksPath")
    if hooks:
        found.append("core.hooksPath=%s" % hooks)
    return found


def resolve_remote_checkout(a, dry):
    """(repo path, info) for --repo URL. Reuses a checkout only if pai recorded it for this
    URL and its origin still is that URL; refuses anything else at the destination."""
    url = a.repo
    dest = os.path.realpath(os.path.expanduser(a.checkout_dir)) if a.checkout_dir else checkout_dest(url)
    rec = checkouts().get(dest)
    info = {"url": gitprep.redact_url(url), "dest": dest, "state": None, "reviewed": bool(rec and rec.get("reviewed_at"))}
    if not os.path.lexists(dest):
        info["state"] = "clone"
        return dest, info
    if not rec:
        raise Stop("pai run: %s already exists and pai did not create it; nothing was changed. Choose another "
                   "--checkout-dir, or run pai from that checkout if it is yours." % dest)
    if rec.get("url") != url:
        raise Stop("pai run: %s was created by pai for %s, not %s; nothing was changed." % (
            dest, gitprep.redact_url(rec.get("url")), gitprep.redact_url(url)))
    if gitprep.toplevel(dest) != dest or gitprep.out(dest, "remote", "get-url", "origin") != url:
        raise Stop("pai run: %s no longer is the checkout pai made for %s (its origin or layout changed); nothing "
                   "was changed." % (dest, gitprep.redact_url(url)))
    info["state"] = "reuse"
    return dest, info


def clone(url, dest):
    if gitprep.transport(url) == "ssh":
        res = probe_url(url)
        if not res["ok"]:
            raise Stop("\n".join(gitprep.auth_lines(res, "Nothing was cloned or started.")))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        p = gitprep.git_net(os.path.dirname(dest), "clone", "--no-recurse-submodules", "--origin", "origin", url, dest,
                            timeout=900)
    except subprocess.TimeoutExpired:
        raise Stop("pai run: cloning %s timed out; nothing was started." % gitprep.redact_url(url))
    if p.returncode != 0:
        raise Stop("pai run: git clone failed: %s. Nothing was started." % gitprep.redact_url(p.stderr.strip()[-300:]))
    d = checkouts()
    d[dest] = {"url": url, "cloned_at": gitprep.now(), "reviewed_at": None}
    save_checkouts(d)


def probe_url(url):
    """remote_auth for a URL that has no checkout yet."""
    user, host, port = gitprep.ssh_target(url)
    res = {"ok": False, "push": False, "remote_name": "(clone)", "url": gitprep.redact_url(url), "transport": "ssh",
           "host": host, "steps": []}
    try:
        p = gitprep.git_net(os.sep, "ls-remote", url, "HEAD", timeout=30)
        if p.returncode == 0:
            res.update(ok=True, state="ok", detail="ssh answered without a prompt")
            return res
        err = p.stderr
    except subprocess.TimeoutExpired:
        err = "timed out"
    agent = gitprep.agent_state()
    keys = gitprep.identity_files(user, host, port)
    add = "ssh-add %s" % shlex.quote(keys[0]) if keys else "ssh-add"
    res.update(state="auth", detail="%s did not answer without a prompt: %s" % (host, gitprep.redact_url(err.strip())[-160:]))
    res["steps"] = (['eval "$(ssh-agent -s)"'] if agent == "no-agent" else []) + [
        add + "   # asks for the passphrase itself, once", "ssh-add -l", "git ls-remote %s HEAD" % shlex.quote(url)]
    return res


# ---------------------------------------------------------------- runs

def run_ids(project):
    d = runs_dir(project)
    return sorted(x for x in os.listdir(d) if os.path.isfile(os.path.join(d, x, "manifest.json"))) if os.path.isdir(d) else []


def run_manifest(project, run_id):
    return os.path.join(runs_dir(project), run_id, "manifest.json")


def run_active(m):
    own = rc.load_own(m)
    if any(l.get("window_id") for l in (own.get("lanes") or {}).values()):
        return True
    return any(wtlock.owned(wtlock.read(w.worktree), "rc", m.run_id, w.lane) for w in m.workers)


def load_run(project, run_id):
    return rc.Manifest(run_manifest(project, run_id))


def current_project(a, strict=True):
    if getattr(a, "project", None):
        p = next((q for q in projects(strict) if q["name"] == a.project), None)
        if not p:
            raise Stop("pai: no project named %s (%s)" % (a.project, project_path(a.project)), 2)
        return p
    repo = enclosing_repo(os.getcwd())
    p = project_for(repo, strict) if repo else None
    if p:
        return p
    # a run may also be found from inside one of its lane worktrees
    for q in projects(strict):
        root = q.get("worktree_root") or ""
        if root and gitprep.under(os.path.realpath(os.getcwd()), root):
            return q
    names = [q["name"] for q in projects()]
    raise Stop("pai: no pai project here (%s).%s" % (
        os.getcwd(), " Known projects: %s; pass --project NAME." % ", ".join(names) if names else
        " Run `pai init` in your checkout first."), 2)


def select_run(a, project):
    ids = run_ids(project["name"])
    if a.run:
        if a.run not in ids:
            raise Stop("pai: project %s has no run %s (runs: %s)" % (project["name"], a.run, ", ".join(ids) or "none"), 2)
        return load_run(project["name"], a.run)
    if not ids:
        raise Stop("pai: project %s has no run yet. Start one with `pai run`." % project["name"], 2)
    ms = [load_run(project["name"], i) for i in ids]
    active = [m for m in ms if run_active(m)]
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise Stop("pai: more than one run of %s is active: %s. Pass --run ID." % (
            project["name"], ", ".join(m.run_id for m in active)), 2)
    log("(no active run; using the most recent, %s)" % ms[-1].run_id)
    return ms[-1]


def ns(**kw):
    base = dict(json=False, no_input=True, want=None, expect_base=None, jev_triage=False, settle=5.0, lane=None,
                no_dispatch=False, branch=None, task=None, plan=False, apply=False, stop_running=False, pai=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def lane_values(items, lanes, what):
    """LANE=VALUE pairs, or one bare VALUE for every lane."""
    res = {}
    for item in items or []:
        k, sep, v = item.partition("=")
        if sep and k.isdigit() and 1 <= int(k) <= len(lanes):
            k = lanes[int(k) - 1]  # --model 2=NAME: the second lane
        if sep:
            if k not in lanes:
                raise Stop("pai run: %s names lane %r; this run's lanes are %s" % (what, k, ", ".join(lanes)), 2)
            res[k] = v
        else:
            res["*"] = item
    return res


# ---------------------------------------------------------------- pai run

RUN_EXAMPLES = """  pai run --workers 2                                   (inside a Git checkout)
  pai run --repo /path/to/local/repo --workers 2
  pai run --repo https://github.com/OWNER/REPO.git --base main --workers 2 --agent claude --agent codex
  pai run --repo https://github.com/OWNER/REPO.git --checkout-dir /path/to/checkout --workers 2 --dry-run"""


def cmd_run(a):
    dry = a.dry_run
    remote_info = None
    if a.repo and is_url(a.repo):
        repo, remote_info = resolve_remote_checkout(a, dry)
    elif a.repo:
        repo = enclosing_repo(os.path.abspath(os.path.expanduser(a.repo)))
        if not repo:
            raise Stop("pai run: %s is not a Git repository; nothing was changed." % a.repo, 2)
    else:
        repo = enclosing_repo(os.getcwd())
        if not repo:
            raise Stop("pai run: %s is not inside a Git repository, and no --repo was given. Nothing was changed.\n"
                       "Examples:\n%s" % (os.getcwd(), RUN_EXAMPLES), 2)
    exists = os.path.isdir(repo)
    proj = project_for(repo) if exists else None
    cfg = settings(proj)  # built-in < global < project; flags below override for this run only
    name = (proj or {}).get("name") or re.sub(r"[^a-z0-9-]+", "-", os.path.basename(repo).lower()).strip("-")[:32] or "repo"
    remote = a.remote or (proj or {}).get("remote") or (infer_remote(repo) if exists else "origin")
    if not remote:
        raise Stop("pai run: cannot tell which remote to fetch (%s). Pass --remote, or run `pai init`." % ", ".join(
            remotes(repo)) or "no remotes", 2)
    base = a.base or cfg.get("base_branch") or (infer_base(repo, remote) if exists else None)
    if not base and not dry:
        base = remote_default_branch(a.repo if remote_info else remote, repo if exists else os.sep)
    # lanes and agents
    n = a.workers or (len(a.agent) if a.agent and len(a.agent) > 1 else None) or cfg.get("workers") or 1
    if not 1 <= n <= len(LANES):
        raise Stop("pai run: --workers must be between 1 and %d" % len(LANES), 2)
    lanes = LANES[:n]
    if a.agent and len(a.agent) not in (1, n):
        raise Stop("pai run: %d --agent values for %d workers: give one (every lane) or exactly one per lane, in "
                   "lane order (%s)" % (len(a.agent), n, ", ".join(lanes)), 2)
    chosen = list(a.agent or [])
    if not chosen:
        default = cfg.get("default_agent")
        if default:
            chosen = [default]
        elif interactive(a):
            ans = ask("No default agent is set. Agent for all %d lanes (%s)" % (n, ", ".join(agents.names())))
            chosen = [ans.strip()]
        else:
            raise Stop("pai run: no agent chosen and no default set. Pass --agent NAME (once for every lane, or once "
                       "per lane), or set one with `pai init --update --default-agent NAME`.", 2)
    per_agent = {lane: chosen[0] if len(chosen) == 1 else chosen[i] for i, lane in enumerate(lanes)}
    models = lane_values(a.model, lanes, "--model")
    efforts = lane_values(a.effort, lanes, "--effort")
    modes = lane_values(a.mode, lanes, "--mode")

    def lane_setting(given, lane, key):
        """--x LANE=V, --x V, then the lane agent's own configured default; never another agent's."""
        v = given.get(lane, given.get("*"))
        return v if v is not None else (cfg["agents"].get(per_agent[lane]) or {}).get(key)
    tasks = lane_values(a.task, lanes, "--task")
    branches = lane_values(a.branch, lanes, "--branch")
    if "*" in tasks or "*" in branches:
        raise Stop("pai run: --task and --branch take LANE=VALUE", 2)
    run_id = a.run_id or time.strftime("%Y%m%d-%H%M%S")
    if not rc.RUN_RE.fullmatch(run_id):
        raise Stop("pai run: run id %r must match %s" % (run_id, rc.RUN_RE.pattern), 2)
    if run_id in run_ids(name):
        if a.run_id:
            raise Stop("pai run: run %s already exists; `pai resume --run %s` continues it." % (run_id, run_id), 2)
        stem, k = run_id, 2
        while "%s-%d" % (stem, k) in run_ids(name):
            k += 1
        run_id = "%s-%d" % (stem, k)
    root = cfg.get("worktree_root") or os.path.join(os.path.dirname(repo), os.path.basename(repo) + "-pai-lanes")
    url = (gitprep.out(repo, "config", "--get", "remote.%s.url" % remote) if exists else None) or (a.repo if remote_info else None)
    d = {"schema": rc.SCHEMA, "run_id": run_id,
         "repository": {"path": repo, "remote": remote, "base_branch": base or "main",
                        "github": (proj or {}).get("github") or gitprep.github_slug(url or ""), "url": url},
         "worktree_root": root, "tmux": {"session": "pai-{run_id}",
                                         "socket": a.tmux_socket or cfg.get("tmux_socket") or "pai-%s" % name},
         "dispatch": not a.no_dispatch and cfg.get("dispatch") is not False, "branch_template": "pai/{run_id}/{lane}",
         "workers": [dict({"lane": l, "agent": per_agent[l], "name": "%s %s %s" % (name, run_id, l)},
                          **{k: lane_setting(g, l, k) for k, g in (("model", models), ("effort", efforts), ("mode", modes))
                             if lane_setting(g, l, k) is not None},
                          **({"task": tasks[l]} if l in tasks else {}),
                          **({"branch": branches[l]} if l in branches else {})) for l in lanes]}
    if cfg.get("tasks"):
        d["tasks"] = cfg["tasks"]
    if cfg.get("review"):
        d["review"] = cfg["review"]
    path = run_manifest(name, run_id)
    try:
        m = rc.Manifest(path, data=d)  # every agent, mode and model is validated before anything else
    except rc.ManifestError as e:
        raise Stop("pai run: %s. Nothing was changed." % e, 2)
    import doctor
    plat = doctor.detect_platform()
    if not plat["backend"]:
        raise Stop("pai run: no worker backend on %s: %s. Nothing was changed." % (plat["system"], plat["backend_note"] or
                                                                                  plat["why"]), 2)

    log("project   %s%s" % (name, "" if proj else " (not initialised: defaults below; `pai init` saves your own)"))
    if remote_info:
        log("source    %s" % remote_info["url"])
        log("checkout  %s (%s)" % (remote_info["dest"], {"clone": "would be cloned" if dry else "will be cloned",
                                                          "reuse": "pai's checkout of it, reused"}[remote_info["state"]]))
    log("repo      %s" % repo)
    log("base      %s/%s: %s" % (remote, base or "(remote default branch, resolved at execution)",
                                 "fetched and pinned to one commit when the run starts (not resolved yet)"))
    for w in m.workers:
        log("lane %-4s %-26s worktree %s%s" % (w.lane, w.describe_agent(), w.worktree, worktree_note(name, w)))
        if not w.adapter.has_rc(w.mode):
            log("          Remote Control: not supported (%s)" % w.adapter.rc_unsupported(w.mode))
    if dry and (not exists or remote_info and remote_info["state"] == "clone"):
        log("\nDRY RUN: nothing was cloned, fetched, created or started. Branches would be %s." % ", ".join(
            w.branch for w in m.workers))
        return 0
    if remote_info and remote_info["state"] == "reuse" and not remote_info["reviewed"]:
        show_review(repo, remote_info)
    if dry:
        specs, problems = rc.build_specs(m, "new")
        plans = [gitprep.plan(s, probe=False) for s in specs]
        problems += rc.preflight(m, rc.Tmux(m.socket), True) if specs else []
        rc.show_plan(m, specs, plans, problems, rc.Tmux(m.socket))
        blocked = bool(problems) or any(pl.blocked for pl in plans)
        log("\nDRY RUN: nothing was fetched, created, written or started; the base commit is resolved when the run "
            "starts.%s" % (" Blocked: fix the REFUSED lines first." if blocked else ""))
        return 1 if blocked else 0
    # every question first, then changes
    tty = interactive(a)
    if tty:
        try:
            rc.ask_branches([w for w in m.workers if w.branch_source != "command line"])
        except (EOFError, KeyboardInterrupt):
            raise Stop("\npai run: no answer; nothing was started.", 130)
        d["workers"] = [dict(x, branch=w.branch) for x, w in zip(d["workers"], m.workers)]
        m = rc.Manifest(path, data=d)
    if not a.yes:
        if not tty:
            raise Stop("pai run: not a terminal; add --yes to accept this plan (it never answers an agent's "
                       "folder-trust prompt). Nothing was changed.", 2)
        if ask("Start %d worker(s)? [y/N]" % n, "n").lower() not in ("y", "yes"):
            raise Stop("Nothing was started.", 1)
    if remote_info and remote_info["state"] == "clone":
        clone(a.repo, repo)
        remote_info["state"] = "reuse"
        show_review(repo, remote_info)
    if remote_info and not checkouts().get(repo, {}).get("reviewed_at"):
        if not (a.reviewed or (tty and ask("Start agents in %s? [y/N]" % repo, "n").lower() in ("y", "yes"))):
            raise Stop("pai run: the checkout is ready for review at %s; no agent was started. When you have reviewed "
                       "it, rerun with --reviewed (or answer yes in a terminal)." % repo, 1)
        c = checkouts()
        c[repo]["reviewed_at"] = gitprep.now()
        save_checkouts(c)
        # the manifest was validated before the clone; its repository now exists
        m = rc.Manifest(path, data=d)
    if not base:
        raise Stop("pai run: the remote's default branch could not be read; pass --base BRANCH.", 2)
    # Every refusal before any state is written: plan, agents, then remote access.
    specs, problems = rc.build_specs(m, "new")
    plans = [gitprep.plan(x, probe=False) for x in specs]
    problems += rc.preflight(m, rc.Tmux(m.socket), True) if specs else []
    if problems or any(pl.blocked for pl in plans):
        rc.show_plan(m, specs, plans, problems, rc.Tmux(m.socket))
        raise Stop("\npai run: REFUSED. Nothing was fetched, created, written or started.", 1)
    bad = rc.ssh_preflight(m)
    if bad:
        raise Stop("\n".join(("REFUSED   " + x) if not x.startswith(("    ", "Run ")) else x for x in bad), 1)
    _write_json(path, d, exclusive=True)
    _write_json(os.path.join(os.path.dirname(path), "run.json"), {"run_id": run_id, "project": name, "repo": repo,
                                                                   "created": gitprep.now()})
    m = rc.Manifest(path)
    code = rc.exclusive(m, rc.cmd_up, ns(want="new", settle=a.wait if a.wait is not None else cfg.get("wait") or 10.0,
                                         no_dispatch=not d["dispatch"], jev_triage=a.jev_triage))
    remember_worktrees(m)
    log("\nrun %s: `pai status`, `pai attach LANE`, `pai trust` (folder-trust prompts), `pai cleanup`." % run_id)
    return code


def worktree_note(project, w):
    """New or replaced directories get their own trust decision from the agent."""
    if not os.path.lexists(w.worktree):
        return "  (new directory: the agent will ask for folder trust)"
    seen = (_read_json(os.path.join(wtlock.home(), "workbench", "worktrees.json")) or {}).get(w.worktree)
    st = os.stat(w.worktree)
    if seen and seen != [st.st_dev, st.st_ino]:
        return "  (replaced since pai last used it: trust is not assumed)"
    return ""


def remember_worktrees(m):
    path = os.path.join(wtlock.home(), "workbench", "worktrees.json")
    d = _read_json(path) or {}
    for w in m.workers:
        if os.path.isdir(w.worktree):
            st = os.stat(w.worktree)
            d[w.worktree] = [st.st_dev, st.st_ino]
    _write_json(path, d)


def show_review(repo, info):
    found = review_list(repo)
    log("\nREVIEW    %s is a new checkout of %s. Cloning ran none of its code, but an agent started there may read "
        "its instructions and run its hooks, plugins and project commands." % (repo, info["url"]))
    log("          Files to look at before any agent starts: %s" % (", ".join(found) or "none of the usual ones"))


# ---------------------------------------------------------------- lane commands

def cmd_status(a):
    m = select_run(a, current_project(a))
    return rc.cmd_status(m, ns(json=a.json), pai=True)


def lane_window(m, lane):
    m.worker(lane)
    own = (rc.load_own(m).get("lanes") or {}).get(lane) or {}
    tmux = rc.Tmux(m.socket)
    proc = rc.lane_process(m, tmux, m.worker(lane))
    if proc["state"] in ("no-window", "ambiguous", "mismatched"):
        raise Stop("pai: lane %s has no window to open (%s). `pai resume --lane %s` starts it again." % (
            lane, proc.get("detail", proc["state"]), lane), 1)
    return tmux, proc["window"], own


def cmd_attach(a):
    m = select_run(a, current_project(a))
    tmux, win, _ = lane_window(m, a.lane)
    target = "=%s:%s" % (m.session, win["window_id"])
    inside = os.environ.get("TMUX", "").split(",")[0]
    same = inside and os.path.realpath(inside) == os.path.realpath(win["socket_path"])
    argv = tmux.base + (["switch-client", "-t", target] if same else ["attach-session", "-t", target])
    if a.print:
        log(" ".join(shlex.quote(x) for x in ["tmux"] + argv[1:]))
        return 0
    env = dict(os.environ)
    env.pop("TMUX", None)  # attaching to pai's own server from inside another tmux
    os.execvpe(argv[0], argv, env)


def cmd_logs(a):
    m = select_run(a, current_project(a))
    tmux, win, _ = lane_window(m, a.lane)
    lines = tmux.capture(win["pane_id"], history=True).rstrip("\n").split("\n")
    tail = a.tail or settings(current_project(a)).get("logs_tail") or 60
    for line in lines[-tail:]:
        print(line)
    if not a.follow:
        return 0
    prev = lines
    try:
        while True:
            time.sleep(1)
            cur = tmux.capture(win["pane_id"], history=True).rstrip("\n").split("\n")
            if cur != prev:
                k = min(len(prev), len(cur))  # the longest end of what was shown that starts the new capture
                while k and cur[:k] != prev[-k:]:
                    k -= 1
                for line in cur[k:]:
                    print(line, flush=True)
                prev = cur
    except KeyboardInterrupt:
        return 0


def cmd_resume(a):
    m = select_run(a, current_project(a))
    if a.redispatch or a.mark_delivered:
        if not a.lane:
            raise Stop("pai resume: --redispatch and --mark-delivered need --lane LANE", 2)
        return resolve_dispatch(m, a)
    code = rc.exclusive(m, rc.cmd_up, ns(want="resume", settle=a.wait, lane=[a.lane] if a.lane else None))
    remember_worktrees(m)
    return code


def resolve_dispatch(m, a):
    import dispatch
    cur = rc.current_lanes(gitprep.common_dir(m.repo), m.run_id)
    if a.lane not in cur:
        raise Stop("pai resume: lane %s has no recorded task" % a.lane, 2)
    rec, rl = cur[a.lane]
    w = m.worker(a.lane)
    tmux = rc.Tmux(m.socket)
    proc = rc.lane_process(m, tmux, w)
    hist = tmux.capture(proc["window"]["pane_id"], history=True) if proc.get("window") else ""
    st = dispatch.state(m, w, rl, proc, hist)
    if st["state"] not in ("unknown", "sent"):
        raise Stop("pai resume: lane %s's dispatch is %s, not unknown: %s" % (a.lane, st["state"], st["detail"]), 1)
    if a.mark_delivered:
        dispatch.mark_delivered(m, w, rl, proc)
        log("lane %s: task %s recorded as delivered by you; it will not be sent." % (a.lane, rl["note"]))
        return 0
    res = rc.exclusive(m, lambda m_, *_: _redispatch(m_, tmux, w, rec, rl))
    return res


def _redispatch(m, tmux, w, rec, rl):
    st = rc.dispatch_lane(m, tmux, w, rec, rl, force=True)
    log("lane %s: %s (%s)" % (w.lane, st["state"], st["detail"]))
    return 0 if st["state"] in ("delivered", "sent") else 1


def cmd_trust(a):
    """Foreground walk through every lane waiting for folder trust: open its prompt in this
    terminal, return here as soon as it is answered, go on to the next. Answering is yours."""
    m = select_run(a, current_project(a))
    tmux = rc.Tmux(m.socket)
    rows, _ = rc.collect_status(m, tmux)
    waiting = [r for r in rows if r["phase"] == "waiting-for-trust"]
    if not waiting:
        log("No lane of run %s is waiting for folder trust." % m.run_id)
        return 0
    log("Lanes waiting for folder trust in run %s:" % m.run_id)
    for r in waiting:
        log("  %-4s %-8s repository %s\n       %-8s worktree   %s" % (r["lane"], r["agent"]["name"], m.repo, "", r["worktree"]))
    if not sys.stdin.isatty():
        log("\nNot a terminal: answer each prompt with `pai attach LANE` (one window at a time), then `pai resume`.")
        return 1
    for r in waiting:
        ad = agents.get(r["agent"]["name"])
        log("\nlane %s: %s asks whether to trust %s.\n  In the window: %s.\n  You come back here when it is answered "
            "(or detach: Ctrl-b d)." % (r["lane"], ad.display, r["worktree"], ad.trust_hint))
        if ask("Open lane %s now? [Y/n]" % r["lane"], "y").lower() not in ("y", "yes"):
            continue
        win = r["tmux"]
        env = dict(os.environ)
        env.pop("TMUX", None)
        p = subprocess.Popen(tmux.base + ["attach-session", "-t", "=%s:%s" % (m.session, win["window_id"])], env=env)
        stop = threading.Event()

        def watch(pane=win["pane_id"], proc=p, agent=ad):
            while not stop.is_set() and proc.poll() is None:
                if not agent.waiting_for_trust(tmux.capture(pane)):
                    for line in tmux.run("list-clients", "-F", "#{client_pid}\t#{client_tty}").stdout.splitlines():
                        cpid, _, tty = line.partition("\t")
                        if cpid == str(proc.pid):
                            tmux.run("detach-client", "-t", tty)
                    return
                time.sleep(0.3)

        t = threading.Thread(target=watch, daemon=True)
        t.start()
        p.wait()
        stop.set()
    log("\nDelivering tasks to lanes that are ready now:")
    return rc.exclusive(m, rc.cmd_up, ns(want="resume", settle=a.wait))


def cmd_pr(a):
    import review
    m = select_run(a, current_project(a))
    argv = [a.pr_cmd, m.path, "--lane", a.lane]
    for flag in ("title", "body_file"):
        if getattr(a, flag, None):
            argv += ["--" + flag.replace("_", "-"), getattr(a, flag)]
    for flag in ("allow_ai_attribution", "jev_verify", "draft"):
        if getattr(a, flag, False):
            argv.append("--" + flag.replace("_", "-"))
    return review.main(argv)


def cmd_next(a):
    m = select_run(a, current_project(a))
    m.worker(a.lane)
    return rc.exclusive(m, rc.cmd_next, ns(lane=[a.lane], task=a.task, plan=a.dry_run, no_input=not interactive(a),
                                           branch=["%s=%s" % (a.lane, a.branch)] if a.branch else None, settle=a.wait))


def cmd_cleanup(a):
    m = select_run(a, current_project(a))
    args = ns(lane=a.lane, apply=a.apply, stop_running=a.stop_running)
    args.stop_hint = None
    hint = "pai cleanup --run %s --lane {lane} --apply --stop-running" % m.run_id
    rc_stop = rc.stop_command
    rc.stop_command = lambda m_, a_, w: hint.format(lane=w.lane)
    try:
        return rc.exclusive(m, rc.cmd_cleanup, args) if a.apply else rc.cmd_cleanup(m, args)
    finally:
        rc.stop_command = rc_stop


# ---------------------------------------------------------------- doctor and setup

def cmd_doctor(a):
    import doctor
    proj = None
    try:
        proj = current_project(types.SimpleNamespace(project=a.project))
    except Stop:
        pass
    names = a.agent or ([proj["default_agent"]] if proj and proj.get("default_agent") else list(agents.names()))
    rows = doctor.check(tuple(names))
    if proj:
        res = gitprep.remote_auth(proj["repo"], proj["remote"], "refs/heads/" + proj["base_branch"])
        rows.append({"tool": "remote access", "used_by": "run, next", "state": "ok" if res["ok"] else "unknown",
                     "detail": "%s %s: %s" % (proj["remote"], res["url"], res["detail"]),
                     "fix": None if res["ok"] else "; ".join(res["steps"][:2]) or None})
        live = 0
        for rid in run_ids(proj["name"]):
            m = load_run(proj["name"], rid)
            if run_active(m):
                live += sum(1 for r in rc.collect_status(m, rc.Tmux(m.socket))[0] if r["rc"]["state"] == "verified")
        rows.append({"tool": "rc sessions", "used_by": "-", "state": "info",
                     "detail": "%d lane(s) of %s with verified Remote Control now" % (live, proj["name"]), "fix": None})
    if a.json:
        json.dump(rows, sys.stdout, indent=1)
        print()
    else:
        doctor.show(rows)
        log("\ndoctor changed nothing.%s" % ("" if proj else " (No pai project here: `pai init` in your checkout.)"))
    return 0 if not doctor.failing(rows) else 1


def cmd_setup(a):
    if a.experimental:
        return server_setup(a)
    import doctor
    proj = None
    try:
        proj = current_project(types.SimpleNamespace(project=None))
    except Stop:
        pass
    names = a.agent or ([proj["default_agent"]] if proj and proj.get("default_agent") else ["claude"])
    argv = ["setup"] + (["--install-missing"] if a.install_missing else ["--dry-run"])
    for n in names:
        argv += ["--agent", n]
    return doctor.main(argv)


# ---------------------------------------------------------------- experimental Markdown server

MD_DIR = os.path.join(HERE, "mdserver")


def md_home():
    return os.path.join(data_home(), "server")


def md_config():
    return _read_json(os.path.join(md_home(), "config.json"))


def md_plan(a):
    roots = [os.path.realpath(os.path.expanduser(r)) for r in (a.root or [])]
    home = os.path.realpath(os.path.expanduser("~"))
    problems = []
    if not roots:
        problems.append("give at least one --root DIR: the Markdown folders it may show and edit")
    for r in roots:
        if not os.path.isdir(r):
            problems.append("--root %s is not a directory" % r)
        elif r in (os.sep, home):
            problems.append("--root %s would expose everything below it; choose the Markdown folder itself" % r)
    port = a.port or 8765
    if not 1024 <= port <= 65535:
        problems.append("--port must be between 1024 and 65535")
    return {"roots": roots, "port": port, "bind": "127.0.0.1", "allowed_hosts": a.allowed_host or [],
            "suffixes": [".md"]}, problems


def server_setup(a):
    cfg, problems = md_plan(a)
    log("EXPERIMENTAL  PAI Markdown server: a remote Nano with a Markdown preview (file picker, plain-text editor,")
    log("              rendered preview, explicit save confirmation). No dashboards, chat, agents, shell, Git or PRs.")
    log("download      nothing: Python standard library and the pack's own files in %s" % MD_DIR)
    log("listens on    %s:%d only (loopback); installing does not start it" % (cfg["bind"], cfg["port"]))
    for r in cfg["roots"]:
        log("folder        %s  (.md files only; no symbolic links, no hidden folders)" % r)
    log("who can use   anyone who can connect to %s:%d on this machine AND has the access token pai creates "
        "(%s, mode 0600). From elsewhere: an SSH tunnel or Tailscale Serve on your tailnet; never Funnel."
        % (cfg["bind"], cfg["port"], os.path.join(md_home(), "token")))
    if cfg["allowed_hosts"]:
        log("also accepts  Host %s (for Tailscale Serve)" % ", ".join(cfg["allowed_hosts"]))
    for pr in problems:
        log("PROBLEM       %s" % pr)
    if problems:
        return 2
    if a.dry_run:
        log("\nDRY RUN: nothing was written, installed or started.")
        return 0
    if not a.accept_experimental:
        if not sys.stdin.isatty():
            log("\nNot a terminal: add --accept-experimental to install it. Nothing was written.")
            return 2
        if ask("Install this experimental server? [y/N]", "n").lower() not in ("y", "yes"):
            log("Nothing was written; PAI is unchanged.")
            return 1
    os.makedirs(md_home(), mode=0o700, exist_ok=True)
    tok = os.path.join(md_home(), "token")
    if not os.path.exists(tok):
        import secrets
        fd = os.open(tok, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(24) + "\n")
    cfg.update(token_file=tok, installed=gitprep.now(), experimental=True)
    _write_json(os.path.join(md_home(), "config.json"), cfg)
    log("\ninstalled     %s (not started). Next: pai server start" % os.path.join(md_home(), "config.json"))
    return 0


def md_pid():
    d = _read_json(os.path.join(md_home(), "server.json")) or {}
    pid = d.get("pid")
    if not pid or wtlock.proc_start(pid) != d.get("start"):
        return None
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            if b"mdserver" not in f.read():
                return None
    except OSError:
        pass
    return pid


def md_healthy(cfg):
    import http.client
    try:
        c = http.client.HTTPConnection(cfg["bind"], cfg["port"], timeout=2)
        c.request("GET", "/healthz", headers={"Host": "127.0.0.1:%d" % cfg["port"]})
        return c.getresponse().status == 200
    except OSError:
        return False


def cmd_server(a):
    cfg = md_config()
    if not cfg:
        log("The experimental Markdown server is not installed (`pai setup --experimental server --dry-run` shows what "
            "it would do). PAI works without it.")
        return 1 if a.server_cmd != "uninstall" else 0
    pid = md_pid()
    url = "http://127.0.0.1:%d/" % cfg["port"]
    if a.server_cmd == "status":
        log("server %s%s; folders: %s" % ("running at %s (pid %d)" % (url, pid) if pid else "stopped",
                                         "" if not pid or md_healthy(cfg) else " but NOT answering",
                                         ", ".join(cfg["roots"])))
        return 0 if pid else 3
    if a.server_cmd == "token":
        with open(cfg["token_file"]) as f:
            print(f.read().strip())
        return 0
    if a.server_cmd == "start":
        if pid:
            log("already running at %s (pid %d)" % (url, pid))
            return 0
        logf = open(os.path.join(md_home(), "server.log"), "ab")
        p = subprocess.Popen([sys.executable, "-B", os.path.join(MD_DIR, "server.py"), "--config",
                              os.path.join(md_home(), "config.json")], stdin=subprocess.DEVNULL, stdout=logf,
                             stderr=logf, start_new_session=True, close_fds=True)
        logf.close()
        _write_json(os.path.join(md_home(), "server.json"), {"pid": p.pid, "start": wtlock.proc_start(p.pid)})
        end = time.time() + 8
        while time.time() < end and not md_healthy(cfg):
            if p.poll() is not None:
                log("the server exited at once (status %s); see %s" % (p.returncode, os.path.join(md_home(), "server.log")))
                return 1
            time.sleep(0.1)
        log("EXPERIMENTAL Markdown server at %s (loopback only, pid %d). Sign in with the token from "
            "`pai server token`. From another machine: ssh -N -L %d:127.0.0.1:%d YOU@THIS-HOST, then open %s"
            % (url, p.pid, cfg["port"], cfg["port"], url))
        return 0
    if a.server_cmd in ("stop", "uninstall"):
        if pid:
            os.kill(pid, 15)
            end = time.time() + 5
            while time.time() < end and wtlock.proc_start(pid) is not None:
                time.sleep(0.1)
            log("stopped (pid %d)" % pid)
        else:
            log("not running")
        if a.server_cmd == "uninstall":
            shutil.rmtree(md_home())
            log("removed %s (configuration, token, log). Your Markdown folders were not touched: %s"
                % (md_home(), ", ".join(cfg["roots"])))
        return 0
    return 2


# ---------------------------------------------------------------- pai config

def editor_argv(path):
    """(argv, where it came from) for editing `path`: $VISUAL, then $EDITOR, then nano when
    installed, then (native Windows) Notepad. (None, None) when there is none; nothing is
    installed for you."""
    import doctor
    windows = doctor.detect_platform()["system"] == "windows"
    for var in ("VISUAL", "EDITOR"):
        v = os.environ.get(var, "").strip()
        if v:
            return shlex.split(v, posix=not windows) + [path], "$" + var
    if shutil.which("nano"):
        return [shutil.which("nano"), path], "nano"
    if windows and shutil.which("notepad"):
        return [shutil.which("notepad"), path], "Notepad"
    return None, None


def config_target(a):
    if a.global_:
        return wbconfig.global_path(), "global"
    proj = current_project(a, strict=False)
    return wbconfig.project_path(proj["name"]), "project"


def config_problems(text, path, scope):
    try:
        data = wbconfig.parse(text, path)
    except wbconfig.ConfigError as e:
        return e.problems
    problems = wbconfig.validate(data, scope)
    if scope == "project":
        for key in ("name", "repo", "common_dir"):
            if not (data.get("project") or {}).get(key):
                problems.append(("project.%s" % key, "is required (pai init sets it); it identifies the checkout"))
    return problems


def cmd_config(a):
    if a.config_cmd == "show" and a.effective:
        proj = None
        try:
            proj = current_project(a)
        except Stop:
            if not a.global_:
                log("(no project here: global and built-in values only)")
        eff = wbconfig.effective((proj or {}).get("_raw") or {}, wbconfig.load(wbconfig.global_path()))
        if a.json:
            json.dump({k: {"value": v, "source": src} for k, (v, src) in eff.items()}, sys.stdout, indent=1)
            print()
            return 0
        for k, (v, src) in eff.items():
            log("%-26s %-40s %s" % (k, json.dumps(v) if v is not None else "(unset)", src))
        log("\nA flag on the command line overrides these for that invocation only; one-time actions (--yes, "
            "--apply, --stop-running, --install-missing) are never stored. API keys are never shown here.")
        return 0
    path, scope = config_target(a)
    if a.config_cmd == "check":
        bad = 0
        for p_, sc in ((wbconfig.global_path(), "global"), (path, scope)) if scope == "project" else ((path, scope),):
            if not os.path.exists(p_):
                log("%-8s %s: not present (built-in defaults apply)" % ("ok", p_))
                continue
            with open(p_, encoding="utf-8") as f:
                problems = config_problems(f.read(), p_, sc)
            for k, why in problems:
                log("INVALID  %s: %s: %s" % (p_, k, why))
            if not problems:
                log("ok       %s" % p_)
            bad += len(problems)
            if os.path.exists(p_ + ".draft"):
                log("note     an unsaved draft is waiting: %s (`pai config edit%s` continues it)" % (
                    p_ + ".draft", " --global" if sc == "global" else ""))
        log("\npai config check started nothing, called nothing and changed nothing.")
        return 1 if bad else 0
    if a.config_cmd == "show":
        if not os.path.exists(path):
            log("%s does not exist yet (built-in defaults apply). `pai config edit%s` creates it." % (
                path, " --global" if scope == "global" else ""))
            return 0
        with open(path, encoding="utf-8") as f:
            sys.stdout.write(f.read())
        return 0
    # edit: on a draft; the file is replaced only by a valid draft
    draft = path + ".draft"
    argv, source = editor_argv(draft)
    if not argv:
        log("No editor: set VISUAL or EDITOR (or install nano). The file is:\n  %s" % path)
        return 0
    if os.path.exists(draft):
        log("Continuing your unsaved draft %s" % draft)
    else:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
        else:
            text = wbconfig.template({}, scope)
        os.makedirs(os.path.dirname(draft), exist_ok=True)
        fd = os.open(draft, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    log("Editing %s with %s (%s)" % (path, os.path.basename(argv[0]), source))
    try:
        code = subprocess.run(argv).returncode
    except OSError as e:
        log("Could not start the editor (%s). Nothing was changed; your draft is %s" % (e, draft))
        return 1
    with open(draft, encoding="utf-8") as f:
        text = f.read()
    problems = config_problems(text, path, scope)
    if problems:
        for k, why in problems:
            log("INVALID  %s: %s" % (k, why))
        log("\nNot saved: %s is unchanged. Your edit is kept in %s; run `pai config edit%s` again to correct it."
            % (path, draft, " --global" if scope == "global" else ""))
        return 1
    wbconfig.write(path, text, scope)
    os.remove(draft)
    log("saved     %s%s" % (path, "" if code == 0 else " (the editor exited with %s)" % code))
    return 0


# ---------------------------------------------------------------- command line

def parser():
    ap = argparse.ArgumentParser(prog="pai", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def p(name, help_, run=True, lane=False):
        x = sub.add_parser(name, help=help_)
        x.add_argument("--project", help="project name (default: the one for the current directory)")
        if run:
            x.add_argument("--run", help="run id (default: the active run; asked when there is more than one)")
        if lane:
            x.add_argument("lane")
        return x

    i = p("init", "project defaults (asks in a terminal; never overwrites without --update)", run=False)
    i.add_argument("--repo", help="repository (default: the one around the current directory)")
    i.add_argument("--name")
    i.add_argument("--remote")
    i.add_argument("--base")
    i.add_argument("--default-agent", help="%s, or none (not set yet)" % ", ".join(agents.names()))
    i.add_argument("--tasks", help="task notes folder (Obsidian)")
    i.add_argument("--check", action="append", metavar="NAME=COMMAND", help="approved review check (repeatable)")
    i.add_argument("--worktree-root")
    i.add_argument("--workers", type=int)
    i.add_argument("--model", help="default model for the default agent (stored for that agent only)")
    i.add_argument("--effort", help="default reasoning effort for the default agent (stored for that agent only)")
    i.add_argument("--update", action="store_true", help="change an existing configuration (only what you pass)")
    i.add_argument("--no-input", action="store_true")
    i.add_argument("--jev", choices=("ask", "skip"), default="ask", help=argparse.SUPPRESS)
    d = p("doctor", "system, tools, agents, logins, remote access (read-only)", run=False)
    d.add_argument("--json", action="store_true")
    d.add_argument("--agent", action="append")
    s = p("setup", "show (default) or install missing tools", run=False)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="show the system and the commands (the default)")
    g.add_argument("--install-missing", action="store_true")
    s.add_argument("--agent", action="append", help="agent CLIs to install when missing (default: the project's)")
    s.add_argument("--experimental", choices=("server",), help="optional experimental pack: the Markdown server")
    s.add_argument("--accept-experimental", action="store_true", help="install it without asking (non-interactive)")
    s.add_argument("--root", action="append", help="server: a Markdown folder it may show and edit (repeatable)")
    s.add_argument("--port", type=int, help="server: loopback port (default 8765)")
    s.add_argument("--allowed-host", action="append", help="server: extra Host name to accept (Tailscale Serve)")
    r = p("run", "start a batch", run=False)
    r.add_argument("--repo", help="local path or clone URL (default: the repository around the current directory)")
    r.add_argument("--checkout-dir", help="where to clone a --repo URL (default: a pai-managed directory)")
    r.add_argument("--reviewed", action="store_true", help="you reviewed a new --repo URL checkout: agents may start")
    r.add_argument("--remote")
    r.add_argument("--base")
    r.add_argument("--workers", type=int)
    r.add_argument("--agent", action="append", help="once for every lane, or once per lane in lane order")
    r.add_argument("--model", action="append", metavar="[LANE=]MODEL", help="LANE is a name (a, b) or position (1, 2)")
    r.add_argument("--effort", action="append", metavar="[LANE=]LEVEL", help="reasoning effort, per agent's own values")
    r.add_argument("--mode", action="append", metavar="[LANE=]MODE")
    r.add_argument("--task", action="append", metavar="LANE=NOTE")
    r.add_argument("--branch", action="append", metavar="LANE=NAME")
    r.add_argument("--run-id")
    r.add_argument("--tmux-socket", help="tmux socket name or path for this run (default pai-PROJECT; recorded)")
    r.add_argument("--dry-run", action="store_true", help="read-only: no clone, fetch, refs, state or sessions")
    r.add_argument("--yes", action="store_true", help="accept pai's plan (never an agent's folder-trust prompt)")
    r.add_argument("--no-input", action="store_true")
    r.add_argument("--no-dispatch", action="store_true", help="start the agents but do not send their tasks")
    r.add_argument("--jev-triage", action="store_true", help="optional: ask JEV whether each assigned note is ready "
                   "(one request per note; advisory, never blocks)")
    r.add_argument("--wait", type=float, help="seconds to wait for lanes to start and take their task (config run.wait)")
    st = p("status", "per lane: process, task dispatch, Remote Control evidence")
    st.add_argument("--json", action="store_true")
    at = p("attach", "open a lane's tmux window", lane=True)
    at.add_argument("--print", action="store_true", help="only print the tmux command")
    lg = p("logs", "a lane's screen and scrollback", lane=True)
    lg.add_argument("--tail", type=int, help="lines (default: config logs.tail, 60)")
    lg.add_argument("--follow", action="store_true")
    tr = p("trust", "answer folder-trust prompts lane by lane from this terminal")
    tr.add_argument("--wait", type=float, default=10.0)
    rs = p("resume", "restart missing workers, deliver undelivered tasks (no fetch)")
    rs.add_argument("--lane")
    rs.add_argument("--wait", type=float, default=10.0)
    rs.add_argument("--redispatch", action="store_true", help="send a task whose dispatch is unknown again")
    rs.add_argument("--mark-delivered", action="store_true", help="record an unknown dispatch as delivered")
    pr = p("pr", "plan | check | open a lane's pull request")
    pr.add_argument("pr_cmd", choices=("plan", "check", "open"))
    pr.add_argument("lane")
    pr.add_argument("--title")
    pr.add_argument("--body-file")
    pr.add_argument("--draft", action="store_true")
    pr.add_argument("--allow-ai-attribution", action="store_true")
    pr.add_argument("--jev-verify", action="store_true")
    nx = p("next", "after a merged PR: the lane's next task", lane=True)
    nx.add_argument("--task")
    nx.add_argument("--branch")
    nx.add_argument("--dry-run", action="store_true")
    nx.add_argument("--wait", type=float, default=10.0)
    jv = p("jev", "optional JEV: status, or check (one real, possibly billed request)", run=False)
    jv.add_argument("jev_cmd", choices=("status", "check"))
    jv.add_argument("--yes", action="store_true", help="send the check request without asking")
    sv = p("server", "EXPERIMENTAL Markdown server: start | status | stop | token | uninstall", run=False)
    sv.add_argument("server_cmd", choices=("start", "status", "stop", "token", "uninstall"))
    cf = p("config", "edit | show --effective | check the workbench defaults", run=False)
    cf.add_argument("config_cmd", choices=("edit", "show", "check"))
    cf.add_argument("--global", dest="global_", action="store_true", help="the defaults shared by every project")
    cf.add_argument("--effective", action="store_true", help="show: resolved values and where each comes from")
    cf.add_argument("--json", action="store_true")
    cl = p("cleanup", "preview, or --apply: close exited workers' windows")
    cl.add_argument("--lane", action="append")
    cl.add_argument("--apply", action="store_true")
    cl.add_argument("--stop-running", action="store_true")
    return ap


COMMANDS = {"init": cmd_init, "doctor": cmd_doctor, "setup": cmd_setup, "run": cmd_run, "status": cmd_status,
            "attach": cmd_attach, "logs": cmd_logs, "trust": cmd_trust, "resume": cmd_resume, "pr": cmd_pr,
            "next": cmd_next, "cleanup": cmd_cleanup, "config": None, "jev": cmd_jev, "server": cmd_server}


def main(argv=None):
    a = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return (COMMANDS[a.cmd] or cmd_config)(a)
    except Stop as e:
        print(str(e), file=sys.stderr if e.code == 2 else sys.stdout, flush=True)
        return e.code
    except rc.ManifestError as e:
        print("pai: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
