"""pai round web: read-only board. Sign-in, the two GET routes, and refusals: every write (even
with a valid session and CSRF header), the editor's routes, foreign Host/Origin, no session."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import round_web  # noqa: E402
from test_round_board import make_round  # noqa: E402


class Web(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["PAI_ROUND_TMUX_SOCKET"] = os.path.join(self.tmp, "no-tmux")
        self.addCleanup(os.environ.pop, "PAI_ROUND_TMUX_SOCKET", None)
        r = make_round(self.tmp)
        with open(os.path.join(r["vault"], "sheet.md"), "w", encoding="utf-8") as f:
            f.write("- [ ] **D5 网页工作台**：本轮只读。\n  - 推荐：同意\n")
        with open(os.path.join(r["vault"], "secret.md"), "w") as f:
            f.write("do not serve\n")
        self.round_file = os.path.join(r["state_dir"], "ROUND.json")
        with open(self.round_file, "w") as f:
            json.dump(r, f)
        self.token = "tok-" + "x" * 20
        self.httpd, self.app = round_web.serve(self.round_file, 0, self.token)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]
        self.host = "127.0.0.1:%d" % self.port

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            h = {"Host": host or self.host}
            h.update(headers or {})
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            data = r.read()
            return r.status, dict(r.getheaders()), data
        finally:
            c.close()

    def sign_in(self):
        code, h, _ = self.req("GET", "/open?t=" + self.token)
        self.assertEqual(code, 303)
        cookie = h["Set-Cookie"].split(";")[0]
        code, _, data = self.req("GET", "/api/session", headers={"Cookie": cookie})
        return cookie, json.loads(data)["csrf"]

    def test_board_after_sign_in(self):
        cookie, _ = self.sign_in()
        code, h, data = self.req("GET", "/api/round/board", headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        b = json.loads(data)
        self.assertTrue(b["read_only"])
        self.assertEqual([l["name"] for l in b["lanes"]], ["alpha", "beta"])
        self.assertEqual(b["decisions"][0]["id"], "D5")
        self.assertNotIn("vault", b["round"])  # local folders of the vault itself are not echoed
        code, _, data = self.req("GET", "/api/round/status", headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        self.assertEqual({x["state"] for x in json.loads(data)["lanes"]}, {"no-window"})
        code, _, data = self.req("GET", "/", headers={"Cookie": cookie})
        self.assertIn(b"PAI Round", data)
        code, _, _ = self.req("GET", "/static/round.js")
        self.assertEqual(code, 200)

    def test_one_time_link_and_no_session(self):
        self.sign_in()
        self.assertEqual(self.req("GET", "/open?t=" + self.token)[0], 401)
        self.assertEqual(self.req("GET", "/api/round/board")[0], 401)
        code, _, data = self.req("GET", "/")
        self.assertIn(b"SIGN IN", data)

    def test_every_write_is_refused_even_with_csrf(self):
        cookie, csrf = self.sign_in()
        h = {"Cookie": cookie, "X-PAI-CSRF": csrf, "Content-Type": "application/json"}
        for path in ("/api/save", "/api/diff", "/api/preview", "/api/round/board", "/api/decide/answer", "/api/pr/publish"):
            with self.subTest(path=path):
                code, hd, _ = self.req("POST", path, body=b"{}", headers=h)
                self.assertEqual(code, 405)
                self.assertEqual(hd.get("Allow"), "GET")
        for m in ("PUT", "PATCH", "DELETE"):
            with self.subTest(method=m):
                self.assertEqual(self.req(m, "/api/round/board", body=b"{}", headers=h)[0], 405)
        self.assertEqual(self.req("POST", "/logout", body=b"{}", headers=h)[0], 200)

    def test_editor_routes_are_not_served(self):
        cookie, _ = self.sign_in()
        for path in ("/api/files?root=0", "/api/file?root=0&path=secret.md", "/static/app.js", "/api/wt/list"):
            with self.subTest(path=path):
                code, _, data = self.req("GET", path, headers={"Cookie": cookie})
                self.assertEqual(code, 404)
                self.assertNotIn(b"do not serve", data)

    def test_foreign_host_or_origin_is_refused(self):
        cookie, csrf = self.sign_in()
        self.assertEqual(self.req("GET", "/api/round/board", headers={"Cookie": cookie}, host="evil.example:80")[0], 403)
        self.assertEqual(self.req("GET", "/api/round/board", headers={"Cookie": cookie, "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/logout", headers={"Cookie": cookie, "X-PAI-CSRF": csrf,
                                                              "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/open?t=" + self.token, host="evil.example")[0], 403)

    def test_listens_on_loopback_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
