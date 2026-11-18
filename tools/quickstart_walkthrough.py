#!/usr/bin/env python3
"""Follow QUICKSTART.md literally in a brand-new HOME, as a new account would, and record every
point where following the text alone gets stuck.

    python3 -B tools/quickstart_walkthrough.py --work DIR [--source SPEC]

The quickstart's install line names the GitHub tag of this version. With --source, that one
spec is replaced (for example git+file:///path/to/repo@v0.4.0, to test a release before its
tag is on GitHub); everything else runs as written. Without --source the walkthrough builds
that stand-in itself: a history-free Git repository of this checkout, tagged v<version>.

The account has: uv and Python on PATH, a Git checkout with a remote (`~/code/myapp`, a local
bare origin), fake Claude and Codex (no real agent, login or network), and tmux confined to
DIR (TMUX_TMPDIR). The code blocks of QUICKSTART.md run in order, each in a new interactive
bash; where the text tells the reader to do something (run `uv tool update-shell` and open a
new terminal, choose an agent, uninstall), the walkthrough does that and nothing more.
QUICKSTART.zh-CN.md must hold the same commands. Writes walkthrough.json in DIR. Linux, macOS.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import onboarding_acceptance as oa  # noqa: E402  (its fakes, pty, served-link and HTTP helpers)

STEPS = []


def step(name, ok, detail="", stuck=None):
    STEPS.append({"step": name, "ok": bool(ok), "stuck": stuck, "detail": str(detail)[-1200:] if not ok or stuck else ""})
    print("%s  %s%s" % ("ok  " if ok else "STUCK", name, ("\n      -> " + stuck) if stuck else
                        ("" if ok else "\n      " + str(detail)[-600:])), flush=True)


def blocks(path):
    return [b.strip("\n") for b in re.findall(r"```sh\n(.*?)```", open(path, encoding="utf-8").read(), re.S)]


def commands(block):
    return [c for c in (line.split("  #")[0].rstrip() for line in block.splitlines()) if c.strip()]


def stand_in(work, version):
    """A history-free Git repository of this checkout, tagged v<version>: what the tag will hold."""
    repo = os.path.join(work, "release-repo")
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "dist", "build", "*.egg-info", "__pycache__"))
    env = dict(os.environ, GIT_AUTHOR_NAME="pai", GIT_AUTHOR_EMAIL="pai@example.invalid", GIT_COMMITTER_NAME="pai",
               GIT_COMMITTER_EMAIL="pai@example.invalid", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "release candidate"],
                ["tag", "v%s" % version]):
        subprocess.run(["git", "-C", repo] + cmd, env=env, check=True, capture_output=True)
    return "git+file://%s@v%s" % (repo, version)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--source", help="install spec used instead of the quickstart's GitHub tag")
    a = ap.parse_args(argv)
    work = os.path.abspath(a.work)
    if os.path.exists(work):
        raise SystemExit("--work %s exists" % work)
    if not shutil.which("uv"):
        raise SystemExit("uv is not on PATH")
    version = re.search(r'__version__ = "([^"]+)"', open(os.path.join(ROOT, "pai", "__init__.py")).read()).group(1)
    home, tmuxdir, ctl = (os.path.join(work, d) for d in ("home", "t", "ctl"))
    for d in (home, tmuxdir, ctl):
        os.makedirs(d)
    fakebin = os.path.join(work, "fakebin")
    oa.fakes(fakebin)
    base = [fakebin] + [os.path.dirname(shutil.which(t)) for t in ("uv", "git", "tmux", "bash", "python3") if shutil.which(t)]
    env = {"HOME": home, "PATH": os.pathsep.join(dict.fromkeys(base + ["/usr/bin", "/bin"])), "SHELL": "/bin/bash",
           "LANG": "C.UTF-8", "TERM": "xterm", "TMUX_TMPDIR": tmuxdir, "FAKE_RC_DIR": ctl,
           "CLAUDE_CONFIG_DIR": os.path.join(work, "claude-config"), "CODEX_HOME": os.path.join(work, "codex-home"),
           "PAI_JEV_CREDENTIAL_STORE": "none", "GIT_CONFIG_NOSYSTEM": "1", "UV_PYTHON_DOWNLOADS": "never"}
    for k in ("UV_CACHE_DIR", "UV_INDEX_URL", "UV_DEFAULT_INDEX"):  # a reachable cache/index, if the host set one
        if os.environ.get(k):
            env[k] = os.environ[k]
    os.makedirs(env["CLAUDE_CONFIG_DIR"])
    origin, app = os.path.join(work, "origin.git"), os.path.join(home, "code", "myapp")
    g = dict(env, GIT_AUTHOR_NAME="Friend", GIT_AUTHOR_EMAIL="friend@example.invalid", GIT_COMMITTER_NAME="Friend",
             GIT_COMMITTER_EMAIL="friend@example.invalid")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], env=g, check=True, capture_output=True)
    subprocess.run(["git", "clone", "-q", origin, app], env=g, check=True, capture_output=True)
    with open(os.path.join(app, "README.md"), "w") as f:
        f.write("# myapp\n")
    for cmd in (["git", "add", "README.md"], ["git", "commit", "-qm", "init"], ["git", "push", "-q", "origin", "main"]):
        subprocess.run(cmd, cwd=app, env=g, check=True, capture_output=True)

    def term(cmd, cwd=home, timeout=300):
        return oa.sh(["bash", "-ic", cmd], env, cwd=cwd, timeout=timeout)
    try:
        en, zh = blocks(os.path.join(ROOT, "QUICKSTART.md")), blocks(os.path.join(ROOT, "QUICKSTART.zh-CN.md"))
        step("0. QUICKSTART.zh-CN.md has exactly the same commands", [commands(b) for b in en] == [commands(b) for b in zh],
             (en, zh))
        install = commands(en[0])
        github = "git+https://github.com/WorldVanquisher/personal-ai-workbench@v%s" % version
        step("1. the install line names this version's tag", install == ["uv tool install %s" % github], install)
        source = a.source or stand_in(work, version)
        rc, o, e = oa.sh(["bash", "-c", install[0].replace(github, source)], env, cwd=home, timeout=900)
        step("1. install runs as written (source: %s)" % ("given" if a.source else "local stand-in for the tag"),
             rc == 0 and "pai" in (o + e), o + e)
        rc, o, e = term("pai --version")
        if rc != 0:  # "If uv says its bin folder is not on your PATH, run `uv tool update-shell`'
            rc2, o2, e2 = oa.sh(["bash", "-c", "uv tool update-shell"], env, cwd=home)
            step("1. `uv tool update-shell`, as the text says when pai is not found", rc2 == 0, o2 + e2)
        rc, o, e = term("pai --version")
        step("1. in a new terminal `pai --version` answers pai %s" % version, rc == 0 and o.strip() == "pai %s" % version, o + e)

        rc, o, e = term(" && ".join(commands(en[1])), timeout=120)
        fixes = re.findall(r"-> (.+)", o)
        step("2. pai doctor names each problem with its fix", rc in (0, 1) and fixes, o + e)

        lines = commands(en[2])
        rc, out = oa.interactive(["bash", "-ic", lines[0]], env, app, lambda p: "1" if p.startswith("Default agent") else "")
        step("3. %s (Enter everywhere but the agent)" % lines[0], rc == 0 and "saved" in out, out[-1500:])
        for cmd in lines[1:]:
            rc, o, e = term(cmd, cwd=app)
            ok = rc == 0 or (cmd == "pai status" and rc == 1)  # the text says 1 is normal while a lane works
            step("3. %s" % cmd, ok, o + e)
        received = os.path.join(ctl, "received.jsonl")
        step("3. the fake agent received the task note", _wait(lambda: os.path.exists(received)))

        web = commands(en[3])
        step("4. the block is one command, pai web", web == ["pai web"], web)
        p, port, link, out = oa.served(web[0] + " --no-browser", env, app)  # no desktop here: the link is printed
        try:
            st, hdrs, _ = oa.request(port, "GET", link) if port else (0, {}, None)
            cookie = (hdrs.get("Set-Cookie") or "").split(";")[0]
            lanes = oa.request(port, "GET", "/api/lanes", cookie=cookie)[2] if cookie else {}
            step("4. pai web prints a link that opens the run's lanes", st == 303 and [l["lane"] for l in lanes.get("lanes", [])]
                 == ["a"], out[-600:])
        finally:
            rc, _ = oa.stop(p)
        step("4. Ctrl-C stops pai web", rc == 0)

        edit = commands(en[4])
        p, port, link, out = oa.served(edit[0] + " --no-browser", env, app)
        try:
            st = oa.request(port, "GET", link)[0] if port else 0
            step("5. %s prints a link to the editor" % edit[0], st == 303, out[-600:])
        finally:
            oa.stop(p)

        rc, o, e = term("uv tool uninstall pai-workbench")
        rc2, o2, e2 = term("pai --version")
        cfg = os.path.join(home, ".config", "pai-workbench")
        step("6. `uv tool uninstall pai-workbench`; pai is gone, configuration stays",
             rc == 0 and rc2 != 0 and os.path.isdir(cfg), o + e + o2 + e2)
    finally:
        for d, _, files in os.walk(tmuxdir):
            for fn in files:
                subprocess.run(["tmux", "-S", os.path.join(d, fn), "kill-server"], capture_output=True)
        res = {"version": version, "steps": STEPS, "stuck_points": [s for s in STEPS if s["stuck"] or not s["ok"]]}
        with open(os.path.join(work, "walkthrough.json"), "w") as f:
            json.dump(res, f, indent=1)
        print("\n%d steps, %d stuck point(s)" % (len(STEPS), len(res["stuck_points"])))
    return 0 if all(s["ok"] for s in STEPS) else 1


def _wait(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end and not fn():
        time.sleep(0.2)
    return fn()


if __name__ == "__main__":
    sys.exit(main())
