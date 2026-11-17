"""First-run checks and setup for pai-swarm workers.

    pai_swarm.py doctor [--agent A]        read-only: system, tools, agent logins, Remote Control readiness
    pai_swarm.py setup [--dry-run]         the same, plus the install command for each missing tool
    pai_swarm.py setup --install-missing   run those install commands (only for missing tools)

`doctor` keeps three things apart, because the fixes differ:
  MISSING        the binary is not on PATH                 -> `setup --install-missing`
  NOT LOGGED IN  the binary is there, the login is not     -> you log in (never automated)
  RC NOT READY   Claude Code is logged in, but Remote Control cannot work with that login

The system is detected from os-release and the package managers actually present, never
from the kernel name alone: Debian/Ubuntu (apt-get), Arch (pacman), Fedora (dnf),
RHEL/Rocky/AlmaLinux/CentOS (dnf, or yum where dnf is absent), Amazon Linux (yum on 2, dnf
on 2023), Alpine (apk), macOS (Homebrew if installed), native Windows (winget if installed),
and WSL as the Linux distribution it runs. A package manager is never installed for you; on
an unknown system the missing tools are listed and nothing is run. Claude Code comes from
Anthropic's installer script (https://claude.ai/install.sh), Codex from npm (@openai/codex).
Logins (`gh auth login`, Claude's /login, `codex login`) are always left to you. Native
Windows has no worker backend (workers need tmux): use WSL for `pai run`.
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
CODEX_PACKAGE = "@openai/codex"
GH_LINUX_DOCS = "https://github.com/cli/cli/blob/trunk/docs/install_linux.md"
CORE = (("git", "every command"), ("tmux", "workers (rc, pai run)"), ("ps", "worker status"), ("gh", "pr open, next"))
AGENT_USE = {"claude": "agent lanes (claude)", "codex": "agent lanes (codex)"}
TOOLS = CORE  # kept for callers that list the core tools
MANAGERS = {  # install argv, then package name per tool; a tool missing from the map has no package there
    "apt-get": (["apt-get", "install", "-y"], {"git": "git", "tmux": "tmux", "gh": "gh", "ps": "procps"}),
    "dnf": (["dnf", "install", "-y"], {"git": "git", "tmux": "tmux", "gh": "gh", "ps": "procps-ng"}),
    "yum": (["yum", "install", "-y"], {"git": "git", "tmux": "tmux", "ps": "procps-ng"}),
    "pacman": (["pacman", "-S", "--needed", "--noconfirm"], {"git": "git", "tmux": "tmux", "gh": "github-cli",
                                                            "ps": "procps-ng"}),
    "apk": (["apk", "add"], {"git": "git", "tmux": "tmux", "gh": "github-cli", "ps": "procps"}),
    "brew": (["brew", "install"], {"git": "git", "tmux": "tmux", "gh": "gh"}),
    "winget": (["winget", "install", "--exact", "--source", "winget", "--id"], {"git": "Git.Git", "gh": "GitHub.cli"}),
}
RHEL_LIKE = ("rhel", "centos", "rocky", "almalinux", "ol")


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


def os_release():
    """{ID, ID_LIKE (list), VERSION_ID, PRETTY_NAME} from /etc/os-release, or None."""
    info = {}
    try:
        with open(os.environ.get("PAI_SWARM_OS_RELEASE") or "/etc/os-release", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k:
                    info[k] = v.strip().strip("\"'")
    except OSError:
        return None
    info["ID"] = info.get("ID", "").lower()
    info["ID_LIKE"] = info.get("ID_LIKE", "").lower().split()
    return info


def detect_platform():
    """Where pai runs and how it could install things there. Nothing is installed or changed.
    {system: linux|wsl|macos|windows|other, arch, distro, version, manager, why, backend, backend_note}"""
    system = os.environ.get("PAI_SWARM_PLATFORM") or platform.system()
    res = {"arch": platform.machine() or "unknown", "distro": None, "version": None, "manager": None, "why": "",
           "backend": None, "backend_note": ""}
    if system == "Windows":
        res.update(system="windows", distro="windows", version=platform.version())
        res["manager"] = "winget" if shutil.which("winget") else None
        res["why"] = "" if res["manager"] else "winget is not installed; pai does not install a package manager for you"
        res["backend_note"] = ("parallel workers need tmux, which has no native Windows build, and pai has no native "
                               "Windows worker backend. Core PAI (init, context, handoff) works here. For workers use "
                               "WSL: `wsl --install -d Ubuntu`, then install and run pai inside that distribution")
        return res
    if system == "Darwin":
        res.update(system="macos", distro="macos", version=platform.mac_ver()[0] or None, backend="tmux")
        res["manager"] = "brew" if shutil.which("brew") else None
        res["why"] = "" if res["manager"] else "Homebrew is not installed (https://brew.sh); pai does not install it for you"
        return res
    if system != "Linux":
        res.update(system="other", distro=system or "unknown", why="%s is not a supported system" % (system or "this system"))
        return res
    wsl = os.environ.get("PAI_SWARM_WSL")
    if wsl is None:
        wsl = bool(os.environ.get("WSL_DISTRO_NAME"))
        try:
            with open("/proc/sys/kernel/osrelease") as f:
                wsl = wsl or "microsoft" in f.read().lower()
        except OSError:
            pass
    res.update(system="wsl" if str(wsl) not in ("", "0", "False") else "linux", backend="tmux")
    info = os_release()
    if not info or not info.get("ID"):
        res["why"] = "no os-release file, so the distribution is unknown"
        return res
    ident, ids = info["ID"], [info["ID"]] + info["ID_LIKE"]
    res.update(distro=ident, version=info.get("VERSION_ID"))
    if ident == "amzn":
        want = "yum" if (info.get("VERSION_ID") or "").split(".")[0] == "2" else "dnf"
    elif ident == "fedora" or any(i in RHEL_LIKE for i in ids):
        want = "dnf" if shutil.which("dnf") or ident == "fedora" else "yum"
    elif any(i in ("debian", "ubuntu") for i in ids):
        want = "apt-get"
    elif "arch" in ids:
        want = "pacman"
    elif "alpine" in ids:
        want = "apk"
    else:
        res["why"] = "%s is not a supported distribution" % ident
        return res
    if shutil.which(want):
        res["manager"] = want
    else:
        res["why"] = "%s, but %s is not on PATH" % (ident, want)
    return res


def platform_manager():
    """(manager name or None, description). Kept for callers of the earlier interface."""
    p = detect_platform()
    return (p["manager"], "%s (%s)" % (p["distro"], p["manager"])) if p["manager"] else (None, p["why"])


def package_for(plat, tool):
    """(package name or None, reason when there is none)."""
    mgr = plat["manager"]
    if not mgr:
        return None, plat["why"]
    if tool == "ps" and plat["system"] in ("macos",):
        return None, "ps is part of macOS"
    if tool == "tmux" and plat["system"] == "windows":
        return None, plat["backend_note"]
    if tool == "gh" and mgr == "dnf" and plat["distro"] != "fedora":
        return None, ("gh is not in %s's own repositories; GitHub documents its RPM repository at %s (add it yourself)"
                      % (plat["distro"], GH_LINUX_DOCS))
    name = MANAGERS[mgr][1].get(tool)
    return (name, None) if name else (None, "%s has no %s package; see %s" % (mgr, tool, GH_LINUX_DOCS if tool == "gh" else "its docs"))


def check(agent_names=("claude",)):
    """[{tool, used_by, state, detail, fix}] with state ok | missing | not-logged-in | rc-not-ready | unknown | info."""
    rows = []

    def add(tool, used_by, state, detail, fix=None):
        rows.append({"tool": tool, "used_by": used_by, "state": state, "detail": detail, "fix": fix})

    plat = detect_platform()
    add("platform", "-", "info", "%s%s, %s; package manager %s; worker backend %s" % (
        plat["system"], " (%s %s)" % (plat["distro"], plat["version"] or "") if plat["distro"] else "", plat["arch"],
        plat["manager"] or "none (%s)" % plat["why"], plat["backend"] or "NONE: " + plat["backend_note"]))
    tools = list(CORE) + [(a, AGENT_USE.get(a, "agent lanes")) for a in agent_names]
    for tool, used_by in tools:
        if tool == "ps" and plat["system"] == "windows":
            continue
        path = shutil.which(tool)
        if not path:
            add(tool, used_by, "missing", "not on PATH", "pai setup --install-missing")
            continue
        add(tool, used_by, "ok", "%s (%s)" % (path, _version(path, "-V" if tool == "tmux" else "--version"))
            if tool != "ps" else path)
        if tool == "gh":
            rc, _, err = _run([path, "auth", "status"])
            if rc == 0:
                add("gh login", used_by, "ok", "authenticated")
            elif rc is None:
                add("gh login", used_by, "unknown", "could not check: %s" % err)
            else:
                add("gh login", used_by, "not-logged-in", "gh is installed but not authenticated", "gh auth login")
        if tool == "codex":
            import agents
            state, detail = agents.get("codex").login(path)
            add("codex login", used_by, state, detail, "codex login" if state != "ok" else None)
            add("codex remote control", "-", "info", "not supported: " + agents.get("codex").no_rc_reason)
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
                    "available (command present, claude.ai login); whether a session is connected is per lane, "
                    "in `pai status`")
    return rows


def install_plan(missing, plat=None):
    """([(label, [argv, ...])], [problem]) for the missing tools. Only documented sources:
    the system's own package manager (never bootstrapped), Anthropic's installer script for
    Claude Code, npm for Codex. Anything else is reported with nothing run."""
    plat = plat or detect_platform()
    steps, problems = [], []
    pkgs, names = [], []
    for t in missing:
        if t in ("claude", "codex"):
            continue
        name, why = package_for(plat, t)
        if name:
            pkgs.append(t)
            names.append(name)
        else:
            problems.append("cannot install %s automatically: %s" % (t, why))
    if pkgs:
        mgr = plat["manager"]
        argv = MANAGERS[mgr][0]
        cmds = [argv + [n] for n in names] if mgr == "winget" else [argv + names]
        if mgr not in ("brew", "winget") and hasattr(os, "geteuid") and os.geteuid() != 0:
            if shutil.which("sudo"):
                cmds = [["sudo"] + c for c in cmds]
            else:
                problems.append("%s needs root and sudo is not available; run as root: %s" % (mgr, " ".join(cmds[0])))
                cmds = None
        if cmds:
            steps.append(("%s from %s" % (", ".join(pkgs), "%s (%s)" % (plat["distro"], mgr)), cmds))
    if "claude" in missing:
        if plat["system"] == "windows":
            problems.append("Claude Code on native Windows: install it yourself from Anthropic's documentation "
                            "(PowerShell installer); pai does not run it")
        elif plat["system"] not in ("linux", "wsl", "macos") or not shutil.which("curl") or not shutil.which("bash"):
            problems.append("cannot install Claude Code automatically here (needs Linux, WSL or macOS with curl and "
                            "bash); see %s" % CLAUDE_INSTALLER)
        else:
            script = os.path.join(tempfile.gettempdir(), "claude-install-%d.sh" % os.getpid())
            steps.append(("Claude Code from Anthropic's installer (%s)" % CLAUDE_INSTALLER,
                          [["curl", "-fsSL", "-o", script, CLAUDE_INSTALLER], ["bash", script]]))
    if "codex" in missing:
        if shutil.which("npm"):
            steps.append(("Codex from npm (%s)" % CODEX_PACKAGE, [["npm", "install", "-g", CODEX_PACKAGE]]))
        else:
            problems.append("cannot install Codex automatically: npm is not installed (Codex is the npm package %s; "
                            "see https://github.com/openai/codex)" % CODEX_PACKAGE)
    return steps, problems


def show(rows):
    label = {"ok": "ok", "missing": "MISSING", "not-logged-in": "NOT LOGGED IN", "rc-not-ready": "RC NOT READY",
             "unknown": "UNKNOWN", "info": "info"}
    for r in rows:
        print("%-14s %-22s %s%s" % (label[r["state"]], r["tool"], r["detail"], "   -> %s" % r["fix"] if r["fix"] else ""))


def failing(rows):
    return [r for r in rows if r["state"] not in ("ok", "info")]


def show_platform(plat):
    print("system         %s%s" % (plat["system"], " (%s %s)" % (plat["distro"], plat["version"] or "") if plat["distro"] else ""))
    print("architecture   %s" % plat["arch"])
    print("installer      %s" % (plat["manager"] or "none: %s" % plat["why"]))
    print("worker backend %s" % (plat["backend"] or "NONE: %s" % plat["backend_note"]))


def main(argv, agent_names=None):
    ap = argparse.ArgumentParser(prog="pai_swarm.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="read-only checks")
    d.add_argument("--json", action="store_true")
    d.add_argument("--agent", action="append", help="agent CLIs to check (default: claude)")
    s = sub.add_parser("setup", help="show, or with --install-missing run, the install commands")
    s.add_argument("--install-missing", action="store_true", help="run the install commands for missing tools")
    s.add_argument("--dry-run", action="store_true", help="only show the system and the commands (the default)")
    s.add_argument("--agent", action="append", help="agent CLIs to install when missing (default: claude)")
    a = ap.parse_args(argv)
    names = tuple(a.agent or agent_names or ("claude",))
    rows = check(names)
    if a.cmd == "doctor":
        if a.json:
            json.dump(rows, sys.stdout, indent=1)
            print()
        else:
            show(rows)
            print("\ndoctor changed nothing.")
        return 0 if not failing(rows) else 1
    if a.install_missing and a.dry_run:
        print("setup: --dry-run and --install-missing exclude each other", file=sys.stderr)
        return 2
    plat = detect_platform()
    show_platform(plat)
    print()
    show(rows)
    missing = [r["tool"] for r in rows if r["state"] == "missing"]
    if plat["system"] == "windows":
        print("\nNOTE: %s." % plat["backend_note"])
    steps, problems = install_plan(missing, plat)
    print()
    if missing:
        print("missing        %s" % ", ".join(missing))
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
        rows = check(names)
        show(rows)
    todo = [r for r in rows if r["state"] in ("not-logged-in", "rc-not-ready")]
    if todo:
        print("\nOwner steps (never automated):")
        for r in todo:
            print("    %s: %s" % (r["tool"], r["fix"] or r["detail"]))
    if missing and not a.install_missing:
        print("\nNothing was installed. Run `pai setup --install-missing` to run the commands above.")
    return 0 if not failing(rows) and not problems else 1
