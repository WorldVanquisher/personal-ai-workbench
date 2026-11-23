"""pai round launch / next with a fake `claude` on an isolated tmux server: the login-shell
command, idempotent starts, one bracketed prompt paste recorded as sending/typed/sent, never a
second paste, never a paste or Enter while the folder-trust dialog is up.

By tmux version: 3.8 and later report #{bracket_paste_flag}, so the paste goes through as
"reported by tmux". Below 3.8 the flag is unknown and the paste is let through only because the
pane runs Claude ("inferred from the command name"). LaunchTmux36 runs every test again through a
wrapper whose `tmux -V` says 3.6 and which blanks #{bracket_paste_flag}, as 3.6 does."""
import re
import json
import os
import shutil
import signal
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
import round_launch as rl  # noqa: E402
import round_status as rs  # noqa: E402

needs_tmux = unittest.skipUnless(shutil.which("tmux") and os.path.isdir("/proc") and shutil.which("bash"),
                                 "needs tmux, bash and /proc")


def tmux_version(binary):
    p = subprocess.run([binary, "-V"], capture_output=True, text=True)
    m = re.search(r"(\d+)\.(\d+)", p.stdout)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


OLD_TMUX = """#!%s
import os, sys
args = sys.argv[1:]
if args == ["-V"]:
    print("tmux 3.6")
    sys.exit(0)
os.execv(%r, [%r] + [a.replace("#{bracket_paste_flag}", "") for a in args])
"""


def fake_tmux_36(folder):
    """A `tmux` that reports 3.6 and cannot tell bracketed paste, around the real one."""
    real = shutil.which("tmux")
    path = os.path.join(folder, "tmux")
    with open(path, "w") as f:
        f.write(OLD_TMUX % (sys.executable, real, real))
    os.chmod(path, 0o755)
    return path


PROMPT = "你是 R9 的 worker。\n线名：alpha。\n第三行，带 \"引号\" 和 $HOME。\n"


@needs_tmux
class Launch(unittest.TestCase):
    OLD = False  # True: run through the 3.6 wrapper

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prl", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sock = os.path.join(self.tmp, "s")
        self.fake = os.path.join(self.tmp, "fake")
        os.makedirs(self.fake)
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        self.claude = os.path.join(bindir, "claude")
        shutil.copy(os.path.join(HERE, "fake_claude_round.py"), self.claude)
        os.chmod(self.claude, 0o755)
        env = {"PAI_ROUND_CLAUDE": self.claude, "PAI_ROUND_SHELL": shutil.which("bash"), "FAKE_ROUND_DIR": self.fake,
               "HOME": self.tmp, "PAI_SWARM_TMUX": shutil.which("tmux")}
        if getattr(self, "OLD", False):
            env["PAI_SWARM_TMUX"] = fake_tmux_36(bindir)
        self.old_tmux = tmux_version(env["PAI_SWARM_TMUX"]) < (3, 8)
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        self.addCleanup(self.kill_fakes)
        self.tmux = rc.Tmux(self.sock)  # after the environment: the tmux server inherits it
        self.addCleanup(lambda: subprocess.run(self.tmux.base + ["kill-server"], capture_output=True))
        self.wt = os.path.join(self.tmp, "wt")
        os.makedirs(self.wt)
        work = os.path.join(self.tmp, "work", "alpha")
        os.makedirs(work)
        self.prompt_file = os.path.join(work, "prompt.txt")
        with open(self.prompt_file, "w", encoding="utf-8") as f:
            f.write(PROMPT)
        self.ln = {"name": "alpha", "worktree": self.wt, "tmux_target": "r9:alpha", "session_name": "W R9 alpha",
                   "work_dir": work, "handoff_dir": os.path.join(self.tmp, "inbox", "alpha"),
                   "prompt_file": self.prompt_file, "prompt_sha256": rnd.sha256_file(self.prompt_file)}
        self.r = {"round": "R9", "project": "w", "lanes": [self.ln]}
        self.lines = []

    def kill_fakes(self):
        for pid, p in rs.processes().items():
            if p["argv"][1:2] == [self.claude] or (p["argv"][:1] == [self.claude]):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def behave(self, **kw):
        with open(os.path.join(self.fake, "behavior.json"), "w") as f:
            json.dump(kw, f)

    def jsonl(self, name):
        try:
            with open(os.path.join(self.fake, name)) as f:
                return [json.loads(l) for l in f]
        except FileNotFoundError:
            return []

    def launch(self, write=True, paste=False, wait=10.0):
        return rl.launch(self.r, self.ln, write, paste, wait, self.lines.append, tmux=self.tmux)

    def wait_until(self, fn, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if fn():
                return True
            time.sleep(0.1)
        return False

    def test_preview_starts_nothing(self):
        self.assertEqual(self.launch(write=False), 0)
        text = "\n".join(self.lines)
        self.assertIn("--remote-control --name", text)
        self.assertIn("W R9 alpha", text)
        self.assertIn("-lc", text)
        self.assertIn("new session", text)
        self.assertFalse(self.tmux.has_session("r9"))

    def test_start_once_in_the_worktree_with_a_login_shell(self):
        self.assertEqual(self.launch(), 0, self.lines)
        starts = self.jsonl("starts.jsonl")
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["argv"], ["--remote-control", "--name", "W R9 alpha"])
        self.assertEqual(os.path.realpath(starts[0]["cwd"]), os.path.realpath(self.wt))
        self.assertEqual(self.launch(), 0)
        self.assertIn("already running", "\n".join(self.lines))
        self.assertEqual(len(self.jsonl("starts.jsonl")), 1)
        row = rs.lane_status(self.ln, self.tmux, rs.processes())
        self.assertEqual((row["state"], row["agent_name"], row["name_ok"]), ("idle", "W R9 alpha", True))

    def test_prompt_is_pasted_once_bracketed_then_entered(self):
        self.assertEqual(self.launch(paste=True), 0, self.lines)
        self.assertTrue(self.wait_until(lambda: self.jsonl("received.jsonl")))
        got = self.jsonl("received.jsonl")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["bracketed"])
        self.assertEqual(got[0]["text"].replace("\r", "\n"), PROMPT.rstrip("\n"))
        rec = rl.load_records(self.ln)["entries"]
        self.assertEqual([e["state"] for e in rec.values()], ["sent"])
        how = list(rec.values())[0]["bracketed_by"]
        if self.old_tmux:  # below 3.8: inferred from the program, and said so
            self.assertTrue(how.startswith("inferred from the command name"), how)
            self.assertIsNone(list(rec.values())[0]["bracketed"])
        else:
            self.assertEqual(how, "reported by tmux")
        # again: the record is final, nothing is pasted twice
        self.assertEqual(self.launch(paste=True), 1)
        self.assertIn("NOT SENT: this prompt was already sent", "\n".join(self.lines))
        time.sleep(0.8)
        self.assertEqual(len(self.jsonl("pasted.jsonl")), 1)

    def test_trust_dialog_is_reported_never_answered_never_pasted_into(self):
        self.behave(trust=True)
        self.assertEqual(self.launch(paste=True), 1)
        text = "\n".join(self.lines)
        self.assertIn("TRUST", text)
        self.assertIn("pai never answers it", text)
        time.sleep(0.5)
        self.assertEqual(self.jsonl("pasted.jsonl"), [])
        self.assertEqual(self.jsonl("received.jsonl"), [])
        self.assertEqual(rl.load_records(self.ln)["entries"], {})
        # a direct delivery is refused as well, and records nothing
        self.lines.clear()
        self.assertEqual(rl.deliver(self.tmux, self.ln, "1", "next", wait=0.5, log=self.lines.append), 1)
        self.assertIn("folder-trust dialog is on screen", "\n".join(self.lines))
        self.assertEqual(rl.load_records(self.ln)["entries"], {})
        row = rs.lane_status(self.ln, self.tmux, rs.processes())
        self.assertEqual(row["state"], "blocked-on-dialog")  # still waiting for the owner

    def test_an_unknown_record_is_never_resent(self):
        self.assertEqual(self.launch(), 0)
        key = "prompt:" + rnd.sha256_bytes(PROMPT.encode("utf-8"))[:16]
        rl.save_record(self.ln, key, state="unknown", why="test")
        self.assertEqual(rl.paste_lane_prompt(self.tmux, self.ln, 2.0, self.lines.append), 1)
        self.assertIn("never repeated", "\n".join(self.lines))
        self.assertEqual(self.jsonl("pasted.jsonl"), [])

    def test_a_changed_prompt_file_is_not_pasted(self):
        self.assertEqual(self.launch(), 0)
        with open(self.prompt_file, "a", encoding="utf-8") as f:
            f.write("tampered\n")
        self.assertEqual(rl.paste_lane_prompt(self.tmux, self.ln, 2.0, self.lines.append), 1)
        self.assertIn("changed since pai round new rendered it", "\n".join(self.lines))

    def test_a_shell_only_window_is_respawned(self):
        self.assertEqual(self.launch(), 0)
        pid = self.jsonl("starts.jsonl")[0]["pid"]
        os.kill(pid, signal.SIGTERM)
        self.assertTrue(self.wait_until(lambda: rs.lane_status(self.ln, self.tmux, rs.processes())["state"] == "exited"))
        self.assertEqual(self.launch(), 0, self.lines)
        self.assertIn("respawn", "\n".join(self.lines))
        self.assertEqual(len(self.jsonl("starts.jsonl")), 2)

    def test_next_builds_and_sends_a_short_message(self):
        cp = os.path.join(self.ln["handoff_dir"], "cp-B")
        os.makedirs(cp)
        with open(os.path.join(cp, "RESTART.md"), "w", encoding="utf-8") as f:
            f.write("# RESTART\n\n有意停在 cp-B，下一个是 R9-ALP-025。\n")
        msg, nid, path = rl.next_message(self.r, self.ln)
        self.assertEqual(nid, "R9-ALP-025")
        self.assertIn(path, msg)
        self.assertIn("cp-B", msg)
        self.assertLess(len(msg), 400)
        self.assertEqual(self.launch(), 0)
        self.assertEqual(rl.deliver(self.tmux, self.ln, msg, "next", enter=False, wait=5, log=self.lines.append), 0)
        self.assertTrue(self.wait_until(lambda: self.jsonl("pasted.jsonl")))
        self.assertEqual(self.jsonl("received.jsonl"), [])  # typed, not submitted
        self.assertEqual([e["state"] for e in rl.load_records(self.ln)["entries"].values()], ["typed"])

    def test_next_without_restart(self):
        with self.assertRaises(rnd.RoundError):
            rl.next_message(self.r, self.ln)


class LaunchTmux36(Launch):
    OLD = True


class Bracketing(unittest.TestCase):
    """rl.bracketing() decides without a pane: tmux's answer when it has one, else the program."""
    def decide(self, flag, cmd, agent_pid=None, allow=False):
        old = rl.panes.info, rl.pane_command
        self.addCleanup(lambda: (setattr(rl.panes, "info", old[0]), setattr(rl, "pane_command", old[1])))
        rl.panes.info = lambda tmux, pane: {"bracketed": flag}
        rl.pane_command = lambda tmux, pane: cmd
        return rl.bracketing(None, "%1", {"agent_pid": agent_pid}, allow)

    def test_tmux_38_answer_is_used_as_is(self):
        self.assertEqual(self.decide(True, "bash"), (False, "reported by tmux"))
        self.assertFalse(self.decide(False, "claude")[0])  # tmux says off: refused even for claude
        self.assertTrue(self.decide(False, "claude", allow=True)[0])

    def test_below_38_claude_node_codex_are_inferred(self):
        for cmd in ("claude", "node", "codex"):
            ok, how = self.decide(None, cmd)
            self.assertTrue(ok)
            self.assertIn("inferred from the command name (pane_current_command=%s" % cmd, how)
        ok, how = self.decide(None, "bash", agent_pid=4242)  # pai round launch: claude under a login shell
        self.assertTrue(ok)
        self.assertIn("Claude process pid 4242", how)

    def test_below_38_other_programs_need_allow_unbracketed(self):
        ok, why = self.decide(None, "vim")
        self.assertFalse(ok)
        self.assertIn("--allow-unbracketed", why)
        ok, how = self.decide(None, "vim", allow=True)
        self.assertTrue(ok)
        self.assertTrue(how.startswith("--allow-unbracketed"))

    def test_the_wrapper_reports_36(self):
        if not shutil.which("tmux"):
            self.skipTest("needs tmux")
        import tempfile as tf
        d = tf.mkdtemp(prefix="prl36", dir="/tmp")
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(tmux_version(fake_tmux_36(d)), (3, 6))


if __name__ == "__main__":
    unittest.main()
