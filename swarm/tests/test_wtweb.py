"""pai worktree web (R3-071..R3-090): the HTTP API behind the page, against disposable
repositories, a local bare remote and the fake gh. The browser itself is covered by the
container smoke (see the R3 handoff); here every route is checked for what it may and may not do."""
import hashlib
import http.client
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@helpers.needs_git
class WorktreeWeb(helpers.Fixture):
    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.tmp, "lane a wt")
        git(self.repo, "worktree", "add", "-q", "-b", "lane/a", self.wt)
        self.queues = os.path.join(self.tmp, "queues")
        self.env["PAI_SWARM_HOME"] = os.path.join(self.tmp, "swarm home")
        p = subprocess.Popen([sys.executable, "-B", PAI, "worktree", "web", "--repo", self.repo, "--queues", self.queues,
                              "--no-browser"], cwd=self.tmp, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait(), p.stdout.close(), p.stderr.close()))
        lines = []
        while not any("open:" in l for l in lines):
            line = p.stdout.readline()
            if not line:
                self.fail("no link: %s" % p.stderr.read())
            lines.append(line)
        url = [l for l in lines if "open:" in l][0].split("open:")[1].strip()
        self.port = int(url.split(":")[2].split("/")[0])
        st, hdrs, _ = self.req("GET", url.split(str(self.port), 1)[1])
        self.assertEqual(st, 303)
        self.cookie = hdrs["Set-Cookie"].split(";")[0]
        self.csrf = self.req("GET", "/api/session", cookie=self.cookie)[2]["csrf"]

    def req(self, method, path, body=None, cookie=None, csrf=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": "127.0.0.1:%d" % self.port}
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

    def get(self, path):
        st, _, d = self.req("GET", path, cookie=self.cookie)
        return st, d

    def post(self, path, body):
        st, _, d = self.req("POST", path, body, cookie=self.cookie, csrf=self.csrf)
        return st, d

    def q(self, wt, file="AGENTS.md"):
        from urllib.parse import quote
        return "wt=%s&file=%s" % (quote(wt), quote(file))

    def cli(self, *args):
        p = subprocess.run([sys.executable, "-B", PAI] + list(args), capture_output=True, text=True, env=self.env,
                           cwd=self.tmp, stdin=subprocess.DEVNULL)
        return p

    def test_chooser_labels_and_guards(self):
        st, d = self.get("/api/wt/list")
        self.assertEqual(st, 200)
        labels = {w["path"]: w["label"] for w in d["worktrees"]}
        self.assertEqual(labels[os.path.realpath(self.wt)], "repo · lane/a")
        self.assertEqual(labels[os.path.realpath(self.repo)], "repo · main (main checkout)")
        self.assertEqual(self.req("GET", "/api/wt/list")[0], 401, "signed in only")
        self.assertEqual(self.req("POST", "/api/wt/save", {}, cookie=self.cookie)[0], 403, "CSRF header required")
        self.assertEqual(self.get("/api/wt/file?" + self.q(self.tmp))[0], 403, "only listed worktrees")
        self.assertEqual(self.get("/api/wt/file?" + self.q(self.wt, "../x.md"))[0], 400)
        st, d = self.post("/api/wt/save", {"wt": self.repo, "file": "AGENTS.md", "text": "x", "base": None, "confirm": sha("x")})
        self.assertEqual(st, 403, "never the main checkout")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "AGENTS.md")))
        os.symlink(os.path.join(self.tmp, "elsewhere.md"), os.path.join(self.wt, "CLAUDE.md"))
        self.assertEqual(self.get("/api/wt/file?" + self.q(self.wt, "CLAUDE.md"))[0], 403)

    def test_generate_proposes_then_save_conflict_and_undo(self):
        target = os.path.join(self.wt, "AGENTS.md")
        st, f = self.get("/api/wt/file?" + self.q(self.wt))
        self.assertEqual((st, f["exists"], f["base"]), (200, False, None))
        draft = "# Lane notes\n\nMy own words.\n"
        st, g = self.post("/api/wt/generate", {"wt": self.wt, "text": draft, "goal": "Fix the tests", "scope": [],
                                               "checks": [["unit", "make test"]], "accept": ["tests-first"]})
        self.assertEqual(st, 200, g)
        self.assertFalse(os.path.exists(target), "a proposal is never written")
        self.assertFalse(g["applied"])
        self.assertTrue(g["text"].startswith(draft), "manual text first, unchanged")
        self.assertIn("+- Goal: Fix the tests", g["diff"])
        self.assertEqual([s["kind"] for s in g["sections"]], ["manual", "generated", "inherited", "generated"])
        text = g["text"]
        st, d = self.post("/api/wt/save", {"wt": self.wt, "text": text, "base": None, "confirm": sha(text + "x")})
        self.assertEqual(st, 400, "a save confirms the exact revision")
        st, d = self.post("/api/wt/save", {"wt": self.wt, "text": text, "base": None, "confirm": sha(text)})
        self.assertEqual((st, d["created"]), (200, True), d)
        with open(target) as fh:
            self.assertEqual(fh.read(), text)
        base = d["base"]
        with open(target, "a") as fh:
            fh.write("an agent's line\n")
        st, d = self.post("/api/wt/diff", {"wt": self.wt, "text": text + "mine\n", "base": base})
        self.assertTrue(d["stale"])
        st, d = self.post("/api/wt/save", {"wt": self.wt, "text": text + "mine\n", "base": base, "confirm": sha(text + "mine\n")})
        self.assertEqual(st, 409)
        st, d = self.post("/api/wt/undo", {"wt": self.wt})
        self.assertEqual(st, 409, "undo would lose the agent's line")
        with open(target) as fh:
            self.assertTrue(fh.read().endswith("an agent's line\n"))
        st, f = self.get("/api/wt/file?" + self.q(self.wt))
        st, d = self.post("/api/wt/save", {"wt": self.wt, "text": f["text"] + "mine\n", "base": f["base"],
                                           "confirm": sha(f["text"] + "mine\n")})
        self.assertEqual(st, 200)
        st, d = self.post("/api/wt/undo", {"wt": self.wt})
        self.assertEqual((st, d["removed"]), (200, False))
        with open(target) as fh:
            self.assertTrue(fh.read().endswith("an agent's line\n"), "the text before this page's last save is back")
        self.assertEqual(self.post("/api/wt/undo", {"wt": self.wt})[0], 409, "one undo per save")

    def test_refresh_readiness_and_retry_status(self):
        self.advance_main()
        git(self.repo, "fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main")
        st, r = self.get("/api/wt/refresh?" + self.q(self.wt))
        self.assertEqual(st, 200)
        fetched = git(self.repo, "rev-parse", "origin/main")
        self.assertEqual((r["expected"], r["actual"], r["dispatch_ready"]), (fetched, self.c0, False))
        self.assertTrue(any("fetch" in c for c in r["bash"]))
        self.assertTrue(any("$BASE = git" in c for c in r["powershell"]))
        st, a = self.post("/api/wt/recheck", {"wt": self.wt})
        st, a = self.post("/api/wt/recheck", {"wt": self.wt})
        self.assertEqual(len(a["attempts"]), 2)
        self.assertIn("not seen yet", a["last"]["status"])
        git(self.wt, "merge", "-q", "--ff-only", fetched)
        st, a = self.post("/api/wt/recheck", {"wt": self.wt})
        self.assertEqual((a["dispatch_ready"], a["last"]["ok"], a["last"]["status"]), (True, True, "ready"))

    def test_web_and_terminal_give_the_same_plan_and_pr_preview_then_publish(self):
        goals = "- api: Add it | paths: src/ | verify: make test | prereqs: none\n" \
                "- docs: Document it | paths: docs/ | verify: read | prereqs: none | needs: api\n"
        st, w = self.post("/api/plan/propose", {"parent": "T", "goals": goals, "workers": 2})
        gf = os.path.join(self.tmp, "goals.md")
        with open(gf, "w") as fh:
            fh.write(goals)
        cli = json.loads(self.cli("plan", "propose", "--parent", "T", "--goals", gf, "--workers", "2", "--repo", self.repo,
                                  "--json").stdout)
        self.assertEqual(w["plan"]["slices"], cli["slices"], "same data model, same outcome")
        self.assertEqual(w["queue_states"], {"api": "not queued", "docs": "not queued"})
        with open(os.path.join(self.wt, "notes.md"), "w") as fh:
            fh.write("n\n")
        req = {"wt": self.wt, "title": "docs: notes", "body": "Adds notes.", "paths": ["notes.md"], "base": "main"}
        st, nogh = self.post("/api/pr/preview", req)
        st, res = self.post("/api/pr/publish", {"digest": nogh["digest"], "state": "draft", "confirm": True})
        self.assertIn("is not a GitHub remote", res["log"])
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.c0, "refused before any commit or push")
        req["github"] = "o/r"
        st, pv = self.post("/api/pr/preview", req)
        self.assertEqual(st, 200, pv)
        bf = os.path.join(self.tmp, "body.md")
        with open(bf, "w") as fh:
            fh.write("Adds notes.")
        cli = json.loads(self.cli("worktree", "preview", self.wt, "--path", "notes.md", "--title", "docs: notes",
                                  "--body-file", bf, "--github", "o/r", "--json").stdout)
        self.assertEqual(pv["preview"], cli)
        self.assertEqual((pv["preview"]["base"]["last_fetched"], pv["preview"]["head"], pv["preview"]["pushed"]["state"]),
                         (self.c0, self.c0, "not on the remote"))
        self.assertEqual(git(self.wt, "status", "--porcelain"), "?? notes.md", "a preview changes nothing")
        for bad in ({"digest": pv["digest"], "confirm": True}, {"digest": pv["digest"], "state": "draft"},
                    {"digest": "0" * 64, "state": "draft", "confirm": True}):
            self.assertIn(self.post("/api/pr/publish", bad)[0], (400, 404))
        self.assertEqual(git(self.origin, "branch", "--list", "lane/a"), "", "nothing published before the review")
        st, res = self.post("/api/pr/publish", {"digest": pv["digest"], "state": "draft", "confirm": True})
        self.assertEqual((st, res["code"]), (200, 0), res)
        self.assertTrue(res["pr"].startswith("https://github.example/"), res)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/lane/a"), git(self.wt, "rev-parse", "HEAD"))
        st, res = self.post("/api/pr/publish", {"digest": pv["digest"], "state": "draft", "confirm": True})
        self.assertIn("reused", res["log"], "a repeat resumes from the journal")


    def test_walkthrough_from_worktree_creation_to_an_owner_reviewed_pr_preview(self):
        new = os.path.join(self.tmp, "fresh lane")
        p = self.cli("worktree", "refresh", "--repo", self.repo, "--new", new, "--branch", "lane/fresh", "--shell", "bash",
                     "--json")
        info = json.loads(p.stdout)
        cmds = [c for c in info["commands"]["bash"] if not c.startswith("pai ")]
        r = subprocess.run(["bash", "-ec", "\n".join(cmds)], capture_output=True, text=True, env=self.env, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        base = git(self.repo, "rev-parse", "origin/main")
        self.assertEqual(self.cli("worktree", "verify", new, "--base", base).returncode, 0)
        st, d = self.get("/api/wt/list")
        self.assertIn(os.path.realpath(new), [w["path"] for w in d["worktrees"]])
        st, g = self.post("/api/wt/generate", {"wt": new, "text": "", "goal": "Add notes", "scope": []})
        st, s = self.post("/api/wt/save", {"wt": new, "text": g["text"], "base": None, "confirm": sha(g["text"])})
        self.assertEqual(st, 200, s)
        st, r2 = self.get("/api/wt/refresh?" + self.q(new))
        self.assertTrue(r2["dispatch_ready"])
        st, pl = self.post("/api/plan/propose", {"parent": "W", "workers": 1,
                                                 "goals": "- notes: Add notes | paths: notes.md | verify: read | prereqs: none\n"})
        self.assertEqual(pl["problems"], [])
        with open(os.path.join(new, "notes.md"), "w") as fh:
            fh.write("notes\n")
        st, pv = self.post("/api/pr/preview", {"wt": new, "title": "docs: notes", "paths": ["notes.md"], "github": "o/r"})
        self.assertEqual((st, pv["preview"]["problems"]), (200, []))
        self.assertEqual(pv["preview"]["paths"]["unexpected"], ["AGENTS.md"], "the instruction file stays out of the PR")
        self.assertTrue(pv["digest"])
        self.assertEqual(git(new, "rev-parse", "HEAD"), base, "the walkthrough ends at the owner's review: nothing published")


if __name__ == "__main__":
    unittest.main()
