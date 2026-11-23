"""pai guide: the whole first run, offline, with a synthetic project and a fake worker.

    pai guide                      plan -> dispatch -> status -> handoff in a new disposable folder
    pai guide --dir DIR            the same, in DIR (must not exist yet)
    pai guide --recovery           the same, but the fake worker crashes on its first start: status
                                   shows it, `pai recover` previews the recovery, --apply does it
    pai guide --handoff [clean]    stop a fake Claude session after step 1 of 3, hand the task off
                                   (`pai recover --continue`), carry it as a transfer packet
                                   (`pai task export --transfer`) to a fresh HOME, preview it there
                                   (`pai task import`) and finish it with a fake Codex session
                                   (`pai run --continue`)
    pai guide --handoff reverse    the same from a fake Codex session to a fake Claude session
    pai guide --handoff interrupted   the session dies with its delivery uncertain; the continuation
                                   carries that open decision; a fresh session finishes; nothing is
                                   sent twice
    pai guide --cleanup DIR        remove a guided demo folder (only one that `pai guide` made)

No account, credentials, network or model are needed, and nothing outside the demo folder is
read or written: the demo has its own HOME, pai configuration and state, Claude config folder and
tmux server, all inside it. The worker is a fake (swarm/guided_agent.py) found through a private
bin folder that comes first on the demo's PATH; pai guide checks the resolved program before the
run and again after it, and stops if it is anything else, so a real Claude or Codex is never
started. Every step runs the normal `pai` command line (init, run --dry-run, run, status, logs,
cleanup), so the demo exercises the same lifecycle wiring as a real run.
"""
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "guided_agent.py")
MARK = "PAI-GUIDED-DEMO.txt"
MARK_HEAD = "PAI guided demo (disposable)"
NOTE = "guided-first-task"
DONE_FILE, DONE_TEXT = "GUIDED-DONE.md", "guided demo complete"
NOTE_TEXT = """---
status: todo
priority: 1
guided_file: %s
guided_text: %s
---
# Write the guided-demo completion file

Create `%s` in this repository with exactly one line, `%s`, commit it on this
lane's branch, and write a short HANDOFF.md saying what was done and checked.

## Done when

`git show <lane branch>:%s` prints `%s`.
""" % (DONE_FILE, DONE_TEXT, DONE_FILE, DONE_TEXT, DONE_FILE, DONE_TEXT)


class Stuck(Exception):
    pass


def pai_argv():
    """The normal `pai` command line of this installation (checkout, archive or package)."""
    import pai
    return [sys.executable, "-B", "-m", "pai.launch"], os.path.dirname(os.path.dirname(os.path.abspath(pai.__file__)))


def say(text=""):
    print(text, flush=True)


def demo_env(demo, pai_root, sock, n=""):
    """The demo's environment; n="-2" gives a second, fresh HOME and state in the same demo folder."""
    tools = [os.path.dirname(p) for p in (shutil.which("git"), shutil.which("tmux"), sys.executable) if p]
    path = os.pathsep.join(dict.fromkeys([os.path.join(demo, "bin")] + tools + ["/usr/bin", "/bin"]))
    st = os.path.join(demo, "state" + n)
    env = {"HOME": os.path.join(demo, "home" + n), "PATH": path, "LANG": "C.UTF-8", "TERM": os.environ.get("TERM") or "xterm",
           "PYTHONPATH": pai_root, "PAI_WORKBENCH_HOME": os.path.join(st, "config"),
           "PAI_WORKBENCH_DATA": os.path.join(st, "data"), "PAI_SWARM_HOME": os.path.join(st, "swarm"),
           "CLAUDE_CONFIG_DIR": os.path.join(st, "claude"), "CODEX_HOME": os.path.join(st, "codex"),
           "PAI_JEV_CREDENTIAL_STORE": "none", "PAI_GUIDED_DEMO": demo, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
           "GIT_AUTHOR_NAME": "PAI guided demo", "GIT_AUTHOR_EMAIL": "guided-demo@example.invalid",
           "GIT_COMMITTER_NAME": "PAI guided demo", "GIT_COMMITTER_EMAIL": "guided-demo@example.invalid"}
    for d in ("HOME", "PAI_WORKBENCH_HOME", "PAI_WORKBENCH_DATA", "PAI_SWARM_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        os.makedirs(env[d], exist_ok=True)
    return env


def socket_path(demo, token, name="tmux.sock"):
    """The demo's own tmux socket: inside the demo unless that path is too long for a socket."""
    inside = os.path.join(demo, name)
    return inside if len(inside) < 100 else os.path.join(tempfile.gettempdir(), "pai-guided-%s.sock" % token)


def process_args(pid):
    """The command line of a running process ("" when it is gone)."""
    env = {k: v for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")}  # ps cuts args to $COLUMNS
    p = subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, env=env)
    return p.stdout.strip() if p.returncode == 0 else ""


def read_mark(demo):
    try:
        with open(os.path.join(demo, MARK), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    if not lines or lines[0] != MARK_HEAD:
        return None
    return dict(l.split(": ", 1) for l in lines[1:] if ": " in l)


def cleanup(demo):
    demo = os.path.realpath(os.path.expanduser(demo))
    mark = read_mark(demo)
    if not mark or os.path.realpath(mark.get("folder", "")) != demo:
        say("pai guide --cleanup: %s is not a folder made by `pai guide` (no %s naming it). Nothing was removed."
            % (demo, MARK))
        return 2
    for key, sock in mark.items():
        if not key.startswith("tmux socket"):
            continue
        if sock and os.path.exists(sock) and shutil.which("tmux"):
            subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True)  # the demo's own servers only
        if sock and os.path.exists(sock) and not sock.startswith(demo + os.sep):
            os.remove(sock)
    shutil.rmtree(demo)
    say("pai guide: removed %s (the guided demo: its project, notes, state and fake worker). Nothing else was touched."
        % demo)
    return 0


def main(a):
    if a.cleanup:
        return cleanup(a.cleanup)
    missing = [t for t in ("git", "tmux") if not shutil.which(t)]
    if missing:
        say("pai guide needs %s (the run itself uses them). Nothing was created." % " and ".join(missing))
        return 2
    if a.dir:
        demo = os.path.realpath(os.path.expanduser(a.dir))
        if os.path.exists(demo):
            say("pai guide: %s exists; give a new folder (pai guide creates it). Nothing was created." % demo)
            return 2
        os.makedirs(demo)
    else:
        demo = os.path.realpath(tempfile.mkdtemp(prefix="pai-guided-demo-"))
    token = secrets.token_hex(4)
    sock = socket_path(demo, token)
    pai, pai_root = pai_argv()
    try:
        if getattr(a, "handoff", None):
            return walk_handoff(demo, sock, token, pai, pai_root, a.handoff)
        return walk(demo, sock, token, pai, pai_root, a)
    except Stuck as e:
        for sk in (sock, socket_path(demo, token + "-2", "tmux-2.sock")):
            subprocess.run(["tmux", "-S", sk, "kill-server"], capture_output=True)
        say("\npai guide: stopped: %s\nThe demo folder is kept for a look: %s\nRemove it with: pai guide --cleanup %s"
            % (e, demo, shlex.quote(demo)))
        return 1


def walk(demo, sock, token, pai, pai_root, a):
    with open(os.path.join(demo, MARK), "w", encoding="utf-8") as f:
        f.write("%s\nfolder: %s\ntmux socket: %s\nid: %s\n\nEverything in this folder was made by `pai guide` and can be "
                "deleted: pai guide --cleanup %s\n" % (MARK_HEAD, demo, sock, token, shlex.quote(demo)))
    env = demo_env(demo, pai_root, sock)
    if getattr(a, "recovery", False):
        env["PAI_GUIDED_CRASH_ONCE"] = os.path.join(demo, "state", "guided-crash-once")
    bindir = os.path.join(demo, "bin")
    os.makedirs(bindir)
    fake = os.path.join(bindir, "claude")
    with open(fake, "w") as f:
        f.write("#!%s\n# pai guide's fake worker (not Claude Code)\nimport runpy, sys\nsys.argv[0] = __file__\n"
                "runpy.run_path(%r, run_name='__main__')\n" % (sys.executable, AGENT))
    os.chmod(fake, 0o755)
    repo, origin, tasks, lanes = (os.path.join(demo, d) for d in ("project", "origin.git", "tasks", "lanes"))
    os.makedirs(tasks)

    def git(*args, cwd=None):
        p = subprocess.run(["git"] + list(args), cwd=cwd, env=env, capture_output=True, text=True)
        if p.returncode != 0:
            raise Stuck("git %s failed: %s" % (" ".join(args), p.stderr.strip()))
        return p.stdout

    def run(args, show=True, cwd=repo, timeout=120):
        if show:
            say("\n$ pai %s" % " ".join(shlex.quote(x) for x in args))
        p = subprocess.run(pai + list(args), cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        if show:
            out = (p.stdout + p.stderr).rstrip()
            say("\n".join("  " + l for l in out.splitlines()))
        return p

    say("pai guide: a first run, offline, with a FAKE worker (no account, no credentials, no network).")
    say("Demo folder (disposable; nothing outside it is touched): %s" % demo)

    say("\n1. A synthetic project and one task note")
    git("init", "-q", "--bare", "-b", "main", origin)
    git("init", "-q", "-b", "main", repo)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("# Guided demo project\n\nSynthetic; made by `pai guide`.\n")
    git("add", "README.md", cwd=repo)
    git("commit", "-q", "-m", "guided demo: initial commit", cwd=repo)
    git("remote", "add", "origin", origin, cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    note = os.path.join(tasks, NOTE + ".md")
    with open(note, "w", encoding="utf-8") as f:
        f.write(NOTE_TEXT)
    say("  project   %s (a Git repository whose remote is the local %s)" % (repo, origin))
    say("  task note %s\n            done when: `%s` on the lane's branch holds `%s`" % (note, DONE_FILE, DONE_TEXT))
    p = run(["init", "--no-input", "--default-agent", "claude", "--base", "main", "--worktree-root", lanes,
             "--tasks", tasks], show=False)
    if p.returncode != 0:
        raise Stuck("pai init failed: %s" % (p.stdout + p.stderr).strip()[-400:])
    resolved = shutil.which("claude", path=env["PATH"])
    if resolved != fake:
        raise Stuck("`claude` resolves to %s, not the guided fake worker; nothing was started" % resolved)
    say("  worker    `claude` on this demo's PATH is the fake worker %s" % fake)

    say("\n2. The plan (nothing is created or started yet; no credentials are needed for this)")
    common = ["--workers", "1", "--agent", "claude", "--mode", "local", "--tmux-socket", sock]
    p = run(["run", "--dry-run"] + common)
    if p.returncode != 0:
        raise Stuck("the plan failed")

    say("\n3. Dispatch: pai starts the worker, waits until it is ready, and sends the task note")
    p = run(["run", "--yes", "--wait", "20"] + common, timeout=180)
    say("  (a real agent asks first whether to trust the new lane folder: that answer is yours, with `pai trust`;\n"
        "   the fake worker does not ask)")
    if getattr(a, "recovery", False):
        end = time.time() + 30
        while time.time() < end:
            st = run(["status", "--json"], show=False)
            try:
                lanes_now = json.loads(st.stdout)["lanes"]
            except (ValueError, KeyError):
                lanes_now = []
            if lanes_now and lanes_now[0]["lifecycle"]["state"] == "exited":
                break
            time.sleep(0.5)
        else:
            raise Stuck("the fake worker was meant to crash on its first start, but status does not show it exited")
        say("\n3a. The worker crashed (on purpose, before taking its task): what pai shows")
        run(["status"])
        say("\n3b. Recovery preview: what pai would do, the lane, task and evidence (nothing changes yet)")
        run(["recover"])
        say("\n3c. Recover: the worker restarts in the same worktree and branch, the task is delivered once, and the\n"
            "    decision is recorded with its before- and after-state")
        q = run(["recover", "--apply", "--wait", "20"], timeout=180)
        if q.returncode != 0:
            raise Stuck("the recovery did not finish (exit %s)" % q.returncode)
    rows, end, checked = [], time.time() + 40, ""
    while time.time() < end:
        st = run(["status", "--json"], show=False)
        try:
            rows = json.loads(st.stdout)["lanes"]
        except (ValueError, KeyError):
            rows = []
        pid = rows[0]["process"].get("pid") if rows else None
        if pid:  # the process that actually runs in the lane must be the fake worker
            args = process_args(pid)
            if args and fake not in args:
                raise Stuck("the lane runs %r, not the guided fake worker" % args)
            checked = args
        if rows and rows[0]["dispatch"]["state"] in ("delivered", "unknown"):
            break
        time.sleep(0.5)
    if not rows:
        raise Stuck("pai run did not record a lane: %s" % (p.stdout + p.stderr).strip()[-400:])
    lane = rows[0]
    if fake not in checked:
        raise Stuck("could not confirm that the lane runs the guided fake worker (%r)" % checked)
    say("  worker    the lane's process is the fake worker: %s" % checked)

    say("\n4. Status: what pai knows, and from which evidence")
    run(["status"])
    if lane["dispatch"]["state"] != "delivered":
        raise Stuck("the task was not delivered (dispatch %s: %s)" % (lane["dispatch"]["state"], lane["dispatch"].get("detail")))
    branch, worktree = lane["branch"], lane["worktree"]
    got, end = "", time.time() + 20
    while time.time() < end:
        q = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (branch, DONE_FILE)], env=env, capture_output=True, text=True)
        got = q.stdout if q.returncode == 0 else ""
        if got:
            break
        time.sleep(0.3)
    if got.strip() != DONE_TEXT:
        raise Stuck("the completion condition is not met: %s on %s holds %r" % (DONE_FILE, branch, got))

    say("\n5. Handoff: the worker's report, and the completion check")
    say("  $ git show %s:%s\n    %s" % (branch, DONE_FILE, got.strip()))
    handoff = os.path.join(worktree, "HANDOFF.md")
    with open(handoff, encoding="utf-8") as f:
        say("\n".join("  | " + l for l in f.read().rstrip().splitlines()))

    logp = run(["logs", "a", "--tail", "40"], show=False)
    runlog = os.path.join(demo, "run-log.txt")
    with open(runlog, "w", encoding="utf-8") as f:
        f.write(logp.stdout)
    manifests = sorted(os.path.join(r, fn) for r, _, fs in os.walk(os.path.join(demo, "state")) for fn in fs
                       if fn == "manifest.json")
    say("\n6. Where everything landed")
    say("  task note    %s" % note)
    say("  run record   %s" % (manifests[0] if manifests else "(none)"))
    say("  run log      %s   (the lane's screen: `pai logs a`)" % runlog)
    say("  handoff      %s   (on branch %s, with %s)" % (handoff, branch, DONE_FILE))
    say("  lane         %s" % worktree)
    if getattr(a, "recovery", False):
        rec = sorted(os.path.join(r, fn) for r, _, fs in os.walk(os.path.join(demo, "state")) for fn in fs
                     if fn.endswith(".jsonl") and os.path.basename(r) == "recovery")
        say("  recovery log %s   (`pai recover --history`)" % (rec[0] if rec else "(none)"))

    say("\n7. Stop the fake worker (the normal cleanup command; files stay for a look)")
    run(["cleanup", "--apply", "--stop-running"])
    subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True)
    say("\nDone: the guided run went plan -> dispatch -> %sdelivered -> completed, with a fake worker." % (
        "crashed -> recovery preview -> recovered -> " if getattr(a, "recovery", False) else ""))
    for line in next_lines():
        say(line)
    say("Remove this demo: pai guide --cleanup %s" % shlex.quote(demo))
    return 0


def manual(name):
    """A manual's local copy (archive or checkout: docs/ beside swarm/), else the file on GitHub for
    this version (an installed package has no docs/)."""
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", name)
    if os.path.isfile(local):
        return local
    try:
        from pai import __version__ as v
        ref = "v" + v
    except ImportError:
        ref = "main"
    return "https://github.com/WorldVanquisher/personal-ai-workbench/blob/%s/docs/%s" % (ref, name)


def next_lines(kind=None):
    """The end of the guide: the next step of the first-use manual, with your own project."""
    if kind:  # the handoff guides: continuing a lane in a fresh session
        return ["Next, for your own lanes: %s, part (e) \"Recovery\" (resuming after a stop, a full "
                "context or a reboot)." % manual("REFRESH.md"),
                "中文：%s 的 (e) 部分「恢复」。" % manual("REFRESH.zh-CN.md")]
    # the Chinese twin names the Chinese manual (R6-FIN-030, newbie2 F3)
    return ["Next, with your own project: %s, section 5 \"Save this checkout's defaults: pai init\", then "
            "section 6 \"Your first round\". (Not done yet? Section 2a makes a practice repository; "
            "section 3 is `pai doctor`.)" % manual("FIRST-USE.md"),
            "中文：%s 第 5 节「保存这个仓库的默认设置」，然后第 6 节「第一轮：一个 worker」。（还没做？第 2a 节建一个"
            "练习仓库，第 3 节是 `pai doctor`。）" % manual("FIRST-USE.zh-CN.md")]


HANDOFF_NOTE = "guided-handoff"
HANDOFF_TEXT = """---
status: todo
priority: 1
guided_steps: [step-1, step-2, step-3]
---
# Write three step files, one commit each

Create `step-1.md`, `step-2.md` and `step-3.md` in this repository, one commit per step, then
write a short HANDOFF.md saying what was done, what is left and what was checked.

## Acceptance criteria

- `step-1.md`, `step-2.md` and `step-3.md` are on the lane's branch
- no step is done twice
"""


def walk_handoff(demo, sock, token, pai, pai_root, kind):
    """`pai guide --handoff clean|reverse|interrupted`: one session stops, a fresh one continues the task."""
    sock2 = socket_path(demo, token + "-2", "tmux-2.sock")
    with open(os.path.join(demo, MARK), "w", encoding="utf-8") as f:
        f.write("%s\nfolder: %s\ntmux socket: %s\ntmux socket 2: %s\nid: %s\n\nEverything in this folder was made by "
                "`pai guide` and can be deleted: pai guide --cleanup %s\n" % (MARK_HEAD, demo, sock, sock2, token,
                                                                           shlex.quote(demo)))
    env1 = dict(demo_env(demo, pai_root, sock), PAI_DISPATCH_CONFIRM_SECONDS="3")
    bindir = os.path.join(demo, "bin")
    os.makedirs(bindir)
    for name, extra in (("claude", ""), ("codex", "import os\nos.environ['PAI_GUIDED_AS'] = 'codex'\n")):
        with open(os.path.join(bindir, name), "w") as f:
            f.write("#!%s\n# pai guide's fake worker (not the real %s)\nimport runpy, sys\n%ssys.argv[0] = __file__\n"
                    "runpy.run_path(%r, run_name='__main__')\n" % (sys.executable, name, extra, AGENT))
        os.chmod(os.path.join(bindir, name), 0o755)
    repo, origin, lanes = (os.path.join(demo, d) for d in ("project", "origin.git", "lanes"))
    tasks1, tasks2 = os.path.join(demo, "tasks"), os.path.join(demo, "tasks-2")
    os.makedirs(tasks1)
    os.makedirs(tasks2)
    moved = kind in ("clean", "reverse")  # carried to a fresh HOME as a transfer packet
    first, second = ("codex", "claude") if kind == "reverse" else ("claude", "codex" if kind == "clean" else "claude")
    label = {"claude": "fake Claude", "codex": "fake Codex"}
    ctl = {"session-1": {"steps": 1}} if moved else {
        "session-1": {"steps": 1, "lose_transcript": True, "crash_after": True}}
    with open(os.path.join(demo, "guided-control.json"), "w") as f:
        json.dump(ctl, f)

    def git(*args, cwd=None, env=env1):
        p = subprocess.run(["git"] + list(args), cwd=cwd, env=env, capture_output=True, text=True)
        if p.returncode != 0:
            raise Stuck("git %s failed: %s" % (" ".join(args), p.stderr.strip()))
        return p.stdout

    def run(args, env=env1, show=True, timeout=180):
        if show:
            say("\n$ pai %s" % " ".join(shlex.quote(x) for x in args))
        p = subprocess.run(pai + list(args), cwd=repo, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        if show:
            say("\n".join("  " + l for l in (p.stdout + p.stderr).rstrip().splitlines()))
        return p

    def lane(env, run_id):
        p = run(["status", "--json", "--run", run_id], env=env, show=False)
        try:
            return json.loads(p.stdout)["lanes"][0]
        except (ValueError, KeyError, IndexError):
            return None

    def wait(what, fn, timeout=45):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.5)
        raise Stuck("timed out waiting for %s" % what)

    def subjects(branch):
        return git("log", "--format=%s", "main..%s" % branch, cwd=repo).splitlines()[::-1]

    def prompts():
        try:
            with open(os.path.join(demo, "guided-received.jsonl")) as f:
                rows = [json.loads(x) for x in f if x.strip()]
        except OSError:
            rows = []
        out = {}
        for r in rows:
            out[r["run"]] = out.get(r["run"], 0) + 1
        return out

    title = {"clean": "a clean handoff from a fake Claude session to a fake Codex session in a fresh HOME",
             "reverse": "a clean handoff from a fake Codex session to a fake Claude session in a fresh HOME",
             "interrupted": "an interrupted session whose delivery is uncertain, continued without a duplicate"}[kind]
    say("pai guide --handoff %s: %s (offline, FAKE workers, no account)." % (kind, title))
    say("Demo folder (disposable; nothing outside it is touched): %s" % demo)

    say("\n1. A synthetic project and one task note with three steps")
    git("init", "-q", "--bare", "-b", "main", origin)
    git("init", "-q", "-b", "main", repo)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("# Guided handoff demo\n\nSynthetic; made by `pai guide --handoff`.\n")
    git("add", "README.md", cwd=repo)
    git("commit", "-q", "-m", "guided demo: initial commit", cwd=repo)
    git("remote", "add", "origin", origin, cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    with open(os.path.join(tasks1, HANDOFF_NOTE + ".md"), "w", encoding="utf-8") as f:
        f.write(HANDOFF_TEXT)
    p = run(["init", "--no-input", "--default-agent", "claude", "--base", "main", "--worktree-root", lanes, "--tasks",
             tasks1], show=False)
    if p.returncode != 0:
        raise Stuck("pai init failed: %s" % (p.stdout + p.stderr)[-400:])
    say("  task note %s: step-1, step-2, step-3; acceptance: all three, none twice" % os.path.join(tasks1, HANDOFF_NOTE + ".md"))

    common = ["--workers", "1", "--mode", "local", "--yes", "--wait", "20"]
    if moved:
        say("\n2. Session 1 (%s) does step 1, writes its handoff, and is stopped" % label[first])
        run(["run", "--run-id", "session-1", "--agent", first, "--tmux-socket", sock] + common)
        wait("session 1's handoff commit", lambda: "guided demo: handoff after step-1" in subjects("pai/session-1/a"))
        run(["inspect", "a", "--run", "session-1"])
        run(["cleanup", "--run", "session-1", "--apply", "--stop-running"])
    else:
        say("\n2. Session 1 (fake Claude) takes the task, commits step 1 and dies; its transcript never recorded the "
            "prompt, so pai cannot know whether it was delivered")
        run(["run", "--run-id", "session-1", "--agent", "claude", "--tmux-socket", sock] + common)
        wait("session 1's step-1 commit", lambda: "guided demo: step-1" in subjects("pai/session-1/a"))
        wait("an unknown dispatch", lambda: (lane(env1, "session-1") or {}).get("dispatch", {}).get("state") == "unknown")
        run(["status", "--run", "session-1", "--brief"])
        say("\n   The in-place recovery would restart the same agent and wait for a choice; a fresh session is wanted "
            "instead, so it is only previewed:")
        run(["recover", "--run", "session-1"])

    say("\n3. Hand the task off: preview, then write the continuation into the task note")
    run(["recover", "--run", "session-1", "--continue", "a"])
    if kind == "interrupted":
        p = run(["recover", "--run", "session-1", "--continue", "a", "--write"])
        if p.returncode != 1:
            raise Stuck("an unknown delivery must stop the handoff until someone decides")
        say("   (refused: the delivery is unknown. The operator carries the question to the next session:)")
        p = run(["recover", "--run", "session-1", "--continue", "a", "--choose", "a=leave", "--write"])
    else:
        p = run(["recover", "--run", "session-1", "--continue", "a", "--write"])
    if p.returncode != 0:
        raise Stuck("the continuation was not written")
    with open(os.path.join(tasks1, HANDOFF_NOTE + ".md"), encoding="utf-8") as f:
        note_text = f.read()
    say("\n   The task note now ends with (what the next agent reads):")
    say("\n".join("  | " + l for l in note_text.split("## Continuation from", 1)[1].splitlines()[:30]))

    if moved:
        card = os.path.join(demo, "transfer-packet.json")
        say("\n4. Carry it as a transfer packet to a fresh HOME (no records, no tmux server, no agent state of "
            "session 1). A packet carries no commits, so session 1's branch goes to the demo's own origin first (a "
            "local bare repository), as it would before moving to another machine:")
        say("  $ git push origin pai/session-1/a")
        git("push", "-q", "origin", "pai/session-1/a", cwd=repo)
        git("fetch", "-q", "origin", cwd=repo)
        run(["task", "export", HANDOFF_NOTE, "--transfer"])
        p = run(["task", "export", HANDOFF_NOTE, "--transfer", "--out", card])
        if p.returncode != 0:
            raise Stuck("the transfer packet was not written")
        env2 = dict(demo_env(demo, pai_root, sock2, "-2"), PAI_DISPATCH_CONFIRM_SECONDS="3")
        p = run(["init", "--no-input", "--default-agent", second, "--base", "main", "--worktree-root", lanes, "--tasks",
                 tasks2], env=env2, show=False)
        if p.returncode != 0:
            raise Stuck("pai init (fresh HOME) failed: %s" % (p.stdout + p.stderr)[-400:])
        say("\n   In the fresh HOME, a read-only preview first (what came, what each agent here can show, what is "
            "missing), then the note is created:")
        p = run(["task", "import", card], env=env2)
        if p.returncode != 0 or os.listdir(tasks2):
            raise Stuck("the import preview failed or wrote something")
        run(["task", "import", card, "--write"], env=env2)
        say("\n5. Session 2 (%s, fresh HOME) continues from the handed-off head" % label[second])
        envc, sockc, tasks_c = env2, sock2, tasks2
        run(["run", "--continue", HANDOFF_NOTE, "--agent", second, "--run-id", "session-2", "--tmux-socket", sock2]
            + common, env=env2)
    else:
        say("\n4. Session 2 (a fresh fake Claude session) continues from the handed-off head")
        envc, sockc, tasks_c = env1, sock, tasks1
        run(["run", "--continue", HANDOFF_NOTE, "--run-id", "session-2", "--tmux-socket", sock] + common)
    wait("session 2's handoff commit", lambda: any(s.startswith("guided demo: handoff after step-2")
                                                   for s in subjects("pai/session-2/a")))
    row2 = wait("session 2 delivered", lambda: (lambda r: r if r and r["dispatch"]["state"] == "delivered" else None)(
        lane(envc, "session-2")))

    say("\n%d. What the fresh session found and did" % (6 if moved else 5))
    commits = subjects("pai/session-2/a")
    say("  $ git log --format=%%s main..pai/session-2/a\n%s" % "\n".join("    " + c for c in commits))
    steps = [c for c in commits if c.startswith("guided demo: step-")]
    if steps != ["guided demo: step-1", "guided demo: step-2", "guided demo: step-3"]:
        raise Stuck("the steps on the branch are %s, not step-1, step-2, step-3 once each" % steps)
    handoff2 = git("show", "pai/session-2/a:HANDOFF.md", cwd=repo)
    say("\n".join("  | " + l for l in handoff2.rstrip().splitlines()))
    if "Completed before this session (from the note's continuation): step-1" not in handoff2:
        raise Stuck("session 2 did not take its completed steps from the continuation")
    if kind == "interrupted" and "OPEN: it is not known whether session 1 received this task" not in handoff2:
        raise Stuck("session 2 was not given the open decision")
    say("  delivered: %s" % row2["dispatch"]["evidence"])

    say("\n%d. No duplicate: every way to send the task again" % (7 if moved else 6))
    before = prompts()
    run(["run", "--continue", HANDOFF_NOTE, "--run-id", "session-3", "--tmux-socket", sockc, "--yes"], env=envc)
    run(["resume", "--run", "session-1", "--wait", "3"])
    run(["recover", "--run", "session-1", "--apply"])
    after = prompts()
    say("\n  prompts each session received (counted by the fake worker itself): %s" % ", ".join(
        "%s %d" % kv for kv in sorted(after.items())))
    if after != before or after.get("session-1") != 1 or after.get("session-2") != 1 or "session-3" in after:
        raise Stuck("a prompt was sent again: before %s, after %s" % (before, after))

    say("\n%d. Stop the fake workers" % (8 if moved else 7))
    run(["cleanup", "--run", "session-2", "--apply", "--stop-running"], env=envc, show=False)
    for sk in (sock, sock2):
        subprocess.run(["tmux", "-S", sk, "kill-server"], capture_output=True)
    say("\nDone: goal, completed step, evidence%s carried from a %s session to a fresh %s session%s; step-1 was "
        "not redone; one prompt per session." % (" and the open decision" if kind == "interrupted" else "",
                                                 label[first], label[second].split()[1],
                                                 " in a fresh HOME via a transfer packet" if moved else ""))
    for line in next_lines(kind):
        say(line)
    say("Remove this demo: pai guide --cleanup %s" % shlex.quote(demo))
    return 0
