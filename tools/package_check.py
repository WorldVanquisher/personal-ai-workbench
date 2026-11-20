#!/usr/bin/env python3
"""Install and upgrade confidence, offline, in disposable folders (1.0 readiness 081-086).

    python3 -B tools/package_check.py --work DIR [--old-ref REF] [--candidate VERSION] [--setuptools PATH]

From a checkout. Builds, from scratch copies (the checkout is never written):
  candidate  this tree, with pai/__init__.py's version set to --candidate (default 0.5.0; X.Y.Z or a pre-release such as 0.5.0rc2) in
             the copy only, so the two installs can be told apart; the tree's own version is kept
  previous   `git archive REF` (default: the last release commit, b02acec = v0.4.0)
each as a wheel (pip, no build isolation, no index: setuptools comes from --setuptools, a local
folder, or the interpreter) and as release archives (tools/build_bundle.py). Then checks:
  081  the candidate wheel installs into a new venv with no network and runs
  082  installed, from a folder that is not a checkout, it finds its workbench (pai/_workbench)
       and runs `pai guide` with the fake worker
  083  upgrade preview: with the previous version installed and a sample configuration (project,
       task notes, one run with a fake agent), the candidate reads all of it without writing
       (config check, status, task list, recover preview: digests unchanged); pip's own
       --dry-run names the upgrade; `bin/pai install --dry-run` shows the archive upgrade and
       writes nothing
  084  an archive install killed at several points always leaves a working launcher
  085  uninstall removes the program and leaves configuration, task notes and run state
  086  one build: wheel metadata, the package's version, the archive name and SHA256SUMS agree,
       and every workbench file in the wheel is byte-identical to the archive's
  console  (R2-058) the installed candidate's `pai web` against a run: lanes, task ids, lifecycle and the
       recovery preview equal `pai status --json` and `pai recover --json`; applying needs confirm,
       the CSRF header and a session; it listens on 127.0.0.1 only
  rollback  (R2-064) with candidate run records (a handed-off run and its continuation) and a task
       card file: install the previous wheel over it, read the records, resume the handed-off run
       (no prompt is sent again), then the candidate again: still handed off, cards import unchanged
  continuation  (0.5.0 slice) `pai guide --handoff clean`, `--handoff reverse` and `--handoff
       interrupted` through the installed wheel from a clean HOME, and `clean` and `reverse` through
       the installed archive launcher: both transfer directions (Claude to Codex, Codex to Claude)
       through a transfer packet into a fresh HOME, and an uncertain delivery; each completes with
       exactly one prompt per session, and HOME stays untouched
Prints PASS/FAIL per check, writes DIR/package-check.json, exits 0 only if all pass.
"""
import argparse
import base64
import csv
import glob
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = []


def check(item, name, ok, detail=""):
    results.append({"item": item, "name": name, "ok": bool(ok), "detail": str(detail)[-600:]})
    print("%s %s  %s%s" % ("PASS" if ok else "FAIL", item, name, "" if ok else "  :: %s" % str(detail)[-600:]), flush=True)
    return ok


def run(argv, env=None, cwd=None, timeout=300, ok=(0,)):
    p = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if ok is not None and p.returncode not in ok:
        raise RuntimeError("%s exited %s: %s" % (" ".join(argv[:5]), p.returncode, (p.stdout + p.stderr)[-1500:]))
    return p


def digest(*dirs):
    h = hashlib.sha256()
    for d in dirs:
        for root, ds, fs in os.walk(d):
            ds.sort()
            for fn in sorted(fs):
                if fn.endswith((".mutex", ".pyc")):
                    continue
                p = os.path.join(root, fn)
                if os.path.islink(p):
                    h.update(p.encode() + b"->" + os.readlink(p).encode())
                    continue
                with open(p, "rb") as f:
                    h.update(p.encode() + b"\0" + f.read())
    return h.hexdigest()


def sources(work, ref, candidate):
    cur = os.path.join(work, "src-candidate")
    shutil.copytree(ROOT, cur, ignore=shutil.ignore_patterns(".git", "__pycache__", "dist", "build", "*.egg-info"))
    init = os.path.join(cur, "pai", "__init__.py")
    with open(init, encoding="utf-8") as f:
        text = f.read()
    with open(init, "w", encoding="utf-8") as f:
        f.write(re.sub(r'__version__ = "[^"]+"', '__version__ = "%s"' % candidate, text))
    old = os.path.join(work, "src-previous")
    os.makedirs(old)
    raw = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", ref], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        t.extractall(old, filter="data")
    return cur, old


def version_of(src):
    with open(os.path.join(src, "pai", "__init__.py"), encoding="utf-8") as f:
        return re.search(r'__version__ = "([^"]+)"', f.read()).group(1)


def build(src, out, setuptools):
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ, PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    if setuptools:
        env["PYTHONPATH"] = setuptools
    tmp = tempfile.mkdtemp(prefix="pai-build-")
    try:
        build_src = os.path.join(tmp, "src")  # setuptools writes build/ and *.egg-info next to the sources
        shutil.copytree(src, build_src)
        run([sys.executable, "-m", "pip", "wheel", "--no-build-isolation", "--no-deps", "--no-index", "-w", out, build_src],
            env=env, timeout=600)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    wheel = glob.glob(os.path.join(out, "pai_workbench-%s-*.whl" % version_of(src).replace("-", "_")))[0]
    p = run([sys.executable, "-B", os.path.join(src, "tools", "build_bundle.py"), "--out", out])
    lines = p.stdout.splitlines()
    tgz = next(l for l in lines if l.endswith(".tar.gz"))
    return wheel, tgz


def venv(path, wheel, env):
    run([sys.executable, "-m", "venv", path], env=env)
    run([os.path.join(path, "bin", "python"), "-m", "pip", "install", "--no-index", "--no-deps", "--quiet", wheel],
        env=dict(env, PIP_DISABLE_PIP_VERSION_CHECK="1"))
    return os.path.join(path, "bin", "pai")


def home_env(work, name):
    h = os.path.join(work, name)
    for d in ("home", "cfg", "data", "state", "claude", "codex", "ctl", "bin"):
        os.makedirs(os.path.join(h, d), exist_ok=True)
    tools = [os.path.dirname(shutil.which(t)) for t in ("git", "tmux", "ps") if shutil.which(t)]
    env = {"HOME": os.path.join(h, "home"), "PATH": os.pathsep.join(dict.fromkeys([os.path.join(h, "bin")] + tools +
                                                                                   ["/usr/bin", "/bin"])),
           "LANG": "C.UTF-8", "TERM": "xterm", "PAI_WORKBENCH_HOME": os.path.join(h, "cfg"),
           "PAI_WORKBENCH_DATA": os.path.join(h, "data"), "PAI_SWARM_HOME": os.path.join(h, "state"),
           "CLAUDE_CONFIG_DIR": os.path.join(h, "claude"), "CODEX_HOME": os.path.join(h, "codex"),
           "FAKE_RC_DIR": os.path.join(h, "ctl"), "PAI_DISPATCH_SETTLE": "0.5", "PAI_JEV_CREDENTIAL_STORE": "none",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Check",
           "GIT_AUTHOR_EMAIL": "check@example.invalid", "GIT_COMMITTER_NAME": "Check",
           "GIT_COMMITTER_EMAIL": "check@example.invalid", "TMPDIR": os.environ.get("TMPDIR", "/tmp")}
    fake = os.path.join(ROOT, "swarm", "tests", "fake_claude_rc.py")
    with open(os.path.join(h, "bin", "claude"), "w") as f:
        f.write("#!%s\nimport runpy, sys\nsys.argv[0] = __file__\nrunpy.run_path(%r, run_name='__main__')\n" % (sys.executable, fake))
    os.chmod(os.path.join(h, "bin", "claude"), 0o755)
    return h, env


def sample_project(h, env, pai):
    repo, origin, tasks = (os.path.join(h, d) for d in ("repo", "origin.git", "tasks"))
    run(["git", "init", "-q", "--bare", "-b", "main", origin], env=env)
    run(["git", "init", "-q", "-b", "main", repo], env=env)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("# sample\n")
    run(["git", "add", "README.md"], env=env, cwd=repo)
    run(["git", "commit", "-q", "-m", "init"], env=env, cwd=repo)
    run(["git", "remote", "add", "origin", origin], env=env, cwd=repo)
    run(["git", "push", "-q", "origin", "main"], env=env, cwd=repo)
    os.makedirs(tasks)
    for n, prio in (("first", 2), ("second", 1)):
        with open(os.path.join(tasks, n + ".md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: todo\npriority: %d\n---\n# Task %s\n\nDo %s.\n" % (prio, n, n))
    sock = os.path.join(h, "t.sock")
    run([pai, "init", "--no-input", "--default-agent", "claude", "--base", "main", "--worktree-root",
         os.path.join(h, "lanes"), "--tasks", tasks], env=env, cwd=repo)
    run([pai, "run", "--workers", "1", "--mode", "local", "--tmux-socket", sock, "--yes", "--wait", "8"], env=env, cwd=repo,
        ok=(0, 1))
    return repo, tasks, sock


def console_check(pai, env, repo, run_id):
    """R2-058: the installed `pai web` shows the same lifecycle and recovery plan as the CLI, on
    127.0.0.1 only, and its preview changes nothing."""
    import http.client
    web = subprocess.Popen([pai, "web", "--no-browser", "--run", run_id], cwd=repo, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    try:
        link, end = None, time.time() + 30
        while time.time() < end and not link:
            line = web.stdout.readline()
            if not line:
                break
            mt = re.search(r"open:\s*(http://127\.0\.0\.1:(\d+)(/\S*))", line)
            if mt:
                link, port, path = mt.group(1), int(mt.group(2)), mt.group(3)
        if not link:
            check("console", "installed `pai web` printed its link", False, web.stderr.read()[-600:] if web.poll() else "")
            return

        def req(method, path, cookie=None, csrf=None, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            h = {"Host": "127.0.0.1:%d" % port}
            if cookie:
                h["Cookie"] = cookie
            if csrf:
                h["X-PAI-CSRF"] = csrf
            if body is not None:
                body = json.dumps(body)
                h["Content-Type"] = "application/json"
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            data = r.read()
            hdrs = dict(r.getheaders())
            c.close()
            try:
                data = json.loads(data)
            except ValueError:
                pass
            return r.status, hdrs, data

        st, hdrs, _ = req("GET", path)
        cookie = (hdrs.get("Set-Cookie") or "").split(";")[0]
        csrf = req("GET", "/api/session", cookie)[2].get("csrf")
        for _ in range(20):  # compare at a stable moment: a busy agent turns running -> delivered between two reads
            cli = json.loads(run([pai, "status", "--json", "--run", run_id], env=env, cwd=repo, ok=None).stdout)
            _, _, lanes = req("GET", "/api/lanes", cookie)
            cli2 = json.loads(run([pai, "status", "--json", "--run", run_id], env=env, cwd=repo, ok=None).stdout)
            web_states = {l["lane"]: (l["task"], l["lifecycle"]["state"]) for l in lanes.get("lanes") or []}
            cli_states = {l["lane"]: (l["ids"]["task"], l["lifecycle"]["state"]) for l in cli["lanes"]}
            if cli_states == {l["lane"]: (l["ids"]["task"], l["lifecycle"]["state"]) for l in cli2["lanes"]} and \
                    web_states == cli_states:
                break
            time.sleep(0.5)
        check("console", "installed `pai web`: lanes, task ids and lifecycle equal `pai status --json` (%s)" % web_states,
              st == 303 and web_states == cli_states and lanes.get("summary", {}).get("delivery") ==
              cli["summary"]["delivery"], json.dumps(lanes)[-600:])
        _, _, prev = req("GET", "/api/recover", cookie)
        rec = json.loads(run([pai, "recover", "--json", "--run", run_id], env=env, cwd=repo, ok=None).stdout)
        check("console", "installed `pai web`: the recovery preview equals `pai recover --json` and changed nothing",
              [(x["lane"], x["action"]) for x in prev.get("lanes") or []] == [(x["lane"], x["action"]) for x in
                                                                              rec["lanes"]] and prev.get("changed") is False,
              json.dumps(prev)[-600:])
        st_apply = req("POST", "/api/recover", cookie, csrf, {})[0]
        st_nocsrf = req("POST", "/api/recover", cookie, None, {"confirm": True})[0]
        st_anon = req("GET", "/api/lanes")[0]
        check("console", "installed `pai web`: applying needs confirm (400), the CSRF header (403) and a session (401)",
              (st_apply, st_nocsrf, st_anon) == (400, 403, 401), (st_apply, st_nocsrf, st_anon))
        listen = []
        for f in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(f) as fh:
                    for row in fh.read().splitlines()[1:]:
                        local, state = row.split()[1], row.split()[3]
                        if state == "0A" and int(local.split(":")[1], 16) == port:
                            listen.append(local.split(":")[0])
            except OSError:
                pass
        check("console", "installed `pai web` listens on 127.0.0.1 only (%s)" % listen, listen == ["0100007F"], listen)
    finally:
        web.terminate()
        try:
            web.wait(10)
        except subprocess.TimeoutExpired:
            web.kill()
            web.wait()
        web.stdout.close()
        web.stderr.close()


def received(env):
    try:
        with open(os.path.join(env["FAKE_RC_DIR"], "received.jsonl"), encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


def handoff_demo(pai, env, demo, kind, how):
    """`pai guide --handoff KIND` through an installed pai: exit 0, one prompt per session, cleaned up."""
    p = run([pai, "guide", "--handoff", kind, "--dir", demo], env=env, cwd=env["HOME"], timeout=400, ok=None)
    keep = os.path.join(os.path.dirname(os.path.dirname(env["HOME"])), "transcripts")
    os.makedirs(keep, exist_ok=True)
    with open(os.path.join(keep, "handoff-%s-%s.txt" % (kind, how.replace(" ", "-"))), "w", encoding="utf-8") as f:
        f.write("$ %s guide --handoff %s --dir %s\n%s%s\nexit %s\n" % (pai, kind, demo, p.stdout, p.stderr, p.returncode))
    try:
        with open(os.path.join(demo, "guided-received.jsonl")) as f:
            got = [json.loads(x)["run"] for x in f if x.strip()]
    except OSError:
        got = None
    check("continuation", "%s: `pai guide --handoff %s` completes with one prompt per session (%s)" % (
        how, kind, got), p.returncode == 0 and got == ["session-1", "session-2"] and
          "nothing is started twice" in p.stdout, (p.stdout + p.stderr)[-900:])
    run([pai, "guide", "--cleanup", demo], env=env, ok=None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--old-ref", default="b02acec")
    ap.add_argument("--candidate", default="0.5.0")
    ap.add_argument("--setuptools", help="a folder holding setuptools (put on PYTHONPATH for the offline build)")
    a = ap.parse_args(argv)
    work = os.path.realpath(a.work)
    if os.path.exists(work):
        print("package_check: %s exists; give a new folder" % work)
        return 2
    os.makedirs(work)
    socks = []
    try:
        cur, old = sources(work, a.old_ref, a.candidate)
        prev_version = version_of(old)
        cwhl, ctgz = build(cur, os.path.join(work, "dist-candidate"), a.setuptools)
        pwhl, ptgz = build(old, os.path.join(work, "dist-previous"), a.setuptools)
        print("built candidate %s and previous %s" % (os.path.basename(cwhl), os.path.basename(pwhl)), flush=True)

        # 086: one build
        with zipfile.ZipFile(cwhl) as z:
            meta = z.read(next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))).decode()
            record = z.read(next(n for n in z.namelist() if n.endswith(".dist-info/RECORD"))).decode()
            init = z.read("pai/__init__.py").decode()
            bad = []
            for row in csv.reader(io.StringIO(record)):
                if len(row) == 3 and row[1]:
                    algo, want = row[1].split("=", 1)
                    got = base64.urlsafe_b64encode(hashlib.new(algo, z.read(row[0])).digest()).rstrip(b"=").decode()
                    if got != want:
                        bad.append(row[0])
            wheel_files = {n: z.read(n) for n in z.namelist() if n.startswith("pai/_workbench/")}
        mv = re.search(r"^Version: (.+)$", meta, re.M).group(1)
        check("086", "wheel metadata, package version and archive name agree (%s)" % mv,
              mv == a.candidate and '__version__ = "%s"' % a.candidate in init and
              os.path.basename(ctgz) == "pai-workbench-%s.tar.gz" % a.candidate, (mv, os.path.basename(ctgz)))
        check("086", "every file in the wheel matches its RECORD hash", not bad, bad)
        with tarfile.open(ctgz) as t:
            top = t.getnames()[0].split("/")[0]
            members = {m.name[len(top) + 1:]: m for m in t.getmembers() if m.isfile()}
            sums = t.extractfile(members["SHA256SUMS"]).read().decode()
            sum_bad = [l for l in sums.splitlines() if l.strip() and hashlib.sha256(
                t.extractfile(members[l.split("  ", 1)[1]]).read()).hexdigest() != l.split("  ", 1)[0]]
            diff = []
            for n, data in wheel_files.items():
                rel = "swarm/" + n[len("pai/_workbench/"):]
                if rel not in members or t.extractfile(members[rel]).read() != data:
                    diff.append(rel)
        check("086", "the archive's SHA256SUMS matches its files", not sum_bad, sum_bad)
        check("086", "the wheel's workbench payload (%d files) is byte-identical to the archive's" % len(wheel_files),
              wheel_files and not diff, diff)

        # 081, 082: clean install, no network, no checkout
        h, env = home_env(work, "fresh")
        pai = venv(os.path.join(work, "venv-candidate"), cwhl, env)
        p = run([pai, "--version"], env=env, cwd=h)
        check("081", "the candidate wheel installs into a new venv and runs (%s)" % p.stdout.strip(),
              p.stdout.strip() == "pai %s" % a.candidate, p.stdout + p.stderr)
        p = run([pai], env=env, cwd=h)
        check("082", "installed, `pai` names its packaged workbench guide", "_workbench/WORKBENCH.md" in p.stdout, p.stdout)
        demo = os.path.join(h, "guide-demo")
        p = run([pai, "guide", "--dir", demo], env=env, cwd=os.path.join(h, "home"), timeout=300, ok=None)
        check("082", "installed `pai guide` runs the whole flow from a non-checkout folder", p.returncode == 0 and
              "guided demo complete" in p.stdout, p.stdout[-800:] + p.stderr[-400:])
        run([pai, "guide", "--cleanup", demo], env=env, ok=None)
        check("081", "the fresh HOME is untouched", os.listdir(env["HOME"]) == [], os.listdir(env["HOME"]))

        # continuation: the 0.5.0 slice, through the installed command, from this clean HOME
        for kind in ("clean", "reverse", "interrupted"):
            handoff_demo(pai, env, os.path.join(h, "handoff-" + kind), kind, "installed wheel")
        check("continuation", "the fresh HOME is still untouched after both handoff demos", os.listdir(env["HOME"]) == [],
              os.listdir(env["HOME"]))

        # 083: upgrade preview over the previous version with a sample configuration
        h, env = home_env(work, "upgrade")
        old_pai = venv(os.path.join(work, "venv-upgrade"), pwhl, env)
        check("083", "the previous version installs (%s)" % run([old_pai, "--version"], env=env).stdout.strip(),
              run([old_pai, "--version"], env=env).stdout.strip() == "pai %s" % prev_version)
        repo, tasks, sock = sample_project(h, env, old_pai)
        socks.append(sock)
        before_status = json.loads(run([old_pai, "status", "--json"], env=env, cwd=repo, ok=(0, 1)).stdout)
        run(["tmux", "-S", sock, "kill-server"], env=env, ok=None)  # keep the records still while they are compared
        keep = digest(env["PAI_WORKBENCH_HOME"], env["PAI_SWARM_HOME"], tasks)
        new_pai = venv(os.path.join(work, "venv-preview"), cwhl, env)
        outs = {}
        for args in (["config", "check"], ["status", "--json"], ["task", "list"], ["recover"], ["task", "export"]):
            outs[" ".join(args)] = run([new_pai] + args, env=env, cwd=repo, ok=None)
        check("083", "the candidate reads the previous version's config (pai config check exit 0)",
              outs["config check"].returncode == 0, outs["config check"].stdout)
        st = json.loads(outs["status --json"].stdout)
        check("083", "the candidate reads the previous version's run (%s, lane a %s)" % (
            st.get("schema"), st["lanes"][0]["lifecycle"]["state"]),
              st.get("schema") == "pai_status_v1" and st["run_id"] == before_status["run_id"] and
              st["lanes"][0]["dispatch"]["state"] == "delivered", outs["status --json"].stdout[-600:])
        check("083", "task list and recover preview work on the old data", outs["task list"].returncode == 0 and
              "first" in outs["task list"].stdout and "RECOVERY PREVIEW" in outs["recover"].stdout,
              outs["recover"].stdout[-400:])
        check("083", "the preview wrote nothing (configuration, run state, task notes unchanged)",
              digest(env["PAI_WORKBENCH_HOME"], env["PAI_SWARM_HOME"], tasks) == keep)
        pip = os.path.join(work, "venv-upgrade", "bin", "python")
        p = run([pip, "-m", "pip", "install", "--dry-run", "--no-index", "--no-deps", cwhl], env=dict(
            env, PIP_DISABLE_PIP_VERSION_CHECK="1"))
        check("083", "pip's own preview names the upgrade", "Would install pai-workbench-%s" % a.candidate in p.stdout, p.stdout)
        run([pip, "-m", "pip", "install", "--no-index", "--no-deps", "--quiet", cwhl], env=env)
        check("083", "after the upgrade the same venv runs the candidate and reads the run", run(
            [old_pai, "--version"], env=env).stdout.strip() == "pai %s" % a.candidate and json.loads(
            run([old_pai, "status", "--json"], env=env, cwd=repo, ok=(0, 1)).stdout)["run_id"] == before_status["run_id"])
        check("083", "the upgrade and its first status left configuration, run state and task notes as they were",
              keep == digest(env["PAI_WORKBENCH_HOME"], env["PAI_SWARM_HOME"], tasks))
        # continuation of a run that the previous version started (nothing converted first)
        rid = before_status["run_id"]
        p = run([old_pai, "recover", "--run", rid, "--continue", "a", "--write"], env=env, cwd=repo, ok=None)
        q = run([old_pai, "run", "--continue", "first", "--run-id", "cont-1", "--tmux-socket", sock, "--mode", "local",
                 "--yes", "--wait", "8"], env=env, cwd=repo, ok=None)
        st2 = json.loads(run([old_pai, "status", "--json", "--run", "cont-1"], env=env, cwd=repo, ok=None).stdout or "{}")
        lane2 = (st2.get("lanes") or [{}])[0]
        check("continuation", "a run started by %s is handed off and continued by the candidate (%s)" % (
            prev_version, (lane2.get("lifecycle") or {}).get("state")),
              p.returncode == 0 and q.returncode == 0 and (lane2.get("dispatch") or {}).get("state") == "delivered" and
              (lane2.get("continues") or {}).get("from", "").startswith(rid + "/a/"),
              (p.stdout + q.stdout + q.stderr)[-900:])
        console_check(old_pai, env, repo, "cont-1")
        run(["tmux", "-S", sock, "kill-server"], env=env, ok=None)

        # R2-064: roll back to the previous version and forward again with task cards and run records
        cards_file = os.path.join(h, "cards-before-rollback.json")
        run([old_pai, "task", "export", "--out", cards_file], env=env, cwd=repo, ok=None)
        sent_before = [r["text"] for r in received(env)]
        run([pip, "-m", "pip", "install", "--no-index", "--no-deps", "--quiet", pwhl], env=env)
        prev_v = run([old_pai, "--version"], env=env).stdout.strip()
        rb = {k: run([old_pai] + args, env=env, cwd=repo, ok=None) for k, args in (
            ("status-cont", ["status", "--json", "--run", "cont-1"]), ("status-old", ["status", "--json", "--run", rid]),
            ("list", ["task", "list"]), ("recover-old", ["recover", "--run", rid]),
            ("resume-old", ["resume", "--run", rid, "--wait", "4"]))}
        def first_lane(text):  # 0.4.0 prints a list of lane rows; later versions pai_status_v1 {"lanes": [...]}
            d = json.loads(text)
            return (d["lanes"] if isinstance(d, dict) else d)[0]
        try:
            row_old = first_lane(rb["status-old"].stdout)
            old_view = (row_old.get("lifecycle") or {}).get("state") or "phase %s, dispatch %s" % (
                row_old.get("phase"), (row_old.get("dispatch") or {}).get("state"))
            cont_view = (first_lane(rb["status-cont"].stdout).get("dispatch") or {}).get("state")
        except (ValueError, KeyError, IndexError, TypeError):
            old_view = cont_view = None
        resent = [t for t in (r["text"] for r in received(env)) if t not in sent_before]
        check("rollback", "after a rollback to %s it reads the candidate's run records (cont-1 dispatch %s; the handed-off "
              "run %s shows %s) and lists the task notes" % (prev_v, cont_view, rid, old_view),
              prev_v == "pai %s" % prev_version and cont_view == "delivered" and old_view is not None and
              rb["list"].returncode == 0, "\n".join("%s: exit %s %s" % (k, v.returncode, (v.stdout + v.stderr)[-300:])
                                                    for k, v in rb.items()))
        why = [l.strip() for l in rb["resume-old"].stdout.splitlines() if "REFUSED" in l][:1]
        check("rollback", "%s's resume of the handed-off run sends no task again (%d new prompt(s); resume exit %s%s); "
              "`pai recover` %s" % (prev_v, len(resent), rb["resume-old"].returncode, ": " + why[0] if why else "",
                                    "is not in %s (exit %s)" % (prev_v, rb["recover-old"].returncode)
                                    if rb["recover-old"].returncode == 2 else "previewed (exit %s)" %
                                    rb["recover-old"].returncode), resent == [], rb["resume-old"].stdout[-600:])
        run(["tmux", "-S", sock, "kill-server"], env=env, ok=None)
        run([pip, "-m", "pip", "install", "--no-index", "--no-deps", "--quiet", cwhl], env=env)
        fwd = json.loads(run([old_pai, "status", "--json", "--run", rid], env=env, cwd=repo, ok=None).stdout or "{}")
        imp = run([old_pai, "task", "import", cards_file], env=env, cwd=repo, ok=None)
        check("rollback", "forward again to %s: the handed-off lane is still handed off (%s) and the task cards saved "
              "before the rollback import as unchanged" % (a.candidate, ((fwd.get("lanes") or [{}])[0].get("lifecycle")
                                                                        or {}).get("state")),
              ((fwd.get("lanes") or [{}])[0].get("lifecycle") or {}).get("state") == "handed-off" and imp.returncode == 0
              and "unchanged" in imp.stdout and not re.search(r"^\s+(create|CONFLICT)\s", imp.stdout, re.M),
              imp.stdout[-600:])
        run(["tmux", "-S", sock, "kill-server"], env=env, ok=None)
        keep = digest(env["PAI_WORKBENCH_HOME"], env["PAI_SWARM_HOME"], tasks)  # the uninstall check compares from here

        # 083, 084: the archives
        ex = os.path.join(work, "extract")
        for tgz in (ptgz, ctgz):
            with tarfile.open(tgz) as t:
                t.extractall(os.path.join(ex, os.path.basename(tgz)[:-7]), filter="data")
        pbin = glob.glob(os.path.join(ex, os.path.basename(ptgz)[:-7], "*", "bin", "pai"))[0]
        cbin = glob.glob(os.path.join(ex, os.path.basename(ctgz)[:-7], "*", "bin", "pai"))[0]
        prefix = os.path.join(work, "prefix")
        aenv = dict(env, PATH=os.path.join(prefix, "bin") + os.pathsep + env["PATH"])
        run([pbin, "install", "--prefix", prefix], env=aenv)
        launcher = os.path.join(prefix, "bin", "pai")
        p = run([cbin, "install", "--dry-run", "--prefix", prefix], env=aenv)
        before = digest(prefix)
        check("083", "archive upgrade preview names the current version and writes nothing",
              "(now %s)" % prev_version in p.stdout and "dry run: nothing is written" in p.stdout and digest(prefix) == before,
              p.stdout)
        seen = set()
        for delay in (0.0, 0.01, 0.03, 0.06, 0.1, 0.2, 0.35):
            q = subprocess.Popen([cbin, "install", "--prefix", prefix], env=aenv, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            time.sleep(delay)
            q.send_signal(signal.SIGKILL)
            q.wait()
            v = run([launcher, "--version"], env=aenv, ok=None)
            seen.add(v.stdout.strip())
            if v.returncode != 0 or v.stdout.strip() not in ("pai %s" % prev_version, "pai %s" % a.candidate):
                check("084", "launcher after an install killed at %.2fs" % delay, False, v.stdout + v.stderr)
                break
        else:
            check("084", "an install killed at 7 points always left a working launcher (saw: %s)" % ", ".join(sorted(seen)),
                  True)
        run([cbin, "install", "--prefix", prefix], env=aenv)
        check("084", "a complete install afterwards switches to the candidate",
              run([launcher, "--version"], env=aenv).stdout.strip() == "pai %s" % a.candidate)
        h3, env3 = home_env(work, "archive-home")
        for kind in ("clean", "reverse"):
            handoff_demo(launcher, env3, os.path.join(h3, "handoff-" + kind), kind, "installed archive launcher")

        # 085: uninstall keeps user data
        data_before = digest(env["PAI_WORKBENCH_HOME"], env["PAI_SWARM_HOME"], tasks)
        run([pip, "-m", "pip", "uninstall", "-y", "--quiet", "pai-workbench"], env=env)
        check("085", "pip uninstall removes the program", not os.path.exists(old_pai))
        os.remove(launcher)
        shutil.rmtree(prefix)
        check("085", "archive uninstall (remove the launcher and PREFIX) removes the program",
              not os.path.lexists(launcher) and not os.path.exists(prefix))
        check("085", "configuration, run state and task notes stay after both uninstalls",
              digest(env["PAI_WORKBENCH_HOME"], env["PAI_SWARM_HOME"], tasks) == data_before)
    except Exception as e:  # noqa: BLE001  report, then stop
        check("-", "package check stopped", False, "%s: %s" % (type(e).__name__, e))
    finally:
        for s in socks:
            subprocess.run(["tmux", "-S", s, "kill-server"], capture_output=True)
    with open(os.path.join(work, "package-check.json"), "w") as f:
        json.dump(results, f, indent=1)
    failed = [r for r in results if not r["ok"]]
    print("\npackage check: %d passed, %d failed (%s)" % (len(results) - len(failed), len(failed),
                                                        os.path.join(work, "package-check.json")))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
