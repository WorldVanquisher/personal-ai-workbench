"""pai web: one pai run in your browser, on this machine only.

    pai web                      opens the browser on the console for the active run
    pai web --no-browser --port 8766   prints the link (then an SSH tunnel from your laptop)

Each lane's tmux screen, live, with its colours; you can select and copy from it like any web
page. Type into a lane (Live keys: what you type goes to the pane, including Ctrl and arrow
keys and your input method), paste from your clipboard in one piece (a bracketed paste, see
panes.py), or write a longer message in the compose box first. Each lane can also get a shell
in its worktree (tmux session RUN-SESSION-shell on the run's tmux server). Notes: the task folder
and the lane worktrees open in the Markdown editor (live preview, confirmed saves).

It runs in the foreground until Ctrl-C and stores nothing. Same protections as `pai edit`:
127.0.0.1 only, a sign-in link that works once, an HttpOnly SameSite=Strict session cookie,
a CSRF header on every write, Host/Origin checks, and a strict Content-Security-Policy.

What it never does: answer an agent's folder-trust prompt (a paste is refused while one is on
the screen; you answer it yourself, by typing), send a task (that is `pai run`/`pai resume`),
or push, merge or publish anything. The browser names a lane, never a tmux pane: pai looks the
pane up itself, so only this run's lanes and their shells are reachable.
"""
import os
import sys
import time

import agents
import panes
import rc

HERE = os.path.dirname(os.path.abspath(__file__))
MD_DIR = os.path.join(HERE, "mdserver")
if MD_DIR not in sys.path:
    sys.path.insert(0, MD_DIR)
import edit  # noqa: E402
import server  # noqa: E402

PANE_TTL = 5.0  # seconds a lane's pane lookup is reused (pai status's lookup reads every process)


class Console:
    def __init__(self, project, m, roots):
        self.project, self.m, self.roots = project, m, roots
        self.tmux = rc.Tmux(m.socket) if m else None
        self.shells = (m.session + "-shell") if m else None
        self._panes = {}  # lane -> (pane id, time)

    # ---------------------------------------------------------------- lookups
    def worker(self, lane):
        if not self.m or not isinstance(lane, str):
            raise server.Refused(404, "no such lane")
        w = next((w for w in self.m.workers if w.lane == lane), None)
        if not w:
            raise server.Refused(404, "run %s has no lane %r" % (self.m.run_id, lane))
        return w

    def agent_pane(self, w, fresh=False):
        hit = self._panes.get(w.lane)
        if hit and not fresh and time.time() - hit[1] < PANE_TTL:
            return hit[0]
        proc = rc.lane_process(self.m, self.tmux, w)
        win = proc.get("window") or {}
        if proc["state"] in ("no-window", "ambiguous", "mismatched") or not win.get("pane_id"):
            self._panes.pop(w.lane, None)
            raise server.Refused(404, "lane %s has no window (%s); `pai resume --lane %s` starts it again"
                                 % (w.lane, proc.get("detail", proc["state"]), w.lane))
        self._panes[w.lane] = (win["pane_id"], time.time())
        return win["pane_id"]

    def shell_pane(self, lane):
        if not self.tmux.has_session(self.shells):
            return None
        p = self.tmux.run("list-windows", "-t", "=" + self.shells, "-F", "#{window_name}\t#{pane_id}")
        for line in p.stdout.splitlines():
            name, _, pane = line.partition("\t")
            if name == lane:
                return pane
        return None

    def target(self, d):
        """(pane id, trust check or None) for {"lane", "target": "agent"|"shell"}."""
        w = self.worker(d.get("lane"))
        if d.get("target") == "shell":
            pane = self.shell_pane(w.lane)
            if not pane:
                raise server.Refused(404, "lane %s has no shell yet (Open a shell)" % w.lane)
            return pane, None
        return self.agent_pane(w), w.adapter.waiting_for_trust

    # ---------------------------------------------------------------- GET routes
    def lanes(self, q, sid):
        base = {"project": self.project["name"], "roots": [os.path.basename(r) or r for r in self.roots]}
        if not self.m:
            return dict(base, run=None, lanes=[], hint="No run yet: `pai run` starts one; then reload this page.")
        rows, _ = rc.collect_status(self.m, self.tmux)
        out = []
        for r in rows:
            note = r.get("note") or ""  # the task note's name in the run's task folder, without .md
            note_link = None
            full = os.path.realpath(os.path.join(self.m.tasks_dir, note + ".md")) if note and self.m.tasks_dir else ""
            for i, root in enumerate(self.roots):
                if full and full.startswith(os.path.join(root, "")):
                    note_link = {"root": i, "path": os.path.relpath(full, root).replace(os.sep, "/")}
                    break
            out.append({"lane": r["lane"], "agent": agents.get(r["agent"]["name"]).display, "phase": r["phase"],
                        "process": r["process"]["state"], "dispatch": r["dispatch"]["state"], "branch": r["branch"],
                        "worktree": r["worktree"], "note": note, "note_link": note_link,
                        "trust": r["phase"] == "waiting-for-trust", "shell": bool(self.shell_pane(r["lane"]))})
        return dict(base, run=self.m.run_id, session=self.m.session, shell_session=self.shells, lanes=out)

    def screen(self, q, sid):
        d = {"lane": (q.get("lane") or [""])[0], "target": (q.get("target") or ["agent"])[0]}
        pane, trust = self.target(d)
        s = panes.screen(self.tmux, pane, history=(q.get("history") or ["0"])[0] == "1")
        if s is None:
            self._panes.pop(d["lane"], None)
            raise server.Refused(404, "the pane is gone")
        s["trust"] = bool(trust and trust(s["text"]))
        return s

    # ---------------------------------------------------------------- POST routes
    def paste(self, d, sid):
        pane, trust = self.target(d)
        text = d.get("text")
        if not isinstance(text, str):
            raise server.Refused(400, "text must be a string")
        try:
            return panes.paste(self.tmux, pane, text, trust=trust, enter=bool(d.get("enter")),
                               allow_unbracketed=bool(d.get("allow_unbracketed")))
        except panes.Refused as e:
            raise server.Refused(409, "%s: %s" % (e.code, e))

    def keys(self, d, sid):
        pane, _ = self.target(d)
        try:
            return panes.send_keys(self.tmux, pane, d.get("keys"))
        except panes.Refused as e:
            raise server.Refused(409 if e.code != "bad-keys" else 400, "%s: %s" % (e.code, e))

    def open_shell(self, d, sid):
        w = self.worker(d.get("lane"))
        pane = self.shell_pane(w.lane)
        if pane:
            return {"pane": pane, "created": False}
        cwd = w.worktree if os.path.isdir(w.worktree) else self.m.repo
        shell = os.environ.get("SHELL") or "/bin/sh"
        if self.tmux.has_session(self.shells):
            p = self.tmux.run("new-window", "-d", "-P", "-F", "#{pane_id}", "-t", "=%s:" % self.shells, "-n", w.lane,
                              "-c", cwd, shell)
        else:
            p = self.tmux.run("new-session", "-d", "-P", "-F", "#{pane_id}", "-s", self.shells, "-n", w.lane, "-c", cwd,
                              "-x", "160", "-y", "45", shell)
        if p.returncode != 0:
            raise server.Refused(500, "tmux could not open a shell: %s" % (p.stderr.strip() or "error"))
        return {"pane": p.stdout.strip(), "created": True, "cwd": cwd}

    def close_shell(self, d, sid):
        w = self.worker(d.get("lane"))
        pane = self.shell_pane(w.lane)
        if not pane:
            return {"closed": False}
        p = self.tmux.run("kill-pane", "-t", pane)
        return {"closed": p.returncode == 0}

    def install(self, httpd, app):
        app.pages = {"/": "console.html", "/notes": "index.html"}
        app.get_routes.update({"/api/lanes": self.lanes, "/api/screen": self.screen})
        app.post_routes.update({"/api/paste": self.paste, "/api/keys": self.keys, "/api/shell/open": self.open_shell,
                                "/api/shell/close": self.close_shell})


def main(project, m, roots, port=0, no_browser=False, opener=None, out=None):
    out = out or sys.stdout
    if port and not 1024 <= port <= 65535:
        print("pai web: --port must be between 1024 and 65535", file=sys.stderr)
        return 2
    c = Console(project, m, [os.path.realpath(r) for r in roots if os.path.isdir(r)])
    print("pai web (EXPERIMENTAL): project %s, %s" % (project["name"], "run %s" % m.run_id if m else "no run yet"), file=out)
    intro = ["The link signs in once. Only this run's lanes, their shells and these folders are reachable, from this "
             "machine only (127.0.0.1):"]
    intro += ["  notes  %s" % r for r in c.roots] or ["  notes  (none: no task folder and no lane worktree yet)"]
    intro += ["Folder-trust prompts stay yours to answer; pai web never answers them and refuses a paste while one shows."]
    return edit.foreground({"roots": c.roots, "port": port}, "pai web", intro, no_browser, opener, out, ready=c.install)
