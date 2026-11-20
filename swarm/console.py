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

Each lane shows the same lifecycle name, explanation and next step as `pai status`, an Inspect
view (the evidence behind it and where each piece is read, as `pai inspect`), and the run has a
"Resume run" action: the `pai recover` preview first, then (on a separate, confirmed click) only
its automatic actions. A paste first shows where it would go and how big it is.

What it never does: answer an agent's folder-trust prompt (a paste is refused while one is on
the screen; you answer it yourself, by typing), resend an unknown dispatch or choose for you
(that stays `pai recover --choose` in a terminal), or push, merge or publish anything. The browser names a lane, never a tmux pane: pai looks the
pane up itself, so only this run's lanes and their shells are reachable.
"""
import contextlib
import io
import os
import sys
import time
import types

import agents
import panes
import rc
import recovery
import runstate

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
            raise server.Refused(404, "run %s has no lane %r (its lanes are %s): reload this page to see the run's "
                                      "lanes as they are now" % (self.m.run_id, lane, ", ".join(x.lane for x in self.m.workers)))
        return w

    def agent_pane(self, w, fresh=False):
        hit = self._panes.get(w.lane)
        if hit and not fresh and time.time() - hit[1] < PANE_TTL:
            return hit[0]
        proc = rc.lane_process(self.m, self.tmux, w)
        win = proc.get("window") or {}
        if proc["state"] in ("no-window", "ambiguous", "mismatched") or not win.get("pane_id"):
            self._panes.pop(w.lane, None)
            raise server.Refused(404, "lane %s has no window to show (%s). Next: Resume run (preview) here, or "
                                      "`pai recover --run %s --lane %s`" % (w.lane, proc.get("detail", proc["state"]),
                                                                            self.m.run_id, w.lane))
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

    def target(self, d, fresh=False):
        """(pane id, trust check or None) for {"lane", "target": "agent"|"shell"}. fresh: look the
        pane up again instead of using the cached id (before anything is written to it)."""
        w = self.worker(d.get("lane"))
        if d.get("target") == "shell":
            pane = self.shell_pane(w.lane)
            if not pane:
                raise server.Refused(404, "lane %s has no shell yet (Open a shell)" % w.lane)
            return pane, None
        return self.agent_pane(w, fresh=fresh), w.adapter.waiting_for_trust

    # ---------------------------------------------------------------- GET routes
    def lanes(self, q, sid):
        base = {"project": self.project["name"], "roots": [os.path.basename(r) or r for r in self.roots]}
        if not self.m:
            return dict(base, run=None, lanes=[], hint="No run yet: `pai run` starts one; then reload this page.")
        rows, _ = rc.collect_status(self.m, self.tmux)
        summary = runstate.annotate(self.m, rows)  # the same names and explanations as `pai status`
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
                        "trust": r["phase"] == "waiting-for-trust", "shell": bool(self.shell_pane(r["lane"])),
                        "task": r["ids"]["task"], "lifecycle": r["lifecycle"],
                        "recovery": {k: v for k, v in recovery.plan_lane(self.m, r).items()
                                     if k in ("action", "would", "human", "choices")}})
        return dict(base, run=self.m.run_id, run_key=runstate.run_key(self.m), session=self.m.session,
                    shell_session=self.shells, lanes=out, summary=summary,
                    summary_line=runstate.summary_line(summary), not_started=summary["not_started"])

    def inspect(self, q, sid):
        """`pai inspect LANE` for the browser: lifecycle, why, evidence with its source, next."""
        w = self.worker((q.get("lane") or [""])[0])
        rows, _ = rc.collect_status(self.m, self.tmux)
        runstate.annotate(self.m, rows)
        row = next((r for r in rows if r["lane"] == w.lane), None)
        if row is None:
            return {"lane": w.lane, "lifecycle": {"state": "not-started", "why": "no task was prepared for it",
                                                  "next": []}, "evidence": []}
        return {"lane": w.lane, "task": row["ids"]["task"], "lifecycle": row["lifecycle"],
                "evidence": runstate.sources(self.m, row)}

    def recover_preview(self, q, sid):
        """`pai recover` (preview) for the browser: nothing changes."""
        if not self.m:
            raise server.Refused(404, "no run yet")
        rows, _ = rc.collect_status(self.m, self.tmux)
        summary = runstate.annotate(self.m, rows)
        plans = recovery.build(self.m, rows, {})
        return {"run": runstate.run_key(self.m), "changed": False, "summary_line": runstate.summary_line(summary),
                "interrupted": recovery.unfinished(self.m), "lanes": plans,
                "apply": [p["lane"] for p in plans if p["action"] in recovery.AUTOMATIC],
                "cli": "pai recover --run %s" % self.m.run_id}

    def recover_apply(self, d, sid):
        """`pai recover --apply` for the browser: only the automatic actions (restart, deliver,
        record-delivered); an unknown dispatch, a trust prompt and other human actions are left
        for the terminal. Same session mutex as the command line; its report is returned."""
        if not self.m:
            raise server.Refused(404, "no run yet")
        if d.get("confirm") is not True:
            raise server.Refused(400, "confirm: true is required (the preview comes first)")
        a = types.SimpleNamespace(wait=float(d.get("wait") or 10.0), choose=None, lane=None, apply=True, history=False,
                                  json=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = recovery.main(self.m, a)
        self._panes.clear()  # restarted lanes have new panes
        return {"code": code, "report": buf.getvalue()}

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
    def paste_preview(self, d, sid):
        """Where a paste would go and how big it is, before anything is sent."""
        w = self.worker(d.get("lane"))
        text = d.get("text")
        if not isinstance(text, str):
            raise server.Refused(400, "text must be a string")
        pane, trust = self.target(d, fresh=True)
        res = panes.preview(self.tmux, pane, text, trust=trust, allow_unbracketed=bool(d.get("allow_unbracketed")))
        res.update(lane=w.lane, target=d.get("target") or "agent", pane=pane,
                   program=w.adapter.display if d.get("target") != "shell" else "shell",
                   submit=bool(d.get("enter")))
        return res

    def paste(self, d, sid):
        pane, trust = self.target(d, fresh=True)  # never into a pane id that may be stale
        text = d.get("text")
        if not isinstance(text, str):
            raise server.Refused(400, "text must be a string")
        try:
            res = panes.paste(self.tmux, pane, text, trust=trust, enter=bool(d.get("enter")),
                              allow_unbracketed=bool(d.get("allow_unbracketed")))
        except panes.Refused as e:
            raise server.Refused(409, "%s: %s" % (e.code, e))
        if d.get("target") != "shell":  # what reached the agent is evidence a later handoff needs
            import pastes
            w = self.worker(d.get("lane"))
            try:
                proc = rc.lane_process(self.m, self.tmux, w)
            except Exception:  # noqa: BLE001
                proc = {}
            pastes.record(self.m, w, text, res, "console", proc)
        return res

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
        app.get_routes.update({"/api/lanes": self.lanes, "/api/screen": self.screen, "/api/inspect": self.inspect,
                               "/api/recover": self.recover_preview})
        app.post_routes.update({"/api/paste": self.paste, "/api/paste/preview": self.paste_preview,
                                "/api/keys": self.keys, "/api/shell/open": self.open_shell,
                                "/api/shell/close": self.close_shell, "/api/recover": self.recover_apply})


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
