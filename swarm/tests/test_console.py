"""`pai web` and `pai paste` end to end with fakes only: a real `pai run` of fake Claude lanes on
an isolated tmux server (lane b stops at its folder-trust prompt), the console started through the
real launcher on 127.0.0.1, and a lane shell that is a raw-mode recorder (fake_term_app.py), so
the exact bytes a paste delivers can be checked. Nothing here answers a trust prompt."""
import http.client
import json
import os
import signal
import subprocess
import sys
import time
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, PAI, needs_311  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FLAG = helpers.tmux_reports_bracketed_paste()  # tmux 3.8 does; 3.5a cannot tell


@helpers.needs_tmux
@needs_311
class Web(WB):
    def setUp(self):
        super().setUp()
        self.note("task-a", priority=9)
        self.note("task-b", priority=8)
        self.init("claude")
        self.behavior(trust=["b"])
        p = self.pai("run", "--workers", "2", "--yes", "--wait", "8")
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        self.assertTrue(self.wait_for(lambda: self.received("a")), "lane a got its task")
        self.shell_log = os.path.join(self.tmp, "shell.log")
        self.fake_shell = os.path.join(self.bin, "fake-shell")
        with open(self.fake_shell, "w") as f:
            f.write("#!/bin/sh\nFAKE_APP_LOG=%s FAKE_APP_BRACKETED=1 exec %s -B %s\n" % (
                self.shell_log, sys.executable, os.path.join(HERE, "fake_term_app.py")))
        os.chmod(self.fake_shell, 0o755)

    # -- the console process and its HTTP API
    def start(self):
        env = dict(self.env, SHELL=self.fake_shell)
        for k in ("DISPLAY", "WAYLAND_DISPLAY"):
            env.pop(k, None)
        p = subprocess.Popen([sys.executable, "-B", PAI, "web", "--no-browser"], cwd=self.repo, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait(), p.stdout.close(), p.stderr.close()))
        lines = []
        while not any("open:" in l for l in lines):
            line = p.stdout.readline()
            if not line:
                self.fail("pai web printed no link: %s" % p.stderr.read())
            lines.append(line)
        url = [l for l in lines if "open:" in l][0].split("open:")[1].strip()
        self.port = int(url.split(":")[2].split("/")[0])
        self.link = url.split(str(self.port), 1)[1]
        return p, lines

    def req(self, method, path, body=None, cookie=None, csrf=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        if cookie:
            h["Cookie"] = cookie
        if csrf:
            h["X-PAI-CSRF"] = csrf
        if body is not None:
            body = json.dumps(body)
            h["Content-Type"] = "application/json"
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            data = json.loads(data)
        except ValueError:
            pass
        return r.status, dict(r.getheaders()), data

    def sign_in(self):
        st, hdrs, _ = self.req("GET", self.link)
        self.assertEqual(st, 303)
        self.cookie = hdrs["Set-Cookie"].split(";")[0]
        self.csrf = self.req("GET", "/api/session", cookie=self.cookie)[2]["csrf"]

    def get(self, path):
        st, _, data = self.req("GET", path, cookie=self.cookie)
        return st, data

    def post(self, path, body):
        st, _, data = self.req("POST", path, body, cookie=self.cookie, csrf=self.csrf)
        return st, data

    def shell_bytes(self, settle=0.5):
        time.sleep(settle)
        if not os.path.exists(self.shell_log):
            return b""
        with open(self.shell_log, "rb") as f:
            return f.read()

    # -- tests
    def test_the_console_shows_the_lanes_and_types_and_pastes_only_where_allowed(self):
        p, lines = self.start()
        out = "".join(lines)
        self.assertIn("run %s" % json.loads(self.pai("status", "--json").stdout)["run_id"], out)
        self.sign_in()
        self.assertEqual(self.req("GET", self.link)[0], 401, "the link signs in once")
        self.assertIn(b'id="screen"', self.req("GET", "/", cookie=self.cookie)[2])
        self.assertIn(b'id="text"', self.req("GET", "/notes", cookie=self.cookie)[2])

        st, d = self.get("/api/lanes")
        self.assertEqual(st, 200, d)
        lanes = {l["lane"]: l for l in d["lanes"]}
        self.assertEqual((lanes["a"]["trust"], lanes["b"]["trust"]), (False, True), lanes)
        self.assertEqual(lanes["a"]["note_link"], {"root": 0, "path": "task-a.md"})
        self.assertIn("task-a.md", self.get("/api/files?root=0")[1]["files"])

        st, s = self.get("/api/screen?lane=a")
        self.assertEqual(st, 200, s)
        self.assertIn("fake claude ready", s["text"])
        self.assertFalse(s["trust"])
        st, s = self.get("/api/screen?lane=b")
        self.assertTrue(s["trust"], s)

        # lane b asks for folder trust: no paste reaches it (the operator answers it, by typing)
        st, d = self.post("/api/paste", {"lane": "b", "text": "yes", "enter": True})
        self.assertEqual(st, 409)
        self.assertIn("trust-prompt", d["error"])
        time.sleep(0.5)
        self.assertTrue(self.get("/api/screen?lane=b")[1]["trust"], "the prompt is still there")
        self.assertEqual(self.received("b"), [])

        # live typing into lane a's agent: text, then Enter
        st, d = self.post("/api/keys", {"lane": "a", "keys": [{"text": "hello from the web; it works"}, {"key": "Enter"}]})
        self.assertEqual(st, 200, d)
        self.assertTrue(self.wait_for(lambda: any(r["text"] == "hello from the web; it works" for r in self.received("a"))))
        # the fake agent never asked for bracketed paste: several lines are refused, one line goes in
        st, d = self.post("/api/paste", {"lane": "a", "text": "line one\nline two"})
        self.assertEqual(st, 409)
        self.assertRegex(d["error"], "^(not-bracketed|bracketed-unknown): ")
        st, d = self.post("/api/paste", {"lane": "a", "text": "pasted and sent", "enter": True})
        self.assertEqual((st, d["enter"]), (200, True), d)
        self.assertTrue(self.wait_for(lambda: any(r["text"] == "pasted and sent" for r in self.received("a"))))

        # a shell in lane a's worktree (here: the recorder), with a real bracketed paste
        self.assertEqual(self.get("/api/screen?lane=a&target=shell")[0], 404)
        st, d = self.post("/api/shell/open", {"lane": "a"})
        self.assertEqual((st, d["created"]), (200, True), d)
        self.assertEqual(d["cwd"], lanes["a"]["worktree"], "the shell starts in the lane's worktree")
        self.assertFalse(self.post("/api/shell/open", {"lane": "a"})[1]["created"], "opened once")
        self.assertTrue(self.wait_for(lambda: "fake app ready" in self.get("/api/screen?lane=a&target=shell")[1].get("text", "")))
        st, d = self.post("/api/paste", {"lane": "a", "target": "shell", "text": "git status\nmake test\n",
                                         "allow_unbracketed": not FLAG})
        self.assertEqual((st, d["bracketed"], d["enter"]), (200, True if FLAG else None, False), d)
        self.assertEqual(self.shell_bytes(), b"\x1b[200~git status\rmake test\x1b[201~")
        st, d = self.post("/api/keys", {"lane": "a", "target": "shell", "keys": [{"text": "x;y"}, {"key": "C-c"}]})
        self.assertEqual(st, 200, d)
        self.assertTrue(self.shell_bytes().endswith(b"x;y\x03"))
        self.assertTrue({l["lane"]: l for l in self.get("/api/lanes")[1]["lanes"]}["a"]["shell"])
        self.assertEqual(self.post("/api/shell/close", {"lane": "a"}), (200, {"closed": True}))

        # what the browser cannot do
        self.assertEqual(self.req("GET", "/api/lanes")[0], 401, "signed in only")
        self.assertEqual(self.req("POST", "/api/keys", {"lane": "a", "keys": [{"key": "Enter"}]}, cookie=self.cookie)[0], 403,
                         "a write needs the CSRF header")
        self.assertEqual(self.req("GET", "/api/lanes", cookie=self.cookie, host="evil.example:%d" % self.port)[0], 403)
        self.assertEqual(self.get("/api/screen?lane=" + urllib.parse.quote("%1"))[0], 404, "a lane, never a pane id")
        self.assertEqual(self.post("/api/keys", {"lane": "a", "keys": [{"key": "kill-server"}]})[0], 400)
        self.assertEqual(self.post("/api/keys", {"lane": "a", "keys": "Enter"})[0], 400)
        self.assertEqual(self.post("/api/save", {"root": 0, "path": "../x.md", "text": "", "base": "", "confirm": ""})[0], 400)
        self.assertEqual(self.received("b"), [], "lane b's trust prompt was never answered")

        p.send_signal(signal.SIGINT)
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertIn("stopped", p.stdout.read())

    def test_pai_paste_from_the_command_line(self):
        msg = os.path.join(self.tmp, "msg.md")
        with open(msg, "w") as f:
            f.write("from pai paste\n")
        p = self.pai("paste", "a", msg)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("nothing submitted", p.stdout)
        time.sleep(0.6)
        self.assertFalse(any(r["text"] == "from pai paste" for r in self.received("a")), "no Enter without --enter")
        self.tmux("send-keys", "-t", self.pane("a"), "C-u")  # the operator clears the input line
        p = self.pai("paste", "a", msg, "--enter")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Enter pressed", p.stdout)
        self.assertTrue(self.wait_for(lambda: any(r["text"] == "from pai paste" for r in self.received("a"))))

        p = self.pai("paste", "b", msg, "--enter")
        self.assertEqual(p.returncode, 1)
        self.assertIn("folder-trust prompt", p.stdout)
        self.assertIn("Nothing was sent", p.stdout)
        with open(msg, "w") as f:
            f.write("first\nsecond\n")
        p = self.pai("paste", "a", msg)
        self.assertEqual(p.returncode, 1)
        self.assertIn("--allow-unbracketed", p.stdout)
        p = self.pai("paste", "a", msg, "--allow-unbracketed", "--enter")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.wait_for(lambda: [r["text"] for r in self.received("a")][-2:] == ["first", "second"]))
        self.assertEqual(self.received("b"), [])
        p = self.pai("paste", "a")  # stdin is empty here
        self.assertEqual(p.returncode, 1)
        self.assertIn("nothing to paste", p.stdout)


if __name__ == "__main__":
    unittest.main()
