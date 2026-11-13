"""RC launcher end to end: disposable repositories, a fake Claude, an isolated tmux server.
No live Remote Control session is started and your own tmux server is never contacted."""
import io
import json
import os
import signal
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import LANES, SWARM, git, commit  # noqa: E402

sys.path.insert(0, SWARM)
import rc  # noqa: E402


def read(path):
    with open(path) as f:
        return f.read()


@helpers.needs_tmux
class Startup(helpers.Fixture):
    def wt(self, lane):
        return os.path.join(self.root, lane)

    def branches(self):
        return git(self.repo, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads").splitlines()

    def test_plan_is_read_only(self):
        tip = self.advance_main()  # the local origin/main is now stale
        m = self.manifest()
        before = (git(self.repo, "for-each-ref"), git(self.repo, "worktree", "list"), sorted(os.listdir(self.root)))
        p = self.rc("plan", m)
        out = self.assert_ok(p)
        self.assertIn("PLAN ONLY", out)
        self.assertIn("main @ %s on the remote now" % tip, out)
        self.assertIn("(stale); it is not used", out)
        self.assertEqual(out.count("create worktree + new branch"), 4)
        self.assertEqual(before, (git(self.repo, "for-each-ref"), git(self.repo, "worktree", "list"), sorted(os.listdir(self.root))))
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), self.c0, "plan must not fetch")
        self.assertFalse(os.path.exists(self.home), "plan must not write state")
        self.assertFalse(os.path.exists(self.socket), "plan must not start a tmux server")
        self.assertEqual(self.starts(), [])

    def test_four_workers_start_on_new_branches_from_the_fetched_tip(self):
        tip = self.advance_main()  # stale local origin/main: the new task must not use it
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), self.c0)
        m = self.manifest()
        out = self.assert_ok(self.rc("up", m, "--new", "--no-input", "--settle", "1"))
        self.assertIn("fetched origin main: %s (local tracking ref was %s)" % (tip, self.c0), out)
        starts = self.starts(4)
        self.assertEqual(sorted(s["lane"] for s in starts), sorted(LANES))
        for s in starts:  # what each Claude process saw when it started
            self.assertEqual(s["cwd"], self.wt(s["lane"]))
            self.assertEqual(s["branch"], "night/run1/" + s["lane"])
            self.assertEqual(s["head"], tip)
            self.assertEqual(s["argv"], ["--remote-control", "--name", "T run1 " + s["lane"]])
            self.assertEqual(s["env"]["PAI_TASK_BASE_SHA"], tip)
            self.assertEqual(s["env"]["PAI_TASK_BRANCH"], "night/run1/" + s["lane"])
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), tip, "the tracking ref was updated explicitly")
        self.assertEqual(sorted(self.branches()), sorted(["main " + self.c0] + ["night/run1/%s %s" % (l, tip) for l in LANES]))
        self.assertEqual(sorted(self.windows("t-run1")), sorted(LANES))
        rows, code = self.status(m)
        self.assertEqual(code, 0)
        for lane in LANES:
            r = rows[lane]
            self.assertEqual((r["process"]["state"], r["rc"]["state"], r["lock"]), ("running", "verified", "this run"))
            self.assertEqual((r["remote"], r["base_sha"], r["branch"], r["worktree"]),
                             ("origin", tip, "night/run1/" + lane, self.wt(lane)))
        text = self.rc("status", m).stdout
        for needle in ("remote    origin %s" % self.origin, "base      main @ %s" % tip, "branch    night/run1/demo",
                       "worktree  %s" % self.wt("demo")):
            self.assertIn(needle, text)
        self.assertIn("Git task run1: remote origin", self.cli("tasks").stdout)
        self.assert_refused(self.rc("up", m, "--new", "--no-input"), "already started")

    def test_a_task_after_the_remote_owner_rewrote_main_uses_the_rewritten_tip(self):
        self.assert_ok(self.rc("up", self.manifest(lanes=("one", "two")), "--no-input", "--settle", "0.5"))
        new = self.rewrite_main()
        m2 = self.manifest(run_id="run2", lanes=("three", "four"))
        self.assert_ok(self.rc("up", m2, "--no-input", "--settle", "0.5"))
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), new)
        self.assertEqual(git(self.repo, "rev-parse", "night/run2/three", "night/run2/four").split(), [new, new])
        self.assertEqual(git(self.repo, "rev-parse", "night/run1/one"), self.c0, "the running task keeps its base")

    def test_rerun_restores_only_the_missing_window_and_keeps_edits(self):
        m = self.manifest()
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "1"))
        before, _ = self.status(m)
        for lane in ("demo", "release"):  # uncommitted worker edits, in the lost lane and in a live one
            with open(os.path.join(self.wt(lane), "README.md"), "a") as f:
                f.write("worker edit in %s\n" % lane)
            with open(os.path.join(self.wt(lane), "new-file.txt"), "w") as f:
                f.write("untracked\n")
        stamp = os.stat(os.path.join(self.wt("demo"), "README.md")).st_mtime_ns
        self.assertEqual(self.tmux("kill-window", "-t", "=t-run1:demo").returncode, 0)
        tip = self.advance_main()  # main moves on; the resumed task must not follow it
        plan = self.rc("plan", m).stdout
        self.assertEqual(plan.count("worker: keep (Claude is running)"), 3)
        self.assertEqual(plan.count("worker: start a window and Claude"), 1)
        self.assertIn("no fetch on resume", plan)
        out = self.assert_ok(self.rc("up", m, "--resume", "--settle", "1"))
        self.assertIn("RESUME", out)
        starts = self.starts(5)
        time.sleep(0.5)
        self.assertEqual(len(self.starts()), 5, "exactly one worker was started again")
        again = starts[-1]
        self.assertEqual((again["lane"], again["branch"], again["head"]), ("demo", "night/run1/demo", self.c0))
        after, code = self.status(m)
        self.assertEqual(code, 0)
        for lane in ("release", "extraction", "workbooks"):
            self.assertEqual(after[lane]["process"]["pid"], before[lane]["process"]["pid"], "live sessions are kept")
        self.assertNotEqual(after["demo"]["process"]["pid"], before["demo"]["process"]["pid"])
        for lane in ("demo", "release"):
            self.assertTrue(read(os.path.join(self.wt(lane), "README.md")).endswith("worker edit in %s\n" % lane))
            self.assertEqual(read(os.path.join(self.wt(lane), "new-file.txt")), "untracked\n")
            self.assertEqual(after[lane]["uncommitted"], 2)
        self.assertEqual(os.stat(os.path.join(self.wt("demo"), "README.md")).st_mtime_ns, stamp)
        self.assertEqual(after["demo"]["base_sha"], self.c0)
        self.assertNotEqual(git(self.repo, "rev-parse", "origin/main"), tip, "a resume does not fetch")

    def test_status_reports_the_process_and_rc_connectivity_separately(self):
        self.behavior(exit={"demo": 3}, no_bridge=["extraction"], notice=["workbooks"])
        m = self.manifest()
        p = self.rc("up", m, "--no-input", "--settle", "1")
        self.assertEqual(p.returncode, 1, "not every worker is up and verified")
        rows, code = self.status(m)
        end = time.time() + 10  # tmux can mark a pane dead a moment before it has the exit status
        while rows["demo"]["process"].get("exit") == "?" and time.time() < end:
            time.sleep(0.2)
            rows, code = self.status(m)
        self.assertEqual(code, 1)
        got = {l: (r["process"]["state"], r["rc"]["state"]) for l, r in rows.items()}
        self.assertEqual(got, {"release": ("running", "verified"), "extraction": ("running", "not-connected"),
                               "workbooks": ("running", "unverified"), "demo": ("exited", "not-running")})
        self.assertEqual(rows["demo"]["process"]["exit"], "3")
        text = self.rc("status", m).stdout
        self.assertIn("claude    running (pid %d)\n  rc        not-connected" % rows["extraction"]["process"]["pid"], text)
        self.assertIn("claude    exited (status 3)", text)
        # A rerun restarts the worker that exited, in its own window, and nothing else.
        self.behavior()
        self.rc("up", m, "--settle", "1")
        again, _ = self.status(m)
        self.assertEqual(again["demo"]["process"]["state"], "running")
        self.assertEqual(sorted(self.windows("t-run1")), sorted(LANES))
        for lane in ("release", "extraction", "workbooks"):
            self.assertEqual(again[lane]["process"]["pid"], rows[lane]["process"]["pid"])
        self.assertEqual([s["lane"] for s in self.starts()].count("demo"), 2)

    def test_a_window_without_claude_is_left_alone(self):
        m = self.manifest(lanes=("one", "two"))
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.5"))
        self.tmux("kill-window", "-t", "=t-run1:two")
        self.tmux("new-window", "-d", "-t", "=t-run1:", "-n", "two", "sleep 300")  # someone's own shell window
        pane = self.windows("t-run1")["two"][1]
        p = self.rc("up", m, "--settle", "0.3")
        self.assertEqual(p.returncode, 1)
        self.assertIn("LEFT ALONE  two", p.stdout)
        self.assertEqual(self.windows("t-run1")["two"][1], pane, "the window was not killed or respawned")
        self.assertEqual(len(self.starts()), 2)
        rows, _ = self.status(m)
        self.assertEqual((rows["two"]["process"]["state"], rows["two"]["rc"]["state"]), ("no-claude", "not-running"))

    def test_rc_handoff_files_a_draft_with_remote_base_branch_and_worktree(self):
        pai = os.path.join(os.path.dirname(SWARM), "bin", "pai.py")
        cfg = os.path.join(self.tmp, "pai.toml")
        os.makedirs(os.path.join(self.vault, "Agent Inbox", "PAI-001-MEM"))
        os.makedirs(os.path.join(self.vault, "30 Projects"))
        with open(os.path.join(self.vault, "Agent Inbox", "PAI-001-MEM", "decisions.md"), "w") as f:
            f.write("# Memory\n\n## Human decisions\n\n- None yet.\n")
        with open(os.path.join(self.vault, "30 Projects", "Demo.md"), "w") as f:
            f.write("---\nproject: demo\n---\n# Demo\n")
        py = os.environ.get("PAI_PYTHON") or sys.executable  # PAI needs 3.11+; swarm runs it the same way
        init = subprocess.run([py, "-B", pai, "--config", cfg, "init", "demo", "--repo", self.repo,
                               "--vault", self.vault, "--memory", "Agent Inbox/PAI-001-MEM", "--inbox", "Agent Inbox",
                               "--markdown-root", "30 Projects", "--project-note", "30 Projects/Demo.md", "--write"],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)
        m = self.manifest(lanes=("one", "two"))
        self.assert_refused(self.rc("handoff", m, "--pai", "demo", "--pai-bin", pai, "--pai-config", cfg), "has not been started")
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.5"))
        out = self.assert_ok(self.rc("handoff", m, "--pai", "demo", "--pai-bin", pai, "--pai-config", cfg))
        path = out.strip().split("draft handoff: ")[1]
        text = read(path)
        for needle in ("approved: false", "lane one: task run1, remote origin (%s), base main @ %s, task branch night/run1/one, "
                       "worktree %s" % (self.origin, self.c0, os.path.join(self.root, "one")),
                       "lane two: Claude process running; Remote Control verified", "0 ahead of base"):
            self.assertIn(needle, text)

    def test_release_refuses_a_live_lane_and_frees_a_stopped_one(self):
        m = self.manifest(lanes=("one", "two"))
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.5"))
        self.tmux("kill-window", "-t", "=t-run1:two")
        p = self.rc("release", m)
        self.assertEqual(p.returncode, 1)
        self.assertIn("one            KEPT", p.stdout)
        self.assertIn("two            lock released", p.stdout)
        rows, _ = self.status(m)
        self.assertEqual((rows["one"]["lock"], rows["two"]["lock"]), ("this run", "NOT HELD"))


@helpers.needs_tmux
class Refusals(helpers.Fixture):
    def nothing_started(self, run="run1"):
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.windows("t-" + run), {})
        self.assertNotIn("night/", git(self.repo, "for-each-ref", "--format=%(refname)", "refs/heads"))

    def test_a_failed_fetch_starts_zero_workers(self):
        git(self.repo, "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        m = self.manifest()
        self.assert_refused(self.rc("plan", m), "not reachable")
        out = self.assert_refused(self.rc("up", m, "--no-input"), "git fetch origin main failed", "No worker was started")
        self.assertIn("stopped before any worker was started", out)
        self.nothing_started()
        self.assertEqual(os.listdir(self.root), [])
        self.assertFalse(os.path.isdir(os.path.join(self.home, "tasks")), "no task was recorded")
        self.assertEqual(os.listdir(os.path.join(self.home, "locks")), [], "locks were given back")

    def test_expect_base_mismatch_starts_nothing(self):
        m = self.manifest()
        self.assert_refused(self.rc("up", m, "--no-input", "--expect-base", "1" * 40), "not the expected", "no worker was started")
        self.nothing_started()
        self.assertFalse(os.path.isdir(os.path.join(self.home, "tasks")))
        self.assert_ok(self.rc("up", m, "--no-input", "--expect-base", self.c0, "--settle", "0.3"))
        self.assertEqual(len(self.starts(4)), 4)

    def test_dirty_or_busy_worktrees_are_refused_before_any_lane_is_switched(self):
        for lane in ("extraction", "demo"):
            git(self.repo, "worktree", "add", "-q", "-b", "old/" + lane, os.path.join(self.root, lane), self.c0)
        dirty = os.path.join(self.root, "extraction", "wip.txt")
        with open(dirty, "w") as f:
            f.write("unsaved work\n")
        git(self.repo, "worktree", "add", "-q", "-b", "old/workbooks", os.path.join(self.root, "workbooks"), self.c0)
        with open(os.path.join(self.root, "workbooks", "README.md"), "a") as f:
            f.write("a tracked file, modified\n")
        demo = os.path.join(self.root, "demo")  # a merge stopped on a conflict
        commit(demo, "README.md", "ours\n", "ours")
        git(self.repo, "branch", "theirs", self.c0)
        git(self.repo, "worktree", "add", "-q", os.path.join(self.tmp, "side"), "theirs")
        commit(os.path.join(self.tmp, "side"), "README.md", "theirs\n", "theirs")
        git(demo, "merge", "theirs", check=False)
        m = self.manifest()
        out = self.assert_refused(self.rc("up", m, "--no-input"), "worktree is dirty (1 changed or untracked path(s), first: wip.txt)",
                                  "worktree is dirty (1 changed or untracked path(s), first: README.md)",
                                  "Git operation in progress (merge)", "Nothing was fetched, created, switched or started")
        self.assertIn("Nothing is reset, cleaned or stashed", out)
        self.nothing_started()
        self.assertEqual(read(dirty), "unsaved work\n")
        self.assertEqual(git(os.path.join(self.root, "extraction"), "symbolic-ref", "--short", "HEAD"), "old/extraction")
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git", "worktrees", "demo", "MERGE_HEAD")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "release")), "clean lanes were not prepared either")

    def test_a_clean_existing_worktree_is_switched_to_a_new_branch(self):
        wt = os.path.join(self.root, "release")
        git(self.repo, "worktree", "add", "-q", "-b", "old/release", wt, self.c0)
        old = commit(wt, "old.txt", "previous task\n", "previous task, committed")
        tip = self.advance_main()
        self.assert_ok(self.rc("up", self.manifest(lanes=("release",)), "--no-input", "--settle", "0.5"))
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD"), "night/run1/release")
        self.assertEqual(git(wt, "rev-parse", "HEAD"), tip)
        self.assertEqual(git(self.repo, "rev-parse", "old/release"), old, "the previous branch is left as it was")

    def test_duplicate_and_foreign_assignments_are_refused(self):
        def run(workers, *needles, **extra):
            d = json.loads(read(self.manifest(**extra)))
            d["workers"] = workers
            path = os.path.join(self.tmp, "bad.json")
            with open(path, "w") as f:
                json.dump(d, f)
            self.assert_refused(self.rc("up", path, "--no-input"), *needles)
            self.nothing_started()

        run([{"lane": "a", "worktree": "same", "branch": "x/a"}, {"lane": "b", "worktree": "same", "branch": "x/b"}],
            "duplicate worktree")
        run([{"lane": "a", "branch": "x/same"}, {"lane": "b", "branch": "x/same"}], "duplicate branch x/same")
        run([{"lane": "a", "branch": "x/a"}, {"lane": "a", "branch": "x/b"}], "duplicate lane name a")
        run([{"lane": "a", "worktree": os.path.join(self.tmp, "elsewhere"), "branch": "x/a"}], "outside the worktree root")
        run([{"lane": "a", "branch": "bad..name"}], "branch name 'bad..name' is not valid")
        run([{"lane": "a", "branch": "main"}], "branch main already exists", "never reused")
        os.makedirs(os.path.join(self.root, "plain"))
        run([{"lane": "plain", "branch": "x/p"}], "exists but is not a worktree of this repository")
        d = json.loads(read(self.manifest()))
        d["repository"]["github"] = "someone/else"
        path = os.path.join(self.tmp, "ident.json")
        with open(path, "w") as f:
            json.dump(d, f)
        self.assert_refused(self.rc("up", path, "--no-input"), "repository identity", "is not github.com/someone/else")
        self.nothing_started()

    def test_a_branch_of_another_task_is_never_reused(self):
        self.assert_ok(self.rc("up", self.manifest(lanes=("one",)), "--no-input", "--settle", "0.5"))
        d = json.loads(read(self.manifest(run_id="run2", lanes=("two",))))
        d["workers"][0]["branch"] = "night/run1/one"
        path = os.path.join(self.tmp, "run2.json")
        with open(path, "w") as f:
            json.dump(d, f)
        self.assert_refused(self.rc("up", path, "--no-input"), "branch night/run1/one already exists",
                            "belongs to task run1")
        self.assertEqual(len(self.starts()), 1)

    def test_preparation_that_stops_partway_resumes_on_the_pinned_base(self):
        wrapper = os.path.join(self.tmp, "git-fails-on-third-worktree")
        with open(wrapper, "w") as f:
            f.write("#!/bin/sh\ncase \" $* \" in *' worktree add '*)\n  n=$(cat %s/n 2>/dev/null || echo 0); n=$((n+1)); echo $n > %s/n\n"
                    "  [ $n -eq 3 ] && { echo 'fatal: injected failure' >&2; exit 128; };;\nesac\nexec git \"$@\"\n" % (self.tmp, self.tmp))
        os.chmod(wrapper, 0o755)
        m = self.manifest()
        out = self.assert_refused(self.rc("up", m, "--no-input", env={"PAI_SWARM_GIT": wrapper}),
                                  "lane demo: `git worktree add", "injected failure", "stopped before any worker was started")
        self.assertEqual(self.starts(), [], "partial preparation starts no worker")
        self.assertEqual(self.windows("t-run1"), {})
        rec = json.loads(read(os.path.join(self.home, "tasks", os.listdir(os.path.join(self.home, "tasks"))[0])))
        self.assertEqual({l: v["state"] for l, v in rec["lanes"].items()},
                         {"release": "prepared", "extraction": "prepared", "demo": "preparing", "workbooks": "planned"})
        self.assertEqual(rec["base_sha"], self.c0)
        with open(os.path.join(self.root, "release", "note.txt"), "w") as f:
            f.write("kept\n")
        self.advance_main()  # main moves between the failed start and the rerun
        self.assert_refused(self.rc("up", m, "--new", "--no-input"), "already started")
        out = self.assert_ok(self.rc("up", m, "--no-input", "--settle", "1"))
        self.assertIn("RESUME", out)
        starts = self.starts(4)
        self.assertEqual(sorted(s["lane"] for s in starts), sorted(LANES))
        self.assertEqual({s["head"] for s in starts}, {self.c0}, "every lane is on the one pinned base")
        self.assertEqual(read(os.path.join(self.root, "release", "note.txt")), "kept\n")

    def test_resume_refuses_a_worktree_that_left_its_task_branch(self):
        m = self.manifest(lanes=("one",))
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.5"))
        self.tmux("kill-window", "-t", "=t-run1:one")
        git(os.path.join(self.root, "one"), "switch", "-q", "-c", "wandered")
        out = self.assert_refused(self.rc("up", m), "not the recorded task branch night/run1/one", "switch night/run1/one")
        self.assertEqual(len(self.starts()), 1, "no Claude process on the wrong branch")
        self.assertEqual(git(os.path.join(self.root, "one"), "symbolic-ref", "--short", "HEAD"), "wandered")


@helpers.needs_tmux
class Locks(helpers.Fixture):
    def tasks(self):
        self.note("one", extra="paths: [src]\n")
        return os.path.join(self.vault, "Tasks")

    def test_headless_run_is_refused_in_a_worktree_an_rc_lane_holds(self):
        self.assert_ok(self.rc("up", self.manifest(lanes=("one",)), "--no-input", "--settle", "0.5"))
        log = os.path.join(self.tmp, "headless.log")
        p = self.cli("run", "--repo", os.path.join(self.root, "one"), "--tasks", self.tasks(), "--task-id", "h1",
                     "--claude-bin", helpers.FAKE_HEADLESS, env={"FAKE_LOG": log})
        self.assert_refused(p, "worktree is locked by Remote Control task run1, lane one", "No worker was started")
        self.assertFalse(os.path.exists(log), "the headless worker never ran")
        self.assertEqual(git(os.path.join(self.root, "one"), "symbolic-ref", "--short", "HEAD"), "night/run1/one")

    def test_rc_up_is_refused_while_a_headless_run_holds_the_worktree(self):
        wt = os.path.join(self.root, "one")
        git(self.repo, "worktree", "add", "-q", "-b", "scratch", wt, self.c0)
        log = os.path.join(self.tmp, "headless.log")
        e = dict(self.env, FAKE_LOG=log, FAKE_SLEEP="20")
        run = subprocess.Popen([sys.executable, "-B", helpers.CLI, "run", "--repo", wt, "--tasks", self.tasks(),
                                "--task-id", "h1", "--claude-bin", helpers.FAKE_HEADLESS],
                               env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            end = time.time() + 20
            while "swarm/h1" != git(wt, "symbolic-ref", "--short", "HEAD") and time.time() < end:
                time.sleep(0.1)
            m = self.manifest(lanes=("one", "two"))
            self.assert_refused(self.rc("up", m, "--no-input"), "worktree is locked by headless task h1, lane main",
                                "Nothing was fetched, created, switched or started")
            self.assertEqual(self.starts(), [])
            self.assertFalse(os.path.exists(os.path.join(self.root, "two")), "the free lane was not started either")
        finally:
            os.killpg(run.pid, signal.SIGKILL)  # a killed run leaves its lock file behind
            run.communicate()
        self.assertEqual(len(os.listdir(os.path.join(self.home, "locks"))), 1)
        # The dead run's lock is stale and is taken over, but its uncommitted work still blocks a new task.
        with open(os.path.join(wt, "left-by-headless.txt"), "w") as f:
            f.write("x\n")
        self.assert_refused(self.rc("up", m, "--no-input"), "worktree is dirty")
        os.remove(os.path.join(wt, "left-by-headless.txt"))
        for leftover in git(wt, "status", "--porcelain").splitlines():
            os.remove(os.path.join(wt, leftover[3:]))
        self.assert_ok(self.rc("up", m, "--no-input", "--settle", "0.5"))
        self.assertEqual(sorted(s["lane"] for s in self.starts(2)), ["one", "two"])


@helpers.needs_tmux
class Provisioning(helpers.Fixture):
    def test_worker_count_named_assignments_and_branch_overrides(self):
        for name, prio in (("alpha", 5), ("beta", 4), ("gamma", 3), ("delta", 2), ("epsilon", 1)):
            self.note(name, priority=prio)
        self.note("later", status="inbox")
        d = json.loads(read(self.manifest()))
        del d["workers"]
        d["tasks"] = os.path.join(self.vault, "Tasks")
        d["branch_template"] = "work/{run_id}/{lane}"
        path = os.path.join(self.tmp, "count.json")
        with open(path, "w") as f:
            json.dump(d, f)
        self.assertEqual(self.rc("plan", path).returncode, 2, "no list and no count")
        self.assert_refused(self.rc("plan", path, "--workers", "4", "--assign", "worker-9=alpha"), "does not have")
        self.assert_refused(self.rc("plan", path, "--workers", "4", "--assign", "worker-1=nope"), "task note 'nope' not found")
        self.assert_refused(self.rc("plan", path, "--workers", "4", "--assign", "worker-1=later"), "status inbox, not todo")
        args = ("--workers", "4", "--assign", "worker-2=epsilon", "--branch", "worker-1=feat/custom-name")
        self.assert_ok(self.rc("up", path, *args, "--no-input", "--settle", "0.5"))
        starts = {s["lane"]: s for s in self.starts(4)}
        self.assertEqual(sorted(starts), ["worker-1", "worker-2", "worker-3", "worker-4"])
        self.assertEqual(starts["worker-1"]["branch"], "feat/custom-name")
        self.assertEqual(starts["worker-3"]["branch"], "work/run1/worker-3")
        self.assertEqual({l: s["env"]["PAI_TASK_NOTE"] for l, s in starts.items()},
                         {"worker-1": "alpha", "worker-2": "epsilon", "worker-3": "beta", "worker-4": "gamma"})
        note = read(os.path.join(self.vault, "Tasks", "epsilon.md"))
        for line in ("status: running", "worker: worker-2", "branch: work/run1/worker-2", "base_sha: " + self.c0,
                     "remote: origin", "worktree: " + os.path.join(self.root, "worker-2")):
            self.assertIn(line, note)
        self.assertIn("status: todo", read(os.path.join(self.vault, "Tasks", "delta.md")), "the queue keeps the rest")
        # A rerun needs none of the overrides and never renames a recorded branch.
        self.assert_ok(self.rc("up", path, "--workers", "4", "--settle", "0.2"))
        self.assert_refused(self.rc("up", path, "--workers", "4", "--branch", "worker-1=other"), "a resume never renames it")
        self.assertEqual(self.rc("plan", path, "--workers", "3").returncode, 1, "a different set of workers is a new run")

    def test_interactive_runs_ask_for_each_branch_name(self):
        m = rc.Manifest(self.manifest(lanes=("one", "two", "three")), branch=["three=cli/given"])
        answers = iter(["feat/typed-by-operator\n", "\n"])
        out = io.StringIO()
        rc.ask_branches(m.workers, input_fn=lambda: next(answers), out=out)
        self.assertEqual([(w.branch, w.branch_source) for w in m.workers],
                         [("feat/typed-by-operator", "prompt"), ("night/run1/two", "manifest"), ("cli/given", "command line")])
        self.assertEqual(out.getvalue(), "Branch for worker one [night/run1/one]: Branch for worker two [night/run1/two]: ")


@helpers.needs_tmux
class Rollover(helpers.Fixture):
    """After GitHub reports the PR merged, the worker gets the next queued task on
    <stem>-2 from the new remote tip. Anything short of merged changes nothing."""

    def setUp(self):
        super().setUp()
        for name, prio in (("first", 9), ("second", 8), ("queued", 5), ("queued-low", 1)):
            self.note(name, priority=prio)
        self.m = self.manifest(lanes=("one", "two"), github="o/r", tasks=os.path.join(self.vault, "Tasks"))
        self.assert_ok(self.rc("up", self.m, "--no-input", "--settle", "0.5"))
        self.wt = os.path.join(self.root, "one")
        self.tip = commit(self.wt, "feature.txt", "work\n", "feat: the first task")
        git(self.wt, "push", "-q", "origin", "night/run1/one")

    def pr(self, state, head=None, merged=False):
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": [{"number": 7, "url": "https://github.example/o/r/pull/7", "state": state,
                                "mergedAt": "2026-11-12T10:00:00Z" if merged else None, "baseRefName": "main",
                                "headRefName": "night/run1/one", "headRefOid": head or self.tip}]}, f)

    def squash_merge(self):
        """What GitHub's squash merge leaves on main: one new commit, not the branch's commit."""
        git(self.upstream, "pull", "-q", "origin", "main")
        return commit(self.upstream, "feature.txt", "work\n", "feat: the first task (#7)")

    def nxt(self, *args, **kw):
        return self.cli("rc", "next", self.m, "--no-input", *args, **kw)

    def unchanged(self):
        self.assertEqual(git(self.wt, "symbolic-ref", "--short", "HEAD"), "night/run1/one")
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/night/run1/one-2"), "")
        self.assertIn("status: todo", read(os.path.join(self.vault, "Tasks", "queued.md")))
        rows, _ = self.status(self.m)
        self.assertEqual(rows["one"]["task_id"], "run1")

    def test_open_closed_or_absent_pr_does_not_roll_over(self):
        squash = self.squash_merge()
        git(self.upstream, "push", "-q", "origin", "main")  # main moved, but GitHub has not said "merged"
        for state, why in (("OPEN", "its PR is still open"), ("CLOSED", "its PR was closed without merging")):
            self.pr(state)
            self.assert_refused(self.nxt("--lane", "one"), "not rolled over: " + why)
            self.unchanged()
        os.remove(self.env["FAKE_GH_STATE"])
        self.assert_refused(self.nxt("--lane", "one"), "not rolled over: it has no PR")
        self.unchanged()
        self.pr("MERGED", head="0" * 40, merged=True)
        self.assert_refused(self.nxt("--lane", "one"), "commits that were not in the merged PR")
        self.unchanged()
        self.assertNotEqual(git(self.repo, "rev-parse", "origin/main"), squash, "nothing was fetched either")

    def test_merged_squash_pr_rolls_the_worker_to_the_next_queued_task(self):
        squash = self.squash_merge()
        git(self.upstream, "push", "-q", "origin", "main")
        self.pr("MERGED", merged=True)
        before, _ = self.status(self.m)
        plan = self.nxt("--lane", "one", "--plan")
        self.assertIn("PLAN ONLY", plan.stdout)
        self.unchanged()
        # Uncommitted work blocks the rollover and is preserved.
        with open(os.path.join(self.wt, "scratch.txt"), "w") as f:
            f.write("not committed\n")
        self.assert_refused(self.nxt("--lane", "one"), "NOT ROLLED OVER", "worktree is dirty", "any edits are unchanged")
        self.unchanged()
        self.assertEqual(read(os.path.join(self.wt, "scratch.txt")), "not committed\n")
        os.remove(os.path.join(self.wt, "scratch.txt"))
        out = self.assert_ok(self.nxt("--lane", "one"))
        self.assertIn("merged -> task queued on new branch night/run1/one-2", out)
        self.assertEqual(git(self.wt, "symbolic-ref", "--short", "HEAD"), "night/run1/one-2")
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), squash, "the next task starts from the updated origin/main")
        self.assertEqual(git(self.repo, "rev-parse", "night/run1/one"), self.tip, "the merged branch is left alone")
        rows, _ = self.status(self.m)
        self.assertEqual((rows["one"]["task_id"], rows["one"]["base_sha"], rows["one"]["note"]), ("run1.one.2", squash, "queued"))
        self.assertEqual(rows["one"]["process"]["pid"], before["one"]["process"]["pid"], "the Claude session is kept")
        self.assertEqual((rows["two"]["task_id"], rows["two"]["branch"]), ("run1", "night/run1/two"), "other workers are untouched")
        self.assertIn("status: done", read(os.path.join(self.vault, "Tasks", "first.md")))
        q = read(os.path.join(self.vault, "Tasks", "queued.md"))
        self.assertIn("status: running", q)
        self.assertIn("branch: night/run1/one-2", q)
        # The run still resumes as a whole, each lane on its own recorded task.
        self.assert_ok(self.rc("up", self.m, "--settle", "0.2"))
        self.assertEqual(len(self.starts()), 2)
        log = read(self.env["FAKE_GH_STATE"] + ".log")
        self.assertNotIn("merge", log.replace("mergedAt", ""))

    def test_operator_can_override_the_next_branch_and_task(self):
        git(self.upstream, "pull", "-q", "origin", "main")
        self.squash_merge()
        git(self.upstream, "push", "-q", "origin", "main")
        self.pr("MERGED", merged=True)
        git(self.repo, "branch", "night/run1/one-2", self.c0)  # the default suffix is taken
        out = self.nxt("--lane", "one", "--plan").stdout
        self.assertIn("night/run1/one-3", out)
        self.assert_ok(self.nxt("--lane", "one", "--task", "queued-low", "--branch", "one=fix/owner-choice"))
        self.assertEqual(git(self.wt, "symbolic-ref", "--short", "HEAD"), "fix/owner-choice")
        self.assertIn("status: running", read(os.path.join(self.vault, "Tasks", "queued-low.md")))
        self.assertIn("status: todo", read(os.path.join(self.vault, "Tasks", "queued.md")))


if __name__ == "__main__":
    unittest.main()
