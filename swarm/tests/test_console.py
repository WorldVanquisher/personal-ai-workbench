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


class Page(unittest.TestCase):
    """048-049 hooks in the shipped console page (static checks; the real-browser check of focus,
    announcements and a 390 px wide screen is the owner's kit, see PAI-1.0-READINESS-HANDOFF-050.md)."""

    def read(self, name):
        with open(os.path.join(helpers.SWARM, "mdserver", "static", name), encoding="utf-8") as f:
            return f.read()

    def test_announcements_focus_and_narrow_layout(self):
        html, css, js = self.read("console.html"), self.read("console.css"), self.read("console.js")
        for needle in ('id="summary" class="summary" role="status" aria-live="polite"',
                       'id="lane-status" class="lane-status" role="status" aria-live="polite"',
                       'id="lane-title" tabindex="-1"', 'id="inbox" class="inbox" role="status" aria-live="polite"',
                       'id="staging" class="staging" role="group" aria-labelledby="staging-text"',
                       'id="recover-title" tabindex="-1"', 'aria-controls="recover-panel"', 'aria-describedby="compose-size"'):
            self.assertIn(needle, html)
        self.assertNotIn("<button>", html, "every button has an explicit type")
        self.assertIn('if (byUser) $("lane-title").focus();', js)
        self.assertIn('if (text === lastStatus) return; // announce a change once, not every poll', js)
        for panel in ("staging", "recover-panel"):
            self.assertIn('$("%s").addEventListener("keydown", (e) => { if (e.key === "Escape")' % panel, js)
        self.assertIn('b.setAttribute("aria-label", "lane " + l.lane', js, "a lane's state is read out with its name")
        self.assertIn(".inbox.staged { border: 2px dashed", css)
        self.assertIn(".inbox.submitted { border: 2px solid", css)
        self.assertIn('content: "STAGED"', css)
        self.assertIn('content: "SUBMITTED"', css)
        self.assertIn(":focus-visible", css)
        self.assertIn(".lane-head h2, .lane-status, .inspect, .plans, .report, .staging p, .inbox { overflow-wrap: anywhere; }", css)
        narrow = css.split("@media (max-width: 760px)")[1]
        self.assertIn("grid-template-columns: minmax(0, 1fr)", narrow)


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

    def test_run_state_recovery_paste_preview_and_stale_lanes_in_the_console(self):
        """041-047: the console's names and explanations are pai status's; resume is a separate,
        previewed action; a paste shows destination and size first; closed panes and stale lane
        addresses get a concrete next action; the new endpoints keep the same local-only rules."""
        self.start()
        self.sign_in()
        # compare once lane a is idle again: a busy agent turns running -> delivered between two reads
        self.assertTrue(self.wait_for(lambda: {l["lane"]: l for l in json.loads(self.pai("status", "--json").stdout)[
            "lanes"]}["a"]["lifecycle"]["state"] == "delivered", 15))
        st, d = self.get("/api/lanes")
        self.assertEqual(st, 200, d)
        web = {l["lane"]: l for l in d["lanes"]}
        cli = json.loads(self.pai("status", "--json").stdout)
        for row in cli["lanes"]:
            self.assertEqual(web[row["lane"]]["lifecycle"]["state"], row["lifecycle"]["state"])
            self.assertEqual(web[row["lane"]]["task"], row["ids"]["task"])
        self.assertEqual(d["summary"]["delivery"], cli["summary"]["delivery"])
        self.assertEqual(web["b"]["lifecycle"]["state"], "waiting-for-trust")
        self.assertEqual((web["b"]["recovery"]["action"], web["b"]["recovery"]["human"]), ("trust", True))
        rec = json.loads(self.pai("recover", "--json").stdout)
        st, prev = self.get("/api/recover")
        self.assertEqual(st, 200, prev)
        self.assertFalse(prev["changed"])
        self.assertEqual([(x["lane"], x["action"]) for x in prev["lanes"]], [(x["lane"], x["action"]) for x in rec["lanes"]])
        st, ins = self.get("/api/inspect?lane=a")
        self.assertEqual(st, 200, ins)
        self.assertEqual({e["kind"] for e in ins["evidence"]} >= {"run", "task", "process", "dispatch", "window"}, True)

        # paste preview: where it goes and how big it is; nothing is sent
        before = len(self.received("a"))
        text = "résumé 中文 line\nsecond line"
        st, pv = self.post("/api/paste/preview", {"lane": "a", "text": text, "enter": True})
        self.assertEqual(st, 200, pv)
        self.assertEqual((pv["lane"], pv["target"], pv["program"], pv["chars"], pv["lines"], pv["bytes"], pv["submit"]),
                         ("a", "agent", "Claude", len(text), 2, len(text.encode()), True))
        self.assertTrue(pv["pane"].startswith("%"))
        st, pv = self.post("/api/paste/preview", {"lane": "b", "text": "yes"})
        self.assertEqual((pv["ok"], pv["code"]), (False, "trust-prompt"))
        time.sleep(0.5)
        self.assertEqual(len(self.received("a")), before, "a preview sends nothing")
        self.assertEqual(self.received("b"), [])

        # the new endpoints follow the same rules as the old ones
        self.assertEqual(self.req("GET", "/api/recover")[0], 401, "signed in only")
        self.assertEqual(self.req("GET", "/api/inspect?lane=a")[0], 401)
        self.assertEqual(self.req("POST", "/api/recover", {"confirm": True}, cookie=self.cookie)[0], 403, "CSRF header")
        self.assertEqual(self.req("POST", "/api/paste/preview", {"lane": "a", "text": "x"}, cookie=self.cookie)[0], 403)
        self.assertEqual(self.req("GET", "/api/recover", cookie=self.cookie, host="evil.example:%d" % self.port)[0], 403)
        self.assertEqual(self.post("/api/recover", {})[0], 400, "applying needs confirm: true after the preview")

        # a stale lane address and a closed pane say what to do next
        st, e = self.get("/api/screen?lane=zz")
        self.assertEqual(st, 404)
        self.assertIn("reload this page", e["error"])
        pane = self.pane("a")
        self.tmux("kill-pane", "-t", pane)
        self.assertTrue(self.wait_for(lambda: self.get("/api/screen?lane=a")[0] == 404, 10))
        st, e = self.get("/api/screen?lane=a")
        self.assertIn("Resume run (preview)", e["error"])
        self.assertIn("pai recover --run", e["error"])
        st, e = self.post("/api/paste", {"lane": "a", "text": "hello"})
        self.assertEqual(st, 404, "the cached pane id is never used for a write")
        st, prev = self.get("/api/recover")
        plans = {x["lane"]: x for x in prev["lanes"]}
        self.assertEqual(plans["a"]["action"], "restart")
        self.assertEqual(prev["apply"], ["a"])
        st, res = self.post("/api/recover", {"confirm": True, "wait": 8})
        self.assertEqual(st, 200, res)
        self.assertIn("DONE     lane a  restart", res["report"])
        self.assertIn("action    trust [HUMAN]", res["report"], "lane b's trust prompt stays a human action")
        self.assertEqual(self.received("b"), [], "the trust lane was not touched")
        self.assertEqual(self.get("/api/screen?lane=a")[0], 200, "the restarted lane is shown again")

    def test_paste_and_handoff_state_are_the_cli_state_and_risky_actions_stay_explicit(self):
        """R2-051..R2-054, R2-059: one surface shows what `pai inspect`/`pai status`/`pai recover`
        show (paste evidence, handed-off lanes); prepared text stays apart from submitted text; a
        cancelled preview changes nothing; recovery in the browser never touches a handed-off lane
        and a handoff is never written from the browser; the console listens on 127.0.0.1 only."""
        import glob
        self.start()
        self.sign_in()
        self.assertTrue(self.wait_for(lambda: {l["lane"]: l for l in json.loads(self.pai("status", "--json").stdout)[
            "lanes"]}["a"]["lifecycle"]["state"] == "delivered", 15))
        listen = []
        for f in ("/proc/net/tcp", "/proc/net/tcp6"):
            with open(f) as fh:
                for row in fh.read().splitlines()[1:]:
                    local, state = row.split()[1], row.split()[3]
                    if state == "0A" and int(local.split(":")[1], 16) == self.port:
                        listen.append(local.split(":")[0])
        self.assertEqual(listen, ["0100007F"], "loopback only")

        def pastes_cli():
            return [e for e in json.loads(self.pai("inspect", "a", "--json").stdout)["evidence"] if e["kind"] == "paste"]

        def pastes_web():
            return [e for e in self.get("/api/inspect?lane=a")[1]["evidence"] if e["kind"] == "paste"]

        before = len(self.received("a"))
        st, pv = self.post("/api/paste/preview", {"lane": "a", "text": "Draft only, then cancelled"})
        self.assertEqual(st, 200, pv)
        self.assertEqual(glob.glob(os.path.join(self.home, "pastes", "*")), [], "a preview leaves no record")
        self.assertEqual(pastes_web(), [])
        st, r = self.post("/api/paste", {"lane": "a", "text": "Please also update the changelog", "enter": False})
        self.assertEqual(st, 200, r)
        time.sleep(0.5)
        self.assertEqual(len(self.received("a")), before, "prepared, not submitted")
        self.assertEqual([e["state"] for e in pastes_web()], ["staged"])
        self.assertEqual(pastes_web(), pastes_cli(), "the same evidence on both surfaces")
        st, r = self.post("/api/keys", {"lane": "a", "keys": [{"key": "Enter"}]})
        self.assertEqual(st, 200, r)
        self.assertTrue(self.wait_for(lambda: len(self.received("a")) == before + 1, 10))
        self.assertTrue(self.wait_for(lambda: [e["state"] for e in pastes_cli()] == ["delivered"], 10),
                        "submitted later with Enter: the transcript shows it")
        self.assertEqual([e["state"] for e in pastes_web()], ["delivered"])

        self.assert_ok(self.pai("cleanup", "--lane", "a", "--apply", "--stop-running"))
        self.assert_ok(self.pai("recover", "--continue", "a", "--write"))
        cli = {l["lane"]: l for l in json.loads(self.pai("status", "--json").stdout)["lanes"]}
        web = {l["lane"]: l for l in self.get("/api/lanes")[1]["lanes"]}
        self.assertEqual(web["a"]["lifecycle"], cli["a"]["lifecycle"])
        self.assertEqual(web["a"]["lifecycle"]["state"], "handed-off")
        rec = {x["lane"]: x["action"] for x in json.loads(self.pai("recover", "--json").stdout)["lanes"]}
        prev = {x["lane"]: x["action"] for x in self.get("/api/recover")[1]["lanes"]}
        self.assertEqual(prev, rec)
        self.assertEqual(prev["a"], "none")
        starts = len([s for s in self.starts() if s["lane"] == "a"])
        sent = len(self.received("a"))
        st, res = self.post("/api/recover", {"confirm": True, "wait": 3, "continue": "a", "write": True})
        self.assertEqual(st, 200, res)
        self.assertEqual(len([s for s in self.starts() if s["lane"] == "a"]), starts, "a handed-off lane is not restarted")
        self.assertEqual(len(self.received("a")), sent, "and nothing is sent to it")
        with open(os.path.join(self.tasks, "task-a.md"), encoding="utf-8") as f:
            self.assertEqual(f.read().count("## Continuation from"), 1, "the browser writes no handoff")

    @unittest.skipIf(os.geteuid() == 0, "as root an unwritable folder is still writable, so the save cannot be made to fail")
    def test_a_failed_note_save_keeps_the_file_and_reports_it(self):
        self.start()
        self.sign_in()
        st, f = self.get("/api/file?root=0&path=task-a.md")
        self.assertEqual(st, 200, f)
        os.chmod(self.tasks, 0o555)  # the folder cannot take the new revision
        self.addCleanup(os.chmod, self.tasks, 0o755)
        st, diff = self.post("/api/diff", {"root": 0, "path": "task-a.md", "text": f["text"] + "\nedit\n", "base": f["base"]})
        st, r = self.post("/api/save", {"root": 0, "path": "task-a.md", "text": f["text"] + "\nedit\n", "base": f["base"],
                                        "confirm": diff["proposed"]})
        self.assertNotEqual(st, 200, r)
        self.assertIn("error", r)
        with open(os.path.join(self.tasks, "task-a.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), f["text"], "the note is unchanged")
        js = os.path.join(helpers.SWARM, "mdserver", "static", "app.js")
        with open(js, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("keepDraft();\n    return showError(\"Not saved: \"", src, "the editor keeps the draft on a failed save")

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
