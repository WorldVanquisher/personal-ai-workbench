"""panes.py (pai paste, pai web) against a real tmux server on its own socket, with a raw-mode
program (fake_term_app.py) that records every byte it receives. No agent, network or your own
tmux server is involved."""
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
sys.path.insert(0, HERE)
import agents  # noqa: E402
import helpers  # noqa: E402
import panes  # noqa: E402
import rc  # noqa: E402

APP = os.path.join(HERE, "fake_term_app.py")
needs_tmux = unittest.skipUnless(shutil.which("tmux") and os.name == "posix", "needs tmux")
CLAUDE_TRUST = agents.get("claude").waiting_for_trust
FLAG = helpers.tmux_reports_bracketed_paste()  # tmux 3.8 reports bracket_paste_flag; 3.5a cannot
SAYS = (lambda v: v) if FLAG else (lambda v: None)  # what panes reports: the flag, or None (cannot tell)


@needs_tmux
class Panes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-panes-", dir="/tmp" if len(tempfile.gettempdir()) > 60 else None)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sock = os.path.join(self.tmp, "t.sock")
        self.tmux = rc.Tmux(self.sock)
        self.addCleanup(lambda: subprocess.run(self.tmux.base + ["kill-server"], capture_output=True))
        self.n = 0

    def app(self, bracketed=True, trust_after=False, colour=False):
        self.n += 1
        log = os.path.join(self.tmp, "app%d.log" % self.n)
        env = "FAKE_APP_LOG=%s FAKE_APP_BRACKETED=%d FAKE_APP_TRUST_AFTER=%d FAKE_APP_COLOUR=%d" % (
            log, bracketed, trust_after, colour)
        first = ["new-session", "-d", "-s", "t", "-x", "100", "-y", "30"] if self.n == 1 else ["new-window", "-d", "-t", "t:"]
        p = subprocess.run(self.tmux.base + ["-f", os.devnull] + first + ["-P", "-F", "#{pane_id}", "env %s %s -B %s" % (
            env, sys.executable, APP)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        pane = p.stdout.strip()
        self.wait(lambda: "fake app ready" in self.tmux.capture(pane))
        return pane, log

    def wait(self, fn, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if fn():
                return True
            time.sleep(0.05)
        self.fail("timed out")

    def got(self, log, settle=0.4):
        time.sleep(settle)
        if not os.path.exists(log):
            return b""
        with open(log, "rb") as f:
            return f.read()

    def test_multi_line_text_arrives_as_one_bracketed_paste_and_nothing_is_submitted(self):
        pane, log = self.app(bracketed=True)
        text = "Plan:\r\n1. read the note\n2. run the tests — 中文\n\n\tindented\n"
        if not FLAG:  # this tmux cannot tell: several lines need the caller's word
            with self.assertRaises(panes.Refused) as c:
                panes.paste(self.tmux, pane, text, trust=CLAUDE_TRUST)
            self.assertEqual(c.exception.code, "bracketed-unknown")
            self.assertEqual(self.got(log, 0.2), b"")
        res = panes.paste(self.tmux, pane, text, trust=CLAUDE_TRUST, allow_unbracketed=not FLAG)
        self.assertEqual((res["lines"], res["bracketed"], res["enter"]), (5, SAYS(True), False), res)
        want = "Plan:\r1. read the note\r2. run the tests — 中文\r\r\tindented"  # the last newline is not Enter
        self.assertEqual(self.got(log), b"\x1b[200~" + want.encode() + b"\x1b[201~")
        self.assertEqual(subprocess.run(self.tmux.base + ["list-buffers"], capture_output=True, text=True).stdout, "",
                         "the buffer is deleted after the paste")

    def test_escape_sequences_in_the_text_cannot_end_the_paste_early(self):
        pane, log = self.app(bracketed=True)
        res = panes.paste(self.tmux, pane, "safe\x1b[201~\x03rm -rf ~\x07\x9b\n", allow_unbracketed=True)
        self.assertIn("4 control character(s) removed", res["notes"])
        self.assertEqual(self.got(log), b"\x1b[200~safe[201~rm -rf ~\x1b[201~")

    def test_enter_only_when_asked_and_never_once_a_trust_prompt_shows(self):
        pane, log = self.app(bracketed=True)
        res = panes.paste(self.tmux, pane, "one line", enter=True, trust=CLAUDE_TRUST)
        self.assertTrue(res["enter"])
        self.assertEqual(self.got(log), b"\x1b[200~one line\x1b[201~\r")
        pane, log = self.app(bracketed=True, trust_after=True)
        res = panes.paste(self.tmux, pane, "another", enter=True, trust=CLAUDE_TRUST)
        self.assertFalse(res["enter"])
        self.assertIn("Enter was NOT pressed", " ".join(res["notes"]))
        self.assertEqual(self.got(log), b"\x1b[200~another\x1b[201~")

    def test_a_visible_trust_prompt_refuses_the_paste(self):
        pane, log = self.app(bracketed=True, trust_after=True)
        panes.send_keys(self.tmux, pane, [{"text": "x"}])  # anything: the app now shows its trust prompt
        self.wait(lambda: CLAUDE_TRUST(self.tmux.capture(pane)))
        before = self.got(log)
        with self.assertRaises(panes.Refused) as c:
            panes.paste(self.tmux, pane, "yes\n", enter=True, trust=CLAUDE_TRUST)
        self.assertEqual(c.exception.code, "trust-prompt")
        self.assertEqual(self.got(log), before, "nothing reached the prompt")

    def test_multi_line_text_to_a_program_without_bracketed_paste_is_refused_unless_asked(self):
        pane, log = self.app(bracketed=False)
        with self.assertRaises(panes.Refused) as c:
            panes.paste(self.tmux, pane, "a\nb")
        self.assertEqual(c.exception.code, "not-bracketed" if FLAG else "bracketed-unknown")
        self.assertEqual(self.got(log), b"")
        res = panes.paste(self.tmux, pane, "single line\n")
        self.assertEqual(res["bracketed"], SAYS(False))
        self.assertEqual(self.got(log), b"single line", "a file's last newline is not Enter")
        res = panes.paste(self.tmux, pane, "a\nb", allow_unbracketed=True)
        self.assertEqual(self.got(log), b"single linea\rb")

    def test_typing_sends_exact_text_and_only_known_keys(self):
        pane, log = self.app(bracketed=True)
        panes.send_keys(self.tmux, pane, [{"text": "a;b"}, {"text": ";"}, {"key": "Enter"}, {"text": "-x é中\\;"},
                                          {"key": "C-c"}, {"key": "Up"}, {"key": "M-x"}, {"text": "z\n\x1b"}])
        self.assertEqual(self.got(log), "a;b;\r-x é中\\;\x03\x1b[A\x1bxz".encode())
        for bad in ({"key": "rm -rf"}, {"key": "M-;"}, {"key": "C-c; kill-server"}, "Enter", {"key": None}):
            with self.assertRaises(panes.Refused):
                panes.send_keys(self.tmux, pane, [{"text": "never"}, bad])
        self.assertEqual(self.got(log, 0.2), "a;b;\r-x é中\\;\x03\x1b[A\x1bxz".encode(), "a refused batch sends nothing")

    def test_copy_mode_and_size_limits_refuse_before_sending(self):
        pane, log = self.app(bracketed=True)
        with self.assertRaises(panes.Refused) as c:
            panes.paste(self.tmux, pane, "x" * (panes.MAX_PASTE + 1))
        self.assertEqual(c.exception.code, "too-large")
        subprocess.run(self.tmux.base + ["copy-mode", "-t", pane], check=True)
        with self.assertRaises(panes.Refused) as c:
            panes.paste(self.tmux, pane, "x")
        self.assertEqual(c.exception.code, "copy-mode")
        with self.assertRaises(panes.Refused) as c:
            panes.paste(self.tmux, "%999", "x")
        self.assertEqual(c.exception.code, "no-pane")
        self.assertEqual(self.got(log, 0.2), b"")

    def test_the_screen_keeps_its_colours_cursor_and_size(self):
        pane, _ = self.app(bracketed=False, colour=True)
        s = panes.screen(self.tmux, pane)
        self.assertIn("\x1b[1m\x1b[31mred bold\x1b[0m plain", s["text"].replace("\x1b[1;31m", "\x1b[1m\x1b[31m"))
        self.assertEqual((s["cursor"], s["size"], s["bracketed"]), ([2, 2], [100, 30], SAYS(False)))
        self.assertIsNone(panes.screen(self.tmux, "%999"))


if __name__ == "__main__":
    unittest.main()
