"""The branch prompts of `rc up` and `rc next` on a real pseudo-terminal.

Both commands ask for branch names only when stdin is a terminal, which no pipe-based test
reaches. Here the command line runs under pty.fork(): the child is a session leader with
the pty as its controlling terminal, so Enter, Ctrl-D and Ctrl-C go through the kernel's
line discipline exactly as they do for an operator, and the prompts are read back from the
master side. Everything else is the usual fixture: disposable repositories, fake Claude,
fake gh and an isolated tmux server on its own -S socket."""
import errno
import json
import os
import select
import signal
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import CLI, git, commit  # noqa: E402

try:
    import pty
except ImportError:  # not POSIX
    pty = None

needs_pty = unittest.skipUnless(pty is not None and sys.platform.startswith("linux"), "needs a Linux pty")
ENTER, CTRL_C, CTRL_D = b"\r", b"\x03", b"\x04"


class Terminal:
    """One command line on a pseudo-terminal: expect() what it prints, send() keystrokes."""

    def __init__(self, test, args, env):
        argv = [sys.executable, "-B", CLI] + [str(a) for a in args]
        env = dict(env, TERM="dumb")
        pid, fd = pty.fork()
        if pid == 0:  # child: never return into the test runner
            try:
                os.execve(argv[0], argv, env)
            finally:
                os._exit(127)
        self.pid, self.fd, self.raw, self.seen, self.code = pid, fd, b"", 0, None
        test.addCleanup(self.close)

    @property
    def text(self):
        return self.raw.decode("utf-8", "replace").replace("\r\n", "\n")

    def _read(self, timeout):
        """False on timeout, None at end of output, True after reading something."""
        if not select.select([self.fd], [], [], max(timeout, 0))[0]:
            return False
        try:
            data = os.read(self.fd, 65536)
        except OSError as e:  # Linux reports the closed slave side as EIO
            if e.errno != errno.EIO:
                raise
            data = b""
        if not data:
            return None
        self.raw += data
        return True

    def expect(self, needle, timeout=30):
        """Wait for `needle` after the previous match; returns what came before it."""
        end = time.time() + timeout
        while True:
            at = self.text.find(needle, self.seen)
            if at >= 0:
                before, self.seen = self.text[self.seen:at], at + len(needle)
                return before
            got = self._read(end - time.time())
            if not got:
                raise AssertionError("%s waiting for %r; output so far:\n%s"
                                     % ("timed out" if got is False else "output ended", needle, self.text))

    def quiet(self, seconds=0.5):
        """What arrives in the next `seconds` (the command should be waiting for input)."""
        start, end = len(self.text), time.time() + seconds
        while time.time() < end and self._read(end - time.time()):
            pass
        return self.text[start:]

    def send(self, data):
        os.write(self.fd, data)

    def wait(self, timeout=90):
        """Exit status (negative for a signal) once the command has finished."""
        end = time.time() + timeout
        while self._read(end - time.time()):
            pass
        while time.time() < end:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.code = os.waitstatus_to_exitcode(status)
                self._drain()
                return self.code
            time.sleep(0.05)
        raise AssertionError("command did not finish within %ss; output:\n%s" % (timeout, self.text))

    def _drain(self):
        while self._read(0.1):
            pass

    def close(self):
        if self.code is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
            self.code = "killed"
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@needs_pty
@helpers.needs_tmux
class UpPrompt(helpers.Fixture):
    LANES = ("one", "two", "three")

    def setUp(self):
        super().setUp()
        self.m = self.manifest(lanes=self.LANES)

    def term(self, *args):
        return Terminal(self, args, self.env)

    def heads(self):
        return git(self.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()

    def records(self):
        d = os.path.join(self.home, "tasks")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def assert_nothing_started(self, t, tip):
        self.assertEqual(self.heads(), ["main"], t.text)
        self.assertEqual(os.listdir(self.root), [], "no worktree was created")
        self.assertEqual(self.records(), [], "no task was recorded")
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), self.c0, "nothing was fetched")
        self.assertNotEqual(tip, self.c0)
        self.assertFalse(os.path.exists(self.socket), "no tmux server was started")
        self.assertEqual(self.starts(), [])
        self.assertNotIn("Traceback", t.text)

    def test_enter_keeps_the_default_a_typed_name_replaces_it_and_a_given_name_is_not_asked(self):
        t = self.term("rc", "up", self.m, "--branch", "three=cli/three", "--settle", "0.3")
        t.expect("Branch for worker one [night/run1/one]: ")
        self.assertNotIn("Branch for worker two", t.quiet(), "one prompt at a time, then wait for the answer")
        t.send(ENTER)
        t.expect("Branch for worker two [night/run1/two]: ")
        t.send(b"mine/two" + ENTER)
        self.assertEqual(t.wait(), 0, t.text)
        self.assertNotIn("Branch for worker three", t.text, "a name from the command line is not asked about")
        self.assertIn("mine/two\n", t.text, "the terminal echoed the typed name")
        want = {"one": "night/run1/one", "two": "mine/two", "three": "cli/three"}
        self.assertEqual({s["lane"]: s["branch"] for s in self.starts(3)}, want, "each Claude started on its branch")
        self.assertEqual(sorted(self.heads()), sorted(["main"] + list(want.values())))
        rows, _ = self.status(self.m)
        self.assertEqual({lane: r["branch"] for lane, r in rows.items()}, want, "the record keeps the typed name")
        self.assertNotIn("Traceback", t.text)

    def test_ctrl_d_at_a_prompt_starts_nothing_even_after_earlier_answers(self):
        tip = self.advance_main()
        t = self.term("rc", "up", self.m, "--settle", "0.3")
        t.expect("Branch for worker one [night/run1/one]: ")
        t.send(b"kept/one" + ENTER)
        t.expect("Branch for worker two [night/run1/two]: ")
        t.send(CTRL_D)
        self.assertEqual(t.wait(), 1, t.text)
        self.assertIn("rc up: no answer to the branch prompt; nothing was started", t.text)
        self.assert_nothing_started(t, tip)

    def test_ctrl_c_at_a_prompt_starts_nothing(self):
        tip = self.advance_main()
        t = self.term("rc", "up", self.m, "--settle", "0.3")
        t.expect("Branch for worker one [night/run1/one]: ")
        t.send(CTRL_C)
        self.assertEqual(t.wait(), 130, t.text)
        self.assertIn("rc up: interrupted at the branch prompt; nothing was started", t.text)
        self.assert_nothing_started(t, tip)

    def test_an_invalid_typed_name_is_refused_before_anything_is_created(self):
        tip = self.advance_main()
        t = self.term("rc", "up", self.m, "--settle", "0.3")
        for lane, answer in (("one", b"bad..name"), ("two", b"--force"), ("three", b"")):
            t.expect("Branch for worker %s [night/run1/%s]: " % (lane, lane))
            t.send(answer + ENTER)
        self.assertEqual(t.wait(), 1, t.text)
        self.assertIn("branch name 'bad..name' is not valid", t.text)
        self.assertIn("branch name '--force' is not valid", t.text)
        self.assertIn("rc up: REFUSED. Nothing was fetched, created, switched or started.", t.text)
        self.assert_nothing_started(t, tip)

    def test_no_input_and_a_resumed_run_never_prompt(self):
        t = self.term("rc", "up", self.m, "--no-input", "--settle", "0.3")
        self.assertEqual(t.wait(), 0, t.text)  # a prompt would block until the timeout
        self.assertNotIn("Branch for worker", t.text)
        self.assertEqual(len(self.starts(3)), 3)
        self.tmux("kill-window", "-t", "=t-run1:two")
        t = self.term("rc", "up", self.m, "--settle", "0.3")
        self.assertEqual(t.wait(), 0, t.text)
        self.assertNotIn("Branch for worker", t.text, "a resume keeps the recorded branches without asking")
        self.assertIn("RESUME", t.text)
        self.assertEqual(sorted(s["lane"] for s in self.starts(4)), ["one", "three", "two", "two"])


@needs_pty
@helpers.needs_tmux
class NextPrompt(helpers.Fixture):
    """`rc next` asks for each rolled-over worker's next branch on a terminal."""

    def setUp(self):
        super().setUp()
        for name, prio in (("first", 9), ("second", 8), ("queued-a", 5), ("queued-b", 4)):
            self.note(name, priority=prio)
        self.m = self.manifest(lanes=("one", "two"), github="o/r", tasks=os.path.join(self.vault, "Tasks"))
        self.assert_ok(self.rc("up", self.m, "--no-input", "--settle", "0.3"))
        prs = []
        for n, lane in enumerate(("one", "two"), 7):
            wt = os.path.join(self.root, lane)
            tip = commit(wt, "%s.txt" % lane, "work\n", "feat: %s" % lane)
            git(wt, "push", "-q", "origin", "night/run1/" + lane)
            prs.append({"number": n, "url": "https://github.example/o/r/pull/%d" % n, "state": "MERGED",
                        "mergedAt": "2026-11-12T10:00:00Z", "baseRefName": "main",
                        "headRefName": "night/run1/" + lane, "headRefOid": tip})
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": prs}, f)

    def heads(self, lane):
        return git(os.path.join(self.root, lane), "symbolic-ref", "--short", "HEAD")

    def test_typed_name_and_default_are_used(self):
        t = Terminal(self, ["rc", "next", self.m, "--plan"], self.env)
        self.assertEqual(t.wait(), 0, t.text)  # read-only: a prompt would block until the timeout
        self.assertNotIn("Branch for worker", t.text)
        self.assertIn("PLAN ONLY", t.text)
        t = Terminal(self, ["rc", "next", self.m], self.env)
        t.expect("Branch for worker one, next task queued-a [night/run1/one-2]: ")
        t.send(b"next/one" + ENTER)
        t.expect("Branch for worker two, next task queued-b [night/run1/two-2]: ")
        t.send(ENTER)
        self.assertEqual(t.wait(), 0, t.text)
        self.assertEqual((self.heads("one"), self.heads("two")), ("next/one", "night/run1/two-2"))
        self.assertNotIn("Traceback", t.text)

    def test_ctrl_d_or_ctrl_c_at_a_later_prompt_rolls_over_no_worker(self):
        for key, code, why in ((CTRL_D, 1, "no answer"), (CTRL_C, 130, "interrupted")):
            with self.subTest(key=key):
                t = Terminal(self, ["rc", "next", self.m], self.env)
                t.expect("Branch for worker one, next task queued-a [night/run1/one-2]: ")
                t.send(ENTER)
                t.expect("Branch for worker two, next task queued-b [night/run1/two-2]: ")
                t.send(key)
                self.assertEqual(t.wait(), code, t.text)
                self.assertIn("rc next: %s at the branch prompt; no worker was rolled over" % why, t.text)
                self.assertNotIn("Traceback", t.text)
                self.assertEqual((self.heads("one"), self.heads("two")), ("night/run1/one", "night/run1/two"))
                rows, _ = self.status(self.m)
                self.assertEqual({r["task_id"] for r in rows.values()}, {"run1"}, "no task was replaced")
                for name in ("queued-a", "queued-b"):
                    with open(os.path.join(self.vault, "Tasks", name + ".md")) as f:
                        self.assertIn("status: todo", f.read())


if __name__ == "__main__":
    unittest.main()
