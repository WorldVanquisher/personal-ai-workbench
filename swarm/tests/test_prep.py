"""The headless runner goes through the same preparation as the RC launcher: a new task
fetches and branches from the fetched tip; a resume keeps its branch, base and edits."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git, commit  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import wtlock  # noqa: E402


def read(path):
    with open(path) as f:
        return f.read()


@helpers.needs_git
class Headless(helpers.Fixture):
    def setUp(self):
        super().setUp()
        self.tasks = os.path.join(self.vault, "Tasks")
        self.note("api", extra="paths: [src/api]\n")
        self.log = os.path.join(self.tmp, "fake.log")

    def run_swarm(self, task_id, *args, **env):
        e = {"FAKE_LOG": self.log, "FAKE_SLEEP": "0.1"}
        e.update(env)
        return self.cli("run", "--repo", self.repo, "--tasks", self.tasks, "--task-id", task_id,
                        "--claude-bin", helpers.FAKE_HEADLESS, *args, env=e)

    def workers_run(self):
        if not os.path.exists(self.log):
            return 0
        return len([l for l in read(self.log).splitlines() if not json.loads(l).get("extra")])

    def test_dry_run_is_read_only(self):
        tip = self.advance_main()
        before = git(self.repo, "for-each-ref")
        out = self.assert_ok(self.run_swarm("t1", "--dry-run"))
        self.assertIn("task      t1 (headless, NEW)", out)
        self.assertIn("main @ %s on the remote now" % tip, out)
        self.assertIn("branch   swarm/t1", out)
        self.assertEqual(git(self.repo, "for-each-ref"), before)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertFalse(os.path.exists(self.home))
        self.assertEqual(self.workers_run(), 0)

    def test_new_task_uses_the_fetched_remote_tip_not_the_stale_tracking_ref(self):
        tip = self.advance_main()
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), self.c0, "the local tracking ref is stale")
        out = self.run_swarm("t1").stdout
        self.assertIn("NEW git task: Git task t1: remote origin (%s), base main @ %s" % (self.origin, tip), out)
        self.assertIn("task branch swarm/t1, worktree %s" % self.repo, out)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "swarm/t1")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), tip)
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), tip)
        self.assertEqual(self.workers_run(), 1)
        note = [f for f in os.listdir(os.path.join(self.tasks, "_runs"))][0]
        text = read(os.path.join(self.tasks, "_runs", note))
        for line in ("git_task: t1", "remote: origin", "base_sha: " + tip, "branch: swarm/t1", "worktree: " + self.repo):
            self.assertIn(line, text)
        self.assertIn("swarm/t1", self.cli("tasks", "--repo", self.repo).stdout)
        self.assertIsNone(self._lock(), "the lock is released when the run ends")

    def _lock(self):
        os.environ["PAI_SWARM_HOME"] = self.home
        self.addCleanup(os.environ.pop, "PAI_SWARM_HOME", None)
        return wtlock.read(self.repo)

    def test_fetch_failure_starts_zero_workers(self):
        git(self.repo, "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        self.assert_refused(self.run_swarm("t1"), "git fetch origin main failed", "No worker was started")
        self.assertEqual(self.workers_run(), 0)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname)", "refs/heads/swarm"), "")
        self.assertIsNone(self._lock())
        self.assertIn("status: todo", read(os.path.join(self.tasks, "api.md")))

    def test_a_dirty_worktree_blocks_a_new_task_and_a_resume_keeps_its_edits(self):
        self.run_swarm("t1")
        self.assertEqual(self.workers_run(), 1)
        edit = os.path.join(self.repo, "src", "api", "api.txt")  # left uncommitted by the worker, as designed
        before = (read(edit), os.stat(edit).st_mtime_ns)
        tip = self.advance_main()
        self.note("web", extra="paths: [src/web]\n")
        out = self.assert_refused(self.run_swarm("t2"), "worktree is dirty", "Nothing is reset, cleaned or stashed",
                                  "No worker was started")
        self.assertEqual(self.workers_run(), 1)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "swarm/t1")
        self.assert_refused(self.run_swarm("t1", "--new"), "already started")
        # Resume: the remote has moved and is not even reachable; branch, base and edits stay.
        os.rename(self.origin, self.origin + ".offline")
        out = self.run_swarm("t1", "--resume").stdout
        self.assertIn("RESUME git task: Git task t1: remote origin (%s), base main @ %s" % (self.origin, self.c0), out)
        self.assertEqual(self.workers_run(), 2)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "swarm/t1")
        self.assertEqual(git(self.repo, "merge-base", "HEAD", self.c0), self.c0)
        self.assertNotEqual(git(self.repo, "rev-parse", "HEAD"), tip)
        self.assertEqual((read(edit), os.stat(edit).st_mtime_ns), before, "the earlier edit is untouched")
        self.note("docs", extra="paths: [docs]\n")
        self.assert_refused(self.run_swarm("t9", "--resume"), "nothing to resume")

    def test_branch_override_identity_and_in_progress_operations(self):
        self.assert_refused(self.run_swarm("t1", "--github", "someone/else"), "repository identity")
        self.assert_refused(self.run_swarm("t1", "--branch", "main"), "branch main already exists", "never reused")
        self.assert_refused(self.run_swarm("t1", "--expect-base", "1" * 40), "not the expected")
        self.assertEqual(self.workers_run(), 0)
        commit(self.repo, "README.md", "ours\n", "ours")
        git(self.repo, "branch", "theirs", self.c0)
        git(self.repo, "switch", "-q", "theirs")
        commit(self.repo, "README.md", "theirs\n", "theirs")
        git(self.repo, "switch", "-q", "main")
        git(self.repo, "merge", "theirs", check=False)
        self.assert_refused(self.run_swarm("t1"), "Git operation in progress (merge)")
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")), "the merge was not aborted")
        git(self.repo, "merge", "--abort")
        out = self.run_swarm("t1", "--branch", "feat/owner-named").stdout
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "feat/owner-named")
        self.assertEqual(self.workers_run(), 1)

    def test_a_worker_is_not_started_if_the_worktree_left_the_task_branch(self):
        self.run_swarm("t1")
        for leftover in git(self.repo, "status", "--porcelain").splitlines():
            os.remove(os.path.join(self.repo, leftover[3:].rstrip("/"))) if os.path.isfile(os.path.join(self.repo, leftover[3:])) else None
        git(self.repo, "switch", "-q", "main")
        self.note("web", extra="paths: [src/web]\n")
        self.assert_refused(self.run_swarm("t1"), "not the recorded task branch swarm/t1")
        self.assertEqual(self.workers_run(), 1)


@helpers.needs_git
class LockUnit(helpers.Fixture):
    def test_lock_ownership_and_stale_takeover(self):
        os.environ["PAI_SWARM_HOME"] = self.home
        self.addCleanup(os.environ.pop, "PAI_SWARM_HOME", None)
        wtlock.acquire(self.repo, "rc", "run1", "one")
        wtlock.acquire(self.repo, "rc", "run1", "one")  # the same lane may re-take it
        for other in (("rc", "run1", "two"), ("rc", "run2", "one"), ("headless", "run1", "one")):
            with self.assertRaises(wtlock.LockHeld):
                wtlock.acquire(self.repo, *other)
        self.assertFalse(wtlock.release(self.repo, "rc", "run2", "one"), "only the owner releases")
        self.assertTrue(wtlock.release(self.repo, "rc", "run1", "one"))
        wtlock.acquire(self.repo, "headless", "h1", "main")
        with self.assertRaises(wtlock.LockHeld):
            wtlock.acquire(self.repo, "headless", "h1", "main")  # a live run is never re-entered
        rec = wtlock.read(self.repo)
        rec["pid"] = 2 ** 22 + 12345  # a process that does not exist
        with open(wtlock.lock_path(self.repo), "w") as f:
            json.dump(rec, f)
        # The run's pid is gone, but its live flock is still held (here by this process, as
        # it would be by a worker the run started): not stale.
        self.assertFalse(wtlock.stale(wtlock.read(self.repo)))
        self.assertIn("a process it started still holds the worktree", wtlock.describe(wtlock.read(self.repo)))
        with self.assertRaises(wtlock.LockHeld):
            wtlock.acquire(self.repo, "rc", "run3", "x")
        os.close(wtlock._live.pop(os.path.realpath(self.repo)))  # the last holder exits
        self.assertTrue(wtlock.stale(wtlock.read(self.repo)))
        wtlock.acquire(self.repo, "rc", "run3", "x")  # a dead run's lock is taken over
        with open(wtlock.lock_path(self.repo), "w") as f:
            f.write("{ not json")
        with self.assertRaises(wtlock.LockHeld):  # an unreadable lock is a lock
            wtlock.acquire(self.repo, "rc", "run3", "x")


if __name__ == "__main__":
    unittest.main()
