"""Shared fixtures: disposable repositories with a local bare `origin`, an isolated tmux
server, an isolated pai-swarm home, fake Claude and fake gh. Nothing here touches your
repositories, your tmux server, your Claude configuration or the network."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SWARM = os.path.dirname(HERE)
CLI = os.path.join(SWARM, "pai_swarm.py")
FAKE_RC = os.path.join(HERE, "fake_claude_rc.py")
FAKE_HEADLESS = os.path.join(HERE, "fake_claude.py")
FAKE_GH = os.path.join(HERE, "fake_gh.py")
HUMAN = {"GIT_AUTHOR_NAME": "Ada Human", "GIT_AUTHOR_EMAIL": "ada@example.org",
         "GIT_COMMITTER_NAME": "Ada Human", "GIT_COMMITTER_EMAIL": "ada@example.org"}
LANES = ("release", "extraction", "demo", "workbooks")
needs_git = unittest.skipUnless(shutil.which("git"), "git is not installed")
needs_tmux = unittest.skipUnless(shutil.which("git") and shutil.which("tmux") and os.path.isdir("/proc"),
                                 "needs git, tmux and /proc")


def git(cwd, *args, check=True, env=None):
    e = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT="0", **HUMAN)
    e.update(env or {})
    p = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, env=e)
    if check and p.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), p.stderr))
    return p.stdout.strip()


def commit(cwd, name, text, message):
    with open(os.path.join(cwd, name), "w") as f:
        f.write(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


class Fixture(unittest.TestCase):
    """tmp/origin.git (bare, the remote), tmp/upstream (the remote owner's clone, used to
    move main), tmp/repo (the operator's clone), tmp/workers (worktree root), tmp/home."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pai-swarm-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin, self.upstream = os.path.join(self.tmp, "origin.git"), os.path.join(self.tmp, "upstream")
        self.repo, self.root = os.path.join(self.tmp, "repo"), os.path.join(self.tmp, "workers")
        self.home, self.ctl = os.path.join(self.tmp, "home"), os.path.join(self.tmp, "ctl")
        self.cfg, self.vault = os.path.join(self.tmp, "claude-config"), os.path.join(self.tmp, "vault")
        for d in (self.root, self.ctl, self.cfg, self.vault):
            os.makedirs(d)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origin], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.upstream], check=True)
        git(self.upstream, "remote", "add", "origin", self.origin)
        self.c0 = commit(self.upstream, "README.md", "hello\n", "init")
        git(self.upstream, "push", "-q", "origin", "main")
        subprocess.run(["git", "clone", "-q", self.origin, self.repo], check=True,
                       env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull))
        self.env = dict(os.environ, PAI_SWARM_HOME=self.home, GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_SYSTEM=os.devnull, FAKE_GH_STATE=os.path.join(self.tmp, "gh.json"),
                        FAKE_GH_REMOTE=self.origin, PAI_SWARM_GH=FAKE_GH, **HUMAN)
        for k in ("TMUX", "TMUX_PANE", "TYPESAFE_API_KEY", "PAI_SWARM_GIT"):
            self.env.pop(k, None)
        self.socket = None

    # -- remote owner moves main
    def advance_main(self, name="more.txt", message="upstream change"):
        sha = commit(self.upstream, name, "x %s\n" % time.time(), message)
        git(self.upstream, "push", "-q", "origin", "main")
        return sha

    def rewrite_main(self):
        """The remote's owner replaces main with different history (a force-push)."""
        git(self.upstream, "reset", "-q", "--hard", self.c0)
        sha = commit(self.upstream, "rewritten.txt", "rewritten\n", "rewritten history")
        git(self.upstream, "push", "-q", "--force", "origin", "main")
        return sha

    # -- command line
    def cli(self, *args, env=None, input=None, timeout=120):
        e = dict(self.env)
        e.update(env or {})
        return subprocess.run([sys.executable, "-B", CLI] + [str(a) for a in args], capture_output=True, text=True,
                              env=e, input=input, timeout=timeout, stdin=None if input is not None else subprocess.DEVNULL)

    def assert_ok(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def assert_refused(self, p, *needles):
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        for n in needles:
            self.assertIn(n, p.stdout + p.stderr)
        return p.stdout + p.stderr

    # -- RC manifest, tmux, fake Claude
    def tmux_socket(self):
        if not self.socket:
            d = self.tmp if len(self.tmp) < 80 else tempfile.mkdtemp(prefix="prc", dir="/tmp")
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
            self.socket = os.path.join(d, "t.sock")
            self.addCleanup(self.tmux, "kill-server")
        return self.socket

    def tmux(self, *args):
        env = {k: v for k, v in self.env.items() if k not in ("TMUX", "TMUX_PANE")}
        return subprocess.run(["tmux", "-S", self.tmux_socket(), "-f", os.devnull] + list(args),
                              capture_output=True, text=True, env=env)

    def manifest(self, run_id="run1", lanes=LANES, github=None, **extra):
        # `url` pins the local bare remote as the repository's identity; `github` then only
        # names the repository for the fake gh.
        d = {"schema": "pai_rc_manifest_v1", "run_id": run_id,
             "repository": dict({"path": self.repo, "remote": "origin", "base_branch": "main"},
                                **({"url": self.origin, "github": github} if github else {})),
             "worktree_root": self.root, "tmux": {"session": "t-{run_id}", "socket": self.tmux_socket()},
             "command": [sys.executable, FAKE_RC, "--remote-control", "--name", "{name}"],
             "env": {"FAKE_RC_DIR": self.ctl, "CLAUDE_CONFIG_DIR": self.cfg},
             "workers": [{"lane": l, "branch": "night/{run_id}/%s" % l, "name": "T {run_id} %s" % l} for l in lanes]}
        d.update(extra)
        path = os.path.join(self.tmp, "%s.json" % run_id)
        with open(path, "w") as f:
            json.dump(d, f)
        return path

    def rc(self, cmd, manifest, *args, **kw):
        return self.cli("rc", cmd, manifest, *args, **kw)

    def starts(self, wait_for=None, timeout=15):
        """Start records written by the fake Claude; optionally wait until there are N."""
        path, end = os.path.join(self.ctl, "starts.jsonl"), time.time() + timeout
        while True:
            rows = []
            if os.path.exists(path):
                with open(path) as f:
                    rows = [json.loads(l) for l in f]
            if wait_for is None or len(rows) >= wait_for or time.time() > end:
                return rows
            time.sleep(0.1)

    def behavior(self, **kw):
        with open(os.path.join(self.ctl, "behavior.json"), "w") as f:
            json.dump(kw, f)

    def windows(self, session):
        p = self.tmux("list-windows", "-t", "=" + session, "-F", "#{window_name}\t#{pane_dead}\t#{pane_pid}")
        return {l.split("\t")[0]: l.split("\t")[1:] for l in p.stdout.splitlines()}

    def status(self, manifest):
        p = self.rc("status", manifest, "--json")
        return {l["lane"]: l for l in json.loads(p.stdout)["lanes"]}, p.returncode

    def note(self, name, status="todo", priority=1, extra="", body="Do the thing."):
        tasks = os.path.join(self.vault, "Tasks")
        os.makedirs(tasks, exist_ok=True)
        path = os.path.join(tasks, name + ".md")
        with open(path, "w") as f:
            f.write("---\nstatus: %s\npriority: %d\n%s---\n# Task %s\n\n%s\n" % (status, priority, extra, name, body))
        return path
