"""pai worktree web: worktrees, their instruction files, a split plan and a PR preview in the browser.

    pai worktree web [--repo PATH] [--queues DIR] [--port N] [--no-browser]

The page calls the same functions as the terminal commands (`pai worktree list/refresh/verify/
init/preview/publish`, `pai plan propose/check`), so both show the same data. It runs on
127.0.0.1 with the protections of `pai edit` (a one-time sign-in link, an HttpOnly SameSite=Strict
cookie, a CSRF header on every write, Host/Origin checks, a strict Content-Security-Policy).

Instruction files: only AGENTS.md, CLAUDE.md or CONTRIBUTING.md at a linked worktree's root,
never through a symbolic link, never in the main checkout. Generating only proposes text into
the editor; a save names the exact revision it confirms and is refused if the file changed on
disk since it was loaded. The last save made here can be undone while the file is still what
this page wrote.

Nothing here fetches, resets, commits or pushes on its own. A PR is published only by the
explicit review action (the preview's digest, and draft or ready chosen), through
`pai worktree publish`, whose plan and journal are kept in $PAI_SWARM_HOME/web-plans so an
interrupted publish can be resumed.
"""
import contextlib
import difflib
import hashlib
import hmac
import io
import json
import os
import secrets
import stat
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
MD_DIR = os.path.join(HERE, "mdserver")
if MD_DIR not in sys.path:
    sys.path.insert(0, MD_DIR)
import agentsmd  # noqa: E402
import gitprep  # noqa: E402
import refresh  # noqa: E402
import server  # noqa: E402
import splitplan  # noqa: E402
import wtpr  # noqa: E402

MAX_BYTES = server.MAX_BYTES


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plans_dir():
    h = os.environ.get("PAI_SWARM_HOME") or os.path.join("~", ".cache", "pai-swarm")
    d = os.path.join(os.path.expanduser(h), "web-plans")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


class WorktreeWeb:
    def __init__(self, repo, remote="origin", base="main", queues=None):
        self.repo, self.remote, self.base, self.queues = repo, remote, base, queues
        self.undo = {}       # (worktree, file) -> {"before": bytes or None, "after": sha}
        self.attempts = {}   # worktree -> [{at, expected, actual, contains, ok}]

    # -- worktrees ---------------------------------------------------------------------------
    def describe(self):
        return refresh.describe(self.repo, self.remote, self.base)

    def worktree(self, path, writable=False):
        """The listed worktree for `path`; anything else is refused."""
        info = self.describe()
        want = os.path.realpath(str(path or ""))
        for w in info["worktrees"]:
            if os.path.realpath(w["path"]) == want and w["exists"]:
                if writable and w["path"] == info["repository"]["path"]:
                    raise server.Refused(403, "this is the main checkout; instruction files are written only in a "
                                              "linked worktree")
                return w, info
        raise server.Refused(403, "not a worktree of %s" % info["repository"]["path"])

    def list(self, q, sid):
        info = self.describe()
        main = info["repository"]["path"]
        label = os.path.basename(main)
        for w in info["worktrees"]:
            w["main"] = w["path"] == main
            w["label"] = "%s · %s%s" % (label, w["branch"] or "detached " + (w["head"] or "")[:8],
                                        " (main checkout)" if w["main"] else "")
        return info

    # -- instruction files -------------------------------------------------------------------
    def target(self, wt, file):
        if file not in agentsmd.INSTRUCTION_NAMES:
            raise server.Refused(400, "only %s at the worktree root" % ", ".join(agentsmd.INSTRUCTION_NAMES))
        path = os.path.join(wt, file)
        if os.path.islink(path):
            raise server.Refused(403, "%s is a symbolic link; it is not followed" % file)
        if os.path.exists(path) and not stat.S_ISREG(os.lstat(path).st_mode):
            raise server.Refused(403, "%s is not a regular file" % file)
        return path

    def raw(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(data) > MAX_BYTES:
            raise server.Refused(413, "file is larger than 2 MB")
        return data

    def file(self, q, sid):
        w, _ = self.worktree(q.get("wt", [""])[0])
        name = q.get("file", ["AGENTS.md"])[0]
        path = self.target(w["path"], name)
        data = self.raw(path)
        try:
            text = (data or b"").decode("utf-8")
        except UnicodeDecodeError:
            raise server.Refused(415, "not UTF-8 text")
        files, rules = agentsmd.inherited(w["path"], [])
        bom = text.startswith("﻿")
        clean = (text[1:] if bom else text).replace("\r\n", "\n")
        return {"worktree": w["path"], "branch": w["branch"], "file": name, "exists": data is not None, "text": clean,
                "base": sha(data) if data is not None else None, "crlf": b"\r\n" in (data or b""), "bom": bom,
                "sections": agentsmd.sections(clean), "instruction_files": files, "inherited": rules,
                "conflicts": agentsmd.conflicts(rules), "undo": (w["path"], name) in self.undo}

    def generate(self, d, sid):
        w, _ = self.worktree(d.get("wt"), writable=True)
        checks = [tuple(c) for c in d.get("checks") or [] if isinstance(c, list) and len(c) == 2 and all(c)]
        text = str(d.get("text") or "")
        try:
            p = agentsmd.plan(w["path"], str(d.get("goal") or "").strip() or "(no goal given)",
                              [str(x) for x in d.get("scope") or [] if str(x).strip()], d.get("owner") or None,
                              d.get("lane") or None, checks, d.get("file") or "AGENTS.md", bool(d.get("jev")),
                              [str(x) for x in d.get("accept") or []], None, existing=text)
        except agentsmd.InitError as e:
            raise server.Refused(409, str(e))
        new = p["new"].lstrip("﻿").replace("\r\n", "\n")
        diff = list(difflib.unified_diff(text.split("\n"), new.split("\n"), "editor", "proposed", lineterm="", n=2))
        return {"text": new, "diff": diff, "proposals": p["proposals"], "accepted": p["accepted"], "jev": p["jev"],
                "template": p["template"], "conflicts": p["conflicts"], "notes": p["notes"], "sections": p["sections"],
                "applied": False}

    def diff(self, d, sid):
        w, _ = self.worktree(d.get("wt"))
        path = self.target(w["path"], d.get("file") or "AGENTS.md")
        data = self.raw(path)
        old = (data or b"").decode("utf-8", "replace").lstrip("﻿").replace("\r\n", "\n")
        text = str(d.get("text") or "").replace("\r\n", "\n")
        cur = sha(data) if data is not None else None
        return {"diff": list(difflib.unified_diff(old.split("\n"), text.split("\n"), "a/" + d.get("file", "AGENTS.md"),
                                                  "b/" + d.get("file", "AGENTS.md"), lineterm="", n=3)),
                "stale": cur != d.get("base"), "current": cur, "proposed": sha(text.encode("utf-8"))}

    def _write(self, path, data, mode=0o644):
        tmp = os.path.join(os.path.dirname(path), ".%s.%s.tmp" % (os.path.basename(path), secrets.token_hex(4)))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            return tmp
        except BaseException:
            os.remove(tmp)
            raise

    def save(self, d, sid):
        w, _ = self.worktree(d.get("wt"), writable=True)
        name = d.get("file") or "AGENTS.md"
        path = self.target(w["path"], name)
        text = d.get("text")
        if not isinstance(text, str) or not hmac.compare_digest(str(d.get("confirm") or ""), sha(text.encode("utf-8"))):
            raise server.Refused(400, "the save was not confirmed for this exact revision; review the diff and confirm again")
        before = self.raw(path)
        if (sha(before) if before is not None else None) != d.get("base"):
            raise server.Refused(409, "%s changed on disk since you opened it; nothing was written and your draft is kept"
                                 % name)
        data = text.replace("\r\n", "\n")
        if d.get("crlf"):
            data = data.replace("\n", "\r\n")
        data = (b"\xef\xbb\xbf" if d.get("bom") else b"") + data.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise server.Refused(413, "file would be larger than 2 MB")
        mode = stat.S_IMODE(os.lstat(path).st_mode) if before is not None else 0o644
        tmp = self._write(path, data, mode)
        try:
            now = self.raw(path)  # last look before the atomic replace
            if (sha(now) if now is not None else None) != d.get("base") or os.path.islink(path):
                raise server.Refused(409, "%s changed on disk while saving; nothing was written" % name)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.undo[(w["path"], name)] = {"before": before, "after": sha(data)}
        return {"base": sha(data), "undo": True, "created": before is None}

    def undo_save(self, d, sid):
        w, _ = self.worktree(d.get("wt"), writable=True)
        name = d.get("file") or "AGENTS.md"
        path = self.target(w["path"], name)
        rec = self.undo.get((w["path"], name))
        if not rec:
            raise server.Refused(409, "nothing to undo: this page has not saved %s in this worktree" % name)
        now = self.raw(path)
        if now is None or sha(now) != rec["after"]:
            raise server.Refused(409, "%s changed after this page saved it; undo would lose those edits, so nothing "
                                      "was changed" % name)
        if rec["before"] is None:
            os.remove(path)  # the page created it and nothing changed it since
            base = None
        else:
            tmp = self._write(path, rec["before"], stat.S_IMODE(os.lstat(path).st_mode))
            os.replace(tmp, path)
            base = sha(rec["before"])
        del self.undo[(w["path"], name)]
        return {"restored": True, "base": base, "removed": rec["before"] is None}

    # -- refresh and dispatch readiness ------------------------------------------------------
    def refresh_info(self, q, sid):
        w, info = self.worktree(q.get("wt", [""])[0])
        try:
            p = refresh.plan(self.repo, self.remote, self.base, worktree=w["path"])
        except refresh.RefreshError as e:
            p = {"error": str(e)}
        expected = info["base"]["last_fetched"]
        v = refresh.verify(w["path"], expected) if expected else None
        return {"plan": p, "bash": refresh.commands(p, "bash") if "error" not in p else [],
                "powershell": refresh.commands(p, "powershell") if "error" not in p else [],
                "expected": expected, "actual": w["head"], "verify": v,
                "dispatch_ready": bool(v and v["contains_base"] and not v["problems"]),
                "attempts": self.attempts.get(w["path"], [])}

    def recheck(self, d, sid):
        w, info = self.worktree(d.get("wt"))
        expected = info["base"]["last_fetched"]
        prev = self.attempts.get(w["path"], [])
        v = refresh.verify(w["path"], expected) if expected else None
        ok = bool(v and v["contains_base"] and not v["problems"])
        why = "ready" if ok else ("the base was never fetched" if not expected else
                                  "fetched base not seen yet (the fetch failed or was not run)" if prev and
                                  prev[-1]["expected"] == expected and not prev[-1]["ok"] else
                                  "; ".join(v["problems"]) if v else "unknown")
        prev.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "expected": expected,
                     "actual": v["head"] if v else w["head"], "contains": bool(v and v["contains_base"]), "ok": ok,
                     "status": why})
        self.attempts[w["path"]] = prev[-20:]
        return dict(self.refresh_info({"wt": [w["path"]]}, sid), last=prev[-1])

    # -- split plan --------------------------------------------------------------------------
    def plan_propose(self, d, sid):
        try:
            plan = splitplan.propose(str(d.get("parent") or ""), str(d.get("goals") or ""), int(d.get("workers") or 4),
                                     self.repo)
        except splitplan.PlanError as e:
            raise server.Refused(400, str(e))
        probs, states = splitplan.check(plan, self.queues)
        return {"plan": plan, "problems": probs, "queue_states": states, "queues": self.queues,
                "accept_command": "pai plan propose --parent %s --goals GOALS.md --out PLAN.json && "
                                  "pai plan accept PLAN.json --queues %s --write" % (plan["parent"], self.queues or "DIR")}

    # -- PR preview and publish --------------------------------------------------------------
    def pr_preview(self, d, sid):
        w, _ = self.worktree(d.get("wt"))
        try:
            pv = wtpr.preview(w["path"], [str(p) for p in d.get("paths") or []], str(d.get("title") or ""),
                              str(d.get("body") or ""), None, None, d.get("base") or self.base, self.remote,
                              bool(d.get("offline")), d.get("github") or None)
        except wtpr.PlanError as e:
            raise server.Refused(409, str(e))
        data = json.dumps(pv, indent=1, sort_keys=True).encode("utf-8")
        digest = sha(data)
        if not pv["problems"]:
            path = os.path.join(plans_dir(), "%s.json" % digest[:24])
            if not os.path.exists(path):
                os.replace(self._write(path, data, 0o600), path)
        return {"preview": pv, "digest": digest if not pv["problems"] else None, "text": wtpr.render(pv)}

    def pr_publish(self, d, sid):
        digest = str(d.get("digest") or "")
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise server.Refused(400, "publish needs the digest of a clean preview")
        if d.get("state") not in ("draft", "ready"):
            raise server.Refused(400, "choose draft or ready: the PR's state is the owner's choice")
        if d.get("confirm") is not True:
            raise server.Refused(400, "publishing needs the explicit review confirmation")
        path = os.path.join(plans_dir(), "%s.json" % digest[:24])
        try:
            with open(path, "rb") as f:
                if sha(f.read()) != digest:
                    raise server.Refused(409, "the stored preview does not match; preview again")
        except FileNotFoundError:
            raise server.Refused(404, "no stored preview with that digest; preview again")
        lines = []
        code = wtpr.publish(path, yes=True, draft=d["state"] == "draft", no_pr=False, log=lines.append)
        text = gitprep.redact_url("\n".join(lines))
        url = next((l.split()[1] for l in text.splitlines() if l.startswith(("opened ", "reused "))), None)
        return {"code": code, "log": text, "pr": url, "journal": os.path.basename(path) + ".journal"}

    def install(self, httpd, app):
        app.pages = {"/": "worktrees.html"}
        app.get_routes.update({"/api/wt/list": self.list, "/api/wt/file": self.file, "/api/wt/refresh": self.refresh_info})
        app.post_routes.update({"/api/wt/generate": self.generate, "/api/wt/diff": self.diff, "/api/wt/save": self.save,
                                "/api/wt/undo": self.undo_save, "/api/wt/recheck": self.recheck,
                                "/api/plan/propose": self.plan_propose, "/api/pr/preview": self.pr_preview,
                                "/api/pr/publish": self.pr_publish})


def main(repo, queues=None, port=0, no_browser=False, opener=None, out=None, remote="origin", base="main"):
    import edit
    out = out or sys.stdout
    try:
        main_path, _ = refresh.main_checkout(repo)
    except refresh.RefreshError as e:
        print("pai worktree web: %s" % e, file=sys.stderr)
        return 2
    web = WorktreeWeb(main_path, remote, base, os.path.realpath(queues) if queues else None)
    intro = ["Worktrees of %s: instruction files, refresh state, a split plan and a PR preview." % main_path,
             "Nothing is fetched, committed or pushed except by an explicit, reviewed publish."]
    return edit.foreground({"roots": [], "port": port}, "pai worktree web", intro, no_browser, opener, out,
                           ready=web.install)
