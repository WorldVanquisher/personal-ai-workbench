"""Regression tests for the PAI-006 review findings (F1-F10). Each test failed against the
code as handed over and describes the behaviour the fix guarantees."""
import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git, commit  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import publish_check  # noqa: E402
import rc  # noqa: E402
import wtlock  # noqa: E402

PY = sys.executable
SESSION_ID = "session_01TestOnlyPrivateIdAbc9"


def read(path):
    with open(path) as f:
        return f.read()


def pids_in_group(pgid, needle):
    """Worker processes in the group: program (argv[0] or script argv[1]) ends with `needle`
    and was started with -p. The orchestrator only names the fake in --claude-bin."""
    res = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % d, "rb") as f:
                argv = f.read().decode("utf-8", "replace").split("\0")
            # "-p": a real worker, not the `--help` probe the runner makes before starting any
            if any(a.endswith(needle) for a in argv[:2]) and "-p" in argv and os.getpgid(int(d)) == pgid \
                    and int(d) != pgid:
                res.append(int(d))
        except (OSError, ProcessLookupError):
            continue
    return res


@helpers.needs_tmux
class F1IgnoredFiles(helpers.Fixture):
    """gitprep.py `git switch -c` silently replaced an ignored file that the new base
    tracks; `git status` called the worktree clean, so the work was lost."""

    def test_switching_to_a_new_task_branch_never_overwrites_an_ignored_file(self):
        wt = os.path.join(self.root, "release")
        git(self.repo, "worktree", "add", "-q", "-b", "old/release", wt, self.c0)
        commit(wt, ".gitignore", "local.cfg\n", "ignore local.cfg")
        with open(os.path.join(wt, "local.cfg"), "w") as f:
            f.write("precious ignored work\n")
        self.assertEqual(git(wt, "status", "--porcelain"), "", "git calls this worktree clean")
        commit(self.upstream, "local.cfg", "from main\n", "track local.cfg")
        git(self.upstream, "push", "-q", "origin", "main")
        m = self.manifest(lanes=("release",))
        self.assert_refused(self.rc("up", m, "--no-input"), "would be overwritten", "local.cfg")
        self.assertEqual(read(os.path.join(wt, "local.cfg")), "precious ignored work\n")
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "old/release")
        self.assertEqual(self.starts(), [])
        # Recoverable: move the file away yourself and run the same command again.
        os.rename(os.path.join(wt, "local.cfg"), os.path.join(self.tmp, "local.cfg.saved"))
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.3"))
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "night/run1/release")


@helpers.needs_tmux
class F2ConcurrentUp(helpers.Fixture):
    """rc.py: two `rc up` for the same run interleaved in ensure_workers and started a
    second Claude in a lane that the first was already starting."""

    def test_a_second_rc_up_for_the_same_run_never_starts_duplicate_workers(self):
        slow = os.path.join(self.tmp, "tmux-slow")
        with open(slow, "w") as f:
            f.write('#!/bin/sh\ncase " $* " in *" new-window "*|*" new-session "*) sleep 1.5;; esac\nexec tmux "$@"\n')
        os.chmod(slow, 0o755)
        m = self.manifest(lanes=("one", "two"))
        env = dict(self.env, PAI_SWARM_TMUX=slow)
        # --settle 1: the first run's closing status must not catch a pane before tmux has started
        # the fake Claude in it (that would make its exit code 1 for a reason unrelated to F2).
        args = [PY, "-B", helpers.CLI, "rc", "up", m, "--no-input", "--settle", "1"]
        first = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 stdin=subprocess.DEVNULL)
        tasks, end = os.path.join(self.home, "tasks"), time.time() + 20
        while time.time() < end:
            recs = [json.loads(read(os.path.join(tasks, f))) for f in os.listdir(tasks)] if os.path.isdir(tasks) else []
            if recs and all(l["state"] != "planned" and l["state"] != "preparing" for l in recs[0]["lanes"].values()):
                break
            time.sleep(0.05)
        second = subprocess.run(args, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        out1 = first.communicate(timeout=60)[0]
        time.sleep(0.5)
        names = self.tmux("list-windows", "-t", "=t-run1", "-F", "#{window_name}").stdout.split()
        self.assertEqual(sorted(names), ["one", "two"], "exactly one window per lane\n" + out1 + second.stdout)
        self.assertEqual(sorted(s["lane"] for s in self.starts()), ["one", "two"], "exactly one Claude per lane")
        self.assertEqual(first.returncode, 0, out1)
        self.assertNotEqual(second.returncode, 0, second.stdout)
        self.assertIn("another `rc up`, `rc next` or `rc release` for tmux session t-run1 is running", second.stdout)

    def test_rc_up_does_not_start_a_lane_whose_lock_it_does_not_hold(self):
        m = self.manifest(lanes=("one",))
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.3"))
        self.tmux("kill-window", "-t", "=t-run1:one")
        mf = rc.Manifest(m)
        with mock.patch.dict(os.environ, {"PAI_SWARM_HOME": self.home}):
            cur = rc.current_lanes(rc.gitprep.common_dir(self.repo), "run1")
            wtlock.release(mf.worker("one").worktree, "rc", "run1", "one")
            failed = rc.ensure_workers(mf, rc.Tmux(mf.socket), [(mf.worker("one"),) + cur["one"]])
        self.assertEqual(failed, 1)
        self.assertEqual(len(self.starts()), 1, "no second Claude without the lane's lock")


def running(pid):
    """True while pid exists and is not a zombie."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


@helpers.needs_tmux
class F3OrphanedWorkers(helpers.Fixture):
    """wtlock.py: a headless lock was 'stale' as soon as the orchestrator pid died, even
    though the claude workers it started were still running in the worktree."""

    def test_workers_of_a_killed_run_keep_their_worktree_locked(self):
        self.note("one", extra="paths: [src]\n")
        wt = os.path.join(self.root, "one")
        git(self.repo, "worktree", "add", "-q", "-b", "scratch", wt, self.c0)
        e = dict(self.env, FAKE_LOG=os.path.join(self.tmp, "fake.log"), FAKE_SLEEP="30")
        run = subprocess.Popen([PY, "-B", helpers.CLI, "run", "--repo", wt, "--tasks", os.path.join(self.vault, "Tasks"),
                                "--task-id", "h1", "--claude-bin", helpers.FAKE_HEADLESS],
                               env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: [os.kill(p, signal.SIGKILL) for p in pids_in_group(run.pid, "fake_claude.py")])
        end = time.time() + 20
        while not pids_in_group(run.pid, "fake_claude.py") and time.time() < end:
            time.sleep(0.1)
        workers = pids_in_group(run.pid, "fake_claude.py")
        self.assertTrue(workers, "the fake worker started")
        os.kill(run.pid, signal.SIGKILL)  # only the orchestrator: its worker keeps running
        run.wait()
        self.assertEqual(git(wt, "status", "--porcelain"), "", "the worktree looks clean and idle")
        m = self.manifest(lanes=("one",))
        out = self.assert_refused(self.rc("up", m, "--no-input"), "locked by headless task h1",
                                  "a process it started still holds the worktree")
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "swarm/h1", "the running worker's branch is kept")
        self.assertEqual(self.starts(), [])
        for p in workers:
            os.kill(p, signal.SIGKILL)
        end = time.time() + 10
        while any(running(p) for p in workers) and time.time() < end:
            time.sleep(0.1)
        self.assertNotIn("locked by", self.rc("plan", m).stdout, "once every worker is gone the lock is stale")

    def test_a_finished_run_releases_its_lock(self):
        self.note("one", extra="paths: [src]\n")
        p = self.cli("run", "--repo", self.repo, "--tasks", os.path.join(self.vault, "Tasks"), "--task-id", "h2",
                     "--claude-bin", helpers.FAKE_HEADLESS, env={"FAKE_LOG": os.path.join(self.tmp, "f.log"), "FAKE_SLEEP": "0.1"})
        self.assertIn("swarm/h2", git(self.repo, "symbolic-ref", "--short", "HEAD"), p.stdout + p.stderr)
        self.assertEqual(os.listdir(os.path.join(self.home, "locks")), [])


@helpers.needs_tmux
class F4MergedElsewhere(helpers.Fixture):
    """rc.py merged_pr: a PR merged into another branch than the task's base counted as
    merged, so the worker rolled over to a base that did not contain its work."""

    def test_a_pr_merged_into_another_branch_does_not_roll_over(self):
        self.note("first", priority=9)
        self.note("queued", priority=5)
        m = self.manifest(lanes=("one",), github="o/r", tasks=os.path.join(self.vault, "Tasks"))
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.3"))
        wt = os.path.join(self.root, "one")
        tip = commit(wt, "feature.txt", "work\n", "feat: first")
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": [{"number": 7, "url": "https://github.example/o/r/pull/7", "state": "MERGED",
                                "mergedAt": "2026-11-12T10:00:00Z", "baseRefName": "release-1.x",
                                "headRefName": "night/run1/one", "headRefOid": tip}]}, f)
        self.assert_refused(self.cli("rc", "next", m, "--no-input", "--lane", "one"),
                            "merged into release-1.x, not main")
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "night/run1/one")
        self.assertIn("status: todo", read(os.path.join(self.vault, "Tasks", "queued.md")))


@helpers.needs_tmux
class F5ChecksChangeTheTree(helpers.Fixture):
    """review.py do_checks: a check that rewrote tracked files still counted as passing,
    and `pr open` pushed a commit whose content was not the content that was tested."""

    def test_a_check_that_modifies_tracked_files_blocks_publication(self):
        self.note("first", priority=9)
        fmt = "import pathlib; p = pathlib.Path('feature.txt'); p.write_text(p.read_text() + 'reformatted\\n')"
        m = self.manifest(lanes=("one",), github="o/r", tasks=os.path.join(self.vault, "Tasks"), review={
            "required_checks": ["fmt"], "checks": {"fmt": [PY, "-c", fmt]}})
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.3"))
        wt = os.path.join(self.root, "one")
        commit(wt, "feature.txt", "work\n", "feat: first")
        out = self.assert_refused(self.cli("pr", "open", m, "--lane", "one"), "changed tracked files: feature.txt",
                                  "Nothing was pushed or opened")
        self.assertEqual(git(self.repo, "ls-remote", "origin", "refs/heads/night/run1/one"), "")
        self.assertIn("review: checks-failed", read(os.path.join(self.vault, "Tasks", "first.md")))


class F6GuardGaps(unittest.TestCase):
    """publish_check.py: variants that passed or were echoed."""

    def rules(self, text):
        return [f["rule"] for f in publish_check.check_text("f", text)]

    def test_bare_or_wrapped_session_ids_are_rejected(self):
        self.assertEqual(self.rules("Session: " + SESSION_ID), ["claude-session"])
        self.assertEqual(self.rules("see https://claude.ai/code/\n" + SESSION_ID), ["claude-session"])
        self.assertEqual(self.rules("Claude-Session-Id: abc123"), ["claude-session"])
        self.assertEqual(self.rules("X-Claude-Session: abc123"), ["claude-session"])

    def test_more_claude_names_are_rejected(self):
        for line in ("Co-authored-by: Anthropic Claude Code <bot@example.com>",
                     "Co-authored-by: Claude Opus 4.5 (1M context) <x@example.com>",
                     "Co-authored-by: Claude-Code <x@example.com>"):
            self.assertEqual(self.rules(line), ["ai-co-author"], line)
        self.assertEqual(self.rules("Generated with Claude Code (https://claude.ai/code)"), ["ai-footer"])

    def test_findings_never_contain_a_session_id(self):
        found = publish_check.check_text("f", "Co-authored-by: Claude <noreply@anthropic.com> " + SESSION_ID)
        self.assertNotIn(SESSION_ID, json.dumps(found))
        self.assertNotIn(SESSION_ID, publish_check.redact("x " + SESSION_ID))

    def test_still_allowed(self):
        for text in ("session_manager_configuration is renamed", "Co-authored-by: Claude Monet <monet@example.org>",
                     "Co-authored-by: Claude Dupont <claude.dupont@example.fr>",
                     "The fixtures were generated with Claude Code last week."):
            self.assertEqual(self.rules(text), [], text)


@helpers.needs_tmux
class F7PlanEchoes(helpers.Fixture):
    """review.py cmd_plan printed the PR title and commit subjects verbatim."""

    def test_pr_plan_never_prints_a_session_link(self):
        self.note("first", priority=9, extra='pr_title: "feat: x https://claude.ai/code/%s"\n' % SESSION_ID)
        m = self.manifest(lanes=("one",), github="o/r", tasks=os.path.join(self.vault, "Tasks"), review={
            "required_checks": ["unit"], "checks": {"unit": [PY, "-c", "pass"]}})
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.3"))
        commit(os.path.join(self.root, "one"), "f.txt", "x\n", "feat: see https://claude.ai/code/" + SESSION_ID)
        p = self.cli("pr", "plan", m, "--lane", "one")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("publication check rejected", p.stdout)
        self.assertNotIn(SESSION_ID, p.stdout + p.stderr)


class F8EvidenceWithoutStartTime(unittest.TestCase):
    """rc.py rc_evidence accepted a session record that had no process start time."""

    @unittest.skipUnless(os.path.isdir("/proc"), "needs /proc")
    def test_a_record_without_procstart_is_not_verified(self):
        with tempfile.TemporaryDirectory() as d:
            wt = os.path.realpath(d)
            sessions = os.path.join(wt, "sessions")
            os.makedirs(sessions)
            with open(os.path.join(sessions, "%d.json" % os.getpid()), "w") as f:
                json.dump({"pid": os.getpid(), "cwd": wt, "bridgeSessionId": "b"}, f)
            state, why = rc.rc_evidence(os.getpid(), wt, sessions, "")
            self.assertEqual(state, "unverified", why)
            self.assertIn("start time", why)


class F9MutexFailsOpen(unittest.TestCase):
    """wtlock.py mutex(): a failed blocking flock yielded False, and acquire() and
    release() went on without the mutex."""

    def test_acquire_refuses_when_the_mutex_cannot_be_taken(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"PAI_SWARM_HOME": d}), \
                mock.patch.object(wtlock.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            with self.assertRaises(wtlock.LockHeld) as cm:
                wtlock.acquire(d, "rc", "run1", "one")
            self.assertIn("mutex", str(cm.exception))
            self.assertFalse(os.path.isdir(os.path.join(d, "locks")) and os.listdir(os.path.join(d, "locks")))


@helpers.needs_git
class F10LockGitInAWorktree(helpers.Fixture):
    """pai_swarm.py: `run --lock-git` in a linked worktree prepared the task (new branch,
    record, lock) and only then failed on the missing .git directory."""

    def test_lock_git_is_refused_before_anything_is_prepared(self):
        self.note("one", extra="paths: [src]\n")
        wt = os.path.join(self.root, "one")
        git(self.repo, "worktree", "add", "-q", "-b", "scratch", wt, self.c0)
        p = self.cli("run", "--repo", wt, "--tasks", os.path.join(self.vault, "Tasks"), "--task-id", "h1", "--lock-git",
                     "--claude-bin", helpers.FAKE_HEADLESS, env={"FAKE_LOG": os.path.join(self.tmp, "f.log")})
        self.assert_refused(p, "--lock-git needs a checkout with its own .git directory")
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "scratch")
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname)", "refs/heads/swarm"), "")
        self.assertFalse(os.path.isdir(os.path.join(self.home, "tasks")))


@helpers.needs_git
class Hardening(helpers.Fixture):
    """Note text that reaches a subprocess argv only as a validated value."""

    def test_a_note_model_that_looks_like_an_option_never_reaches_claude(self):
        self.note("one", extra="paths: [src]\nmodel: --dangerously-skip-permissions\n")
        log = os.path.join(self.tmp, "f.log")
        p = self.cli("run", "--repo", self.repo, "--tasks", os.path.join(self.vault, "Tasks"), "--task-id", "h1",
                     "--claude-bin", helpers.FAKE_HEADLESS, env={"FAKE_LOG": log, "FAKE_SLEEP": "0.1"})
        self.assertFalse(os.path.exists(log), p.stdout + p.stderr)
        self.assertIn("model name '--dangerously-skip-permissions' is not valid",
                      read(os.path.join(self.vault, "Tasks", "one.md")))



class F12BranchNamesDoNotDependOnTheCurrentDirectory(unittest.TestCase):
    """gitprep.valid_branch ran `git check-ref-format --branch` in the caller's current
    directory: inside a worktree whose .git file points nowhere (a moved or copied worktree)
    every name, `main` included, was "not valid"; inside a repository `@{-1}` was accepted."""

    def test_names_are_judged_the_same_from_a_broken_worktree(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="pai-swarm-f12-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with open(os.path.join(tmp, ".git"), "w") as f:
            f.write("gitdir: %s\n" % os.path.join(tmp, "missing", "worktrees", "x"))
        here = os.getcwd()
        os.chdir(tmp)
        self.addCleanup(os.chdir, here)
        for name in ("main", "night/run1/one", "fix/owner-choice", "a-2"):
            self.assertTrue(rc.gitprep.valid_branch(name), name)
        for name in ("@{-1}", "HEAD", "-x", "--force", "bad..name", "a b", "x.lock", "", " main"):
            self.assertFalse(rc.gitprep.valid_branch(name), name)

if __name__ == "__main__":
    unittest.main()
