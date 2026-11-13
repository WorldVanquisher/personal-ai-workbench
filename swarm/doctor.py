"""First-run checks and setup for pai-swarm workers.

    pai_swarm.py doctor                    read-only: tools, logins and Remote Control readiness
    pai_swarm.py setup                     the same, plus the install command for each missing tool
    pai_swarm.py setup --install-missing   run those install commands (only for missing tools)

`doctor` keeps three things apart, because the fixes differ:
  MISSING        the binary is not on PATH                 -> `setup --install-missing`
  NOT LOGGED IN  the binary is there, the login is not     -> you log in (never automated)
  RC NOT READY   Claude Code is logged in, but Remote Control cannot work with that login

Install sources: the operating system's own package manager for git, tmux and gh (apt-get,
dnf, pacman, apk; Homebrew on macOS), and Anthropic's installer script from
https://claude.ai/install.sh for Claude Code. Anything else is reported as unsupported with
nothing run. Logins (`gh auth login`, Claude's /login) are always left to you.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

CLAUDE_INSTALLER = "https://claude.ai/install.sh"
TOOLS = (("git", "every command"), ("tmux", "rc"), ("claude", "run, rc"), ("gh", "pr open, rc next"))
MANAGERS = {
    "apt-get": (["apt-get", "install", "-y"], {"git": "git", "tmux": "tmux", "gh": "gh"}),
    "dnf": (["dnf", "install", "-y"], {"git": "git", "tmux": "tmux", "gh": "gh"}),
    "pacman": (["pacman", "-S", "--needed", "--noconfirm"], {"git": "git", "tmux": "tmux", "gh": "github-cli"}),
    "apk": (["apk", "add"], {"git": "git", "tmux": "tmux", "gh": "github-cli"}),
    "brew": (["brew", "install"], {"git": "git", "tmux": "tmux", "gh": "gh"}),
}
FAMILIES = (("apt-get", ("debian", "ubuntu")), ("dnf", ("fedora", "rhel", "centos")),
            ("pacman", ("arch",)), ("apk", ("alpine",)))


def _run(argv, timeout=25):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timed out after %ds" % timeout
    except OSError as e:
        return None, "", str(e)


def _version(path, flag):
    rc, out, err = _run([path, flag])
    return (out or err).strip().splitlines()[0][:60] if rc == 0 and (out or err).strip() else "version unknown"


def check():
    """[{tool, used_by, state, detail, fix}] with state ok | missing | not-logged-in | rc-not-ready | unknown."""
    rows = []

    def add(tool, used_by, state, detail, fix=None):
        rows.append({"tool": tool, "used_by": used_by, "state": state, "detail": detail, "fix": fix})

    for tool, used_by in TOOLS:
        path = shutil.which(tool)
        if not path:
            add(tool, used_by, "missing", "not on PATH", "pai_swarm.py setup --install-missing")
            continue
        add(tool, used_by, "ok", "%s (%s)" % (path, _version(path, "-V" if tool == "tmux" else "--version")))
        if tool == "gh":
            rc, _, err = _run([path, "auth", "status"])
            if rc == 0:
                add("gh login", used_by, "ok", "authenticated")
            elif rc is None:
                add("gh login", used_by, "unknown", "could not check: %s" % err)
            else:
                add("gh login", used_by, "not-logged-in", "gh is installed but not authenticated", "gh auth login")
        if tool == "claude":
            rc, out, err = _run([path, "auth", "status"])
            try:
                auth = json.loads(out) if out.strip() else {}
            except ValueError:
                auth = {}
            if rc is None or (rc == 0 and not isinstance(auth, dict)):
                add("claude login", used_by, "unknown", "could not check: %s" % (err or "unexpected output"))
                continue
            if rc != 0 or not auth.get("loggedIn"):
                add("claude login", used_by, "not-logged-in", "Claude Code is installed but not logged in",
                    "run `claude` once and use /login (or `claude auth login`)")
                add("claude remote control", "rc", "rc-not-ready", "needs the Claude login first")
                continue
            add("claude login", used_by, "ok", "logged in (%s)" % (auth.get("authMethod") or "method unknown"))
            rc2, _, _ = _run([path, "remote-control", "--help"])
            if rc2 != 0:
                add("claude remote control", "rc", "rc-not-ready", "this Claude Code has no `remote-control` command",
                    "update Claude Code")
            elif auth.get("authMethod") != "claude.ai":
                add("claude remote control", "rc", "rc-not-ready",
                    "Remote Control needs a claude.ai subscription login; the current login is %s"
                    % (auth.get("authMethod") or "unknown"), "log in with your claude.ai account (/login)")
            else:
                add("claude remote control", "rc", "ok",
                    "command present and claude.ai login; a session's own connection is shown by `rc status`")
    return rows


def platform_manager():
    """(manager name or None, description). The package manager for this operating system."""
    if platform.system() == "Darwin":
        return ("brew", "macOS with Homebrew") if shutil.which("brew") else (None, "macOS without Homebrew (https://brew.sh)")
    ids = []
    try:
        with open(os.environ.get("PAI_SWARM_OS_RELEASE") or "/etc/os-release", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k in ("ID", "ID_LIKE"):
                    ids += v.strip("\"'").lower().split()
    except OSError:
        return None, "%s (no os-release file)" % platform.system()
    for manager, names in FAMILIES:
        if any(i in names for i in ids):
            return (manager, "%s (%s)" % (ids[0], manager)) if shutil.which(manager) else \
                (None, "%s, but %s is not on PATH" % (ids[0], manager))
    return None, "%s is not a supported distribution" % (ids[0] if ids else "this system")


def install_plan(missing):
    """([(label, [argv, ...])], [problem]) for the missing tools."""
    steps, problems = [], []
    manager, where = platform_manager()
    pkgs = [t for t in missing if t != "claude"]
    if pkgs:
        if not manager:
            problems.append("cannot install %s automatically: %s. Install them with your system's package "
                            "manager." % (", ".join(pkgs), where))
        else:
            argv, names = MANAGERS[manager]
            cmd = argv + [names[t] for t in pkgs]
            if manager != "brew" and os.geteuid() != 0:
                if shutil.which("sudo"):
                    cmd = ["sudo"] + cmd
                else:
                    problems.append("%s needs root and sudo is not available; run as root: %s" % (manager, " ".join(cmd)))
                    cmd = None
            if cmd:
                steps.append(("%s from %s" % (", ".join(pkgs), where), [cmd]))
    if "claude" in missing:
        if platform.system() not in ("Linux", "Darwin") or not shutil.which("curl") or not shutil.which("bash"):
            problems.append("cannot install Claude Code automatically here (needs Linux or macOS with curl and "
                            "bash); see %s" % CLAUDE_INSTALLER)
        else:
            script = os.path.join(tempfile.gettempdir(), "claude-install-%d.sh" % os.getpid())
            steps.append(("Claude Code from Anthropic's installer (%s)" % CLAUDE_INSTALLER,
                          [["curl", "-fsSL", "-o", script, CLAUDE_INSTALLER], ["bash", script]]))
    return steps, problems


def show(rows):
    label = {"ok": "ok", "missing": "MISSING", "not-logged-in": "NOT LOGGED IN", "rc-not-ready": "RC NOT READY",
             "unknown": "UNKNOWN"}
    for r in rows:
        print("%-14s %-22s %s%s" % (label[r["state"]], r["tool"], r["detail"], "   -> %s" % r["fix"] if r["fix"] else ""))


def main(argv):
    ap = argparse.ArgumentParser(prog="pai_swarm.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="read-only checks")
    d.add_argument("--json", action="store_true")
    s = sub.add_parser("setup", help="show, or with --install-missing run, the install commands")
    s.add_argument("--install-missing", action="store_true", help="run the install commands for missing tools")
    a = ap.parse_args(argv)
    rows = check()
    if a.cmd == "doctor":
        if a.json:
            json.dump(rows, sys.stdout, indent=1)
            print()
        else:
            show(rows)
            print("\ndoctor changed nothing.")
        return 0 if all(r["state"] == "ok" for r in rows) else 1
    show(rows)
    missing = [r["tool"] for r in rows if r["state"] == "missing"]
    steps, problems = install_plan(missing)
    print()
    for label, cmds in steps:
        print("%s %s:" % ("installing" if a.install_missing else "would install", label))
        for cmd in cmds:
            print("    %s" % " ".join(cmd))
            if a.install_missing:
                try:
                    rc = subprocess.run(cmd).returncode
                except OSError as e:
                    rc = "not run: %s" % e
                if rc != 0:
                    problems.append("`%s` failed (%s); nothing after it in this step was run" % (" ".join(cmd), rc))
                    break
    for p in problems:
        print("PROBLEM  %s" % p)
    if a.install_missing and steps:
        print("\nafter installing:")
        rows = check()
        show(rows)
    todo = [r for r in rows if r["state"] in ("not-logged-in", "rc-not-ready")]
    if todo:
        print("\nOwner steps (never automated):")
        for r in todo:
            print("    %s: %s" % (r["tool"], r["fix"] or r["detail"]))
    if missing and not a.install_missing:
        print("\nNothing was installed. Run `pai_swarm.py setup --install-missing` to run the commands above.")
    return 0 if all(r["state"] == "ok" for r in rows) and not problems else 1
