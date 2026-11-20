#!/usr/bin/env python3
"""The console's normal flow, end to end, with a fake agent: inspect, edit, paste (1.0 readiness 050).

    python3 -B tools/console_walkthrough.py [--keep]

From a checkout. Everything happens in a new disposable folder with its own HOME, pai state,
Claude config folder and tmux server; the agent is the test fake (swarm/tests/fake_claude_rc.py),
found first on the demo's PATH. No account, credentials or network. Steps, through the real
`pai run` and `pai web` (its HTTP API, as the page uses it):

  1. a synthetic project, one task note, `pai run` with the fake agent (task delivered)
  2. the console's lane list: the lifecycle name and why, the same as `pai status`
  3. inspect: the evidence behind it and where each piece is read
  4. edit: open the task note, review the diff, save the confirmed revision
  5. paste: preview (destination and size), paste (staged, not submitted), then Enter (submitted);
     the fake agent's transcript records it
  6. stop: pai web, the fake worker, the tmux server; the folder is removed unless --keep

Exit 0 and "WALKTHROUGH OK" when every step's evidence was seen.
"""
import argparse
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAI = [sys.executable, "-B", os.path.join(ROOT, "bin", "pai.py")]
FAKE = os.path.join(ROOT, "swarm", "tests", "fake_claude_rc.py")


class Failed(Exception):
    pass


def say(msg=""):
    print(msg, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true", help="keep the demo folder")
    ap.add_argument("--serve", type=float, metavar="SECONDS", help="afterwards, start pai web once more and print its "
                    "one-time link as `BROWSER: URL`, for a real-browser check (owner's kit); stop after SECONDS")
    a = ap.parse_args(argv)
    if not (shutil.which("git") and shutil.which("tmux")):
        say("console walkthrough: needs git and tmux")
        return 2
    demo = os.path.realpath(tempfile.mkdtemp(prefix="pai-console-demo-"))
    sock = os.path.join(demo, "t.sock") if len(demo) < 90 else os.path.join(tempfile.mkdtemp(prefix="pcd", dir="/tmp"), "t.sock")
    web = None
    env = {}
    try:
        env, web = walk(demo, sock, restart=bool(a.serve))
        say("\nWALKTHROUGH OK: inspect, edit and paste went through pai web with a fake agent.")
        if a.serve:
            web.send_signal(signal.SIGINT)
            web.wait(10)
            web.stdout.close()
            web.stderr.close()
            web = subprocess.Popen(PAI + ["web", "--no-browser"], cwd=os.path.join(demo, "project"), env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            line = ""
            while "open:" not in line:
                line = web.stdout.readline()
                if not line:
                    raise Failed("pai web printed no link")
            say("BROWSER: %s" % line.split("open:")[1].strip())
            time.sleep(a.serve)
        return 0
    except Failed as e:
        say("\nWALKTHROUGH FAILED: %s" % e)
        return 1
    finally:
        if web and web.poll() is None:
            web.send_signal(signal.SIGINT)
            try:
                web.wait(10)
            except subprocess.TimeoutExpired:
                web.kill()
                web.wait()
        if web:
            web.stdout.close()
            web.stderr.close()
        subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True)
        if a.keep:
            say("kept %s" % demo)
        else:
            shutil.rmtree(demo, ignore_errors=True)
            if not sock.startswith(demo):
                shutil.rmtree(os.path.dirname(sock), ignore_errors=True)


def walk(demo, sock, restart=False):
    home, ctl, cfg, tasks, lanes = (os.path.join(demo, d) for d in ("home", "ctl", "claude", "tasks", "lanes"))
    bindir, repo, origin = os.path.join(demo, "bin"), os.path.join(demo, "project"), os.path.join(demo, "origin.git")
    for d in (home, ctl, cfg, tasks, bindir):
        os.makedirs(d)
    with open(os.path.join(bindir, "claude"), "w") as f:
        f.write("#!%s\nimport runpy, sys\nsys.argv[0] = __file__\nrunpy.run_path(%r, run_name='__main__')\n" % (sys.executable, FAKE))
    os.chmod(os.path.join(bindir, "claude"), 0o755)
    tools = [os.path.dirname(shutil.which(t)) for t in ("git", "tmux", "ps")]
    env = {"HOME": home, "PATH": os.pathsep.join(dict.fromkeys([bindir] + tools + ["/usr/bin", "/bin"])), "LANG": "C.UTF-8",
           "TERM": "xterm", "PAI_WORKBENCH_HOME": os.path.join(demo, "wb"), "PAI_WORKBENCH_DATA": os.path.join(demo, "wbdata"),
           "PAI_SWARM_HOME": os.path.join(demo, "swarm"), "CLAUDE_CONFIG_DIR": cfg, "CODEX_HOME": os.path.join(demo, "codex"),
           "FAKE_RC_DIR": ctl, "PAI_DISPATCH_SETTLE": "0.5", "PAI_JEV_CREDENTIAL_STORE": "none", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.invalid",
           "GIT_COMMITTER_NAME": "Demo", "GIT_COMMITTER_EMAIL": "demo@example.invalid", "SHELL": "/bin/sh"}

    def run(*args, cwd=repo, ok=(0,)):
        p = subprocess.run(list(args), cwd=cwd, env=env, capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
        if p.returncode not in ok:
            raise Failed("%s exited %s: %s" % (" ".join(args[:4]), p.returncode, (p.stdout + p.stderr)[-600:]))
        return p

    say("1. A synthetic project, one task note, and a run with the FAKE agent")
    run("git", "init", "-q", "--bare", "-b", "main", origin, cwd=demo)
    run("git", "init", "-q", "-b", "main", repo, cwd=demo)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("# Console demo\n")
    run("git", "add", "README.md")
    run("git", "commit", "-q", "-m", "init")
    run("git", "remote", "add", "origin", origin)
    run("git", "push", "-q", "origin", "main")
    with open(os.path.join(tasks, "demo-task.md"), "w", encoding="utf-8") as f:
        f.write("---\nstatus: todo\npriority: 1\n---\n# Write the demo line\n\nAppend `demo` to NOTES.md.\n")
    run(*PAI, "init", "--no-input", "--default-agent", "claude", "--base", "main", "--worktree-root", lanes, "--tasks", tasks)
    run(*PAI, "run", "--workers", "1", "--mode", "local", "--tmux-socket", sock, "--yes", "--wait", "10", ok=(0, 1))
    st = json.loads(run(*PAI, "status", "--json", ok=(0, 1)).stdout)
    say("   pai status: lane a %s" % st["lanes"][0]["lifecycle"]["state"])

    web = subprocess.Popen(PAI + ["web", "--no-browser"], cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True)
    link, end = None, time.time() + 20
    while time.time() < end and not link:
        line = web.stdout.readline()
        if "open:" in line:
            link = line.split("open:")[1].strip()
    if not link:
        raise Failed("pai web printed no link")
    port = int(link.split(":")[2].split("/")[0])
    path = link.split(str(port), 1)[1]

    def req(method, url, body=None, cookie=None, csrf=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        h = {"Host": "127.0.0.1:%d" % port}
        if cookie:
            h["Cookie"] = cookie
        if csrf:
            h["X-PAI-CSRF"] = csrf
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body), "application/json"
        c.request(method, url, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        hdrs = dict(r.getheaders())
        c.close()
        try:
            return r.status, hdrs, json.loads(raw)
        except ValueError:
            return r.status, hdrs, raw

    st_, hdrs, _ = req("GET", path)
    cookie = hdrs.get("Set-Cookie", "").split(";")[0]
    csrf = req("GET", "/api/session", cookie=cookie)[2]["csrf"]
    get = lambda u: req("GET", u, cookie=cookie)[2]  # noqa: E731
    post = lambda u, b: req("POST", u, b, cookie=cookie, csrf=csrf)  # noqa: E731
    say("   pai web on 127.0.0.1:%d, signed in once with its link" % port)

    say("\n2. The lane list: the same lifecycle name and why as pai status")
    lanes_ = get("/api/lanes")
    lane = lanes_["lanes"][0]
    if lane["lifecycle"]["state"] != st["lanes"][0]["lifecycle"]["state"]:
        raise Failed("console and pai status disagree: %s vs %s" % (lane["lifecycle"]["state"], st["lanes"][0]["lifecycle"]["state"]))
    say("   %s" % lanes_["summary_line"])
    say("   lane a: %s: %s" % (lane["lifecycle"]["state"], lane["lifecycle"]["why"]))

    say("\n3. Inspect: the evidence and where each piece is read")
    ins = get("/api/inspect?lane=a")
    for e in ins["evidence"]:
        say("   %-9s %s  (from %s)" % (e["kind"], e["state"], e["source"]))
    if not {"process", "dispatch", "task"} <= {e["kind"] for e in ins["evidence"]}:
        raise Failed("inspect lacks process, dispatch or task evidence")

    say("\n4. Edit the task note: review the diff, save the confirmed revision")
    f = get("/api/file?root=0&path=demo-task.md")
    new = f["text"] + "\nEdited in the console demo.\n"
    s_, _, diff = post("/api/diff", {"root": 0, "path": "demo-task.md", "text": new, "base": f["base"]})
    s_, _, saved = post("/api/save", {"root": 0, "path": "demo-task.md", "text": new, "base": f["base"], "confirm": diff["proposed"]})
    if s_ != 200:
        raise Failed("save failed: %s" % saved)
    with open(os.path.join(tasks, "demo-task.md"), encoding="utf-8") as fh:
        if "Edited in the console demo." not in fh.read():
            raise Failed("the saved revision is not on disk")
    say("   saved revision %s; diff had %d line(s)" % (saved["base"][:8], len(diff["diff"])))

    say("\n5. Paste: preview, paste (staged), then Enter (submitted)")
    text = "Please also add a line to NOTES.md: console demo"
    s_, _, pv = post("/api/paste/preview", {"lane": "a", "text": text})
    say("   preview: lane %s, %s (%s pane %s), %d characters, %d line(s), %d bytes of at most %d; ok=%s" % (
        pv["lane"], pv["program"], pv["target"], pv["pane"], pv["chars"], pv["lines"], pv["bytes"], pv["limit"], pv["ok"]))
    if not pv["ok"]:
        raise Failed("preview refused: %s" % pv["why"])

    def received():
        p_ = os.path.join(ctl, "received.jsonl")
        if not os.path.exists(p_):
            return []
        with open(p_) as fh:
            return [json.loads(x) for x in fh if x.strip()]
    before = len(received())
    s_, _, res = post("/api/paste", {"lane": "a", "text": text})
    if s_ != 200 or res["enter"]:
        raise Failed("paste: %s %s" % (s_, res))
    time.sleep(1)
    if len(received()) != before:
        raise Failed("a staged paste was submitted")
    say("   STAGED: %d characters in the input box; nothing submitted (the agent received nothing new)" % res["pasted"])
    post("/api/keys", {"lane": "a", "keys": [{"key": "Enter"}]})
    end = time.time() + 15
    while time.time() < end and not any(r["text"] == text for r in received()):
        time.sleep(0.2)
    if not any(r["text"] == text for r in received()):
        raise Failed("the submitted text did not reach the agent")
    say("   SUBMITTED: the fake agent's transcript records the line")
    say("   lane a now: %s" % get("/api/lanes")["lanes"][0]["lifecycle"]["state"])

    if restart:  # --serve: the lane stays up for the browser check
        return env, web
    say("\n6. Stop: pai web, the fake worker and its tmux server")
    run(*PAI, "cleanup", "--apply", "--stop-running", ok=(0, 1))
    return env, web


if __name__ == "__main__":
    sys.exit(main())
