"""pai round status: screen classification (captured and synthetic screens), /proc process
discovery, and one row per lane from an isolated tmux server running a fake `claude`."""
import contextlib
import io
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
sys.path.insert(0, SWARM)
sys.path.insert(0, os.path.dirname(SWARM))
import rc  # noqa: E402
import round as rnd  # noqa: E402
import round_status as rs  # noqa: E402

SCREENS = os.path.join(HERE, "fixtures", "round_screens")
IDENT = {"GIT_AUTHOR_NAME": "PAI Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
         "GIT_COMMITTER_NAME": "PAI Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
needs_tmux = unittest.skipUnless(shutil.which("git") and shutil.which("tmux") and os.path.isdir("/proc"),
                                 "needs git, tmux and /proc")


def screen(name):
    with open(os.path.join(SCREENS, name), encoding="utf-8") as f:
        return f.read()


class Classify(unittest.TestCase):
    def test_real_running_screen(self):
        self.assertEqual(rs.classify(screen("real-running-2.1.330.txt"))[0], "running")

    def test_real_idle_screen(self):
        self.assertEqual(rs.classify(screen("real-idle-2.1.327.txt"))[0], "idle")

    def test_trust_dialogs_of_each_version(self):
        for name in ("synthetic-trust-2.1.txt", "synthetic-trust-older.txt"):
            with self.subTest(name=name):
                self.assertTrue(rs.trust_prompt(screen(name)))
                self.assertEqual(rs.classify(screen(name))[0], "blocked-on-dialog")

    def test_quoting_the_dialog_is_not_the_dialog(self):
        s = screen("synthetic-quoted-in-transcript.txt")
        self.assertFalse(rs.trust_prompt(s))
        self.assertEqual(rs.classify(s)[0], "running")

    def test_marker_without_the_exit_option_is_not_the_dialog(self):
        self.assertFalse(rs.trust_prompt("Quick safety check\nYes, I trust this folder\n"))

    def test_colours_do_not_hide_the_dialog(self):
        s = screen("synthetic-trust-2.1.txt").replace("Quick safety", "\x1b[1mQuick\x1b[0m safety")
        self.assertTrue(rs.trust_prompt(s))

    def test_dialogs(self):
        self.assertEqual(rs.dialog(screen("synthetic-teach-auto-mode.txt")), "teach-auto-mode")
        self.assertEqual(rs.classify(screen("synthetic-teach-auto-mode.txt"))[0], "blocked-on-dialog")
        self.assertEqual(rs.dialog(screen("synthetic-trust-2.1.txt")), "trust")
        self.assertIsNone(rs.dialog(screen("real-running-2.1.330.txt")))
        quoted = "> 例如 Teach auto mode about your environment? Enter to confirm\n" + screen("real-ansi-busy-empty-2.1.330.txt")
        self.assertIsNone(rs.dialog(quoted))  # quoted above an input box: not a dialog

    def test_input_box_placeholder_suggestion_and_draft(self):
        box = rs.input_box(screen("real-ansi-placeholder-2.1.330.txt"))
        self.assertTrue(box["placeholder"] and box["dim"])
        self.assertIsNone(rs.draft(screen("real-ansi-placeholder-2.1.330.txt")))
        self.assertEqual(rs.classify(screen("real-ansi-placeholder-2.1.330.txt"))[0], "idle")
        sug = rs.input_box(screen("real-ansi-dim-suggestion-2.1.330.txt"))
        self.assertTrue(sug["dim"] and sug["text"])  # Claude's prompt suggestion, nobody typed it
        self.assertIsNone(rs.draft(screen("real-ansi-dim-suggestion-2.1.330.txt")))
        self.assertEqual(rs.input_box(screen("real-ansi-busy-empty-2.1.330.txt"))["text"], "")
        self.assertEqual(rs.classify(screen("real-ansi-busy-empty-2.1.330.txt"))[0], "running")
        d = rs.draft(screen("synthetic-ansi-owner-draft.txt"))
        self.assertTrue(d and d == sug["text"])
        self.assertEqual(rs.classify(screen("synthetic-ansi-owner-draft.txt"))[0], "owner-draft")

    def test_queued(self):
        self.assertEqual(rs.classify(screen("synthetic-queued.txt"))[0], "queued")
        self.assertTrue(rs.busy(screen("synthetic-queued.txt")))

    def test_dead_pane_and_missing_agent_are_exited(self):
        self.assertEqual(rs.classify("", pane_dead=True)[0], "exited")
        self.assertEqual(rs.classify(screen("real-idle-2.1.327.txt"), agent=False)[0], "exited")


class Flags(unittest.TestCase):
    def test_fresh_start_screen_and_pending_update(self):
        self.assertEqual(rs.screen_flags(screen("real-idle-2.1.327.txt")), {"fresh": True, "update": True})
        self.assertEqual(rs.screen_flags(screen("real-running-2.1.330.txt")), {"fresh": False, "update": False})

    def test_claude_record_for_exactly_this_pid(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "sessions"))
        with open(os.path.join(d, "sessions", "42.json"), "w") as f:
            json.dump({"pid": 42, "status": "idle", "version": "2.1.330", "name": "W R9 a", "bridgeSessionId": "session_x",
                       "sessionId": "s"}, f)
        with open(os.path.join(d, "sessions", "43.json"), "w") as f:
            json.dump({"pid": 99, "status": "busy"}, f)
        rec = rs.claude_record(42, d)
        self.assertEqual((rec["status"], rec["rc"], rec["version"]), ("idle", True, "2.1.330"))
        self.assertNotIn("session_x", json.dumps(rec))  # ids are not kept
        self.assertIsNone(rs.claude_record(43, d))  # a record of another process
        self.assertIsNone(rs.claude_record(44, d))


class Proc(unittest.TestCase):
    def test_session_name_and_claude_detection(self):
        self.assertEqual(rs.session_name(["claude", "--remote-control", "--name", "PAI R6 round"]), "PAI R6 round")
        self.assertEqual(rs.session_name(["claude", "--name=X"]), "X")
        self.assertTrue(rs.is_claude({"comm": "claude", "argv": []}))
        self.assertTrue(rs.is_claude({"comm": "node", "argv": ["node", "/opt/bin/claude"]}))
        self.assertFalse(rs.is_claude({"comm": "bash", "argv": ["bash", "-c", "echo claude"]}))

    def test_descendants_of_this_process(self):
        p = subprocess.Popen(["sleep", "30"])
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        procs = rs.processes()
        self.assertIn(p.pid, rs.descendants(procs, os.getpid()))

    def test_busy_in_sees_a_process_inside_a_folder(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = subprocess.Popen(["sleep", "30"], cwd=d)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.assertIn(p.pid, [pid for pid, _ in rs.busy_in(d)])


FAKE = """#!/usr/bin/env python3
import sys, time
sys.stdout.write(open(sys.argv[-1], encoding="utf-8").read())
sys.stdout.flush()
time.sleep(300)
"""


@needs_tmux
class Status(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prs", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sock = os.path.join(self.tmp, "tmux.sock")
        self.tmux = rc.Tmux(self.sock)
        self.addCleanup(lambda: subprocess.run(self.tmux.base + ["kill-server"], capture_output=True))
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        self.claude = os.path.join(bindir, "claude")
        with open(self.claude, "w") as f:
            f.write(FAKE)
        os.chmod(self.claude, 0o755)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, **IDENT)
        self.wt = os.path.join(self.tmp, "wt")
        subprocess.run(["git", "init", "-q", "-b", "main", self.wt], check=True, env=env)
        open(os.path.join(self.wt, "a"), "w").close()
        subprocess.run(["git", "-C", self.wt, "add", "a"], check=True, env=env)
        subprocess.run(["git", "-C", self.wt, "commit", "-q", "-m", "a"], check=True, env=env)
        self.run_tmux("new-session", "-d", "-s", "r9", "-n", "shell", "-x", "100", "-y", "40", "-c", self.wt, "sh")
        for win, fixture in (("alpha", "real-running-2.1.330.txt"), ("beta", "synthetic-trust-2.1.txt"),
                             ("gamma", "real-idle-2.1.327.txt")):
            self.run_tmux("new-window", "-t", "=r9", "-n", win, "-c", self.wt,
                          "%s --remote-control --name 'W R9 %s' %s" % (self.claude, win, os.path.join(SCREENS, fixture)))
        self.handoff = os.path.join(self.tmp, "inbox", "alpha")
        os.makedirs(os.path.join(self.handoff, "cp-A"))
        open(os.path.join(self.handoff, "cp-A", "RESTART.md"), "w").close()
        self.work = os.path.join(self.tmp, "work", "alpha")
        os.makedirs(self.work)
        with open(os.path.join(self.tmp, "items.txt"), "w") as f:
            f.write("X-001. one\nX-002. two\nX-003. three\n")
        from pai import workqueue  # noqa: F401  (the queue is made by the real command)
        subprocess.run([sys.executable, "-B", os.path.join(os.path.dirname(SWARM), "bin", "pai.py"), "queue", "init",
                        os.path.join(self.work, "queue"), "--from", os.path.join(self.tmp, "items.txt"), "--write"],
                       check=True, capture_output=True)
        subprocess.run([sys.executable, "-B", os.path.join(os.path.dirname(SWARM), "bin", "pai.py"), "queue", "mark",
                        os.path.join(self.work, "queue"), "X-001", "done", "--evidence", self.wt],
                       check=True, capture_output=True)
        lanes = []
        for name in ("alpha", "beta", "gamma", "shell", "delta"):
            lanes.append({"name": name, "tmux_target": "r9:%s" % name, "session_name": "W R9 %s" % name,
                          "worktree": self.wt if name != "delta" else os.path.join(self.tmp, "gone"),
                          "handoff_dir": os.path.join(self.tmp, "inbox", name), "work_dir": os.path.join(self.tmp, "work", name)})
        self.r = {"round": "R9", "project": "w", "base_sha": "0" * 40, "lanes": lanes}
        self.wait_for_screens()

    def run_tmux(self, *args):
        p = subprocess.run(self.tmux.base + ["-f", os.devnull] + list(args), capture_output=True, text=True)
        if p.returncode != 0:
            raise AssertionError(p.stderr)

    def wait_for_screens(self):
        deadline = time.time() + 10
        while time.time() < deadline:
            p = subprocess.run(self.tmux.base + ["capture-pane", "-p", "-t", "=r9:=gamma"], capture_output=True, text=True)
            if "auto mode on" in p.stdout:
                return
            time.sleep(0.1)

    def test_one_row_per_lane(self):
        rows = {x["lane"]: x for x in rs.status(self.r, tmux=self.tmux)}
        self.assertEqual(rows["alpha"]["state"], "running")
        self.assertEqual(rows["beta"]["state"], "blocked-on-dialog")
        self.assertEqual(rows["gamma"]["state"], "idle")
        self.assertEqual(rows["shell"]["state"], "exited")
        self.assertEqual(rows["delta"]["state"], "no-window")
        self.assertEqual(rows["alpha"]["agent_name"], "W R9 alpha")
        self.assertTrue(rows["alpha"]["name_ok"])
        self.assertEqual(rows["alpha"]["worktree"], "clean")
        self.assertEqual(rows["delta"]["worktree"], "missing")
        self.assertEqual(rows["alpha"]["checkpoint"]["name"], "cp-A")
        self.assertTrue(rows["alpha"]["checkpoint"]["restart"])
        self.assertEqual(rows["alpha"]["queue"]["done"], 1)
        self.assertEqual(rows["alpha"]["queue"]["todo"], 2)
        self.assertEqual(rows["alpha"]["queue"]["next"], "X-002")
        self.assertEqual((rows["alpha"]["queue"]["since_checkpoint"], rows["alpha"]["queue"]["checkpointed"]), (1, False))
        self.assertIsNone(rows["beta"]["queue"])

    def test_dirty_worktree_and_text_and_json(self):
        open(os.path.join(self.wt, "new"), "w").close()
        rows = rs.status(self.r, tmux=self.tmux)
        self.assertEqual(rows[0]["worktree"], "1 changed")
        text = rs.render(self.r, rows)
        self.assertIn("TRUST: answer it yourself", text)
        self.assertIn("done 1, prototype 0", text)
        data = json.loads(json.dumps(rs.as_json(self.r, rows)))
        self.assertEqual(data["format"], "pai-round-status")
        self.assertEqual([x["state"] for x in data["lanes"]], ["running", "blocked-on-dialog", "idle", "exited", "no-window"])

    def test_cli_json(self):
        path = os.path.join(self.tmp, "ROUND.json")
        r = dict(self.r, format="pai-round", version=1)
        with open(path, "w") as f:
            json.dump(r, f)
        out = io.StringIO()
        old = os.environ.get("PAI_ROUND_TMUX_SOCKET")
        os.environ["PAI_ROUND_TMUX_SOCKET"] = self.sock
        try:
            with contextlib.redirect_stdout(out):
                code = rnd.main(["status", path, "--json"])
        finally:
            if old is None:
                os.environ.pop("PAI_ROUND_TMUX_SOCKET")
            else:
                os.environ["PAI_ROUND_TMUX_SOCKET"] = old
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["lanes"][1]["state"], "blocked-on-dialog")


if __name__ == "__main__":
    unittest.main()
