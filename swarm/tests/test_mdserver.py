"""The experimental PAI Markdown server, against disposable document folders only. It never
opens a non-loopback port, and nothing here touches your documents or your network."""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SWARM = os.path.dirname(HERE)
PAI = os.path.join(os.path.dirname(SWARM), "bin", "pai.py")
sys.path.insert(0, os.path.join(SWARM, "mdserver"))
import render  # noqa: E402
import server  # noqa: E402

TOKEN = "test-token-not-secret"
needs_linux = unittest.skipUnless(sys.platform.startswith("linux") and sys.version_info >= (3, 11), "Linux, Python 3.11+")


def read(path, mode="r"):
    """The file's content, read with the handle closed."""
    with open(path, mode) as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pai-md-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.docs = os.path.join(self.tmp, "docs")
        os.makedirs(os.path.join(self.docs, "sub"))
        self.outside = os.path.join(self.tmp, "outside.md")
        with open(self.outside, "w") as f:
            f.write("# secret outside the root\n")
        self.note = os.path.join(self.docs, "note.md")
        with open(self.note, "wb") as f:
            f.write(b"---\r\ntitle: Note\r\nstatus: todo\r\n---\r\n# Hello\r\n\r\nFirst line.\r\n")


class Live(Base):
    def setUp(self):
        super().setUp()
        cfg = {"roots": [self.docs], "port": 0, "bind": "127.0.0.1"}
        self.httpd = server.serve(cfg, TOKEN, ready=lambda h, app: setattr(self, "app", app))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.cookie = None

    def req(self, method, path, body=None, headers=None, form=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": "127.0.0.1:%d" % self.port}
        if self.cookie:
            h["Cookie"] = self.cookie
        if form is not None:
            body = form
            h["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            body = json.dumps(body)
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, r.getheaders(), data

    def login(self):
        st, hdrs, _ = self.req("POST", "/login", form="token=" + TOKEN)
        self.assertEqual(st, 303)
        self.cookie = dict(hdrs)["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", dict(hdrs)["Set-Cookie"])
        self.assertIn("SameSite=Strict", dict(hdrs)["Set-Cookie"])
        st, _, data = self.req("GET", "/api/session")
        self.csrf = json.loads(data)["csrf"]

    def post(self, path, body, csrf=True, **kw):
        st, _, data = self.req("POST", path, body, headers={"X-PAI-CSRF": self.csrf} if csrf else {}, **kw)
        return st, json.loads(data or b"{}")

    def test_everything_needs_the_token_and_writes_need_csrf_and_our_host(self):
        self.assertEqual(self.req("GET", "/api/files")[0], 401)
        st, _, data = self.req("GET", "/")
        self.assertIn(b'name="token"', data, "the login page, not the editor")
        self.assertEqual(self.req("POST", "/login", form="token=wrong")[0], 401)
        self.login()
        self.assertEqual(self.post("/api/preview", {"text": "x"}, csrf=False)[0], 403)
        st, _, _ = self.req("POST", "/api/preview", {"text": "x"}, headers={"X-PAI-CSRF": self.csrf, "Origin": "https://evil.example"})
        self.assertEqual(st, 403, "a cross-site request is refused")
        st, _, _ = self.req("GET", "/api/files", headers={"Host": "evil.example"})
        self.assertEqual(st, 403, "DNS rebinding: an unexpected Host is refused")
        st, hdrs, _ = self.req("GET", "/")
        csp = dict(hdrs)["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("img-src 'none'", csp)

    def test_traversal_symlinks_and_other_types_are_refused(self):
        self.login()
        os.symlink(self.outside, os.path.join(self.docs, "link.md"))
        os.symlink(self.tmp, os.path.join(self.docs, "sub", "up"))
        with open(os.path.join(self.docs, "notes.txt"), "w") as f:
            f.write("x")
        for bad in ("../outside.md", "/etc/passwd.md", "sub/../../outside.md", "link.md", "sub/up/outside.md",
                    "notes.txt", ".hidden.md", "sub\\..\\..\\outside.md", ""):
            st, _, _ = self.req("GET", "/api/file?root=0&path=" + bad.replace("/", "%2F"))
            self.assertIn(st, (400, 403, 404), bad)
        st, body = self.post("/api/save", {"root": 0, "path": "link.md", "text": "x", "base": "x", "confirm": "x"})
        self.assertIn(st, (400, 403))
        self.assertEqual(read(self.outside), "# secret outside the root\n")
        files = json.loads(self.req("GET", "/api/files?root=0")[2])["files"]
        self.assertEqual(files, ["note.md"])

    def test_preview_and_diff_never_write(self):
        self.login()
        before = (read(self.note, "rb"), os.stat(self.note).st_mtime_ns)
        doc = json.loads(self.req("GET", "/api/file?root=0&path=note.md")[2])
        st, body = self.post("/api/preview", {"text": doc["text"] + "\nmore\n"})
        self.assertEqual(st, 200)
        self.assertIn('<pre class="front-matter">title: Note', body["html"])
        st, d = self.post("/api/diff", {"root": 0, "path": "note.md", "text": doc["text"] + "more\n", "base": doc["base"]})
        self.assertEqual((st, d["stale"]), (200, False))
        self.assertIn("+more", d["diff"])
        self.assertEqual((read(self.note, "rb"), os.stat(self.note).st_mtime_ns), before)

    def test_a_save_needs_the_confirmed_revision_and_keeps_line_endings_and_front_matter(self):
        self.login()
        doc = json.loads(self.req("GET", "/api/file?root=0&path=note.md")[2])
        new = doc["text"].replace("First line.", "First line, edited.")
        base = {"root": 0, "path": "note.md", "text": new, "base": doc["base"], "crlf": doc["crlf"], "bom": doc["bom"]}
        st, body = self.post("/api/save", dict(base))
        self.assertEqual(st, 400, "no confirmation, no write")
        st, d = self.post("/api/diff", base)
        st, body = self.post("/api/save", dict(base, confirm=d["proposed"], text=new + "sneaked in\n"))
        self.assertEqual(st, 400, "a confirmation covers one exact revision only")
        self.assertIn(b"First line.\r\n", read(self.note, "rb"))
        st, body = self.post("/api/save", dict(base, confirm=d["proposed"]))
        self.assertEqual(st, 200, body)
        raw = read(self.note, "rb")
        self.assertEqual(raw, b"---\r\ntitle: Note\r\nstatus: todo\r\n---\r\n# Hello\r\n\r\nFirst line, edited.\r\n")
        self.assertEqual([f for f in os.listdir(self.docs) if f.endswith(".tmp")], [])

    def test_a_stale_draft_never_overwrites_newer_content(self):
        self.login()
        doc = json.loads(self.req("GET", "/api/file?root=0&path=note.md")[2])
        with open(self.note, "ab") as f:  # SSH, an agent or another browser changes the file
            f.write(b"Written by someone else.\r\n")
        newer = read(self.note, "rb")
        draft = doc["text"] + "my draft\n"
        st, d = self.post("/api/diff", {"root": 0, "path": "note.md", "text": draft, "base": doc["base"]})
        self.assertTrue(d["stale"])
        import hashlib
        st, body = self.post("/api/save", {"root": 0, "path": "note.md", "text": draft, "base": doc["base"],
                                           "confirm": hashlib.sha256(draft.encode()).hexdigest()})
        self.assertEqual(st, 409)
        self.assertIn("your draft is kept", body["error"])
        self.assertEqual(read(self.note, "rb"), newer)

    def test_malicious_markdown_is_rendered_inert(self):
        evil = ("<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[a](javascript:alert(1)) [b](JaVa&#115;cript:x) "
                "[c](data:text/html,<b>x</b>) [d](vbscript:x)\n![i](https://tracker.example/p.png)\n"
                "<iframe src=//evil></iframe>\n```js\nfetch('/api/save')\n```\n[ok](https://example.org/a?b=1&c=2)")
        out = render.render(evil)
        import html.parser

        class Tags(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self.seen = []

            def handle_starttag(self, tag, attrs):
                self.seen.append((tag, dict(attrs)))

        t = Tags()
        t.feed(out)
        allowed = {"p", "br", "a", "span", "pre", "code", "em", "strong", "del", "h1", "h2", "h3", "h4", "h5", "h6",
                   "ul", "ol", "li", "input", "blockquote", "table", "thead", "tbody", "tr", "th", "td", "hr"}
        for tag, attrs in t.seen:
            self.assertIn(tag, allowed, "only inert elements: %s" % tag)
            self.assertLessEqual(set(attrs), {"href", "rel", "class", "data-lang", "type", "disabled", "checked"}, attrs)
            if "href" in attrs:
                self.assertRegex(attrs["href"], r"^(https?:|mailto:|#|\./|[^:]*$)", attrs["href"])
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("[image: i]", out)
        self.assertIn('href="https://example.org/a?b=1&amp;c=2"', out)
        self.assertIn("<pre class=\"code\" data-lang=\"js\"><code>fetch(&#x27;/api/save&#x27;)</code></pre>", out)


@needs_linux
class Pack(Base):
    def setUp(self):
        super().setUp()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.env = dict(os.environ, PAI_WORKBENCH_DATA=os.path.join(self.tmp, "data"),
                        PAI_WORKBENCH_HOME=os.path.join(self.tmp, "config"), PAI_SWARM_HOME=os.path.join(self.tmp, "home"))
        self.addCleanup(lambda: self.pai("server", "stop"))

    def pai(self, *args):
        return subprocess.run([sys.executable, "-B", PAI] + list(args), cwd=self.tmp, env=self.env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=60)

    def listening(self):
        """Local addresses with our port in LISTEN state, from /proc/net/tcp{,6}."""
        found = []
        for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                rows = read(fn).splitlines()[1:]
            except OSError:
                continue
            for r in rows:
                local, state = r.split()[1], r.split()[3]
                addr, port = local.rsplit(":", 1)
                if int(port, 16) == self.port and state == "0A":
                    found.append(addr)
        return found

    def test_dry_run_and_declining_write_nothing_and_core_pai_is_unaffected(self):
        p = self.pai("setup", "--experimental", "server", "--root", self.docs, "--port", str(self.port), "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for needle in ("EXPERIMENTAL", "download      nothing", "127.0.0.1:%d only (loopback)" % self.port,
                       "folder        %s" % self.docs, "who can use", "never Funnel", "DRY RUN"):
            self.assertIn(needle, p.stdout)
        p = self.pai("setup", "--experimental", "server", "--root", self.docs, "--port", str(self.port))
        self.assertEqual(p.returncode, 2)
        self.assertIn("add --accept-experimental", p.stdout)
        self.assertFalse(os.path.exists(self.env["PAI_WORKBENCH_DATA"]), "declining wrote nothing")
        self.assertEqual(self.pai("server", "status").returncode, 1)
        home = os.path.expanduser("~")
        p = self.pai("setup", "--experimental", "server", "--root", home, "--dry-run")
        self.assertEqual(p.returncode, 2)
        self.assertIn("would expose everything below it", p.stdout)

    def test_install_does_not_start_then_start_status_stop_uninstall(self):
        p = self.pai("setup", "--experimental", "server", "--root", self.docs, "--port", str(self.port), "--accept-experimental")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.listening(), [], "installing did not start it")
        token = os.path.join(self.env["PAI_WORKBENCH_DATA"], "server", "token")
        self.assertEqual(oct(os.stat(token).st_mode & 0o777), "0o600")
        p = self.pai("server", "start")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn(read(token).strip(), p.stdout, "the token is never printed by start")
        self.assertEqual(self.listening(), ["0100007F"], "listening on 127.0.0.1 only")
        self.assertIn("running at http://127.0.0.1:%d/" % self.port, self.pai("server", "status").stdout)
        p = self.pai("server", "stop")
        self.assertIn("stopped", p.stdout)
        self.assertEqual(self.listening(), [])
        p = self.pai("server", "uninstall")
        self.assertIn("Your Markdown folders were not touched", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.env["PAI_WORKBENCH_DATA"], "server")))
        self.assertTrue(os.path.isfile(self.note))



@needs_linux
class Edit(Base):
    """`pai edit FILE.md` through the real launcher: one file, one-time link, loopback."""

    def start(self, *extra):
        env = dict(os.environ, PAI_WORKBENCH_HOME=os.path.join(self.tmp, "config"),
                   PAI_SWARM_HOME=os.path.join(self.tmp, "home"))
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        p = subprocess.Popen([sys.executable, "-B", PAI, "edit", self.note] + list(extra), cwd=self.tmp, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait(), p.stdout.close(), p.stderr.close()))
        lines = []
        while not any("open:" in l for l in lines):
            line = p.stdout.readline()
            if not line:  # read stderr only once stdout has ended (it blocks until the child exits)
                self.fail("pai edit printed no link: %s" % p.stderr.read())
            lines.append(line)
        url = [l for l in lines if "open:" in l][0].split("open:")[1].strip()
        self.port = int(url.split(":")[2].split("/")[0])
        return p, url, lines

    def read_note(self):
        with open(self.note, "rb") as f:
            return f.read()

    def get(self, path, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers=dict({"Host": "127.0.0.1:%d" % self.port}, **({"Cookie": cookie} if cookie else {})))
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def post(self, path, body, cookie, csrf):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", path, body=json.dumps(body), headers={"Host": "127.0.0.1:%d" % self.port, "Cookie": cookie,
                                                                "X-PAI-CSRF": csrf, "Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data

    def test_one_command_opens_exactly_that_file_with_a_one_time_link(self):
        with open(os.path.join(self.docs, "other.md"), "w") as f:
            f.write("# not this one\n")
        p, url, lines = self.start()
        self.assertTrue(url.startswith("http://127.0.0.1:%d/open?t=" % self.port))
        rest = [p.stdout.readline() for _ in range(4)]
        self.assertIn("  could not open a browser here: open the link above yourself\n", rest)
        st, hdrs, _ = self.get(url.split(str(self.port), 1)[1])
        self.assertEqual(st, 303)
        cookie = hdrs["Set-Cookie"].split(";")[0]
        self.assertEqual(self.get(url.split(str(self.port), 1)[1])[0], 401, "the link signs in once")
        st, _, page = self.get("/", cookie)
        self.assertIn(b'id="text"', page)
        session = json.loads(self.get("/api/session", cookie)[2])
        self.assertEqual(session["single"], {"root": 0, "path": "note.md"})
        self.assertEqual(json.loads(self.get("/api/files?root=0", cookie)[2])["files"], ["note.md"])
        self.assertEqual(self.get("/api/file?root=0&path=other.md", cookie)[0], 403, "only the one file")
        csrf = session["csrf"]
        doc = json.loads(self.get("/api/file?root=0&path=note.md", cookie)[2])
        before = self.read_note()
        st, prev = self.post("/api/preview", {"text": doc["text"] + "\n**typed**\n"}, cookie, csrf)
        self.assertIn("<strong>typed</strong>", prev["html"])
        self.assertEqual(self.read_note(), before, "the preview wrote nothing")
        new = doc["text"] + "typed\n"
        st, d = self.post("/api/diff", {"root": 0, "path": "note.md", "text": new, "base": doc["base"]}, cookie, csrf)
        self.assertEqual(self.post("/api/save", {"root": 0, "path": "note.md", "text": new, "base": doc["base"],
                                                 "crlf": True}, cookie, csrf)[0], 400, "no confirmation, no write")
        with open(self.note, "ab") as f:
            f.write(b"external edit\r\n")
        st, body = self.post("/api/save", {"root": 0, "path": "note.md", "text": new, "base": doc["base"],
                                           "confirm": d["proposed"], "crlf": True}, cookie, csrf)
        self.assertEqual(st, 409)
        self.assertTrue(self.read_note().endswith(b"external edit\r\n"), "the newer file was kept")
        p.send_signal(2)
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertIn("stopped", p.stdout.read())

    def test_listening_is_loopback_only_and_bad_input_is_refused(self):
        p, url, _ = self.start()
        with open("/proc/net/tcp") as f:
            rows = [r.split() for r in f.read().splitlines()[1:]]
        mine = [r[1] for r in rows if int(r[1].rsplit(":", 1)[1], 16) == self.port and r[3] == "0A"]
        self.assertEqual([m.split(":")[0] for m in mine], ["0100007F"])
        p.send_signal(2)
        p.wait(timeout=10)
        for bad, why in ((os.path.join(self.docs, "missing.md"), "does not exist"), (self.outside[:-3] + ".txt", ".md files"),
                         (os.path.join(self.docs, "sub"), ".md files")):
            r = subprocess.run([sys.executable, "-B", PAI, "edit", bad], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2)
            self.assertIn(why, r.stderr)
        os.symlink(self.note, os.path.join(self.docs, "link.md"))
        r = subprocess.run([sys.executable, "-B", PAI, "edit", os.path.join(self.docs, "link.md")], capture_output=True,
                           text=True, timeout=30)
        self.assertIn("symbolic link", r.stderr)

    def test_a_failing_browser_only_means_the_link_is_printed(self):
        import edit

        def broken(url):
            raise RuntimeError("no browser")
        old = edit.gui_available
        edit.gui_available = lambda: True
        try:
            self.assertFalse(edit.open_browser("http://127.0.0.1:1/open?t=x", broken))
            self.assertTrue(edit.open_browser("http://127.0.0.1:1/open?t=x", lambda u: True))
        finally:
            edit.gui_available = old


if __name__ == "__main__":
    unittest.main()
