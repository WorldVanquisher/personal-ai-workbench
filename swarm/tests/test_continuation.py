"""Cross-session continuation (0.5.0 candidate slice): stop one fake-agent session, keep the goal,
completed steps, evidence and open decisions in the task note, and resume in a fresh session, with
the same or another agent, in the same or a fresh HOME. Never a silent duplicate: session 1's task
is not resent, and a continuation is used once.

Fakes only (fake Claude and Codex on an isolated tmux server). The test plays the agent's work by
committing in the lane's worktree while the fake idles."""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311, lines  # noqa: E402


@helpers.needs_tmux
@needs_311
class Continue(WB):
    def setUp(self):
        super().setUp()
        self.note("task-a", priority=9, body="Write STEP-1.md, STEP-2.md and STEP-3.md.\n\n## Acceptance criteria\n\n"
                                             "- the three files exist on the branch")
        self.env["PAI_DISPATCH_CONFIRM_SECONDS"] = "2"
        self.init()

    # -- helpers
    def status(self, run=None):
        p = self.pai("status", "--json", *(["--run", run] if run else []))
        d = json.loads(p.stdout)
        return {l["lane"]: l for l in d["lanes"]}, d

    def tagged(self, run):
        return [r for r in self.received() if r["text"].startswith("[pai %s/a/task-a]" % run)]

    def note_text(self):
        with open(os.path.join(self.tasks, "task-a.md"), encoding="utf-8") as f:
            return f.read()

    def session_one(self, agent="claude", **behavior):
        self.behavior(**behavior)
        p = self.pai("run", "--workers", "1", "--agent", agent, "--yes", "--wait", "8", "--run-id", "s1")
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        rows, _ = self.status("s1")
        wt = rows["a"]["worktree"]
        helpers.commit(wt, "STEP-1.md", "step 1 done\n", "step 1: STEP-1.md")
        helpers.commit(wt, "HANDOFF.md", "# Handoff\n\n- Done: step 1\n- Next: steps 2 and 3\n- Token seen: ghp_%s\n"
                       % ("Ab12Cd34" * 5), "handoff after step 1")
        return wt

    def recovery_log(self):
        d = os.path.join(self.home, "recovery")
        return [e for f in sorted(os.listdir(d)) for e in lines(os.path.join(d, f))] if os.path.isdir(d) else []

    # -- tests
    def test_clean_handoff_from_claude_to_codex_without_a_fetch(self):
        wt = self.session_one()
        self.assertEqual(len(self.tagged("s1")), 1)
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 1, "session 1 still runs")
        self.assertIn("Stop it first: pai cleanup --run s1 --lane a --apply --stop-running", p.stdout)
        self.assertNotIn("## Continuation", self.note_text())
        self.assert_ok(self.pai("cleanup", "--run", "s1", "--apply", "--stop-running"))
        p = self.pai("recover", "--run", "s1", "--continue", "a")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("PREVIEW (nothing written)", p.stdout)
        self.assertIn("step 1: STEP-1.md", p.stdout)
        self.assertNotIn("## Continuation", self.note_text(), "a preview writes nothing")
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        text = self.note_text()
        for needle in ("## Continuation from s1/a", "### Completed steps (already committed; do not redo them)",
                       "step 1: STEP-1.md", "handoff after step 1", "> - Next: steps 2 and 3", "dispatch: delivered",
                       "<removed: GitHub token>", "### Open decisions", "<!-- pai-continuation v1 ",
                       "the three files exist on the branch", "status: todo"):
            self.assertIn(needle, text)
        self.assertNotIn("Ab12Cd34Ab12", text, "the agent's handoff is privacy-filtered")
        rows, _ = self.status("s1")
        self.assertEqual(rows["a"]["lifecycle"]["state"], "handed-off")
        self.assertEqual([(e["phase"], e["action"]) for e in self.recovery_log()], [("begin", "handoff"), ("end", "handoff")])
        p = self.pai("resume", "--run", "s1", "--wait", "2")
        self.assertIn("HANDED OFF", p.stdout)
        self.pai("recover", "--run", "s1", "--apply")
        self.assertEqual(len(self.tagged("s1")), 1, "session 1's task is never resent")

        head = helpers.git(wt, "rev-parse", "HEAD")
        os.rename(self.origin, self.origin + ".moved")  # a continuation fetches nothing
        p = self.pai("run", "--continue", "task-a", "--agent", "codex", "--yes", "--wait", "8", "--run-id", "s2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("base pinned to", p.stdout)
        rows, _ = self.status("s2")
        a = rows["a"]
        self.assertEqual((a["branch"], a["agent"]["name"], a["fetched_at"]), ("pai/s2/a", "codex", None))
        self.assertEqual(helpers.git(wt, "rev-parse", "HEAD"), head, "the fresh branch starts at the handed-off head")
        self.assertIn("Codex's own rollout file", a["dispatch"]["evidence"], "delivered on Codex's evidence")
        got = self.tagged("s2")
        self.assertEqual(len(got), 1)
        self.assertIn("This continues an earlier session (s1/a/task-a)", got[0]["text"])
        self.assertEqual(len(self.tagged("s1")), 1)
        rows1, _ = self.status("s1")
        self.assertIn("continued in s2/a", rows1["a"]["lifecycle"]["why"])
        p = self.pai("run", "--continue", "task-a", "--yes", "--run-id", "s3")
        self.assertEqual(p.returncode, 1)
        self.assertIn("was already used by s2/a", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, "workbench", "runs", "repo", "s3")), "nothing started")

    def check_contract(self, d):
        """The committed fields of pai_status_v1 (RUN-STATE.md), with their types."""
        self.assertEqual(d["schema"], "pai_status_v1")
        for k, t in (("run", str), ("run_id", str), ("ok", bool), ("summary", dict), ("lanes", list)):
            self.assertIsInstance(d[k], t, k)
        sm = d["summary"]
        self.assertIn(sm["delivery"], ("complete", "partial", "none", "no-tasks"))
        for k, t in (("lanes", int), ("started", int), ("with_task", int), ("delivered", list), ("not_started", list),
                     ("attention", list), ("not_delivered", dict), ("states", dict)):
            self.assertIsInstance(sm[k], t, k)
        names = {"queued", "waiting-for-trust", "sent", "running", "delivered", "idle", "not-accepting-input", "exited",
                 "stopped", "unknown", "not-started", "handed-off"}
        for ln in d["lanes"]:
            for k in ("lane", "note", "branch", "worktree", "handed_off", "continues"):
                self.assertIn(k, ln)
            self.assertEqual(set(ln["ids"]) >= {"run", "task", "record", "generation"}, True)
            lc = ln["lifecycle"]
            self.assertIn(lc["state"], names)
            for k in ("terminal", "attention", "delivered"):
                self.assertIsInstance(lc[k], bool)
            self.assertIsInstance(lc["next"], list)
            self.assertIn(ln["dispatch"]["state"], ("none", "pending", "sent", "delivered", "unknown"))
            self.assertIn(ln["process"]["state"], ("running", "exited", "no-window", "no-claude", "ambiguous",
                                                   "mismatched"))
            self.assertIn("name", ln["agent"])
            self.assertIn("mode", ln["agent"])

    def test_status_json_contract_before_and_after_a_handoff(self):
        self.session_one()
        self.check_contract(self.status("s1")[1])
        self.assert_ok(self.pai("cleanup", "--run", "s1", "--apply", "--stop-running"))
        self.assert_ok(self.pai("recover", "--run", "s1", "--continue", "a", "--write"))
        rows, d = self.status("s1")
        self.check_contract(d)
        self.assertEqual(set(rows["a"]["handed_off"]), {"at", "head", "branch", "note", "id", "continued_by"})
        self.pai("run", "--continue", "task-a", "--agent", "codex", "--yes", "--wait", "8", "--run-id", "s2")
        rows, d = self.status("s2")
        self.check_contract(d)
        self.assertEqual(set(rows["a"]["continues"]), {"id", "from", "head", "branch", "agent"})
        self.assertEqual(self.status("s1")[0]["a"]["handed_off"]["continued_by"], "s2/a")

    def test_interruption_with_uncertain_delivery_never_duplicates(self):
        wt = self.session_one(no_transcript=["a"])
        rows, _ = self.status("s1")
        self.assertTrue(self.wait_for(lambda: self.status("s1")[0]["a"]["dispatch"]["state"] == "unknown", 10))
        os.kill(rows["a"]["process"]["pid"], signal.SIGKILL)  # the session dies; did it get the task? unknown
        self.assertTrue(self.wait_for(lambda: self.status("s1")[0]["a"]["lifecycle"]["state"] == "unknown", 10))
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 1)
        self.assertIn("session 1's delivery is unknown", p.stdout)
        self.assertIn("--choose a=leave", p.stdout)
        self.assertNotIn("## Continuation", self.note_text())
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--choose", "a=leave", "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("- [ ] OPEN: it is not known whether session 1 received this task", self.note_text())
        begin = [e for e in self.recovery_log() if e["phase"] == "begin"]
        self.assertEqual((begin[0]["choice"], begin[0]["before"]["dispatch"]), ("leave", "unknown"))
        for args in (["resume", "--run", "s1", "--wait", "2"], ["recover", "--run", "s1", "--apply"]):
            self.pai(*args)
        self.assertEqual(len(self.tagged("s1")), 1, "the uncertain prompt was not sent again")
        p = self.pai("run", "--continue", "task-a", "--yes", "--wait", "8", "--run-id", "s2")
        self.assertEqual(p.returncode, 1, "this fake keeps no transcript in lane a, so session 2 is unknown too")
        self.assertEqual((len(self.tagged("s1")), len(self.tagged("s2"))), (1, 1), "one prompt per session, no duplicate")
        self.assertIn("STEP-1.md", helpers.git(wt, "ls-files"))

    def test_refusals_before_anything_is_written_or_started(self):
        wt = self.session_one()
        self.assert_ok(self.pai("cleanup", "--run", "s1", "--apply", "--stop-running"))
        with open(os.path.join(wt, "WIP.md"), "w") as f:
            f.write("not committed\n")
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 1)
        self.assertIn("1 uncommitted path(s)", p.stdout)
        os.remove(os.path.join(wt, "WIP.md"))
        self.assert_ok(self.pai("recover", "--run", "s1", "--continue", "a", "--write"))
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 1)
        self.assertIn("already handed off", p.stdout)
        squatter = subprocess.Popen(["sleep", "60"], cwd=wt)
        self.addCleanup(lambda: (squatter.kill(), squatter.wait()))
        p = self.pai("run", "--continue", "task-a", "--yes", "--run-id", "s2")
        self.assertEqual(p.returncode, 1)
        self.assertIn("a process still works in %s (pid %d" % (wt, squatter.pid), p.stdout)
        squatter.kill()
        squatter.wait()
        p = self.pai("run", "--continue", "task-a", "--dry-run", "--run-id", "s2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("DRY RUN", p.stdout)
        self.assertEqual(self.tagged("s2"), [])
        helpers.commit(wt, "OTHER.md", "x\n", "someone moved the head")
        p = self.pai("run", "--continue", "task-a", "--yes", "--run-id", "s2")
        self.assertEqual(p.returncode, 1)
        self.assertIn("not the handed-off pai/s1/a", p.stdout)

    def test_a_fresh_home_continues_from_a_task_card(self):
        self.session_one()
        self.assert_ok(self.pai("cleanup", "--run", "s1", "--apply", "--stop-running"))
        self.assert_ok(self.pai("recover", "--run", "s1", "--continue", "a", "--write"))
        card = os.path.join(self.tmp, "handoff-card.json")
        self.assert_ok(self.pai("task", "export", "task-a", "--out", card))
        fresh = os.path.join(self.tmp, "fresh")
        tasks2 = os.path.join(fresh, "tasks")
        os.makedirs(tasks2)
        env2 = {"PAI_WORKBENCH_HOME": os.path.join(fresh, "wb"), "PAI_WORKBENCH_DATA": os.path.join(fresh, "data"),
                "PAI_SWARM_HOME": os.path.join(fresh, "state"), "HOME": os.path.join(fresh, "home")}
        os.makedirs(env2["HOME"])
        p = self.pai("init", "--no-input", "--default-agent", "claude", "--worktree-root", self.root, "--tasks", tasks2,
                     env=env2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        project = p.stdout.split("saved       ")[1].split()[0]
        self.set_config({("run", "tmux_socket"): self.tmux_socket()}, path=project)
        self.assertEqual(self.pai("task", "import", card, "--write", env=env2).returncode, 0)
        p = self.pai("run", "--continue", "task-a", "--agent", "codex", "--yes", "--wait", "8", "--run-id", "s2", env=env2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.tagged("s2")), 1)
        self.assertEqual(len(self.tagged("s1")), 1)


if __name__ == "__main__":
    unittest.main()
