#!/usr/bin/env python3
"""Rehearse the owner-to-worker loop offline: post-merge refresh -> verify -> AGENTS.md ->
split plan -> queue -> worker edit -> owner-reviewed PR preview -> dry run (and, with
--publish, the reviewed publish to a local bare remote with the fake gh).

    python3 -B tools/worktree_rehearsal.py [--dir NEW_DIR] [--keep] [--publish] [--stop-after STEP]

Everything happens in a new folder: a bare `origin.git`, the main checkout `repo`, a worker
worktree `lane a` (a space in its name on purpose), a clone that merges an upstream change,
the queues and the plans. The `pai` it runs is this checkout's (bin/pai.py, through a `pai`
shim on PATH, so the printed owner commands run exactly as printed). GitHub is the fake gh
(swarm/tests/fake_gh.py) and the remote is local: nothing reaches the network or an account.

Each step prints the command, its output and a CHECK line; the run stops at the first failed
check (exit 1). After every step the rehearsal records where it stands in two files in the
folder: `rehearsal-state.json` (the steps done and the values later steps need) and a synthetic
task card, `TASK-CARD.md` (a pai task note: the goal, each step's stop point with the durable
files it left, and the exact resume command). `--stop-after STEP` ends cleanly after a step;
`--resume DIR` continues from those files alone (nothing is kept in memory between runs).
Exit 0 when every check holds.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAI = os.path.join(ROOT, "bin", "pai.py")
FAKE_GH = os.path.join(ROOT, "swarm", "tests", "fake_gh.py")
STEPS = ("setup", "refresh", "verify", "rules", "plan", "queue", "edit", "preview", "dryrun", "publish")
DURABLE = {"setup": "origin.git, repo/, lane a/ (branch lane/a), upstream/ (the merged change, not fetched)",
           "refresh": "repo's refs/remotes/origin/main = the merged base; lane a fast-forwarded",
           "verify": "nothing new: `pai worktree verify` reads only",
           "rules": "lane a/AGENTS.md (the pai-managed section)",
           "plan": "goals.md, split.json (the split plan)",
           "queue": "queues/*.json and their .log, split.json.accepted.json",
           "edit": "lane a/src/report/__init__.py changed; NOTES.txt staged (unrelated)",
           "preview": "pr.json (the owner-reviewed PR plan)",
           "dryrun": "nothing new: publish without --yes changes nothing",
           "publish": "the commit on lane/a, origin's lane/a, the fake gh's PR, pr.json.journal"}
IDENT = {"GIT_AUTHOR_NAME": "Owner", "GIT_AUTHOR_EMAIL": "owner@example.invalid", "GIT_COMMITTER_NAME": "Owner",
         "GIT_COMMITTER_EMAIL": "owner@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
GOALS = """# R4 demo: export the report as JSON
- export: Add a JSON export to the report | paths: src/report/ | verify: python3 -m unittest discover -s tests | prereqs: none | needs:
- docs: Document the JSON export | paths: docs/export.md | verify: test -s docs/export.md | prereqs: none | needs: export
"""


class Failed(Exception):
    pass


class Rehearsal:
    def __init__(self, d, out=print):
        self.d, self.say = d, out
        self.origin, self.repo, self.lane = (os.path.join(d, x) for x in ("origin.git", "repo", "lane a"))
        self.bin, self.queues = os.path.join(d, "bin"), os.path.join(d, "queues")
        self.env = dict(os.environ, **IDENT)
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "PAI_CONFIG", "XDG_CONFIG_HOME"):
            self.env.pop(k, None)
        self.env.update(HOME=os.path.join(d, "home"), PAI_SWARM_HOME=os.path.join(d, "state"),
                        PAI_WORKBENCH_HOME=os.path.join(d, "cfg"), PAI_WORKBENCH_DATA=os.path.join(d, "data"),
                        PAI_SWARM_GH=FAKE_GH, FAKE_GH_STATE=os.path.join(d, "gh.json"), FAKE_GH_REMOTE=self.origin,
                        PAI_JEV_CREDENTIAL_STORE="none", PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))

    # -- where the rehearsal stands (durable: a resumed run reads only these files)
    def save_state(self, done):
        values = {k: getattr(self, k) for k in ("merged", "plan_path", "pr_plan") if hasattr(self, k)}
        with open(os.path.join(self.d, "rehearsal-state.json"), "w") as f:
            json.dump({"done": done, "values": values}, f, indent=1)
        nxt = next((x for x in STEPS if x not in done), None)
        rows = ["| %s | %s | %s |" % (x, "done" if x in done else ("NEXT" if x == nxt else "todo"), DURABLE[x])
                for x in STEPS]
        card = ["---", "status: %s" % ("done" if nxt is None else "in-progress"), "priority: 1",
                "stopped_after: %s" % (done[-1] if done else "nothing"), "resume_with: %s" % (nxt or "nothing"), "---",
                "# R4 rehearsal: export the report as JSON", "",
                "Owner-to-worker loop, offline: refresh, verify, rules, split, queue, edit, preview, dry run.", "",
                "## Stop and resume points", "", "| Step | State | Durable files it leaves |", "| --- | --- | --- |"] + rows + [
                "", "Resume (reads only the files above and rehearsal-state.json):", "",
                "    python3 -B tools/worktree_rehearsal.py --resume %s%s" % (shlex.quote(self.d), "" if nxt != "publish"
                                                                          else " --publish"), "",
                "## Acceptance criteria", "", "- every CHECK line of the rehearsal holds", "- nothing reaches the network", ""]
        with open(os.path.join(self.d, "TASK-CARD.md"), "w") as f:
            f.write("\n".join(card))

    def load_state(self):
        with open(os.path.join(self.d, "rehearsal-state.json")) as f:
            st = json.load(f)
        for k, v in st["values"].items():
            setattr(self, k, v)
        return st["done"]

    # -- helpers
    def run(self, argv, show=True, cwd=None, ok=(0,), shell=False):
        if show:
            self.say("\n$ %s" % (argv if shell else " ".join(shlex.quote(a) for a in argv)))
        p = subprocess.run(argv, cwd=cwd or self.d, env=self.env, capture_output=True, text=True, shell=shell,
                           executable="/bin/bash" if shell else None, stdin=subprocess.DEVNULL, timeout=300)
        if show:
            text = (p.stdout + p.stderr).rstrip()
            if text:
                self.say("\n".join("  " + l for l in text.splitlines()))
        if ok is not None and p.returncode not in ok:
            raise Failed("exit %s from %s" % (p.returncode, argv if shell else argv[:4]))
        return p

    def pai(self, *args, ok=(0,), show=True):
        return self.run(["pai"] + list(args), ok=ok, show=show)

    def git(self, cwd, *args):
        return self.run(["git", "-C", cwd] + list(args), show=False).stdout.strip()

    def check(self, what, cond, detail=""):
        self.say("CHECK %s: %s%s" % ("ok  " if cond else "FAIL", what, "" if cond else " (%s)" % detail))
        if not cond:
            raise Failed(what)

    def gh_calls(self):
        try:
            with open(self.env["FAKE_GH_STATE"] + ".log") as f:
                return [json.loads(l) for l in f if l.strip()]
        except OSError:
            return []

    # -- steps
    def setup(self):
        self.say("1. A repository, a worker worktree on the old base, and an upstream merge")
        os.makedirs(self.bin)
        with open(os.path.join(self.bin, "pai"), "w") as f:
            f.write("#!/bin/sh\nexec %s -B %s \"$@\"\n" % (shlex.quote(sys.executable), shlex.quote(PAI)))
        os.chmod(os.path.join(self.bin, "pai"), 0o755)
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": []}, f)
        self.run(["git", "init", "-q", "--bare", "-b", "main", self.origin], show=False)
        self.run(["git", "init", "-q", "-b", "main", self.repo], show=False)
        for rel, text in (("README.md", "# Report\n"), ("src/report/__init__.py", "def rows():\n    return [1, 2]\n"),
                          ("tests/test_report.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_rows(self):\n"
                                                   "        self.assertTrue(True)\n")):
            os.makedirs(os.path.dirname(os.path.join(self.repo, rel)) or self.repo, exist_ok=True)
            with open(os.path.join(self.repo, rel), "w") as f:
                f.write(text)
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "init")
        self.git(self.repo, "remote", "add", "origin", self.origin)
        self.git(self.repo, "push", "-q", "origin", "main")
        self.git(self.repo, "fetch", "-q", "origin")
        self.git(self.repo, "worktree", "add", "-q", "-b", "lane/a", self.lane)
        up = os.path.join(self.d, "upstream")
        self.run(["git", "clone", "-q", self.origin, up], show=False)
        with open(os.path.join(up, "CHANGELOG.md"), "w") as f:
            f.write("- merged: the previous PR\n")
        self.git(up, "add", "CHANGELOG.md")
        self.git(up, "commit", "-qm", "Merge the previous PR")
        self.git(up, "push", "-q", "origin", "main")
        self.merged = self.git(up, "rev-parse", "HEAD")
        self.say("  origin/main moved to %s on the server; nothing was fetched here yet" % self.merged[:12])

    def refresh(self):
        self.say("\n2. Post-merge refresh: pai prints the owner's commands; the owner runs them")
        p = self.pai("worktree", "refresh", "--repo", self.repo, "--worktree", self.lane, "--json", show=False)
        plan = json.loads(p.stdout)
        self.pai("worktree", "refresh", "--repo", self.repo, "--worktree", self.lane, "--shell", "bash")
        self.check("refresh changed nothing by itself", self.git(self.lane, "rev-parse", "HEAD") != self.merged)
        self.say("\n  (the owner runs the printed Bash commands, unchanged:)")
        p = self.run(" && ".join(plan["commands"]["bash"]), shell=True)
        self.check("the worktree now contains the merged base",
                   self.git(self.lane, "merge-base", "--is-ancestor", self.merged, "HEAD") == "" and
                   self.git(self.repo, "rev-parse", "origin/main") == self.merged)

    def verify(self):
        self.say("\n3. Before any dispatch: does the worktree itself contain the fetched base?")
        base = self.git(self.repo, "rev-parse", "origin/main")
        p = self.pai("worktree", "verify", self.lane, "--base", base)
        self.check("verify exits 0 for the refreshed worktree", p.returncode == 0)

    def rules(self):
        self.say("\n4. Repository rules for the lane: preview, then write the managed AGENTS.md section")
        args = ["worktree", "init", self.lane, "--goal", "Add a JSON export to the report", "--scope", "src/report/",
                "--lane", "a", "--owner", "owner", "--check", "unit=python3 -m unittest discover -s tests"]
        self.pai(*args)
        self.check("the preview wrote nothing", not os.path.exists(os.path.join(self.lane, "AGENTS.md")))
        self.pai(*(args + ["--write"]))
        with open(os.path.join(self.lane, "AGENTS.md"), encoding="utf-8") as f:
            text = f.read()
        self.check("AGENTS.md has the managed section with the lane's scope", "src/report/" in text and "lane `a`" in text,
                   text[:300])

    def plan(self):
        self.say("\n5. Split the goal into worker slices (stable ids, dependencies, owners)")
        goals = os.path.join(self.d, "goals.md")
        with open(goals, "w") as f:
            f.write(GOALS)
        self.plan_path = os.path.join(self.d, "split.json")
        self.pai("plan", "propose", "--parent", "R4DEMO", "--goals", goals, "--workers", "2", "--repo", self.repo,
                 "--out", self.plan_path)
        self.pai("plan", "check", self.plan_path, "--queues", self.queues)
        with open(self.plan_path) as f:
            sl = {s["id"]: s for s in json.load(f)["slices"]}
        self.check("slices get stable ids from the parent and keep their dependency",
                   [(k, s["item"], s["needs"]) for k, s in sorted(sl.items())] ==
                   [("docs", "R4DEMO-002", ["export"]), ("export", "R4DEMO-001", [])], sl)
        self.check("the slice in src/report/ goes to the lane that owns it", sl["export"]["worker"] == "a", sl["export"])

    def queue(self):
        self.say("\n6. Accept the plan into the workers' durable queues (twice: nothing is added twice)")
        os.makedirs(self.queues, exist_ok=True)
        self.pai("plan", "accept", self.plan_path, "--queues", self.queues, "--write")
        before = sorted(os.listdir(self.queues))
        sizes = {f: os.path.getsize(os.path.join(self.queues, f)) for f in before}
        self.pai("plan", "accept", self.plan_path, "--queues", self.queues, "--write")
        self.check("a second accept changes no queue", {f: os.path.getsize(os.path.join(self.queues, f))
                                                       for f in sorted(os.listdir(self.queues))} == sizes)
        for f in sorted(os.listdir(self.queues)):
            if f.endswith(".json"):
                self.pai("queue", "status", os.path.join(self.queues, f))

    def edit(self):
        self.say("\n7. The worker does its slice (simulated: one file changed, one staged unrelated edit)")
        with open(os.path.join(self.lane, "src", "report", "__init__.py"), "a") as f:
            f.write("\n\ndef as_json():\n    import json\n    return json.dumps(rows())\n")
        with open(os.path.join(self.lane, "NOTES.txt"), "w") as f:
            f.write("scratch, not for the PR\n")
        self.git(self.lane, "add", "NOTES.txt")

    def preview(self):
        self.say("\n8. The owner reviews a PR preview of the approved path only")
        body = os.path.join(self.d, "body.md")
        with open(body, "w") as f:
            f.write("Adds `as_json()` to the report.\n")
        self.pr_plan = os.path.join(self.d, "pr.json")
        self.pai("worktree", "preview", self.lane, "--path", "src/report/__init__.py", "--title",
                 "report: add a JSON export", "--body-file", body, "--github", "owner/report", "--out", self.pr_plan)
        with open(self.pr_plan) as f:
            pv = json.load(f)
        self.check("the preview keeps the staged unrelated edit apart", pv["paths"]["staged_unrelated"] == ["NOTES.txt"],
                   pv["paths"])

    def dryrun(self):
        self.say("\n9. Dry run: publish without --yes changes nothing")
        head = self.git(self.lane, "rev-parse", "HEAD")
        p = self.pai("worktree", "publish", self.pr_plan, ok=(1,))
        self.check("the dry run says what it would do and did nothing", "Nothing was done" in p.stdout and
                   self.git(self.lane, "rev-parse", "HEAD") == head and
                   not self.git(self.origin, "branch", "--list", "lane/a") and not self.gh_calls_create(), p.stdout[-300:])

    def gh_calls_create(self):
        return [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]

    def publish(self):
        self.say("\n10. The owner approves: publish --yes --draft (local bare remote, fake gh)")
        self.pai("worktree", "publish", self.pr_plan, "--yes", "--draft")
        head = self.git(self.lane, "rev-parse", "HEAD")
        self.check("pushed exactly the reviewed commit and opened one draft PR",
                   self.git(self.origin, "rev-parse", "refs/heads/lane/a") == head and len(self.gh_calls_create()) == 1
                   and "--draft" in self.gh_calls_create()[0])
        self.check("the unrelated staged edit stayed staged and out of the commit",
                   self.git(self.lane, "diff", "--cached", "--name-only") == "NOTES.txt" and
                   "NOTES.txt" not in self.git(self.lane, "show", "--name-only", "--format=", head).split("\n"))
        self.pai("worktree", "publish", self.pr_plan, "--yes", "--draft")
        self.check("a retry reuses the PR", len(self.gh_calls_create()) == 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="a new folder (default: a new temporary one)")
    ap.add_argument("--keep", action="store_true", help="keep the folder afterwards")
    ap.add_argument("--publish", action="store_true", help="also publish to the local remote with the fake gh")
    ap.add_argument("--stop-after", choices=STEPS, help="stop cleanly after this step (the folder is kept)")
    ap.add_argument("--resume", metavar="DIR", help="continue a stopped rehearsal from its folder's files")
    a = ap.parse_args(argv)
    if a.resume:
        d = os.path.realpath(a.resume)
        if not os.path.isfile(os.path.join(d, "rehearsal-state.json")):
            print("worktree_rehearsal: %s has no rehearsal-state.json; nothing to resume" % d)
            return 2
        a.keep = True
    else:
        d = a.dir or tempfile.mkdtemp(prefix="pai-rehearsal-")
        if a.dir:
            if os.path.exists(d):
                print("worktree_rehearsal: %s exists; give a new folder" % d)
                return 2
            os.makedirs(d)
        d = os.path.realpath(d)
    r = Rehearsal(d)
    steps = list(STEPS) if a.publish else list(STEPS[:-1])
    done = r.load_state() if a.resume else []
    print("pai worktree rehearsal in %s (offline: local bare remote, fake gh)%s" % (
        d, "; resuming after %s" % (done[-1] if done else "nothing") if a.resume else ""))
    code = 0
    try:
        for step in steps:
            if step in done:
                print("(%s: done in an earlier run, skipped)" % step)
                continue
            getattr(r, step)()
            done.append(step)
            r.save_state(done)
            if step == a.stop_after:
                print("\nSTOPPED after %s (on request). The state is in %s: TASK-CARD.md says how to resume." % (step, d))
                return 0
        print("\nRehearsal complete: every check held.")
    except Failed as e:
        print("\nREHEARSAL FAILED at a check: %s (folder kept: %s)" % (e, d))
        code, a.keep = 1, True
    finally:
        if not a.keep and not a.stop_after:
            shutil.rmtree(d, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
