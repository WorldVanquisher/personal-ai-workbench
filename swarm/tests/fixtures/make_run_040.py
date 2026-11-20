#!/usr/bin/env python3
"""Make the saved 0.4.x run-state fixture (swarm/tests/fixtures/run-0.4.0/) from a real run of an
earlier pai, so tests can check that the current pai reads it.

    python3 -B swarm/tests/fixtures/make_run_040.py --ref b02acec --root /var/tmp/pai-fx040 --out DIR

It extracts REF with `git archive` (read-only), makes a synthetic repository with fixed commit
dates and identity (so the base commit has the same id everywhere), runs that pai's `init` and
`run` with the fake Claude (no network, no account), stops its tmux server and saves the run state
it wrote. In the saved files the root folder becomes @ROOT@, the path hashes in file names become
@CDIR@ (12 hex) and @LOCK@ (24 hex), and the device/inode pair of the worktree registry becomes
"@DEV@"/"@INO@"; meta.json keeps the original values so a test can check that the current code
still names the files the same way.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
FAKE = os.path.join(os.path.dirname(HERE), "fake_claude_rc.py")
FIXED = {"GIT_AUTHOR_DATE": "2026-11-01T09:00:00Z", "GIT_COMMITTER_DATE": "2026-11-01T09:00:00Z",
         "GIT_AUTHOR_NAME": "Ada Human", "GIT_AUTHOR_EMAIL": "ada@example.org", "GIT_COMMITTER_NAME": "Ada Human",
         "GIT_COMMITTER_EMAIL": "ada@example.org", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
NOTE = "---\nstatus: todo\npriority: 2\n---\n# Task first\n\nDo first.\n"
RUN_ID = "r040"


def setup_repo(root, env):
    """The synthetic repository, origin and task folder (same ids on every machine)."""
    def git(*a, cwd=None):
        subprocess.run(["git"] + list(a), cwd=cwd, env=env, check=True, capture_output=True)
    git("init", "-q", "--bare", "-b", "main", os.path.join(root, "origin.git"))
    repo = os.path.join(root, "repo")
    git("init", "-q", "-b", "main", repo)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("# fixture\n")
    git("add", "README.md", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    git("remote", "add", "origin", os.path.join(root, "origin.git"), cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    os.makedirs(os.path.join(root, "tasks"), exist_ok=True)
    with open(os.path.join(root, "tasks", "first.md"), "w") as f:
        f.write(NOTE)
    return repo


def env_for(root):
    for d in ("home", "cfg", "data", "state", "claude", "ctl", "bin"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    env = dict(os.environ, HOME=os.path.join(root, "home"), PAI_WORKBENCH_HOME=os.path.join(root, "cfg"),
               PAI_WORKBENCH_DATA=os.path.join(root, "data"), PAI_SWARM_HOME=os.path.join(root, "state"),
               CLAUDE_CONFIG_DIR=os.path.join(root, "claude"), FAKE_RC_DIR=os.path.join(root, "ctl"),
               PATH=os.path.join(root, "bin") + os.pathsep + os.environ.get("PATH", ""), PAI_DISPATCH_SETTLE="0.5",
               **FIXED)
    for k in ("PAI_CONFIG", "XDG_CONFIG_HOME", "CODEX_HOME", "SSH_AUTH_SOCK"):
        env.pop(k, None)
    return env


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ref", default="b02acec")
    ap.add_argument("--root", required=True, help="a new folder for the run (its path is replaced by @ROOT@)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    root = os.path.realpath(a.root)
    if os.path.exists(root) or os.path.exists(a.out):
        sys.exit("make_run_040: --root and --out must not exist")
    old = os.path.join(root, "old")
    os.makedirs(old)
    arc = subprocess.run(["git", "-C", ROOT, "archive", a.ref], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", old], input=arc, check=True)
    env = env_for(root)
    with open(os.path.join(root, "bin", "claude"), "w") as f:
        f.write("#!%s\nimport runpy, sys\nsys.argv[0] = __file__\nrunpy.run_path(%r, run_name='__main__')\n"
                % (sys.executable, FAKE))
    os.chmod(os.path.join(root, "bin", "claude"), 0o755)
    repo = setup_repo(root, env)
    pai = [sys.executable, "-B", os.path.join(old, "bin", "pai.py")]
    version = subprocess.run(pai + ["--version"], env=env, capture_output=True, text=True).stdout.strip()
    sock = os.path.join(root, "t.sock")
    subprocess.run(pai + ["init", "--no-input", "--default-agent", "claude", "--base", "main", "--worktree-root",
                          os.path.join(root, "lanes"), "--tasks", os.path.join(root, "tasks")], cwd=repo, env=env,
                   check=True, capture_output=True)
    r = subprocess.run(pai + ["run", "--workers", "1", "--mode", "local", "--tmux-socket", sock, "--yes", "--wait", "8",
                              "--run-id", RUN_ID], cwd=repo, env=env, capture_output=True, text=True)
    subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True)
    cdir = os.path.realpath(os.path.join(repo, ".git"))
    ch = hashlib.sha256(cdir.encode()).hexdigest()[:12]
    wt = os.path.join(root, "lanes", "a")
    lh = hashlib.sha256(os.path.realpath(wt).encode()).hexdigest()[:24]
    files = ["state/tasks/%s-%s.json" % (ch, RUN_ID), "state/rc/%s-%s.json" % (ch, RUN_ID),
             "state/dispatch/%s-%s.json" % (ch, RUN_ID), "state/locks/%s.json" % lh, "state/workbench/worktrees.json",
             "state/workbench/runs/repo/%s/manifest.json" % RUN_ID, "state/workbench/runs/repo/%s/run.json" % RUN_ID,
             "cfg/projects/repo.toml"]
    os.makedirs(a.out)
    saved = []
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            text = f.read()
        if rel.endswith("worktrees.json"):
            d = json.loads(text)
            text = json.dumps({k: ["@DEV@", "@INO@"] for k in d}, indent=1)
        text = text.replace(root, "@ROOT@")
        name = rel.replace(ch, "@CDIR@").replace(lh, "@LOCK@")
        dst = os.path.join(a.out, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        saved.append(name)
    base = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    with open(os.path.join(a.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"made_by": "%s (git %s), swarm/tests/fixtures/make_run_040.py" % (version, a.ref), "root": root,
                   "names": {"cdir": ch, "lock": lh}, "run": RUN_ID, "base": base, "files": saved,
                   "run_exit": r.returncode, "git": FIXED, "note": NOTE}, f, indent=1, sort_keys=True)
    print("saved %d files from %s into %s (run exit %s, base %s)" % (len(saved), version, a.out, r.returncode, base[:12]))
    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
