"""pai round web: the round board in the browser, READ-ONLY (prototype for docs/WORKSPACE.md).

    pai round web [ROUND.json] [--port N] [--no-browser]

Shows ROUND.json (lanes, branches, sessions, folders), the live lane state (the same tmux +
/proc + files reading as `pai round status`; no model), the open decisions with their sha, and
the last `pai round collect`. It has no button that changes anything: approving, answering a
decision and publishing stay in the terminal (pai decide answer, pai worktree publish) for this
round of the design (owner decision D5).

Same protections as `pai edit` and `pai worktree web` (swarm/mdserver/server.py): 127.0.0.1
only, a one-time sign-in link, an HttpOnly SameSite=Strict session cookie, Host and Origin
checks, a strict Content-Security-Policy. On top of that, this server answers only its own
pages and GET routes: every POST except /login and /logout, every PUT/PATCH/DELETE and every other path
(the editor's /api/file, /api/save, ...) is refused, even with a valid session and CSRF header.
"""
import http.server
import os
import secrets
import signal
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
MD_DIR = os.path.join(HERE, "mdserver")
for d in (HERE, MD_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)
import round as rnd  # noqa: E402
import round_board  # noqa: E402
import round_status  # noqa: E402
import server  # noqa: E402

STATIC = {"/static/round.js": ("round.js", "text/javascript; charset=utf-8"),
          "/static/round.css": ("round.css", "text/css; charset=utf-8"),
          "/static/app.css": ("app.css", "text/css; charset=utf-8")}
OPEN_GET = {"/", "/open", "/healthz", "/api/session"}
SAFE_ROUND_FIELDS = ("round", "project", "github", "base_branch", "base_sha", "base_source", "vault_source", "trusted_root",
                     "tmux_session", "created", "status", "closed")


class Board:
    def __init__(self, round_file, tmux=None):
        self.round_file = round_file
        self.tmux = tmux

    def load(self):
        try:
            return rnd.load_round(self.round_file)
        except rnd.RoundError as e:
            raise server.Refused(404, str(e))

    def board(self, q, sid):
        r = self.load()
        dec = round_board.open_decisions(r)
        collect = None
        p = os.path.join(r["state_dir"], "COLLECT.json")
        if os.path.isfile(p):
            import json
            with open(p, encoding="utf-8") as f:
                try:
                    collect = json.load(f)
                except ValueError:
                    collect = {"error": "COLLECT.json is not JSON"}
        lanes = [{k: ln.get(k) for k in ("name", "branch", "session_name", "tmux_target", "worktree", "handoff_dir",
                                         "prompt_sha256")} for ln in r["lanes"]]
        return {"round": {k: r.get(k) for k in SAFE_ROUND_FIELDS}, "lanes": lanes,
                "decisions": [{"id": d["id"], "title": d["title"], "text": d["text"], "sha": d["sha"][:12],
                               "fields": d["fields"], "sheet": os.path.relpath(d["sheet"], r["vault"])} for d in dec],
                "collect": collect, "read_only": True}

    def status(self, q, sid):
        r = self.load()
        rows = round_status.status(r, tmux=self.tmux)
        keep = ("lane", "state", "why", "dialog", "draft_chars", "window", "target", "worktree", "agent_name", "name_ok", "queue")
        return {"lanes": [dict({k: x.get(k) for k in keep},
                               checkpoint=(x.get("checkpoint") or {}).get("name"),
                               restart=bool((x.get("checkpoint") or {}).get("restart"))) for x in rows]}


def make_handler(app, board):
    Base = server.make_handler(app)
    routes = {"/api/round/board": board.board, "/api/round/status": board.status}

    class ReadOnly(Base):
        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {"error": "unexpected Host or Origin"})
            path = urllib.parse.urlsplit(self.path).path
            if path in STATIC:
                fn, ctype = STATIC[path]
                with open(os.path.join(MD_DIR, "static", fn), "rb") as f:
                    return self.send(200, f.read(), ctype)
            if path in routes:
                sid = self.session()
                if not sid:
                    return self.send(401, {"error": "log in first"})
                try:
                    return self.send(200, routes[path](urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query), sid))
                except server.Refused as e:
                    return self.send(e.code, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    return self.send(500, {"error": "%s: %s" % (type(e).__name__, e)})
            if path not in OPEN_GET:
                return self.send(404, {"error": "not found (this board is read-only)"})
            return Base.do_GET(self)

        def do_POST(self):
            if not self.host_ok():
                return self.send(403, {"error": "unexpected Host or Origin"})
            if urllib.parse.urlsplit(self.path).path in ("/login", "/logout"):  # signing in or out writes no data
                return Base.do_POST(self)
            return self.send(405, {"error": "this board is read-only"}, extra=(("Allow", "GET"),))

        def _refuse(self):
            return self.send(405, {"error": "this board is read-only"}, extra=(("Allow", "GET"),))

        do_PUT = do_PATCH = do_DELETE = _refuse

    return ReadOnly


def serve(round_file, port=0, token=None, tmux=None):
    cfg = {"roots": [], "port": port, "one_time_token": True}
    app = server.App(cfg, token or secrets.token_urlsafe(24))
    app.pages = {"/": "round.html"}
    httpd = server.Server(("127.0.0.1", port), make_handler(app, Board(round_file, tmux)))
    if port == 0:
        app.port = httpd.server_address[1]
        app.hosts |= {"127.0.0.1:%d" % app.port, "localhost:%d" % app.port}
    return httpd, app


def main(round_file, port=0, no_browser=False, out=None, opener=None):
    out = out or sys.stdout
    rnd.load_round(round_file)  # refuse early when it cannot be read
    if port and not 1024 <= port <= 65535:
        raise rnd.RoundError("--port must be between 1024 and 65535")
    token = secrets.token_urlsafe(24)
    try:
        httpd, app = serve(round_file, port, token)
    except OSError as e:
        print("pai round web: cannot listen on 127.0.0.1:%s (%s)" % (port or "any", e.strerror or e), file=sys.stderr)
        return 1
    url = "http://127.0.0.1:%d/open?t=%s" % (httpd.server_address[1], token)
    print("pai round web (EXPERIMENTAL, read-only): %s" % round_file, file=out)
    print("  open:  %s" % url, file=out)
    print("  The link signs in once. Nothing on the page changes anything; answer decisions with pai decide answer.", file=out)
    print("  From another computer: ssh -N -L %d:127.0.0.1:%d YOU@THIS-HOST, then open the link there."
          % (httpd.server_address[1], httpd.server_address[1]), file=out)
    out.flush()
    if not no_browser:
        import edit
        edit.open_browser(url, opener)

    def stop(*_):
        raise KeyboardInterrupt

    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), stop)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    print("\npai round web: stopped. Nothing was changed.", file=out)
    return 0
