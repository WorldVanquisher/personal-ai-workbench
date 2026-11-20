"""Task splitting (R3-061..R3-070): a four-worker demo with one dependency and one shared-file
conflict, ownership from worktree AGENTS.md sections, cycles, reassignment, and idempotent
acceptance into durable queues."""
import json
import os
import signal
import subprocess
import time
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")
sys.path.insert(0, helpers.SWARM)
sys.path.insert(0, os.path.dirname(helpers.SWARM))
import agentsmd  # noqa: E402
from pai import workqueue  # noqa: E402

GOALS = """# Export feature
- api: Add the export endpoint | paths: src api/ | verify: python -m pytest tests/api | prereqs: none
- web: Add the export button | paths: web/ | verify: npm test -- export | prereqs: none
- docs: Document export | paths: docs/export.md | verify: proofread by the owner | prereqs: the endpoint exists | needs: api
- cli: Add pai export | paths: web/shared.js, cli/ | verify: python -m pytest tests/cli | prereqs: none
"""


@helpers.needs_git
class SplitPlan(helpers.Fixture):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.upstream, "src api"))
        with open(os.path.join(self.upstream, "src api", "x.py"), "w") as f:
            f.write("x = 1\n")
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-qm", "api")
        git(self.upstream, "push", "-q", "origin", "main")
        git(self.repo, "pull", "-q", "origin", "main")
        self.wt = os.path.join(self.tmp, "lane a wt")
        git(self.repo, "worktree", "add", "-q", "-b", "lane/a", self.wt)
        agentsmd.write(agentsmd.plan(self.wt, "the API", ["src api"], lane="a"))
        self.goals = os.path.join(self.tmp, "goals.md")
        with open(self.goals, "w") as f:
            f.write(GOALS)
        self.plan = os.path.join(self.tmp, "export plan.json")
        self.queues = os.path.join(self.tmp, "queues")

    def pai(self, *args):
        return subprocess.run([sys.executable, "-B", PAI] + list(args), capture_output=True, text=True, env=self.env,
                              cwd=self.tmp, stdin=subprocess.DEVNULL)

    def edit(self, fn):
        with open(self.plan) as f:
            plan = json.load(f)
        fn(plan)
        with open(self.plan, "w") as f:
            json.dump(plan, f)

    def test_four_worker_demo_with_a_dependency_and_a_shared_file_conflict(self):
        p = self.pai("plan", "propose", "--parent", "EXP", "--goals", self.goals, "--workers", "4", "--repo", self.repo,
                     "--out", self.plan)
        self.assertEqual(p.returncode, 1, "the shared file is reported")
        self.assertIn("PROBLEM   web+cli shared-write-paths: web and cli would both write web/shared.js", p.stdout)
        with open(self.plan) as f:
            plan = json.load(f)
        self.assertEqual(plan["parent"], "EXP")
        self.assertEqual([(s["id"], s["item"], s["worker"]) for s in plan["slices"]],
                         [("api", "EXP-001", "a"), ("web", "EXP-002", "b"), ("docs", "EXP-003", "c"), ("cli", "EXP-004", "d")])
        self.assertIn("owns %s" % os.path.realpath(self.wt), plan["slices"][0]["assigned_by"])
        p = self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")
        self.assertEqual(p.returncode, 1)
        self.assertIn("nothing was queued", p.stdout)
        self.assertFalse(os.path.exists(self.queues))
        self.edit(lambda pl: pl["slices"][3].update(paths=["cli/"]))           # edit the split
        p = self.pai("plan", "assign", self.plan, "docs", "b")                 # reassign a slice
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.pai("plan", "assign", self.plan, "api", "c")
        self.assertIn("owned-elsewhere", p.stdout, "lane a owns src api")
        self.pai("plan", "assign", self.plan, "api", "a")
        self.assertEqual(self.pai("plan", "check", self.plan).returncode, 0)
        p = self.pai("plan", "accept", self.plan, "--queues", self.queues, "--slice", "api", "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")  # retry after a partial accept
        self.assertIn("already   api", p.stdout)
        self.assertIn("EXP-003 (blocked: waits for api)", p.stdout)
        self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")
        files = sorted(os.listdir(self.queues))
        self.assertEqual([f for f in files if f.endswith(".queue")], ["EXP-a.queue", "EXP-b.queue", "EXP-d.queue"])
        b = workqueue.load(os.path.join(self.queues, "EXP-b.queue"))
        self.assertEqual([i["id"] for i in b["items"]], ["EXP-002", "EXP-003"], "each item once")
        evs, problems = workqueue.events(os.path.join(self.queues, "EXP-b.queue"))
        st = workqueue.state(b, evs)
        self.assertEqual((st["EXP-003"]["state"], st["EXP-003"]["reason"]), ("blocked", "waits for api"))
        self.assertEqual(problems, [])
        self.assertIn("verify: proofread by the owner; prereqs: the endpoint exists; needs: api",
                      b["items"][1]["title"])
        p = self.pai("plan", "check", self.plan, "--queues", self.queues)
        self.assertIn("queue blocked", p.stdout)
        self.assertIn("queue todo", p.stdout)
        p = self.pai("plan", "assign", self.plan, "web", "a")
        self.assertIn("already in a queue", p.stderr)

    def test_missing_criteria_unknown_dependencies_and_cycles_are_flagged(self):
        with open(self.goals, "w") as f:
            f.write("- one: A | paths: a/ | verify: t | prereqs: none | needs: two\n"
                    "- two: B | paths: b/ | verify: t | prereqs: none | needs: three\n"
                    "- three: C | paths: c/ | verify: t | prereqs: none | needs: one\n"
                    "- four: D | paths: d/ | needs: nine\n- five: E\n")
        p = self.pai("plan", "propose", "--parent", "CYC", "--goals", self.goals, "--repo", self.repo, "--json")
        kinds = sorted({(x["slice"], x["kind"]) for x in json.loads(p.stdout)["problems"]})
        self.assertIn(("one", "dependency-cycle"), kinds)
        for want in (("four", "no-verification"), ("four", "no-prereqs"), ("four", "unknown-dependency"),
                     ("five", "no-scope")):
            self.assertIn(want, kinds)
        p = self.pai("plan", "propose", "--parent", "bad id", "--goals", self.goals, "--repo", self.repo)
        self.assertEqual(p.returncode, 2)
        self.pai("plan", "propose", "--parent", "OK", "--goals", self.goals, "--repo", self.repo, "--out", self.plan)
        self.assertIn("lower-case letters", self.pai("plan", "assign", self.plan, "one", "../../evil").stderr)
        self.edit(lambda pl: pl.update(parent="../x"))
        p = self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")
        self.assertEqual(p.returncode, 2)
        self.assertFalse(os.path.exists(self.queues), "an edited plan cannot write outside the queue folder")

    def test_queue_add_appends_without_touching_existing_items(self):
        q = os.path.join(self.tmp, "w.queue")
        src = os.path.join(self.tmp, "list.md")
        with open(src, "w") as f:
            f.write("W-001. First\nW-002. Second\n")
        self.assertEqual(self.pai("queue", "init", q, "--from", src, "--write").returncode, 0)
        self.pai("queue", "mark", q, "W-001", "done", "--evidence", "x", "--session", "s")
        with open(src, "w") as f:
            f.write("W-003. Third\n")
        p = self.pai("queue", "add", q, "--from", src)
        self.assertIn("would add 1 item(s)", p.stdout)
        self.assertEqual(self.pai("queue", "add", q, "--from", src, "--write").returncode, 0)
        p = self.pai("queue", "add", q, "--from", src, "--write")
        self.assertEqual(p.returncode, 2)
        self.assertIn("already has item(s) W-003", p.stderr)
        doc = workqueue.load(q)
        evs, problems = workqueue.events(q)
        self.assertEqual(problems, [])
        st = workqueue.state(doc, evs)
        self.assertEqual([(i, st[i]["state"]) for i in ("W-001", "W-002", "W-003")],
                         [("W-001", "done"), ("W-002", "todo"), ("W-003", "todo")])
        self.assertEqual(doc["additions"][0]["ids"], ["W-003"], "recorded in the file, not as a new log event kind")

    def accept_plan(self, n):
        with open(self.goals, "w") as f:
            for i in range(n):
                f.write("- s%d: Slice %d | paths: p%d/ | verify: t | prereqs: none%s\n" % (i, i, i, " | needs: s0" if i else ""))
        self.assertEqual(self.pai("plan", "propose", "--parent", "K", "--goals", self.goals, "--repo", self.repo,
                                  "--out", self.plan).returncode, 0)

    def test_accept_killed_at_any_point_resumes_with_every_slice_queued_once(self):
        self.accept_plan(12)
        for delay in (0.05, 0.1, 0.15, 0.2, 0.3, 0.4):
            p = subprocess.Popen([sys.executable, "-B", PAI, "plan", "accept", self.plan, "--queues", self.queues,
                                  "--write"], env=self.env, cwd=self.tmp, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            time.sleep(delay)
            p.send_signal(signal.SIGKILL)
            p.wait()
        p = self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        seen = []
        for f in sorted(os.listdir(self.queues)):
            if f.endswith(".queue"):
                doc = workqueue.load(os.path.join(self.queues, f))
                evs, problems = workqueue.events(os.path.join(self.queues, f))
                self.assertEqual([x for x in problems if "not a queue event" in x], [], f)
                seen += [i["id"] for i in doc["items"]]
        self.assertEqual(sorted(seen), ["K-%03d" % i for i in range(1, 13)], "each slice exactly once")
        with open(self.plan + ".accepted.json") as f:
            self.assertEqual(len(json.load(f)["slices"]), 12)

    def test_a_mixed_batch_where_one_lane_has_a_ci_blocker(self):
        self.accept_plan(4)
        self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")
        with open(self.plan) as f:
            plan = json.load(f)
        q = {s["id"]: os.path.join(self.queues, "K-%s.queue" % s["worker"]) for s in plan["slices"]}
        item = {s["id"]: s["item"] for s in plan["slices"]}
        self.pai("queue", "mark", q["s0"], item["s0"], "done", "--evidence", "PR 1 merged", "--session", "t")
        self.pai("queue", "mark", q["s2"], item["s2"], "blocked", "--reason",
                 "CI: unit job fails on macOS (log: ci/123); owner decides", "--session", "t")
        self.pai("queue", "mark", q["s3"], item["s3"], "done", "--evidence", "PR 3 merged", "--session", "t")
        p = self.pai("plan", "check", self.plan, "--queues", self.queues, "--json")
        states = json.loads(p.stdout)["queue_states"]
        self.assertEqual(states, {"s0": "done", "s1": "blocked", "s2": "blocked", "s3": "done"},
                         "s1 still waits (recorded at accept); s2 has the CI blocker; the others are done")
        self.assertEqual([r["slice"] for r in json.loads(p.stdout)["startable"]], ["s1"],
                         "s1's dependency is done: shown as ready to start; s2 (CI) is not")
        p = self.pai("queue", "status", q["s2"])
        self.assertIn("CI: unit job fails on macOS", p.stdout)
        p = self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")
        self.assertEqual(p.stdout.count("already"), 4, "nothing re-queued around the blocker")

    # -- R4

    def read_plan(self):
        with open(self.plan) as f:
            return json.load(f)

    def test_the_same_goal_gives_the_same_ids_and_dependencies(self):
        """R4-PAI-026: proposing twice gives the same plan; a slice added at the end leaves earlier ids alone."""
        outs = []
        for i in range(2):
            out = os.path.join(self.tmp, "p%d.json" % i)
            self.pai("plan", "propose", "--parent", "EXP", "--goals", self.goals, "--repo", self.repo, "--out", out)
            with open(out) as f:
                outs.append(f.read())
        self.assertEqual(outs[0], outs[1], "byte-identical plans from the same goals")
        first = {s["id"]: (s["item"], s.get("needs", [])) for s in json.loads(outs[0])["slices"]}
        self.assertEqual(first["docs"], ("EXP-003", ["api"]))
        with open(self.goals, "a") as f:
            f.write("- tests: Add export tests | paths: tests/export/ | verify: python -m pytest tests/export | prereqs: none\n")
        out = os.path.join(self.tmp, "p2.json")
        self.pai("plan", "propose", "--parent", "EXP", "--goals", self.goals, "--repo", self.repo, "--out", out)
        with open(out) as f:
            later = {s["id"]: (s["item"], s.get("needs", [])) for s in json.load(f)["slices"]}
        self.assertEqual({k: later[k] for k in first}, first, "earlier ids unchanged")
        self.assertEqual(later["tests"][0], "EXP-005")

    def test_contested_paths_name_the_lane_that_owns_them(self):
        """R4-PAI-028: two slices writing one path, and a slice writing a path another lane owns, are refused with the owner."""
        goals = os.path.join(self.tmp, "contested.md")
        with open(goals, "w") as f:
            f.write("- ui: Change the API page | paths: src api/page.py | verify: make ui | prereqs: none | worker: b\n"
                    "- web: Web | paths: web/ | verify: make web | prereqs: none\n"
                    "- cli: Cli | paths: web/shared.js | verify: make cli | prereqs: none\n")
        p = self.pai("plan", "propose", "--parent", "CON", "--goals", goals, "--repo", self.repo, "--out", self.plan)
        self.assertEqual(p.returncode, 1)
        self.assertIn("PROBLEM   ui owned-elsewhere: src api is owned by lane a (%s)" % self.wt, p.stdout)
        self.assertIn("PROBLEM   web+cli shared-write-paths: web and cli would both write web/shared.js", p.stdout)
        p = self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(self.queues), "nothing is queued while a path is contested")

    def test_a_blocked_cloud_task_becomes_a_local_rehearsal_and_an_owner_decision(self):
        """R4-PAI-029: split-owner keeps the worker's item for the rehearsal, adds the owner's decision after it,
        moves dependents onto the decision, and queues the decision as owner-only in the owner's queue."""
        goals = os.path.join(self.tmp, "cloud.md")
        with open(goals, "w") as f:
            f.write("- export: Add the export job | paths: jobs/export/ | verify: python -m pytest tests/jobs | prereqs: none\n"
                    "- deploy: Deploy the export job to AWS staging | paths: infra/export/ | verify: terraform plan "
                    "(local) | prereqs: none | needs: export\n"
                    "- announce: Announce the export | paths: docs/news.md | verify: proofread | prereqs: none | needs: deploy\n")
        p = self.pai("plan", "propose", "--parent", "CLD", "--goals", goals, "--repo", self.repo, "--out", self.plan)
        self.assertIn("WARNING   deploy looks-owner-only: the goal mentions 'Deploy'", p.stdout)
        self.assertIn("pai plan split-owner PLAN deploy", p.stdout)
        before = {s["id"]: s["item"] for s in self.read_plan()["slices"]}
        p = self.pai("plan", "split-owner", self.plan, "deploy")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        sl = {s["id"]: s for s in self.read_plan()["slices"]}
        self.assertEqual(list(sl), ["export", "deploy-rehearsal", "deploy-decision", "announce"])
        self.assertEqual(sl["deploy-rehearsal"]["item"], before["deploy"], "the rehearsal keeps the worker's item")
        self.assertEqual((sl["deploy-rehearsal"]["paths"], sl["deploy-rehearsal"]["verify"]), (["infra/export/"],
                                                                                              "terraform plan (local)"))
        self.assertTrue(sl["deploy-rehearsal"]["goal"].startswith("Rehearse locally, with no live writes or spending:"))
        self.assertEqual((sl["deploy-decision"]["item"], sl["deploy-decision"]["worker"], sl["deploy-decision"]["needs"]),
                         ("CLD-004", "owner", ["deploy-rehearsal"]))
        self.assertEqual(sl["announce"]["needs"], ["deploy-decision"], "dependents wait for the owner's decision")
        self.assertNotIn("WARNING", p.stdout, "nothing left that looks owner-only")
        self.assertEqual(self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write").returncode, 0)
        owner_q = os.path.join(self.queues, "CLD-owner.queue")
        st = json.loads(self.pai("queue", "status", owner_q, "--json").stdout)
        self.assertEqual(st["owner_only"][0]["id"], "CLD-004")
        self.assertIn("owner decision: Owner decision: Deploy the export job to AWS staging", st["owner_only"][0]["reason"])
        self.assertIn("waits for deploy-rehearsal", st["owner_only"][0]["reason"])
        p = self.pai("plan", "split-owner", self.plan, "export")
        self.assertEqual(p.returncode, 2)
        self.assertIn("already in a queue", p.stderr)

    def test_lane_status_and_other_lanes_continue_past_a_blocked_one(self):
        """R4-PAI-032/035: one lane's current, blocked and remaining ids; another lane keeps working while one is
        blocked on its dependency; the blocked one becomes ready when the dependency is done."""
        self.pai("plan", "propose", "--parent", "EXP", "--goals", self.goals, "--repo", self.repo, "--out", self.plan)
        self.edit(lambda plan: plan["slices"][3].update(paths=["cli/"]))
        self.assertEqual(self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write").returncode, 0)
        sl = {s["id"]: s for s in self.read_plan()["slices"]}
        docs_lane, api_lane = sl["docs"]["worker"], sl["api"]["worker"]
        st = json.loads(self.pai("plan", "status", self.plan, "--queues", self.queues, "--lane", docs_lane, "--json").stdout)
        self.assertEqual(list(st), [docs_lane])
        self.assertEqual(st[docs_lane]["blocked"][0]["reason"], "waits for api")
        q = lambda s_: os.path.join(self.queues, "EXP-%s.queue" % sl[s_]["worker"])  # noqa: E731
        self.pai("queue", "mark", q("web"), sl["web"]["item"], "doing", "--session", "w")
        self.pai("queue", "mark", q("cli"), sl["cli"]["item"], "done", "--evidence", "cli tests", "--session", "c")
        p = self.pai("plan", "status", self.plan, "--queues", self.queues)
        self.assertIn("current    %s web" % sl["web"]["item"], p.stdout, "the web lane works on")
        self.assertIn("BLOCKED    %s docs: waits for api" % sl["docs"]["item"], p.stdout)
        self.assertIn("done       %s cli" % sl["cli"]["item"], p.stdout)
        self.pai("queue", "mark", q("api"), sl["api"]["item"], "done", "--evidence", "api tests", "--session", "a")
        st = json.loads(self.pai("plan", "status", self.plan, "--queues", self.queues, "--json").stdout)
        self.assertEqual(st[docs_lane]["startable"], ["docs"], "the dependency is done: docs can start")
        self.assertEqual(st[api_lane]["done"][0]["slice"], "api")
        p = self.pai("plan", "status", self.plan, "--queues", self.queues, "--lane", "zz")
        self.assertEqual(p.returncode, 2)
        self.assertIn("no lane zz in this plan", p.stderr)

    def test_reapplying_the_accepted_plan_adds_no_row(self):
        """R4-PAI-030: accept twice (and once more after an owner split was accepted): every queue is byte-identical."""
        self.pai("plan", "propose", "--parent", "EXP", "--goals", self.goals, "--repo", self.repo, "--out", self.plan)
        self.edit(lambda plan: plan["slices"][3].update(paths=["cli/"]))
        self.assertEqual(self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write").returncode, 0)

        def snap():
            out = {}
            for name in sorted(os.listdir(self.queues)):
                with open(os.path.join(self.queues, name), "rb") as f:
                    out[name] = f.read()
            return out
        before = snap()
        for _ in range(2):
            p = self.pai("plan", "accept", self.plan, "--queues", self.queues, "--write")
            self.assertEqual(p.stdout.count("already"), 4)
        self.assertEqual(snap(), before)


if __name__ == "__main__":
    unittest.main()
