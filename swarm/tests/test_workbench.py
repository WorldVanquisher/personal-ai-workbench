"""`pai` workbench end to end with fakes only: disposable repositories with a local bare
origin, fake Claude and Codex on PATH, fake gh, fake ssh/ssh-add, an isolated tmux server on
its own -S socket and isolated pai homes. Nothing here reaches a network, a real agent, your
tmux server or your configuration. Where a test answers an agent's folder-trust prompt it
does so with tmux send-keys, playing the operator; product code never does."""
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import SWARM, git, commit  # noqa: E402

PAI = os.path.join(os.path.dirname(SWARM), "bin", "pai.py")
sys.path.insert(0, SWARM)
needs_311 = unittest.skipUnless(sys.version_info >= (3, 11), "pai (and its TOML defaults) needs Python 3.11+")
# Install plans add sudo only for a non-root user; as root (a container, or `unshare -r`) they do not.
SUDO = "" if hasattr(os, "geteuid") and os.geteuid() == 0 else "sudo "
HERE = os.path.dirname(os.path.abspath(__file__))
FAKES = {"claude": "fake_claude_rc.py", "codex": "fake_codex.py", "ssh": "fake_ssh.py", "ssh-add": "fake_ssh_add.py"}


def wrapper(bindir, name, fake):
    """An executable named like the real tool that runs the fake (so `ps` shows the name)."""
    path = os.path.join(bindir, name)
    with open(path, "w") as f:
        f.write("#!%s\nimport runpy, sys\nsys.argv[0] = __file__\nrunpy.run_path(%r, run_name='__main__')\n"
                % (sys.executable, os.path.join(HERE, fake)))
    os.chmod(path, 0o755)
    return path


def read(path, missing=None):
    """The file's text, read with the handle closed. A file that cannot be read raises, unless
    the caller names what that means (`missing`)."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        if missing is None:
            raise
        return missing


def lines(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    with open("/proc/%d/stat" % pid) as f:
        return f.read().split(") ")[1].split()[0] != "Z"


class WB(helpers.Fixture):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for name, fake in FAKES.items():
            wrapper(self.bin, name, fake)
        self.keyfile = os.path.join(self.tmp, "id_test")
        with open(self.keyfile, "w") as f:
            f.write("not a real key\n")
        self.sshlog = os.path.join(self.tmp, "ssh.log")
        # A short dispatch settle window for every fixture run: only the tests of the window itself
        # (test_dispatch, production_settle()) use the shipped 3 s. pai guide and the onboarding
        # acceptance keep the shipped default on purpose: they check the product as shipped.
        self.env["PAI_DISPATCH_SETTLE"] = "0.5"
        self.env.update(PATH=self.bin + os.pathsep + os.environ.get("PATH", ""), PAI_WORKBENCH_HOME=os.path.join(self.tmp, "wb"),
                        PAI_WORKBENCH_DATA=os.path.join(self.tmp, "wbdata"), FAKE_RC_DIR=self.ctl, CLAUDE_CONFIG_DIR=self.cfg,
                        FAKE_SSH_LOG=self.sshlog, FAKE_SSH_KEYFILE=self.keyfile, FAKE_SSH_ACCEPT="good-key")
        for k in ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "GIT_SSH", "GIT_SSH_COMMAND", "PAI_CONFIG", "XDG_CONFIG_HOME"):
            self.env.pop(k, None)
        self.tasks = os.path.join(self.vault, "Tasks")
        os.makedirs(self.tasks, exist_ok=True)
        self.socket = None

    def pai(self, *args, cwd=None, env=None, timeout=180):
        e = dict(self.env)
        e.update(env or {})
        for k, v in list(e.items()):
            if v is None:
                del e[k]
        return subprocess.run([sys.executable, "-B", PAI] + [str(a) for a in args], cwd=cwd or self.repo, env=e,
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def init(self, agent="none", *extra):
        out = self.assert_ok(self.pai("init", "--no-input", "--default-agent", agent, "--worktree-root", self.root,
                                      "--tasks", self.tasks, *extra))
        self.project_file = out.split("saved       ")[1].split()[0]
        self.set_config({("run", "tmux_socket"): self.tmux_socket()})  # an -S path: the test's own tmux server
        return self.project_cfg()

    def set_config(self, updates, path=None):
        import wbconfig
        path = path or self.project_file
        text = read(path, missing="")
        with open(path, "w") as f:
            f.write(wbconfig.set_values(text, updates))

    def project_cfg(self):
        import tomllib
        import wbconfig
        with open(self.project_file, "rb") as f:
            return wbconfig.flat(tomllib.load(f))

    def run_id(self, out):
        return out.split("run ")[-1].split(":")[0].strip()

    def status(self, run=None, **kw):
        p = self.pai("status", "--json", *(["--run", run] if run else []), **kw)
        d = json.loads(p.stdout)
        return {l["lane"]: l for l in d["lanes"]}, p.returncode

    def received(self, lane=None):
        rows = lines(os.path.join(self.ctl, "received.jsonl"))
        return [r for r in rows if lane is None or r["lane"] == lane]

    def wait_for(self, fn, timeout=15, step=0.2):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(step)
        return fn()

    def pane(self, lane, session_prefix="pai-"):
        p = self.tmux("list-panes", "-a", "-F", "#{window_name}\t#{pane_id}")
        return dict(l.split("\t") for l in p.stdout.splitlines())[lane]

    def snapshot(self):
        def tree(d):
            res = []
            for root, dirs, files in os.walk(d):
                dirs.sort()
                for fn in sorted(files):
                    path = os.path.join(root, fn)
                    with open(path, "rb") as f:
                        res.append((os.path.relpath(path, d), f.read()))
            return res
        sock = self.socket and os.path.exists(self.socket)
        return (git(self.repo, "for-each-ref"), git(self.repo, "worktree", "list"), tree(self.root), tree(self.home),
                tree(self.env["PAI_WORKBENCH_HOME"]), tree(self.env["PAI_WORKBENCH_DATA"]) if os.path.isdir(
                    self.env["PAI_WORKBENCH_DATA"]) else None, tree(self.tasks), sock and self.tmux("list-panes", "-a", "-F",
                    "#{window_id} #{window_name} #{pane_id} #{pane_pid} #{pane_dead}").stdout)


@helpers.needs_tmux
@needs_311
class Init(WB):
    def test_init_asks_for_the_default_agent_and_never_silently_overwrites(self):
        from test_tty import Terminal, ENTER
        t = Terminal(self, ["init", "--worktree-root", self.root, "--jev", "skip"], self.env, cli=PAI, cwd=self.repo)
        before = t.expect("Default agent [not set yet]: ")
        self.assertIn("1) claude", before)
        self.assertIn("installed: %s" % os.path.join(self.bin, "claude"), before)
        self.assertIn("2) codex", before)
        self.assertIn("Remote Control: not supported", before)
        self.assertIn("3) not set yet", before)
        t.send(b"2" + ENTER)
        t.expect("Default model for codex (Enter: codex's own default): ")
        t.send(b"gpt-test" + ENTER)
        t.expect("Reasoning effort for codex (minimal, low, medium, high; Enter: codex's own default): ")
        t.send(b"high" + ENTER)
        t.expect("Remote [origin]: ")
        t.send(ENTER)
        t.expect("Base branch [main]: ")
        t.send(ENTER)
        t.expect("Task notes folder (an Obsidian folder, or Enter for pai's own; '-' for none) [")
        t.send(self.tasks.encode() + ENTER)
        t.expect("'-' for none) [-]: ")
        t.send(b"verify=true" + ENTER)
        t.expect("[Y/n] [y]: ")  # a new file: Enter saves it
        t.send(b"y" + ENTER)
        self.assertEqual(t.wait(), 0, t.text)
        path = self.project_file = t.text.split("saved       ")[1].split()[0]
        cfg = self.project_cfg()
        self.assertEqual((cfg["default_agent"], cfg["remote"], cfg["base_branch"], cfg["tasks"]),
                         ("codex", "origin", "main", self.tasks))
        self.assertEqual(cfg["review"], {"checks": {"verify": ["true"]}, "required_checks": ["verify"]})
        self.assertEqual({k: v for k, v in cfg["agents"].items() if v}, {"codex": {"model": "gpt-test", "effort": "high"}})
        self.assertIn("# effort = ", read(path), "unset settings stay as commented examples")
        with open(path, "rb") as f:
            saved = f.read()
        # Not a terminal: an existing configuration is never replaced without --update.
        p = self.pai("init", "--no-input", "--default-agent", "claude")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("already configured", p.stdout)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), saved)
        # --update changes only what is given.
        with open(path, "a") as f:
            f.write("# my own note, kept\n")
        self.assert_ok(self.pai("init", "--no-input", "--update", "--default-agent", "claude"))
        cfg2 = self.project_cfg()
        self.assertEqual(cfg2["default_agent"], "claude")
        self.assertEqual({k: v for k, v in cfg2["agents"].items() if v}, {"codex": {"model": "gpt-test", "effort": "high"}},
                         "switching to claude neither copies nor drops codex's own settings")
        self.assertIn("# my own note, kept", read(path))
        self.assertEqual((cfg2["review"], cfg2["tasks"], cfg2["worktree_root"]), (cfg["review"], cfg["tasks"], cfg["worktree_root"]))

    def test_noninteractive_init_needs_explicit_values(self):
        p = self.pai("init", "--no-input")
        self.assertEqual(p.returncode, 2)
        self.assertIn("Pass --default-agent", p.stderr)
        out = os.path.join(self.tmp, "outside")
        os.makedirs(out)
        p = self.pai("init", "--no-input", "--default-agent", "claude", cwd=out)
        self.assertEqual(p.returncode, 2)
        self.assertIn("is not inside a Git repository", p.stderr)
        self.assertFalse(os.path.exists(self.env["PAI_WORKBENCH_HOME"]))


@helpers.needs_tmux
@needs_311
class MixedBatch(WB):
    def setUp(self):
        super().setUp()
        self.note("task-a", priority=9)
        self.note("task-b", priority=8)
        self.init()

    def test_claude_waits_for_trust_while_codex_gets_its_task_then_claude_gets_it_once(self):
        self.behavior(trust=["a"])
        p = self.pai("run", "--workers", "2", "--agent", "claude", "--agent", "codex", "--model", "b=gpt-test",
                     "--yes", "--wait", "8")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)  # not every lane is ready: a waits for its operator
        run = out.split("pai attach a --run ")[1].split()[0]
        starts = {s["lane"]: s for s in self.starts(2)}
        self.assertEqual((starts["a"]["agent"], starts["b"]["agent"]), ("claude", "codex"))
        self.assertEqual(starts["a"]["argv"], ["--remote-control", "--name", "repo %s a" % run])
        self.assertEqual(starts["b"]["argv"], ["--model", "gpt-test"])
        self.assertTrue(alive(starts["a"]["pid"]) and alive(starts["b"]["pid"]), "both agents run at the same time")
        self.assertLess(abs(starts["a"]["time"] - starts["b"]["time"]), 5)
        got = self.received()
        self.assertEqual([(r["lane"], r["text"].split("]")[0] + "]") for r in got], [("b", "[pai %s/b/task-b]" % run)],
                         "codex got its task; claude, still at the trust prompt, got nothing")
        self.assertIn("waiting for folder trust", out)
        rows, code = self.status()
        self.assertEqual(code, 1)
        self.assertEqual((rows["a"]["phase"], rows["a"]["dispatch"]["state"], rows["a"]["rc"]["state"]),
                         ("waiting-for-trust", "pending", "not-connected"))
        self.assertEqual((rows["b"]["phase"], rows["b"]["dispatch"]["state"], rows["b"]["rc"]["state"]),
                         ("running", "delivered", "not-supported"))
        cmd = self.assert_ok(self.pai("attach", "a", "--print")).strip()
        target = cmd.split("-t ")[1].strip("'")
        self.assertEqual(self.tmux("display-message", "-p", "-t", target, "#{window_name}").stdout.strip(), "a")
        # A resume while a still waits: nothing is sent to a, nothing again to b, nothing restarted.
        self.pai("resume", "--wait", "1")
        self.assertEqual(len(self.received()), 1)
        self.assertEqual(len(self.starts()), 2)
        # The operator answers the prompt (here: send-keys, standing in for a person at the window).
        self.tmux("send-keys", "-t", self.pane("a"), "yes", "Enter")
        p = self.pai("resume", "--wait", "8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual([r["text"].split("]")[0] for r in self.received("a")], ["[pai %s/a/task-a" % run])
        self.assertEqual(len(self.received("b")), 1)
        self.assertEqual(len(self.starts()), 2, "resume restarted nobody")
        self.assert_ok(self.pai("resume", "--wait", "1"))
        self.assertEqual(len(self.received()), 2, "a third resume sends nothing")
        rows, code = self.status()
        self.assertEqual(code, 0)
        self.assertEqual(rows["a"]["rc"]["state"], "verified")
        self.assertEqual(rows["a"]["process"]["pid"], starts["a"]["pid"])

    def test_a_dispatch_whose_outcome_is_unknown_is_never_sent_again_automatically(self):
        self.assert_ok(self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--no-dispatch", "--wait", "3"))
        self.assertEqual(self.received(), [])
        rows, _ = self.status()
        run = json.loads(self.pai("status", "--json").stdout)["run_id"]
        pid = rows["a"]["process"]["pid"]
        dfile = [os.path.join(self.home, "dispatch", f) for f in os.listdir(os.path.join(self.home, "dispatch"))] if \
            os.path.isdir(os.path.join(self.home, "dispatch")) else []
        self.assertEqual(dfile, [])
        # pai stopped after recording `sending` and before tmux confirmed anything:
        os.makedirs(os.path.join(self.home, "dispatch"))
        cdir = git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        import hashlib
        path = os.path.join(self.home, "dispatch", "%s-%s.json" % (hashlib.sha256(os.path.realpath(cdir).encode()).hexdigest()[:12], run))
        with open("/proc/%d/stat" % pid) as f:
            start = f.read().split(") ")[1].split()[19]
        with open(path, "w") as f:
            json.dump({"schema": "pai_dispatch_v1", "entries": {"a/task-a": {
                "state": "sending", "lane": "a", "task": "task-a", "pid": pid, "pid_start": start, "attempt": 1}}}, f)
        rows, _ = self.status()
        self.assertEqual(rows["a"]["dispatch"]["state"], "unknown")
        self.pai("resume", "--wait", "2")
        self.assertEqual(self.received(), [], "an unknown dispatch is not resent by resume")
        p = self.pai("resume", "--lane", "a", "--redispatch")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.received("a")), 1)
        self.pai("resume", "--wait", "2")
        self.assertEqual(len(self.received("a")), 1)
        # The process ends after a send: the next state is unknown again, never a resend. (No
        # transcript shows the prompt either: with that evidence it would count as delivered.)
        with open(path) as f:
            d = json.load(f)
        d["entries"]["a/task-a"].update(state="sent")
        d["entries"]["a/task-a"].pop("evidence", None)
        with open(path, "w") as f:
            json.dump(d, f)
        shutil.rmtree(os.path.join(self.env["CODEX_HOME"], "sessions"), ignore_errors=True)
        self.tmux("kill-window", "-t", "=%s:a" % ("pai-" + run))
        self.pai("resume", "--wait", "3")  # restarts the worker: a new process, which never saw the task
        rows, _ = self.status()
        self.assertEqual(rows["a"]["dispatch"]["state"], "unknown")
        self.assertIn("--redispatch", rows["a"]["dispatch"]["detail"])
        self.assertEqual(len(self.received("a")), 1, "the new process was not sent the task on its own")
        self.assert_ok(self.pai("resume", "--lane", "a", "--mark-delivered"))
        self.pai("resume", "--wait", "2")
        rows, _ = self.status()
        self.assertEqual((rows["a"]["dispatch"]["state"], len(self.received("a"))), ("delivered", 1))



@helpers.needs_tmux
@needs_311
class SSH(WB):
    """The remote is ssh://git@fakehost.invalid/<local bare repo>, reached through the fake ssh."""

    def setUp(self):
        super().setUp()
        self.url = "ssh://git@fakehost.invalid" + self.origin
        git(self.repo, "remote", "set-url", "origin", self.url)
        self.note("task-a", priority=9)
        self.init()
        self.set_config({("project", "github"): "o/r"})  # the fake gh's repository; the ssh URL itself is not GitHub
        self.good = self.agent_file("good", ["good-key"])

    def agent_file(self, name, keys):
        path = os.path.join(self.tmp, "agent-%s" % name)
        with open(path, "w") as f:
            f.write("".join(k + "\n" for k in keys))
        return path

    def ssh_calls(self):
        return lines(self.sshlog)

    def assert_nothing_started(self):
        self.assertEqual(self.starts(), [])
        self.assertEqual(os.listdir(self.root), [])
        self.assertFalse(os.path.isdir(os.path.join(self.home, "tasks")))
        self.assertFalse(os.path.exists(self.socket) if self.socket else False)

    def test_missing_or_wrong_key_fails_fast_without_a_prompt_or_a_worker(self):
        began = time.time()
        p = self.pai("run", "--workers", "1", "--agent", "codex", "--yes")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("no ssh-agent is reachable from this shell (SSH_AUTH_SOCK is not set)", out)
        self.assertIn('eval "$(ssh-agent -s)"', out)
        self.assertIn("ssh-add %s" % self.keyfile, out)
        self.assertIn("Nothing was locked, fetched, created or started", out)
        self.assert_nothing_started()
        wrong = self.agent_file("wrong", ["other-key"])
        p = self.pai("run", "--workers", "1", "--agent", "codex", "--yes", env={"SSH_AUTH_SOCK": wrong})
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("refused the loaded key(s)", out)
        self.assertNotIn("ssh-agent -s", out, "an agent is running; it needs the right key, not a new agent")
        self.assert_nothing_started()
        self.assertLess(time.time() - began, 30)
        calls = self.ssh_calls()
        self.assertTrue(calls)
        self.assertTrue(all(c["batch"] and not c["tty"] for c in calls if "-G" not in c["argv"]),
                        "every ssh call was non-interactive: BatchMode and no terminal")
        self.assertEqual({tuple(c) for c in lines(self.sshlog + ".add")}, {("-l",)}, "pai only ever lists keys")

    def test_a_valid_agent_runs_and_the_worker_gets_this_shells_socket_not_the_servers(self):
        stale = self.agent_file("stale", [])
        # A tmux server that was started earlier from another login, with its own agent socket.
        e = {k: v for k, v in self.env.items() if k not in ("TMUX", "TMUX_PANE")}
        e["SSH_AUTH_SOCK"] = stale
        subprocess.run(["tmux", "-S", self.tmux_socket(), "-f", os.devnull, "new-session", "-d", "-s", "earlier"], env=e,
                       check=True)
        p = self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "5", env={"SSH_AUTH_SOCK": self.good})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("answered `git ls-remote` without a prompt", p.stdout)
        start = self.starts(1)[0]
        self.assertEqual(start["ssh_auth_sock"], self.good, "the worker has this shell's agent, not the server's stale one")
        fetches = [c for c in self.ssh_calls() if any("upload-pack" in a for a in c["argv"])]
        self.assertTrue(fetches and all(c["sock"] == self.good and c["batch"] and not c["tty"] for c in fetches))
        # A shell without an agent: the worker it starts must not inherit the server's socket either.
        self.tmux("kill-window", "-t", self.pane("a"))
        p = self.pai("resume", "--wait", "3", env={"SSH_AUTH_SOCK": None})
        self.assertIsNone(self.starts(2)[-1]["ssh_auth_sock"])

    def test_pai_next_in_a_fresh_shell_uses_its_own_agent_and_stops_without_one(self):
        self.note("task-b", priority=5)
        self.assert_ok(self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "5",
                                env={"SSH_AUTH_SOCK": self.good}))
        run = json.loads(self.pai("status", "--json").stdout)["run_id"]
        wt = os.path.join(self.root, "a")
        tip = commit(wt, "a.txt", "work\n", "feat: a")
        git(wt, "push", "-q", self.origin, "pai/%s/a" % run, env={"SSH_AUTH_SOCK": self.good})
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": [{"number": 1, "url": "https://github.example/o/r/pull/1", "state": "MERGED",
                                "mergedAt": "2026-11-12T10:00:00Z", "baseRefName": "main",
                                "headRefName": "pai/%s/a" % run, "headRefOid": tip}]}, f)
        p = self.pai("next", "a", env={"SSH_AUTH_SOCK": None})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("No lane was rolled over", p.stdout)
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "pai/%s/a" % run)
        fresh = self.agent_file("fresh", ["good-key"])  # a new login, a new agent socket
        before = len(self.ssh_calls())
        p = self.pai("next", "a", "--wait", "5", env={"SSH_AUTH_SOCK": fresh})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "pai/%s/a-2" % run)
        used = [c for c in self.ssh_calls()[before:] if "-G" not in c["argv"]]
        self.assertTrue(used and all(c["sock"] == fresh for c in used))
        self.assertEqual([r["text"].split("]")[0] for r in self.received("a")],
                         ["[pai %s/a/task-a" % run, "[pai %s/a/task-b" % run], "the kept agent got the next task once")

    def test_pr_open_without_a_key_pushes_nothing(self):
        self.set_config({("review.checks", "verify"): ["true"], ("review", "required_checks"): ["verify"]})
        self.assert_ok(self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "5",
                                env={"SSH_AUTH_SOCK": self.good}))
        commit(os.path.join(self.root, "a"), "a.txt", "work\n", "feat: a change")
        p = self.pai("pr", "open", "a", env={"SSH_AUTH_SOCK": None})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("SSH preflight for remote origin", p.stdout)
        self.assertIn("push URL", p.stdout)
        self.assertEqual(git(self.origin, "for-each-ref", "refs/heads"), git(self.origin, "for-each-ref", "refs/heads/main"))
        self.assertFalse(any(c[:2] == ["pr", "create"] for c in lines(self.env["FAKE_GH_STATE"] + ".log")))
        p = self.pai("pr", "open", "a", "--draft", env={"SSH_AUTH_SOCK": self.good})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("opened    https://github.example/", p.stdout)
        self.assertTrue(any(c[:2] == ["pr", "create"] and "--draft" in c for c in lines(self.env["FAKE_GH_STATE"] + ".log")))
        p = self.pai("pr", "open", "a", env={"SSH_AUTH_SOCK": self.good})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("reused    https://github.example/", p.stdout)

    def test_https_and_local_remotes_need_no_agent(self):
        sys.path.insert(0, SWARM)
        import gitprep
        for url, t in (("https://github.com/o/r.git", "https"), ("git@github.com:o/r.git", "ssh"),
                       ("ssh://git@host:2222/o/r", "ssh"), ("/srv/git/r.git", "local"), ("file:///srv/r.git", "file"),
                       ("./rel:path", "local")):
            self.assertEqual(gitprep.transport(url), t, url)
        git(self.repo, "remote", "set-url", "origin", "https://github.example/o/r.git")
        old = os.environ.copy()
        try:
            os.environ.update(self.env)
            os.environ.pop("SSH_AUTH_SOCK", None)
            res = gitprep.remote_auth(self.repo, "origin")
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual((res["ok"], res["state"]), (True, "not-ssh"))
        self.assertEqual(self.ssh_calls(), [])
        self.assertFalse(os.path.exists(self.sshlog + ".add"))


@helpers.needs_tmux
@needs_311
class Repositories(WB):
    def test_run_finds_the_repository_from_a_subdirectory_and_a_linked_worktree_and_dry_run_changes_nothing(self):
        self.init()
        sub = os.path.join(self.repo, "deep", "dir")
        os.makedirs(sub)
        linked = os.path.join(self.tmp, "linked")
        git(self.repo, "worktree", "add", "-q", "-b", "side", linked)
        self.advance_main()
        before = self.snapshot()
        for cwd in (sub, linked):
            p = self.pai("run", "--workers", "2", "--agent", "codex", "--dry-run", cwd=cwd)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("repo      %s\n" % self.repo, p.stdout)
            self.assertIn("fetched and pinned to one commit when the run starts (not resolved yet)", p.stdout)
            self.assertIn("DRY RUN: nothing was fetched", p.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), self.c0, "no fetch")
        self.assertEqual(self.starts(), [])

    def test_outside_git_without_repo_shows_the_examples_and_changes_nothing(self):
        out = os.path.join(self.tmp, "outside")
        os.makedirs(out)
        p = self.pai("run", "--workers", "2", "--agent", "codex", cwd=out)
        self.assertEqual(p.returncode, 2)
        self.assertIn("pai run --repo https://github.com/OWNER/REPO.git", p.stderr)
        self.assertFalse(os.path.exists(self.home))

    def test_a_remote_url_is_cloned_into_a_managed_checkout_reviewed_then_reused(self):
        out = os.path.join(self.tmp, "outside")
        os.makedirs(out)
        url = "file://" + self.origin
        p = self.pai("run", "--repo", url, "--workers", "2", "--agent", "claude", "--agent", "codex", "--dry-run", cwd=out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("(would be cloned)", p.stdout)
        self.assertIn("DRY RUN: nothing was cloned", p.stdout)
        self.assertFalse(os.path.exists(self.env["PAI_WORKBENCH_DATA"]))
        self.assertFalse(os.path.exists(self.home))
        # A new clone is its own trust decision: cloned and shown, but no agent starts unreviewed.
        p = self.pai("run", "--repo", url, "--workers", "1", "--agent", "codex", "--yes", cwd=out)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("REVIEW", p.stdout)
        self.assertIn("no agent was started", p.stdout)
        dest = p.stdout.split("checkout  ")[1].split(" (")[0]
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))
        self.assertEqual(self.starts(), [])
        p = self.pai("run", "--repo", url, "--workers", "1", "--agent", "codex", "--yes", "--reviewed", "--wait", "5",
                     "--tmux-socket", self.tmux_socket(), cwd=out)
        self.assertIn("pai's checkout of it, reused", p.stdout)
        self.assertEqual(len(self.starts(1)), 1, p.stdout + p.stderr)
        self.assertEqual(self.starts()[0]["cwd"], os.path.join(dest + "-pai-lanes", "a"))
        # Occupied and mismatched destinations are refused before anything starts.
        occupied = os.path.join(self.tmp, "occupied")
        os.makedirs(occupied)
        p = self.pai("run", "--repo", url, "--checkout-dir", occupied, "--workers", "1", "--agent", "codex", "--yes", cwd=out)
        self.assertEqual(p.returncode, 1)
        self.assertIn("already exists and pai did not create it", p.stdout)
        other = os.path.join(self.tmp, "other.git")
        subprocess.run(["git", "clone", "-q", "--bare", self.origin, other], check=True)
        p = self.pai("run", "--repo", "file://" + other, "--checkout-dir", dest, "--workers", "1", "--agent", "codex",
                     "--yes", cwd=out)
        self.assertEqual(p.returncode, 1)
        self.assertIn("was created by pai for", p.stdout)
        self.assertEqual(len(self.starts()), 1)



@helpers.needs_tmux
@needs_311
class Cleanup(WB):
    """One exited worker (a), one live worker (b), a Claude waiting for folder trust (c), a lane
    whose ownership record no longer matches (d), and someone's own window in the session."""

    def setUp(self):
        super().setUp()
        self.init()
        self.behavior(codex_exit={"a": 0}, trust=["c"])
        p = self.pai("run", "--workers", "4", "--agent", "codex", "--agent", "codex", "--agent", "claude",
                     "--agent", "codex", "--yes", "--no-dispatch", "--wait", "4")
        self.run = p.stdout.split("run ")[-1].split(":")[0].strip()
        self.session = "pai-" + self.run
        self.tmux("new-window", "-d", "-t", "=%s:" % self.session, "-n", "notes", "sleep 600")
        self.notes_id = [l.split()[0] for l in self.tmux("list-windows", "-t", "=" + self.session, "-F",
                                                         "#{window_id} #{window_name}").stdout.splitlines() if l.endswith(" notes")][0]
        own_dir = os.path.join(self.home, "rc")
        self.own_file = os.path.join(own_dir, os.listdir(own_dir)[0])
        with open(self.own_file) as f:
            own = json.load(f)
        own["lanes"]["d"]["window_id"] = self.notes_id  # the record now points at someone else's window
        with open(self.own_file, "w") as f:
            json.dump(own, f)
        with open(os.path.join(self.root, "b", "README.md"), "a") as f:
            f.write("uncommitted edit in b\n")
        self.wait_for(lambda: self.status()[0]["a"]["process"]["state"] == "exited")

    def windows_now(self):
        return {l.split("\t")[1]: l.split("\t")[0] for l in self.tmux("list-windows", "-t", "=" + self.session, "-F",
                                                                       "#{window_id}\t#{window_name}").stdout.splitlines()}

    def lock_owned(self, lane):
        sys.path.insert(0, SWARM)
        import wtlock
        old = os.environ.get("PAI_SWARM_HOME")
        os.environ["PAI_SWARM_HOME"] = self.home
        try:
            return wtlock.owned(wtlock.read(os.path.join(self.root, lane)), "rc", self.run, lane)
        finally:
            if old is None:
                del os.environ["PAI_SWARM_HOME"]
            else:
                os.environ["PAI_SWARM_HOME"] = old

    def test_preview_changes_nothing_and_shows_every_target(self):
        before = self.snapshot()
        p = self.pai("cleanup")
        self.assertEqual(self.snapshot(), before)
        out = p.stdout
        self.assertIn("socket %s" % self.socket, out)
        self.assertIn("session %s" % self.session, out)
        for needle in ("exited (status 0)", "would close window", "would release the lock", "running (pid",
                       "waiting for folder trust", "mismatched: ", "REFUSED", "'d' is not one of these targets",
                       "the window is named notes, not d",
                       "PREVIEW: nothing was closed", "lock this run", "codex/local", "claude/rc"):
            self.assertIn(needle, out)
        self.assertIn("pai cleanup --run %s --lane b --apply --stop-running" % self.run, out)

    def test_apply_closes_only_the_exited_worker_and_a_second_run_is_harmless(self):
        wins = self.windows_now()
        rows, _ = self.status()
        p = self.pai("cleanup", "--apply")
        self.assertEqual(p.returncode, 1, p.stdout)  # live and mismatched lanes were kept
        after = self.windows_now()
        self.assertNotIn("a", after)
        self.assertEqual({k: after[k] for k in ("b", "c", "d", "notes")}, {k: wins[k] for k in ("b", "c", "d", "notes")})
        self.assertEqual((self.lock_owned("a"), self.lock_owned("b"), self.lock_owned("c"), self.lock_owned("d")),
                         (False, True, True, True))
        again, _ = self.status()
        for lane in ("b", "c"):
            self.assertEqual(again[lane]["process"]["pid"], rows[lane]["process"]["pid"], "live workers kept")
        self.assertEqual(again["c"]["phase"], "waiting-for-trust")
        for lane in "abcd":
            self.assertTrue(os.path.isdir(os.path.join(self.root, lane)))
            self.assertEqual(git(self.repo, "rev-parse", "--verify", "-q", "pai/%s/%s" % (self.run, lane)) != "", True)
        self.assertIn("uncommitted edit in b", read(os.path.join(self.root, "b", "README.md")))
        before = self.snapshot()
        p = self.pai("cleanup", "--apply", "--lane", "a")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("already cleaned", p.stdout)
        self.assertEqual(self.snapshot()[:-1], before[:-1])

    def test_stopping_a_live_worker_needs_apply_and_stop_running_and_touches_nothing_else(self):
        wins = self.windows_now()
        pid = self.status()[0]["b"]["process"]["pid"]
        p = self.pai("cleanup", "--lane", "b", "--stop-running")
        self.assertEqual(self.windows_now(), wins, "--stop-running without --apply is a preview")
        self.assertTrue(alive(pid))
        p = self.pai("cleanup", "--lane", "b", "--apply", "--stop-running")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("stopped: the worker exited", p.stdout)
        self.assertFalse(self.wait_for(lambda: not alive(pid)) is False)
        after = self.windows_now()
        self.assertNotIn("b", after)
        self.assertEqual({k: after[k] for k in ("a", "c", "d", "notes")}, {k: wins[k] for k in ("a", "c", "d", "notes")})
        self.assertFalse(self.lock_owned("b"))
        self.assertIn("uncommitted edit in b", read(os.path.join(self.root, "b", "README.md")))
        # the documented way back: resume starts the closed worker again on its branch, with its edit
        p = self.pai("resume", "--lane", "b", "--wait", "3")
        self.assertIn("b", self.windows_now())
        self.assertEqual(git(os.path.join(self.root, "b"), "symbolic-ref", "--short", "HEAD"), "pai/%s/b" % self.run)
        self.assertTrue(self.lock_owned("b"))

    def test_a_mismatched_ownership_record_fails_closed(self):
        p = self.pai("cleanup", "--lane", "d", "--apply", "--stop-running")
        self.assertEqual(p.returncode, 1)
        self.assertIn("REFUSED", p.stdout)
        self.assertIn(self.notes_id, self.windows_now().values())
        self.assertTrue(self.lock_owned("d"))


@needs_311
class Planner(unittest.TestCase):
    """The installer planner on simulated systems: os-release, platform and package managers
    are fakes, nothing is installed. Simulated coverage only; no real OS was exercised here.

    WSL detection is stubbed explicitly for every simulated Linux (PAI_SWARM_WSL=0 unless a test
    says otherwise): the real detection reads the host's /proc/sys/kernel/osrelease, so on a WSL
    host a simulated "plain Linux" would otherwise be reported as WSL."""

    def plan(self, system, ident=None, like="", version=None, managers=(), wsl="0", have=()):
        tmp = os.path.realpath(__import__("tempfile").mkdtemp(prefix="pai-planner-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        b = os.path.join(tmp, "bin")
        os.makedirs(b)
        for n in tuple(managers) + tuple(have):
            with open(os.path.join(b, n), "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(os.path.join(b, n), 0o755)
        osr = os.path.join(tmp, "os-release")
        with open(osr, "w") as f:
            f.write("ID=%s\n" % ident if ident else "")
            f.write('ID_LIKE="%s"\n' % like if like else "")
            f.write('VERSION_ID="%s"\n' % version if version else "")
        env = {"PATH": b, "HOME": tmp, "PAI_SWARM_PLATFORM": system, "PAI_SWARM_OS_RELEASE": osr,
               "PAI_WORKBENCH_HOME": os.path.join(tmp, "wb"), "PAI_SWARM_HOME": os.path.join(tmp, "home")}
        if system == "Linux":
            env["PAI_SWARM_WSL"] = wsl  # never the host's own /proc: see the class docstring
        p = subprocess.run([sys.executable, "-B", PAI, "setup", "--dry-run"], cwd=tmp, env=env, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        self.assertFalse(os.path.exists(os.path.join(tmp, "home")), "setup --dry-run writes nothing")
        return p.stdout + p.stderr

    def test_linux_distributions_by_os_release_and_present_manager(self):
        out = self.plan("Linux", "ubuntu", "debian", "24.04", ["apt-get", "sudo"])
        self.assertIn("system         linux (ubuntu 24.04)", out)
        self.assertIn(SUDO + "apt-get install -y git tmux procps gh", out)
        out = self.plan("Linux", "arch", managers=["pacman", "sudo"])
        self.assertIn(SUDO + "pacman -S --needed --noconfirm git tmux procps-ng github-cli", out)
        out = self.plan("Linux", "rocky", "rhel centos fedora", "9.4", ["dnf", "sudo"])
        self.assertIn(SUDO + "dnf install -y git tmux procps-ng\n", out)
        self.assertIn("gh is not in rocky's own repositories", out)
        out = self.plan("Linux", "almalinux", "rhel centos fedora", "8.10", ["yum", "sudo"])
        self.assertIn(SUDO + "yum install -y git tmux procps-ng", out)
        out = self.plan("Linux", "amzn", "fedora", "2", ["yum", "sudo"])
        self.assertIn(SUDO + "yum install -y git tmux procps-ng", out)
        out = self.plan("Linux", "amzn", "fedora", "2023", ["dnf", "sudo"])
        self.assertIn(SUDO + "dnf install -y git tmux procps-ng", out)
        out = self.plan("Linux", "fedora", None, "41", ["dnf", "sudo"])
        self.assertIn(SUDO + "dnf install -y git tmux procps-ng gh", out)

    def test_a_distribution_whose_manager_is_absent_or_unknown_is_not_guessed(self):
        out = self.plan("Linux", "ubuntu", "debian", "24.04", [])
        self.assertIn("ubuntu, but apt-get is not on PATH", out)
        self.assertNotIn("would install git", out)
        out = self.plan("Linux", "plan9", managers=["apt-get", "dnf", "pacman"])
        self.assertIn("plan9 is not a supported distribution", out)
        self.assertNotIn("install -y", out)
        self.assertIn("missing        git, tmux, ps, gh, claude", out)

    def test_macos_and_windows(self):
        out = self.plan("Darwin", managers=["brew"])
        self.assertIn("system         macos", out)
        self.assertIn("brew install git tmux gh", out)
        out = self.plan("Darwin")
        self.assertIn("Homebrew is not installed (https://brew.sh); pai does not install it for you", out)
        self.assertNotIn("brew install", out)
        out = self.plan("Windows", managers=["winget"])
        self.assertIn("system         windows", out)
        self.assertIn("worker backend NONE", out)
        self.assertIn("winget install --exact --source winget --id Git.Git", out)
        self.assertIn("winget install --exact --source winget --id GitHub.cli", out)
        self.assertIn("wsl --install -d Ubuntu", out)
        self.assertNotIn("winget install --exact --source winget --id tmux", out)
        out = self.plan("Windows")
        self.assertIn("winget is not installed", out)

    def test_wsl_when_detection_says_so(self):
        # the positive case, kept apart from the plain-Linux simulations above
        out = self.plan("Linux", "ubuntu", "debian", "22.04", ["apt-get", "sudo"], wsl="1")
        self.assertIn("system         wsl (ubuntu 22.04)", out)
        self.assertIn("worker backend tmux", out)
        self.assertIn(SUDO + "apt-get install -y git tmux procps gh", out)
        out = self.plan("Linux", "ubuntu", "debian", "22.04", ["apt-get", "sudo"], wsl="0")
        self.assertIn("system         linux (ubuntu 22.04)", out, "an explicit 0 is plain Linux")

    def test_runtime_wsl_detection_is_unchanged(self):
        # with no stub, detection still reads WSL_DISTRO_NAME and the kernel's osrelease
        import doctor
        env = {k: v for k, v in os.environ.items() if k not in ("PAI_SWARM_WSL", "PAI_SWARM_PLATFORM", "WSL_DISTRO_NAME")}
        real = platform.system() == "Linux" and "microsoft" in read("/proc/sys/kernel/osrelease", missing="").lower()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("platform.system", return_value="Linux"):
            self.assertEqual(doctor.detect_platform()["system"], "wsl" if real else "linux")
            with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}):
                self.assertEqual(doctor.detect_platform()["system"], "wsl")
            with mock.patch("builtins.open", mock.mock_open(read_data="6.6.87.2-microsoft-standard-WSL2\n")):
                self.assertEqual(doctor.detect_platform()["system"], "wsl", "a WSL kernel is detected as such")

    def test_pai_run_on_native_windows_is_refused_before_anything(self):
        tmp = os.path.realpath(__import__("tempfile").mkdtemp(prefix="pai-planner-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q", tmp], check=True)
        env = dict(os.environ, PAI_SWARM_PLATFORM="Windows", PAI_WORKBENCH_HOME=os.path.join(tmp, "wb"),
                   PAI_SWARM_HOME=os.path.join(tmp, "home"))
        p = subprocess.run([sys.executable, "-B", PAI, "run", "--agent", "codex", "--remote", "x", "--base", "main",
                            "--yes"], cwd=tmp, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 2)
        self.assertIn("no worker backend on windows", p.stderr)
        self.assertIn("WSL", p.stderr)
        self.assertFalse(os.path.exists(os.path.join(tmp, "home")))


@helpers.needs_tmux
@needs_311
class Compatibility(WB):
    def test_an_old_claude_only_manifest_without_command_still_works(self):
        m = self.manifest(lanes=("one",))
        with open(m) as f:
            d = json.load(f)
        del d["command"]  # the default launch line: `claude --remote-control --name ...` from PATH
        with open(m, "w") as f:
            json.dump(d, f)
        out = self.assert_ok(self.rc("up", m, "--no-input", "--settle", "3"))
        self.assertIn("claude (rc)", out)
        s = self.starts(1)[0]
        self.assertEqual((s["agent"], s["argv"]), ("claude", ["--remote-control", "--name", "T run1 one"]))
        rows, code = helpers.Fixture.status(self, m)
        self.assertEqual((code, rows["one"]["rc"]["state"], rows["one"]["dispatch"]["state"]), (0, "verified", "none"))
        self.assertEqual(self.received(), [], "dispatch stays off for rc manifests unless they ask for it")

    def test_an_unsupported_mode_is_refused_before_anything(self):
        self.init()
        p = self.pai("run", "--agent", "codex", "--mode", "rc", "--yes")
        self.assertEqual(p.returncode, 2)
        self.assertIn("codex has no launch mode 'rc' (it supports: local). An unsupported mode is refused", p.stderr)
        m = self.manifest(lanes=("one", "two"))
        with open(m) as f:
            d = json.load(f)
        del d["command"]
        with open(m, "w") as f:
            json.dump(d, f)
        p = self.rc("plan", m, "--agent", "two=codex", "--mode", "two=rc")
        self.assertEqual(p.returncode, 2)
        self.assertIn("codex has no launch mode 'rc'", p.stderr)
        self.assertFalse(os.path.exists(self.home))

    def test_claude_without_remote_control_is_refused_and_a_model_never_picks_the_agent(self):
        self.init()
        p = self.pai("run", "--agent", "claude", "--yes", env={"FAKE_CLAUDE_NO_RC": "1"})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("has no --remote-control option", p.stdout)
        self.assertEqual(self.starts(), [])
        p = self.pai("run", "--model", "gpt-test", "--yes")
        self.assertEqual(p.returncode, 2)
        self.assertIn("no agent chosen", p.stderr)
        p = self.pai("run", "--agent", "codex", "--model", "--dangerous", "--yes")
        self.assertNotEqual(p.returncode, 0)

    def test_a_stale_bridge_record_is_inconclusive(self):
        self.init()
        self.assert_ok(self.pai("run", "--agent", "claude", "--yes", "--no-dispatch", "--wait", "3"))
        rows, code = self.status()
        self.assertEqual((rows["a"]["rc"]["state"], code), ("verified", 0))
        time.sleep(2.2)
        rows, code = self.status(env={"PAI_RC_STALE_SECONDS": "1"})
        self.assertEqual((rows["a"]["rc"]["state"], code), ("unverified", 1))
        self.assertIn("stale", rows["a"]["rc"]["evidence"])

    def test_a_new_run_fetches_the_moved_base_and_resume_does_not_fetch(self):
        self.init()
        tip = self.advance_main()
        self.assert_ok(self.pai("run", "--agent", "codex", "--yes", "--no-dispatch", "--wait", "3"))
        rows, _ = self.status()
        self.assertEqual(rows["a"]["base_sha"], tip)
        newer = self.advance_main()
        self.tmux("kill-window", "-t", self.pane("a"))
        self.assert_ok(self.pai("resume", "--wait", "3"))
        rows, _ = self.status()
        self.assertEqual(rows["a"]["base_sha"], tip)
        self.assertNotEqual(git(self.repo, "rev-parse", "origin/main"), newer, "resume does not fetch")

    def test_next_rolls_over_only_after_the_pr_is_merged(self):
        self.note("first", priority=9)
        self.note("second", priority=1)
        self.init()
        self.assert_ok(self.pai("run", "--workers", "1", "--agent", "codex", "--yes", "--wait", "4"))
        run = json.loads(self.pai("status", "--json").stdout)["run_id"]
        mf = os.path.join(self.home, "workbench", "runs", "repo", run, "manifest.json")
        with open(mf) as f:
            d = json.load(f)
        d["repository"]["github"] = "o/r"
        with open(mf, "w") as f:
            json.dump(d, f)
        wt = os.path.join(self.root, "a")
        tip = commit(wt, "x.txt", "x\n", "feat: x")
        git(wt, "push", "-q", "origin", "pai/%s/a" % run)
        pr = {"number": 4, "url": "https://github.example/o/r/pull/4", "state": "OPEN", "mergedAt": None,
              "baseRefName": "main", "headRefName": "pai/%s/a" % run, "headRefOid": tip}
        for state, merged in (("OPEN", None), ("CLOSED", None)):
            with open(self.env["FAKE_GH_STATE"], "w") as f:
                json.dump({"prs": [dict(pr, state=state, mergedAt=merged)]}, f)
            p = self.pai("next", "a", "--wait", "2")
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "pai/%s/a" % run)
        squash = commit(self.upstream, "squash.txt", "x\n", "feat: x (#4)")
        git(self.upstream, "push", "-q", "origin", "main")
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": [dict(pr, state="MERGED", mergedAt="2026-11-12T10:00:00Z")]}, f)
        p = self.pai("next", "a", "--wait", "4")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "pai/%s/a-2" % run, p.stdout)
        self.assertEqual(git(wt, "rev-parse", "HEAD"), squash, "the next task starts from the fetched squash commit")
        self.assertEqual([r["text"].split("]")[0] for r in self.received("a")],
                         ["[pai %s/a/first" % run, "[pai %s/a/second" % run])



@needs_311
@helpers.needs_tmux
class Config(WB):
    def editor(self, name, action):
        """A fake editor: runs `action` (python) on the file it is given and logs its own name."""
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write("#!%s\nimport sys\nopen(%r, 'a').write(%r + '\\n')\np = sys.argv[-1]\ntext = open(p).read()\n%s\n"
                    "open(p, 'w').write(text)\n" % (sys.executable, os.path.join(self.tmp, "editors.log"), name, action))
        os.chmod(path, 0o755)
        return path

    def editors(self):
        p = os.path.join(self.tmp, "editors.log")
        return read(p, missing="").split()

    def test_precedence_is_cli_then_project_then_global_then_built_in(self):
        self.init()
        glob = os.path.join(self.env["PAI_WORKBENCH_HOME"], "config.toml")
        self.set_config({("run", "workers"): 3, ("agents.codex", "model"): "global-model",
                         ("agents.codex", "effort"): "low", ("logs", "tail"): 7}, path=glob)
        self.set_config({("agents.codex", "model"): "project-model", ("run", "workers"): None})
        eff = json.loads(self.assert_ok(self.pai("config", "show", "--effective", "--json")))
        self.assertEqual(eff["run.workers"], {"value": 3, "source": "global"})
        self.assertEqual(eff["agents.codex.model"], {"value": "project-model", "source": "project"})
        self.assertEqual(eff["agents.codex.effort"], {"value": "low", "source": "global"})
        self.assertEqual(eff["run.wait"], {"value": 10.0, "source": "built-in"})
        self.assertEqual(eff["agents.claude.model"]["value"], None)
        out = self.assert_ok(self.pai("run", "--agent", "codex", "--model", "1=cli-model", "--dry-run"))
        self.assertIn("lane a    codex (local, model cli-model, effort low)", out)
        self.assertIn("lane b    codex (local, model project-model, effort low)", out)
        self.assertIn("lane c    codex (local, model project-model, effort low)", out)

    def test_editor_selection_follows_visual_editor_nano_and_notepad_on_windows(self):
        self.init()
        keep = "pass"
        self.editor("vis", keep)
        self.editor("ed", keep)
        self.editor("nano", keep)
        self.assert_ok(self.pai("config", "edit", env={"VISUAL": "vis", "EDITOR": "ed"}))
        self.assert_ok(self.pai("config", "edit", env={"VISUAL": None, "EDITOR": "ed --wait"}))
        self.assert_ok(self.pai("config", "edit", env={"VISUAL": None, "EDITOR": None}))
        self.assertEqual(self.editors(), ["vis", "ed", "nano"])
        os.remove(os.path.join(self.bin, "nano"))
        only = os.path.join(self.tmp, "only-git")  # no host editor can be found on this PATH
        os.makedirs(only)
        os.symlink(shutil.which("git"), os.path.join(only, "git"))
        bare = self.bin + os.pathsep + only
        self.editor("notepad", keep)
        self.assert_ok(self.pai("config", "edit", env={"VISUAL": None, "EDITOR": None, "PAI_SWARM_PLATFORM": "Windows",
                                                       "PATH": bare}))
        self.assertEqual(self.editors()[-1], "notepad")
        for system in ("Linux", "Darwin"):  # no Notepad fallback there, and nothing is installed
            p = self.pai("config", "edit", env={"VISUAL": None, "EDITOR": None, "PAI_SWARM_PLATFORM": system,
                                                "PATH": bare})
            self.assertEqual(p.returncode, 0)
            self.assertIn("No editor: set VISUAL or EDITOR", p.stdout)
            self.assertIn(self.project_file, p.stdout)
        self.assertEqual(len(self.editors()), 4)

    def test_an_invalid_edit_keeps_the_previous_file_and_the_draft(self):
        self.init()
        before = read(self.project_file)
        self.editor("bad", "text = text.replace('workers = 2', 'workers = \"many\"\\napi_key = \"sk-not-real\"')")
        p = self.pai("config", "edit", env={"EDITOR": "bad"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("INVALID  run.workers: must be a whole number", p.stdout)
        self.assertIn("INVALID  run.api_key: API keys and other credentials never belong in this file", p.stdout)
        self.assertNotIn("sk-not-real", p.stdout)
        self.assertEqual(read(self.project_file), before)
        draft = self.project_file + ".draft"
        self.assertIn('workers = "many"', read(draft))
        p = self.pai("config", "check")
        self.assertIn("an unsaved draft is waiting", p.stdout)
        self.editor("fix", "import re\ntext = re.sub(r'(?m)^api_key.*\\n', '', text).replace('workers = \"many\"', 'workers = 4')")
        p = self.pai("config", "edit", env={"EDITOR": "fix"})
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("Continuing your unsaved draft", p.stdout)
        self.assertFalse(os.path.exists(draft))
        self.assertEqual(self.project_cfg()["workers"], 4)
        self.assertIn("# pai workbench project defaults", read(self.project_file), "comments survive an edit")
        self.assertEqual(self.pai("config", "check").returncode, 0)

    def test_each_agent_gets_its_own_model_and_effort_and_a_lane_override_stays_in_its_lane(self):
        self.init()
        self.set_config({("agents.claude", "model"): "opus-test", ("agents.claude", "effort"): "high",
                         ("agents.codex", "model"): "gpt-test", ("agents.codex", "effort"): "low"})
        p = self.pai("run", "--workers", "3", "--agent", "claude", "--agent", "codex", "--agent", "codex",
                     "--effort", "3=minimal", "--yes", "--no-dispatch", "--wait", "4")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        argv = {s_["lane"]: s_["argv"] for s_ in self.starts(3)}
        self.assertEqual(argv["a"][-4:], ["--model", "opus-test", "--effort", "high"])
        self.assertEqual(argv["b"], ["--model", "gpt-test", "-c", 'model_reasoning_effort="low"'])
        self.assertEqual(argv["c"], ["--model", "gpt-test", "-c", 'model_reasoning_effort="minimal"'])

    def test_unsupported_settings_fail_before_anything_starts(self):
        self.init()
        for args, needle in ((["--agent", "codex", "--effort", "max"], "codex does not take reasoning effort 'max'"),
                             (["--agent", "claude", "--effort", "minimal"], "claude does not take reasoning effort 'minimal'"),
                             (["--agent", "claude", "--agent", "codex", "--workers", "2", "--effort", "2=xhigh"],
                              "lane b: codex does not take reasoning effort 'xhigh'"),
                             (["--agent", "codex", "--model=-oops"], "is not a model name")):
            p = self.pai("run", *args, "--yes")
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn(needle, p.stderr)
        self.assertEqual(os.listdir(self.root), [])
        self.assertFalse(os.path.exists(self.home))
        self.assertEqual(self.starts(), [])
        self.set_config({("agents.codex", "effort"): "max"})
        p = self.pai("config", "check")
        self.assertEqual(p.returncode, 1)
        self.assertIn("agents.codex.effort: codex does not take reasoning effort 'max'", p.stdout)


if __name__ == "__main__":
    unittest.main()
