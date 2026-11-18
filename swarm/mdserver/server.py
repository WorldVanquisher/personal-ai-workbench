"""EXPERIMENTAL PAI Markdown server: a remote Nano with a Markdown preview.

A file picker for the operator's approved Markdown folders, a plain-text editor, a rendered
preview and an explicit, per-revision save confirmation. Nothing else: no task dashboard,
chat, agent control, shell, Git or PR operation, and no credentials are shown.

    python3 server.py --config CONFIG.json        (pai server start runs this)

Loopback only (127.0.0.1 or ::1). Reach it through an SSH tunnel or Tailscale Serve, never
Funnel. Access needs the token in the pai data directory; a session cookie is HttpOnly and
SameSite=Strict, and every write also needs the session's CSRF header and a Host/Origin of
this server. Reads and previews never write. A save names the exact revision it confirms
and is refused if the file changed on disk since the editor loaded it. Standard library.
"""
import argparse
import difflib
import hashlib
import hmac
import http.server
import json
import os
import secrets
import socket
import stat
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import render  # noqa: E402

LOOPBACK = ("127.0.0.1", "::1")
MAX_BYTES = 2 * 1024 * 1024
STATIC = {"/static/app.css": ("app.css", "text/css; charset=utf-8"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/static/console.css": ("console.css", "text/css; charset=utf-8"),
          "/static/console.js": ("console.js", "text/javascript; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


class Refused(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Docs:
    """The approved roots and the only file operations the server has. With `single`, the
    one file `pai edit` opened is the only one it lists, reads or writes."""

    def __init__(self, roots, suffixes=(".md",), single=None):
        self.roots = [os.path.realpath(r) for r in roots]
        self.suffixes = tuple(suffixes)
        self.single = single

    def resolve(self, root, rel):
        """Absolute path of an existing or new file inside root `root`; refuses traversal,
        absolute paths, symlinks anywhere on the way, and other file types."""
        if not isinstance(root, int) or not 0 <= root < len(self.roots):
            raise Refused(404, "no such document root")
        if not isinstance(rel, str) or not rel or "\x00" in rel or os.path.isabs(rel) or "\\" in rel:
            raise Refused(400, "not a relative path")
        if self.single is not None and (root, rel) != (0, self.single):
            raise Refused(403, "this editor was opened for %s only" % self.single)
        parts = rel.split("/")
        if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            raise Refused(400, "path is outside the approved folder")
        if not rel.endswith(self.suffixes):
            raise Refused(400, "only %s files" % ", ".join(self.suffixes))
        base = self.roots[root]
        cur = base
        for p in parts:
            cur = os.path.join(cur, p)
            if os.path.islink(cur):
                raise Refused(403, "symbolic links are not followed")
        real = os.path.realpath(cur)
        if not real.startswith(os.path.join(base, "")):  # base + one separator, also for a drive or / root
            raise Refused(403, "path is outside the approved folder")
        return cur

    def listing(self, root):
        if not 0 <= root < len(self.roots):
            raise Refused(404, "no such document root")
        if self.single is not None:
            return [self.single] if root == 0 else []
        base, found = self.roots[root], []
        for d, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not os.path.islink(os.path.join(d, x)))
            for f in sorted(files):
                full = os.path.join(d, f)
                if f.endswith(self.suffixes) and not f.startswith(".") and not os.path.islink(full):
                    found.append(os.path.relpath(full, base).replace(os.sep, "/"))
                    if len(found) >= 5000:
                        return found
        return found

    def read(self, root, rel):
        path = self.resolve(root, rel)
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            raise Refused(404, "not a regular file")
        if st.st_size > MAX_BYTES:
            raise Refused(413, "file is larger than 2 MB")
        with open(path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise Refused(415, "not UTF-8 text")
        return {"root": root, "path": rel, "text": text.replace("\r\n", "\n"), "base": sha(raw),
                "crlf": b"\r\n" in raw, "bom": raw.startswith(b"\xef\xbb\xbf")}

    def current(self, root, rel):
        path = self.resolve(root, rel)
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def encode(self, text, crlf, bom):
        data = text.replace("\r\n", "\n")
        if crlf:
            data = data.replace("\n", "\r\n")
        return (b"\xef\xbb\xbf" if bom else b"") + data.encode("utf-8")

    def diff(self, root, rel, text, base):
        raw = self.current(root, rel)
        old = (raw or b"").decode("utf-8-sig", "replace").replace("\r\n", "\n")
        cur = sha(raw) if raw is not None else None
        lines = list(difflib.unified_diff(old.splitlines(), text.replace("\r\n", "\n").splitlines(),
                                          "a/" + rel, "b/" + rel, lineterm="", n=3))
        return {"path": rel, "diff": lines, "stale": cur != base, "current": cur, "proposed": sha(text.encode("utf-8"))}

    def save(self, root, rel, text, base, confirm, crlf=False, bom=False):
        """Atomic write of exactly `text`, only for the confirmed revision and only if the file is
        still what the editor loaded."""
        if not isinstance(text, str) or not hmac.compare_digest(str(confirm or ""), sha(text.encode("utf-8"))):
            raise Refused(400, "the save was not confirmed for this exact revision; review the diff and confirm again")
        path = self.resolve(root, rel)
        raw = self.current(root, rel)
        if raw is None:
            raise Refused(404, "the file no longer exists; nothing was written and your draft is kept")
        if sha(raw) != base:
            raise Refused(409, "the file changed on disk since you opened it; nothing was written and your draft is kept")
        data = self.encode(text, crlf, bom)
        if len(data) > MAX_BYTES:
            raise Refused(413, "file would be larger than 2 MB")
        tmp = os.path.join(os.path.dirname(path), ".%s.%s.tmp" % (os.path.basename(path), secrets.token_hex(4)))
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(os.lstat(path).st_mode))
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                now = self.current(root, rel)  # last look, right before the atomic replace
                if now is None or sha(now) != base:
                    raise Refused(409, "the file changed on disk while saving; nothing was written and your draft is kept")
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        except OSError as e:  # read-only file or folder; on Windows also a file another program holds open
            raise Refused(500, "could not write %s (%s); nothing was written and your draft is kept"
                          % (rel, e.strerror or e))
        return {"path": rel, "base": sha(data)}


class App:
    def __init__(self, cfg, token):
        self.docs = Docs(cfg["roots"], cfg.get("suffixes") or (".md",), cfg.get("single"))
        self.one_time = bool(cfg.get("one_time_token"))  # `pai edit`: the link signs in once
        self.port = cfg["port"]
        self.hosts = {"127.0.0.1:%d" % self.port, "localhost:%d" % self.port, "[::1]:%d" % self.port}
        self.hosts |= set(cfg.get("allowed_hosts") or [])
        self.token = token
        self.sessions = {}  # session id -> csrf token
        # Pages and API routes beyond the editor's own (`pai web` adds its console here). A GET
        # route gets (query, session id); a POST route gets (parsed JSON body, session id). Both
        # run only after sign-in, a POST only with the session's CSRF header and our Host/Origin.
        self.pages = {"/": "index.html"}
        self.get_routes, self.post_routes = {}, {}


def make_handler(app):
    class H(http.server.BaseHTTPRequestHandler):
        server_version = "pai-md/0"
        sys_version = ""

        def log_message(self, fmt, *args):  # no request bodies or tokens in logs
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

        def send(self, code, body, ctype="application/json; charset=utf-8", extra=()):
            data = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
            self.send_response(code)
            for k, v in (("Content-Type", ctype), ("Content-Length", str(len(data))), ("Content-Security-Policy", CSP),
                         ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                         ("Cache-Control", "no-store"), ("X-Frame-Options", "DENY")) + tuple(extra):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def session(self):
            cookie = self.headers.get("Cookie") or ""
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "pai_md" and v in app.sessions:
                    return v
            return None

        def sign_in(self):
            sid = secrets.token_urlsafe(32)
            app.sessions[sid] = secrets.token_urlsafe(32)
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", "pai_md=%s; HttpOnly; SameSite=Strict; Path=/" % sid)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def host_ok(self):
            if (self.headers.get("Host") or "") not in app.hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or urllib.parse.urlsplit(origin).netloc in app.hosts

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {"error": "unexpected Host or Origin"})
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/healthz":
                return self.send(200, {"ok": True, "experimental": True})
            if url.path in STATIC:
                fn, ctype = STATIC[url.path]
                with open(os.path.join(HERE, "static", fn), "rb") as f:
                    return self.send(200, f.read(), ctype)
            if url.path == "/open" and app.one_time and app.token:
                given = urllib.parse.parse_qs(url.query).get("t", [""])[0]
                if not hmac.compare_digest(given.encode(), app.token.encode()):
                    return self.send(401, "This link was already used or is wrong. Run pai edit again.",
                                     "text/plain; charset=utf-8")
                app.token = None  # used up: a copied or logged link cannot sign in again
                return self.sign_in()
            sid = self.session()
            if url.path in app.pages:
                with open(os.path.join(HERE, "static", app.pages[url.path] if sid else "login.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if not sid:
                return self.send(401, {"error": "log in first"})
            q = urllib.parse.parse_qs(url.query)
            try:
                if url.path == "/api/session":
                    return self.send(200, {"csrf": app.sessions[sid], "roots": [os.path.basename(r) or r for r in app.docs.roots],
                                           "experimental": True,
                                           "single": {"root": 0, "path": app.docs.single} if app.docs.single else None})
                if url.path == "/api/files":
                    return self.send(200, {"files": app.docs.listing(int(q.get("root", ["0"])[0]))})
                if url.path == "/api/file":
                    return self.send(200, app.docs.read(int(q.get("root", ["0"])[0]), q.get("path", [""])[0]))
                if url.path in app.get_routes:
                    return self.send(200, app.get_routes[url.path](q, sid))
            except (Refused, ValueError) as e:
                return self.send(getattr(e, "code", 400), {"error": str(e)})
            except Exception as e:  # noqa: BLE001  a route's own failure (tmux timed out, ...): say so, keep serving
                return self.send(500, {"error": "%s: %s" % (type(e).__name__, e)})
            return self.send(404, {"error": "not found"})

        def do_POST(self):
            if not self.host_ok():
                return self.send(403, {"error": "unexpected Host or Origin"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BYTES * 2:
                return self.send(413, {"error": "too large"})
            body = self.rfile.read(n)
            if self.path == "/login":
                token = urllib.parse.parse_qs(body.decode("utf-8", "replace")).get("token", [""])[0].strip()
                if not app.token or not hmac.compare_digest(token.encode(), app.token.encode()):
                    return self.send(401, "Wrong token. <a href=\"/\">Back</a>", "text/html; charset=utf-8")
                if app.one_time:
                    app.token = None
                return self.sign_in()
            sid = self.session()
            if not sid:
                return self.send(401, {"error": "log in first"})
            if not hmac.compare_digest((self.headers.get("X-PAI-CSRF") or "").encode(), app.sessions[sid].encode()):
                return self.send(403, {"error": "missing or wrong CSRF token"})
            try:
                d = json.loads(body or b"{}")
                if self.path == "/api/preview":
                    return self.send(200, {"html": render.render(str(d.get("text") or ""))})
                if self.path == "/api/diff":
                    return self.send(200, app.docs.diff(d.get("root"), d.get("path"), str(d.get("text") or ""), d.get("base")))
                if self.path == "/api/save":
                    return self.send(200, app.docs.save(d.get("root"), d.get("path"), d.get("text"), d.get("base"),
                                                        d.get("confirm"), bool(d.get("crlf")), bool(d.get("bom"))))
                if self.path == "/logout":
                    app.sessions.pop(sid, None)
                    return self.send(200, {"ok": True})
                if self.path in app.post_routes:
                    if not isinstance(d, dict):
                        raise ValueError("a JSON object is expected")
                    return self.send(200, app.post_routes[self.path](d, sid))
            except Refused as e:
                return self.send(e.code, {"error": str(e)})
            except (ValueError, TypeError) as e:
                return self.send(400, {"error": "bad request (%s)" % type(e).__name__})
            except Exception as e:  # noqa: BLE001
                return self.send(500, {"error": "%s: %s" % (type(e).__name__, e)})
            return self.send(404, {"error": "not found"})

    return H


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR (which http.server sets) would let another program bind the same
    # port and receive requests meant for this server; take the port exclusively there instead.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(cfg, token, ready=None):
    if cfg.get("bind", "127.0.0.1") not in LOOPBACK:
        raise SystemExit("pai md server: refusing to listen on %s; only loopback (127.0.0.1, ::1) is allowed. Use an "
                         "SSH tunnel or Tailscale Serve for remote access." % cfg.get("bind"))
    app = App(cfg, token)
    if cfg.get("bind") == "::1":
        Server.address_family = socket.AF_INET6
    httpd = Server((cfg.get("bind", "127.0.0.1"), cfg["port"]), make_handler(app))
    if cfg["port"] == 0:
        app.port = httpd.server_address[1]
        app.hosts |= {"127.0.0.1:%d" % app.port, "localhost:%d" % app.port}
    if ready:
        ready(httpd, app)
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    with open(cfg["token_file"], encoding="utf-8") as f:
        token = f.read().strip()
    httpd = serve(cfg, token)
    sys.stderr.write("pai md server (EXPERIMENTAL) on http://%s:%d/\n" % (cfg.get("bind", "127.0.0.1"), httpd.server_address[1]))
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
