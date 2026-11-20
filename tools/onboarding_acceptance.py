#!/usr/bin/env python3
"""New-user acceptance, from built archives to a first delivered task, with fakes only.

    python3 -B tools/onboarding_acceptance.py --work /abs/new/dir [--archive TGZ]
    python3 -B tools/onboarding_acceptance.py --work /abs/new/dir --uv SPEC

With --archive, steps 2-3 and 5 use that built archive (its .sha256 beside it) instead of a fresh
build. With --uv, a package replaces the archive: `uv tool install SPEC` (SPEC as a user would
type it, for example git+https://github.com/OWNER/REPO@vX.Y.Z; uv from PATH), the PATH line uv
prints, then step 4 as below, then a reinstall, `pai install` pointing back to uv, and
`uv tool uninstall` leaving configuration and task notes alone.

Linux and macOS (it needs git, tmux, bash and a pseudo-terminal). In a new WORK directory,
with a fresh HOME there and nothing inherited from your environment except a minimal PATH:

  1. builds this checkout's archives (and a copy versioned one patch higher, for the upgrade)
  2. verifies the .sha256 sidecar with sha256sum/shasum, and shows a corrupted copy refused
  3. extracts, runs bin/pai install, and shows that a fresh shell cannot find `pai` until the
     printed PATH line is applied; then `pai --version`, bare `pai` and `pai --help`
  4. in a new Git checkout (with a local bare origin): pai doctor, then `pai init` through a
     pseudo-terminal answering with Enter wherever that is the safe default (each prompt is
     recorded), pai task new (preview, --write, refused rewrite), pai task list,
     pai run --dry-run, pai run with fake Claude Code on an isolated tmux socket, pai status;
     then the browser tools from the same installed pai: pai paste into the lane, pai web
     (one-time link, the lane's screen, typing that reaches the agent, a multi-line paste the
     fake agent cannot take refused) and pai edit on the task note
  5. reinstall (kept), upgrade (configuration and task note unchanged), a damaged bundle and an
     unwritable install folder both refused with the previous version still answering

Agents, gh and ssh are this checkout's test fakes (swarm/tests); no real agent, network or
account is used, and nothing outside WORK is touched (the tmux server has its own socket in
WORK). Writes WORK/onboarding-results.json; exit 0 only if every check passed.
"""
import argparse
import http.client
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "swarm", "tests")
RESULTS, TIMES, PROMPTS, META = [], {}, [], {}


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[-600:]})
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "\n      " + str(detail)[-600:]), flush=True)
    return ok


def skip(name, why):
    """A check this environment cannot stage: reported as SKIPPED with the reason, never as passed."""
    RESULTS.append({"check": name, "ok": None, "skipped": why})
    print("SKIP  %s (%s)" % (name, why), flush=True)


# A shell reports a command it cannot find with exit status 127 (POSIX). The message differs:
# bash says "pai: command not found", Ubuntu's command-not-found hook "Command 'pai' not found,
# ...", zsh "command not found: pai". So the checks below test the status, and keep stderr only
# as the diagnostic.
COMMAND_NOT_FOUND = 127


def read(path, mode="r"):
    """The file's content, read with the handle closed."""
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def write(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
        f.write(data)


def jsonl(path):
    """Parsed JSON lines of a file, [] when it does not exist."""
    return [json.loads(l) for l in read(path).splitlines() if l.strip()] if os.path.exists(path) else []


def pai_missing(rc):
    """True when a fresh shell could not find `pai` (exit status 127, whatever it printed)."""
    return rc == COMMAND_NOT_FOUND


def sh(argv, env, cwd=None, timeout=180, input=None):
    p = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=input,
                       stdin=None if input is not None else subprocess.DEVNULL)
    return p.returncode, p.stdout, p.stderr


def build(src, out):
    rc, o, e = sh([sys.executable, "-B", os.path.join(src, "tools", "build_bundle.py"), "--root", src, "--out", out],
                  dict(os.environ, SOURCE_DATE_EPOCH="0"))
    if rc != 0:
        raise SystemExit("build failed: %s" % e)
    return [l for l in o.splitlines() if l.endswith(".tar.gz")][0]


def next_version_tree(work):
    """A copy of this checkout with the patch version raised (for the upgrade check)."""
    dest = os.path.join(work, "src-next")
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
    init = os.path.join(dest, "pai", "__init__.py")
    text = read(init)
    m = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', text)
    new = "%s.%s.%d" % (m.group(1), m.group(2), int(m.group(3)) + 1)
    with open(init, "w", encoding="utf-8") as f:
        f.write(text.replace(m.group(0), '__version__ = "%s"' % new))
    return dest, new


def fakes(bindir):
    os.makedirs(bindir)
    for name, fake in (("claude", "fake_claude_rc.py"), ("codex", "fake_codex.py"), ("gh", "fake_gh.py"),
                       ("ssh", "fake_ssh.py"), ("ssh-add", "fake_ssh_add.py")):
        path = os.path.join(bindir, name)
        with open(path, "w") as f:
            f.write("#!%s\nimport runpy, sys\nsys.argv[0] = __file__\nrunpy.run_path(%r, run_name='__main__')\n"
                    % (sys.executable, os.path.join(TESTS, fake)))
        os.chmod(path, 0o755)


def interactive(argv, env, cwd, answer, timeout=120):
    """Run argv on a pseudo-terminal; for each prompt (output ending in ': ') ask answer(prompt)
    for the text to type. Returns (exit code, transcript)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, env)
    buf, pending, end = b"", b"", time.time() + timeout
    status = None
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buf += chunk
            pending += chunk
            text = pending.decode("utf-8", "replace")
            if re.search(r"[:?\]] $", text.rstrip("\r\n").split("\n")[-1] if text.strip() else "") and text.endswith(" "):
                prompt = text.rstrip().split("\n")[-1].strip()
                reply = answer(prompt)
                PROMPTS.append({"prompt": prompt, "typed": reply})
                os.write(fd, (reply + "\r").encode())
                pending = b""
        else:
            done, st = os.waitpid(pid, os.WNOHANG)
            if done:
                status = st
                break
    if status is None:
        try:
            _, status = os.waitpid(pid, 0 if time.time() < end else os.WNOHANG)
        except ChildProcessError:
            status = 0
        if status is None:
            os.kill(pid, signal.SIGKILL)
            status = 9
    os.close(fd)
    return (os.waitstatus_to_exitcode(status) if status is not None else -1), buf.decode("utf-8", "replace")


def served(cmd, env, cwd):
    """Start `cmd` (pai web / pai edit, --no-browser) in a fresh login-like bash; (process, port,
    path of its one-time link, printed lines)."""
    p = subprocess.Popen(["bash", "-ic", "exec " + cmd], env=env, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    lines, end = [], time.time() + 60
    while time.time() < end and not any("open:" in l for l in lines):
        line = p.stdout.readline()
        if not line:
            break
        lines.append(line)
    url = next((l.split("open:")[1].strip() for l in lines if "open:" in l), "")
    port = int(url.split(":")[2].split("/")[0]) if url else 0
    return p, port, url.split(str(port), 1)[1] if url else "", "".join(lines)


def request(port, method, path, body=None, cookie=None, csrf=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": "127.0.0.1:%d" % port}
    h.update({"Cookie": cookie} if cookie else {})
    h.update({"X-PAI-CSRF": csrf, "Content-Type": "application/json"} if csrf else {})
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        data = json.loads(data)
    except ValueError:
        pass
    return r.status, dict(r.getheaders()), data


def stop(p):
    p.send_signal(signal.SIGINT)
    try:
        rc = p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = p.wait()
    out = p.stdout.read()
    p.stdout.close()
    p.stderr.close()
    return rc, out


def browser_tools(fresh, env0, repo, ctl, note, work):
    """pai paste, pai web and pai edit, from the installed pai, against the run just started."""
    def received():
        path = os.path.join(ctl, "received.jsonl")
        return [r["text"] for r in jsonl(path)]

    def wait(fn, timeout=15):
        end = time.time() + timeout
        while time.time() < end and not fn():
            time.sleep(0.2)
        return fn()
    msg = os.path.join(work, "message.md")
    with open(msg, "w") as f:
        f.write("from pai paste\n")
    rc, o2, e2 = fresh("pai paste a %s --enter" % shlex.quote(msg), cwd=repo)
    check("pai paste: a message file reaches the lane's agent in one piece, Enter only with --enter",
          rc == 0 and "Enter pressed" in o2 and wait(lambda: "from pai paste" in received()), o2 + e2)

    p, port, link, out = served("pai web --no-browser", env0, repo)
    try:
        st, hdrs, _ = request(port, "GET", link) if port else (0, {}, None)
        cookie = (hdrs.get("Set-Cookie") or "").split(";")[0]
        again = request(port, "GET", link)[0] if port else 0
        check("pai web prints a one-time 127.0.0.1 link that signs in once",
              port and st == 303 and again == 401 and "127.0.0.1" in out, out[-500:])
        if port and cookie:
            csrf = request(port, "GET", "/api/session", cookie=cookie)[2]["csrf"]
            st, _, lanes = request(port, "GET", "/api/lanes", cookie=cookie)
            st2, _, screen = request(port, "GET", "/api/screen?lane=a", cookie=cookie)
            check("pai web lists the lane with its task note and shows its screen",
                  st == 200 and [l["lane"] for l in lanes["lanes"]] == ["a"] and lanes["lanes"][0]["note_link"]
                  and st2 == 200 and "received:" in screen.get("text", ""), (lanes, screen.get("text", "")[-300:]))
            st, _, d = request(port, "POST", "/api/keys", {"lane": "a", "keys": [{"text": "typed in the browser"},
                                                                              {"key": "Enter"}]}, cookie, csrf)
            check("pai web: typing in the browser reaches the agent",
                  st == 200 and wait(lambda: "typed in the browser" in received()), d)
            st, _, d = request(port, "POST", "/api/paste", {"lane": "a", "text": "two\nlines"}, cookie, csrf)
            check("pai web: a multi-line paste to a program without bracketed paste is refused, nothing sent",
                  st == 409 and re.match(r"(not-bracketed|bracketed-unknown): ", d.get("error", ""))
                  and "two" not in received(), d)
    finally:
        rc, out2 = stop(p)
    check("pai web stops on Ctrl-C", rc == 0 and "stopped" in out2, out2[-300:])

    before = read(note, "rb")
    p, port, link, out = served("pai edit %s --no-browser" % shlex.quote(note), env0, repo)
    try:
        st, hdrs, _ = request(port, "GET", link) if port else (0, {}, None)
        cookie = (hdrs.get("Set-Cookie") or "").split(";")[0]
        page = request(port, "GET", "/", cookie=cookie)[2] if cookie else b""
        doc = request(port, "GET", "/api/file?root=0&path=" + os.path.basename(note), cookie=cookie)[2] if cookie else {}
        check("pai edit opens the task note in the browser editor from the installed pai",
              st == 303 and b'id="text"' in page and "Add a smoke line" in doc.get("text", ""), out[-400:])
    finally:
        rc, out2 = stop(p)
    check("pai edit stops on Ctrl-C and the note is unchanged", rc == 0 and read(note, "rb") == before, out2[-300:])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", required=True, help="a new absolute directory")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--archive", help="a built pai-workbench-X.Y.Z.tar.gz (with its .sha256 beside it) to test")
    mode.add_argument("--uv", metavar="SPEC", help="test `uv tool install SPEC` instead of an archive")
    a = ap.parse_args(argv)
    work = os.path.abspath(a.work)
    if os.path.exists(work):
        raise SystemExit("--work %s exists; give a new directory" % work)
    os.makedirs(work)
    sock = os.path.join(work, "tmux.sock")
    env0 = version = None
    try:
        t0 = time.time()
        META.update(mode="uv tool install" if a.uv else "archive", source=a.uv or a.archive or "built from this checkout",
                    platform=sys.platform, python=sys.version.split()[0])
        if a.uv:
            version = re.search(r'__version__ = "([^"]+)"', read(os.path.join(ROOT, "pai", "__init__.py"))).group(1)
        else:
            tgz = os.path.abspath(a.archive) if a.archive else build(ROOT, os.path.join(work, "dist"))
            nxt_src, nxt = next_version_tree(work)
            tgz_next = build(nxt_src, os.path.join(work, "dist-next"))
            TIMES["build archives (developer side)"] = round(time.time() - t0, 1)
            version = re.search(r"pai-workbench-([\d.]+)\.tar\.gz", tgz).group(1)

        home, dl, ctl = (os.path.join(work, d) for d in ("home", "downloads", "ctl"))
        for d in (home, dl, ctl):
            os.makedirs(d)
        fakes(os.path.join(work, "fakebin"))
        base_path = os.pathsep.join([os.path.join(work, "fakebin")] + [os.path.dirname(shutil.which(t)) for t in (
            "git", "tmux", "bash", "tar", "python3") + (("uv",) if a.uv else ()) if shutil.which(t)] + ["/usr/bin", "/bin"])
        base_path = os.pathsep.join(dict.fromkeys(base_path.split(os.pathsep)))
        env0 = {"HOME": home, "PATH": base_path, "SHELL": "/bin/bash", "LANG": "C.UTF-8", "TERM": "xterm",
                "FAKE_RC_DIR": ctl, "CLAUDE_CONFIG_DIR": os.path.join(work, "claude-config"),
                "CODEX_HOME": os.path.join(work, "codex-home"), "FAKE_GH_STATE": os.path.join(work, "gh.json"),
                "FAKE_GH_REMOTE": os.path.join(work, "origin.git"), "PAI_SWARM_GH": os.path.join(work, "fakebin", "gh"),
                "PAI_JEV_CREDENTIAL_STORE": "none", "GIT_CONFIG_NOSYSTEM": "1"}
        os.makedirs(env0["CLAUDE_CONFIG_DIR"])

        def fresh(cmd, cwd=None, timeout=180):
            """A new interactive bash with this HOME: it reads ~/.bashrc like a new terminal."""
            return sh(["bash", "-ic", cmd], env0, cwd=cwd or home, timeout=timeout)

        setup = time.time()
        if a.uv:
            # -- install with uv; the missing PATH; the repair
            uv = shutil.which("uv", path=base_path)
            rc, o, e = sh([uv, "tool", "install", a.uv], env0, cwd=home, timeout=600)
            TIMES["uv tool install (download and build)"] = round(time.time() - setup, 1)
            check("uv tool install %s succeeds without root" % a.uv, rc == 0 and "Installed 1 executable: pai" in (o + e),
                  o + e)
            m = re.search(r"run `(export PATH=[^`]+)`", o + e)
            check("uv names the missing PATH and the exact line to add", "is not on your PATH" in (o + e) and m, o + e)
            rc, o2, e2 = fresh("pai --version")
            check("before the fix, a fresh shell cannot find `pai` (exit 127)", pai_missing(rc),
                  "exit %s; %s" % (rc, o2 + e2))
            if m:
                with open(os.path.join(home, ".bashrc"), "a") as f:
                    f.write(m.group(1) + "\n")
            rc, o2, e2 = fresh("pai --version")
            check("after that line, a fresh shell runs pai %s" % version, rc == 0 and o2.strip() == "pai %s" % version,
                  o2 + e2)
            rc, o2, _ = fresh("pai")
            check("bare `pai` shows the start-here path and the packaged guide", rc == 0 and all(s in o2 for s in (
                "pai doctor", "pai init", "pai task new", "pai run --dry-run", "_workbench/WORKBENCH.md")), o2)
        else:
            # -- download and verify
            for f in (tgz, tgz + ".sha256", tgz_next, tgz_next + ".sha256"):
                shutil.copy(f, dl)
            name = os.path.basename(tgz)
            summer = ["sha256sum", "-c"] if shutil.which("sha256sum") else ["shasum", "-a", "256", "-c"]
            rc, o, e = sh(summer + [name + ".sha256"], env0, cwd=dl)
            check("published .sha256 verifies with %s" % " ".join(summer), rc == 0 and "OK" in o, o + e)
            side = read(os.path.join(dl, name + ".sha256"))
            check("sidecar format is '<sha256>  <file>'", re.fullmatch(r"[0-9a-f]{64}  %s\n" % re.escape(name), side), side)
            bad = os.path.join(work, "bad")
            os.makedirs(bad)
            data = bytearray(read(tgz, "rb"))
            data[len(data) // 2] ^= 0xFF
            write(os.path.join(bad, name), bytes(data))
            shutil.copy(tgz + ".sha256", bad)
            rc, o, e = sh(summer + [name + ".sha256"], env0, cwd=bad)
            check("a corrupted archive fails the checksum", rc != 0 and "FAILED" in (o + e), o + e)

            # -- install; the missing PATH; the repair
            rc, o, e = sh(["tar", "-xzf", name], env0, cwd=dl)
            bundle = os.path.join(dl, "pai-workbench-%s" % version)
            rc, o, e = sh([os.path.join(bundle, "bin", "pai"), "install"], env0, cwd=dl)
            check("bin/pai install succeeds without root", rc == 0 and "launcher" in o, o + e)
            fix = [l.strip() for l in o.splitlines() if l.strip().startswith("echo 'export PATH=")]
            check("install names the missing PATH and the exact line to add", "is not on PATH" in o and fix, o)
            rc, o2, e2 = fresh("pai --version")
            check("before the fix, a fresh shell cannot find `pai` (exit 127)", pai_missing(rc),
                  "exit %s; %s" % (rc, o2 + e2))
            if fix:
                sh(["bash", "-c", fix[0]], env0, cwd=home)
            rc, o2, e2 = fresh("pai --version")
            check("after the printed line, a fresh shell runs pai %s" % version, rc == 0 and o2.strip() == "pai %s" % version,
                  o2 + e2)
            rc, o2, _ = fresh("pai")
            check("bare `pai` shows the start-here path", rc == 0 and all(s in o2 for s in (
                "pai doctor", "pai init", "pai task new", "pai run --dry-run", "pai run ")), o2)
            rc, o2, _ = fresh("pai --help")
            check("`pai --help` starts with the same path, then every command",
                  rc == 0 and o2.index("pai doctor") < o2.index("usage:") and "install" in o2 and "task" in o2, o2[:400])

        # -- a checkout
        origin, repo = env0["FAKE_GH_REMOTE"], os.path.join(home, "code", "demo")
        git = lambda *args, cwd=None: sh(["git"] + list(args), dict(env0, GIT_AUTHOR_NAME="New User",  # noqa: E731
                                         GIT_AUTHOR_EMAIL="new@example.org", GIT_COMMITTER_NAME="New User",
                                         GIT_COMMITTER_EMAIL="new@example.org"), cwd=cwd)
        git("init", "-q", "--bare", "-b", "main", origin)
        git("clone", "-q", origin, repo)
        write(os.path.join(repo, "README.md"), "demo\n")
        git("add", "README.md", cwd=repo)
        git("commit", "-q", "-m", "init", cwd=repo)
        git("push", "-q", "origin", "main", cwd=repo)
        rc, o2, e2 = fresh("pai doctor", cwd=repo)
        check("pai doctor: this pai is on PATH and intact (%s), the checkout needs `pai init`"
              % ("package RECORD" if a.uv else "SHA256SUMS"), "runs this pai %s" % version in o2 and "-> pai init" in o2
              and ("match its RECORD" if a.uv else "match SHA256SUMS") in o2, o2 + e2)
        TIMES["doctor"] = round(time.time() - setup, 1)

        def answer(prompt):
            return "1" if prompt.startswith("Default agent") else ""  # 1 = claude; Enter everywhere else
        rc, out = interactive(["bash", "-ic", "pai init"], env0, repo, answer)
        cfgs = [os.path.join(r, f) for r, _, fs in os.walk(os.path.join(home, ".config", "pai-workbench")) for f in fs
                if f.endswith(".toml")]
        check("pai init in a terminal saves with Enter for every default but the agent", rc == 0 and cfgs and
              sum(1 for p in PROMPTS if p["typed"]) == 1, out[-800:])
        check("pai init ends with the next commands for this setup", "pai task new" in out and "pai run --dry-run" in out,
              out[-600:])
        rc, o2, e2 = fresh('pai task new "Add a smoke line" --body "Append the line smoke to NOTES.md and commit it."',
                           cwd=repo)
        tasks = os.path.join(home, ".local", "share", "pai-workbench", "tasks", "demo")
        note = os.path.join(tasks, "add-a-smoke-line.md")
        check("pai task new previews and writes nothing", rc == 0 and "PREVIEW" in o2 and not os.path.exists(note), o2 + e2)
        rc, o2, e2 = fresh('pai task new "Add a smoke line" --body "Append the line smoke to NOTES.md and commit it." '
                           '--write', cwd=repo)
        check("pai task new --write saves a todo note", rc == 0 and os.path.isfile(note) and "status: todo" in read(note),
              o2 + e2)
        before = read(note, "rb") if os.path.isfile(note) else b""
        rc, o2, e2 = fresh('pai task new "Add a smoke line" --body "other" --write', cwd=repo)
        check("an existing note is never overwritten", rc == 1 and "already exists" in (o2 + e2)
              and read(note, "rb") == before, o2 + e2)
        rc, o2, _ = fresh("pai task list", cwd=repo)
        check("pai task list shows the queue", rc == 0 and "add-a-smoke-line" in o2 and "1 todo" in o2, o2)
        refs = git("for-each-ref", cwd=repo)[1]
        rc, o2, e2 = fresh("pai run --dry-run --workers 1 --tmux-socket %s" % shlex.quote(sock), cwd=repo)
        check("pai run --dry-run changes nothing", rc == 0 and "DRY RUN" in o2 and git("for-each-ref", cwd=repo)[1] == refs
              and not os.path.exists(sock), o2 + e2)
        rc, o2, e2 = fresh("pai run --workers 1 --yes --wait 20 --tmux-socket %s" % shlex.quote(sock), cwd=repo, timeout=240)
        rc, o3, _ = fresh("pai status --json", cwd=repo)
        try:
            lane = json.loads(o3)["lanes"][0]
        except (ValueError, IndexError, KeyError):
            lane = {}
        got = jsonl(os.path.join(ctl, "received.jsonl"))
        check("pai run: the fake agent took the note as its prompt, and status shows the evidence",
              (lane.get("dispatch") or {}).get("state") == "delivered" and "transcript" in lane["dispatch"].get("evidence", "")
              and len(got) == 1 and "add-a-smoke-line" in got[0]["text"], (o2 + e2)[-600:] + o3[-400:])
        TIMES["setup: %s -> first task delivered" % ("uv tool install" if a.uv else "verify download")] = round(
            time.time() - setup, 1)
        browser_tools(fresh, env0, repo, ctl, note, work)

        cfg_bytes = {p: read(p, "rb") for p in cfgs}
        note_bytes = read(note, "rb") if os.path.isfile(note) else None
        unchanged = lambda: (all(read(p, "rb") == b for p, b in cfg_bytes.items())  # noqa: E731
                             and os.path.isfile(note) and read(note, "rb") == note_bytes)
        if a.uv:
            rc, o2, e2 = sh([uv, "tool", "install", a.uv], env0, cwd=home, timeout=600)
            rc2, v, _ = fresh("pai --version")
            check("the same uv tool install again (kept or rebuilt by uv) leaves pai %s, its configuration and "
                  "the task note" % version, rc == 0 and v.strip() == "pai %s" % version and unchanged(), o2 + e2 + v)
            rc, o2, e2 = fresh("pai install")
            check("`pai install` in a uv install says to use uv instead, and changes nothing",
                  rc == 2 and "uv tool install --force" in e2 and unchanged(), o2 + e2)
            rc, o2, e2 = sh([uv, "tool", "uninstall", "pai-workbench"], env0, cwd=home, timeout=120)
            rc2, _, e3 = fresh("pai --version")
            check("uv tool uninstall removes pai (a fresh shell exits 127) and leaves the configuration and the task note",
                  rc == 0 and pai_missing(rc2) and unchanged(), "exit %s; %s" % (rc2, o2 + e2 + e3))
            return finish(work, sock, env0, version)

        # -- reinstall, upgrade, failures
        rc, o2, e2 = sh([os.path.join(bundle, "bin", "pai"), "install"], env0, cwd=dl)
        check("a second install of the same version keeps it", rc == 0 and "already installed and intact" in o2, o2 + e2)
        nname = os.path.basename(tgz_next)
        sh(summer + [nname + ".sha256"], env0, cwd=dl)
        sh(["tar", "-xzf", nname], env0, cwd=dl)
        nbundle = os.path.join(dl, "pai-workbench-%s" % nxt)
        rc, o2, e2 = fresh("%s install" % shlex.quote(os.path.join(nbundle, "bin", "pai")))
        rc2, v, _ = fresh("pai --version")
        check("upgrade %s -> %s switches the launcher" % (version, nxt), rc == 0 and v.strip() == "pai %s" % nxt
              and "was %s" % version in o2, o2 + e2 + v)
        check("upgrade leaves the configuration and the task note byte for byte", unchanged())
        damaged = os.path.join(work, "damaged")
        shutil.copytree(nbundle, damaged)
        with open(os.path.join(damaged, "swarm", "rc.py"), "a") as f:
            f.write("\n# changed\n")
        rc, o2, e2 = sh([os.path.join(damaged, "bin", "pai"), "install"], env0, cwd=dl)
        rc2, v, _ = fresh("pai --version")
        check("a damaged bundle is refused and the installed version still answers",
              rc == 2 and "does not match its SHA256SUMS" in e2 and v.strip() == "pai %s" % nxt, o2 + e2 + v)
        versions = os.path.join(home, ".local", "share", "pai-workbench", "versions")
        shutil.rmtree(os.path.join(versions, version))  # make the older version a real copy again
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            # root writes into a mode-0555 folder anyway, so this failure cannot be staged here
            skip("a failed install (unwritable folder) leaves the previous version answering",
                 "running as root (a container or `unshare -r`): an unwritable folder cannot be made")
        else:
            os.chmod(versions, 0o555)
            try:
                rc, o2, e2 = sh([os.path.join(bundle, "bin", "pai"), "install"], env0, cwd=dl)
            finally:
                os.chmod(versions, 0o755)
            rc2, v, _ = fresh("pai --version")
            check("a failed install (unwritable folder) leaves the previous version answering",
                  rc == 2 and "unchanged" in e2 and v.strip() == "pai %s" % nxt, o2 + e2 + v)
        rc, o2, _ = sh([os.path.join(versions, nxt, "bin", "pai"), "doctor"], dict(env0, PATH=base_path), cwd=repo)
        check("pai doctor names the missing PATH entry and its fix", "is not on PATH" in o2 and "export PATH=" in o2, o2)
    except BaseException:
        finish(work, sock, env0, version)
        raise
    return finish(work, sock, env0, version)


def finish(work, sock, env0, version):
    if env0 and os.path.exists(sock):
        sh(["tmux", "-S", sock, "kill-server"], env0)
    res = {"version": version, **META, "checks": RESULTS, "seconds": TIMES, "prompts": PROMPTS,
           "manual_prompts_answered_with_text": sum(1 for p in PROMPTS if p["typed"]),
           "passed": bool(RESULTS) and all(r["ok"] is not False for r in RESULTS),
           "skipped": [r["check"] for r in RESULTS if r["ok"] is None]}
    with open(os.path.join(work, "onboarding-results.json"), "w") as f:
        json.dump(res, f, indent=1)
    print("\n%d/%d checks passed%s; times %s; prompts %d (typed %d)" % (
        sum(1 for r in RESULTS if r["ok"] is True), len(RESULTS),
        ", %d skipped" % len(res["skipped"]) if res["skipped"] else "", TIMES, len(PROMPTS),
        res["manual_prompts_answered_with_text"]))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
