"""Transfer packets (0.5.0 candidate): one stopped task carried between fake agents, HOMEs and
directions, with identity, evidence and permissions checked; nothing sent twice.

Fixture (`TwoAgents`): one repository, one task note with three steps, fake Claude and fake Codex
on the test's own tmux server, fixed commit dates and identities, so the base and the commits
the test makes have the same ids on every run. The test plays the agent's work by committing in
the lane's worktree while the fake idles."""
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311, lines  # noqa: E402

FIXED = {"GIT_AUTHOR_DATE": "2026-11-01T09:00:00Z", "GIT_COMMITTER_DATE": "2026-11-01T09:00:00Z"}
BODY = ("Write STEP-1.md, STEP-2.md and STEP-3.md, one commit each.\n\n## Acceptance criteria\n\n"
        "- the three files exist on the branch\n- no step is done twice")


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def tree_hashes(root, skip=()):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in skip]
        for f in files:
            p = os.path.join(d, f)
            if os.path.isfile(p) and not os.path.islink(p):
                out[os.path.relpath(p, root)] = sha256(p)
    return out


class TwoAgents(WB):
    """Deterministic disposable fixture: two fake agents, one shared task (R2-009)."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in FIXED}
        os.environ.update(FIXED)
        self.addCleanup(self._restore)
        super().setUp()
        self.env.update(FIXED)
        self.note("task-a", priority=9, body=BODY)
        self.env["PAI_DISPATCH_CONFIRM_SECONDS"] = "2"
        self.init()
        self.set_config({("review.checks", "unit"): [sys.executable, "-c", "print('3 passed')"],
                         ("review", "required_checks"): ["unit"]})

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def status(self, run=None, env=None):
        p = self.pai("status", "--json", *(["--run", run] if run else []), env=env)
        d = json.loads(p.stdout)
        return {l["lane"]: l for l in d["lanes"]}, d

    def tagged(self, run):
        return [r for r in self.received() if r["text"].startswith("[pai %s/a/task-a]" % run)]

    def note_text(self, folder=None):
        with open(os.path.join(folder or self.tasks, "task-a.md"), encoding="utf-8") as f:
            return f.read()

    def recovery_log(self, home=None):
        d = os.path.join(home or self.home, "recovery")
        return [e for f in sorted(os.listdir(d)) for e in lines(os.path.join(d, f))] if os.path.isdir(d) else []

    def session_one(self, agent="claude", run="s1", **behavior):
        self.behavior(**behavior)
        p = self.pai("run", "--workers", "1", "--agent", agent, "--yes", "--wait", "8", "--run-id", run)
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        rows, _ = self.status(run)
        wt = rows["a"]["worktree"]
        helpers.commit(wt, "STEP-1.md", "step 1 done\n", "step 1: STEP-1.md")
        helpers.commit(wt, "HANDOFF.md", "# Handoff\n\n- Done: step 1\n- Next: steps 2 and 3\n", "handoff after step 1")
        return wt

    def hand_off(self, run="s1", *extra):
        self.assert_ok(self.pai("cleanup", "--run", run, "--apply", "--stop-running"))
        self.assert_ok(self.pai("recover", "--run", run, "--continue", "a", "--write", *extra))

    def fresh_home(self, name="fresh", agent="claude", project=None):
        fresh = os.path.join(self.tmp, name)
        tasks2 = os.path.join(fresh, "tasks")
        os.makedirs(tasks2)
        env2 = {"PAI_WORKBENCH_HOME": os.path.join(fresh, "wb"), "PAI_WORKBENCH_DATA": os.path.join(fresh, "data"),
                "PAI_SWARM_HOME": os.path.join(fresh, "state"), "HOME": os.path.join(fresh, "home"),
                "CLAUDE_CONFIG_DIR": os.path.join(fresh, "claude-config"), "CODEX_HOME": os.path.join(fresh, "codex-home")}
        os.makedirs(env2["HOME"])
        os.makedirs(env2["CLAUDE_CONFIG_DIR"])
        p = self.pai("init", "--no-input", "--default-agent", agent, "--worktree-root", self.root, "--tasks", tasks2,
                     *(["--name", project] if project else []), env=env2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        path = p.stdout.split("saved       ")[1].split()[0]
        self.set_config({("run", "tmux_socket"): self.tmux_socket()}, path=path)
        return env2, tasks2, path

    def packet(self, *extra, name="packet.json"):
        out = os.path.join(self.tmp, name)
        p = self.pai("task", "export", "task-a", "--transfer", "--out", out, *extra)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(out, encoding="utf-8") as f:
            return out, json.load(f), p.stdout


@helpers.needs_tmux
@needs_311
class Fixture(TwoAgents):
    def test_fixture_is_deterministic(self):
        """The fixed dates give the same base and step commits on every run of this fixture."""
        base = helpers.git(self.repo, "rev-parse", "HEAD")
        wt = self.session_one()
        head = helpers.git(wt, "rev-parse", "HEAD")
        again = helpers.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(base, again)
        # recompute the same commits in a scratch clone: same ids
        clone = os.path.join(self.tmp, "clone")
        helpers.git(self.tmp, "clone", "-q", self.repo, clone)
        helpers.git(clone, "checkout", "-q", "-b", "x", base)
        helpers.commit(clone, "STEP-1.md", "step 1 done\n", "step 1: STEP-1.md")
        helpers.commit(clone, "HANDOFF.md", "# Handoff\n\n- Done: step 1\n- Next: steps 2 and 3\n", "handoff after step 1")
        self.assertEqual(helpers.git(clone, "rev-parse", "HEAD"), head, "same content and dates, same commit id")


@helpers.needs_tmux
@needs_311
class Identity(TwoAgents):
    def test_one_task_identity_across_cards_run_state_recovery_and_packet(self):
        """R2-006/R2-018: the note name is the task; each attempt is RUN/LANE/NOTE; every record links."""
        self.session_one()
        rows, _ = self.status("s1")
        tid = rows["a"]["ids"]["task"]
        self.assertEqual(tid, "s1/a/task-a")
        self.assertEqual(len(self.tagged("s1")), 1, "the dispatch tag is the task id")
        self.hand_off()
        log = self.recovery_log()
        self.assertEqual({e["task"] for e in log}, {tid})
        text = self.note_text()
        mk = json.loads(re.search(r"<!-- pai-continuation v1 (\{.*\}) -->", text).group(1))
        self.assertEqual(mk["from"], tid)
        self.assertIn("handed_off_from: %s" % mk["id"], text)
        self.assertEqual((log[0]["continuation"], log[0]["id"]), (mk["id"], mk["id"] + ".1"))
        _, doc, _ = self.packet()
        self.assertEqual([c["name"] for c in doc["cards"]], ["task-a"])
        tk = doc["transfer"]["tasks"][0]
        self.assertEqual((tk["card"], tk["task"], tk["continuation"]), ("task-a", tid, mk["id"]))
        p = self.pai("run", "--continue", "task-a", "--agent", "codex", "--yes", "--wait", "8", "--run-id", "s2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows2, _ = self.status("s2")
        self.assertEqual(rows2["a"]["ids"]["task"], "s2/a/task-a", "a new attempt gets a new run id, same task note")
        self.assertEqual(rows2["a"]["continues"]["id"], mk["id"])
        rows1, _ = self.status("s1")
        self.assertIn("continued in s2/a", rows1["a"]["lifecycle"]["why"])
        self.assertIn("continued_in: s2/a", self.note_text())


@helpers.needs_tmux
@needs_311
class LostEvidence(TwoAgents):
    def test_without_transcripts_recorded_delivery_stays_and_unknown_needs_a_person(self):
        """R2-007: what is lost when the agent's transcript files are gone."""
        self.session_one()
        rows, _ = self.status("s1")
        self.assertEqual(rows["a"]["dispatch"]["state"], "delivered")
        shutil.rmtree(os.path.join(self.cfg, "projects"))  # the transcripts are gone
        rows, _ = self.status("s1")
        self.assertEqual(rows["a"]["dispatch"]["state"], "delivered", "a recorded delivery is kept")
        self.assertIn("transcript", rows["a"]["dispatch"]["evidence"])
        self.hand_off()
        self.assertIn("dispatch: delivered", self.note_text(), "the continuation keeps the recorded evidence")

    def test_unknown_delivery_without_transcripts_only_a_person_can_settle(self):
        self.session_one(no_transcript=["a"])
        self.assertTrue(self.wait_for(lambda: self.status("s1")[0]["a"]["dispatch"]["state"] == "unknown", 10))
        rows, _ = self.status("s1")
        os.kill(rows["a"]["process"]["pid"], signal.SIGKILL)
        shutil.rmtree(os.path.join(self.cfg, "projects"), ignore_errors=True)
        self.assertTrue(self.wait_for(lambda: self.status("s1")[0]["a"]["lifecycle"]["state"] == "unknown", 10))
        rows, _ = self.status("s1")
        self.assertEqual(rows["a"]["dispatch"]["state"], "unknown")
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 1)
        self.assertIn("--choose a=mark-delivered", p.stdout)
        self.assertEqual(len(self.tagged("s1")), 1)

    def test_lost_run_records_public_metadata_is_what_remains(self):
        """R2-008: with this HOME's run records gone, pai refuses to guess; the note's continuation
        (when written) and the branch are public and still enough to continue."""
        wt = self.session_one()
        self.hand_off()
        head = helpers.git(wt, "rev-parse", "HEAD")
        shutil.rmtree(self.home)  # PAI_SWARM_HOME: run records, dispatch, recovery log, reviews
        p = self.pai("recover", "--run", "s1", "--continue", "a")
        self.assertEqual(p.returncode, 2)
        for needle in ("has no record of run s1, so pai cannot tell whether its tasks were delivered",
                       "note task-a: worker a, branch pai/s1/a from base", "Treat the delivery as unknown"):
            self.assertIn(needle, p.stderr)
        self.assertEqual(len(self.tagged("s1")), 1)
        out, doc, text = self.packet()
        tk = doc["transfer"]["tasks"][0]
        self.assertEqual(tk["repository"]["head"], head)
        self.assertIn("step 1: STEP-1.md", [c["subject"] for c in tk["completed"]])
        what = {o["what"]: o for o in tk["omitted"]}
        self.assertEqual(what["the run record of s1"]["impact"], "limits")
        self.assertIn("only what the continuation section says is carried", text)


@helpers.needs_tmux
@needs_311
class Transfer(TwoAgents):
    def test_claude_to_codex_through_a_packet_in_a_fresh_home(self):
        """R2-011..R2-015, R2-017: packet content, read-only import preview with capabilities, a
        Codex stand-in finishes without the old transcript or HOME."""
        wt = self.session_one()
        p = self.pai("pr", "check", "--run", "s1", "a")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.hand_off()
        prev = self.pai("task", "export", "task-a", "--transfer")
        self.assertEqual(prev.returncode, 0, prev.stdout)
        self.assertIn("PREVIEW (nothing written)", prev.stdout)
        out, doc, _ = self.packet()
        self.assertEqual((doc["format"], doc["version"], doc["transfer"]["version"]), ("pai-task-cards", 1, 1))
        card = doc["cards"][0]
        self.assertEqual(card["acceptance"], ["the three files exist on the branch", "no step is done twice"])
        tk = doc["transfer"]["tasks"][0]
        self.assertEqual([c["subject"] for c in tk["completed"]], ["step 1: STEP-1.md", "handoff after step 1"])
        self.assertEqual(tk["decisions"], [])
        ev = {e["id"]: e for e in tk["evidence"]}
        self.assertEqual(set(ev), {"continuation", "agent-handoff", "check:unit"})
        self.assertEqual(ev["check:unit"]["ref"], "run:s1/a/check/unit")
        self.assertEqual(ev["check:unit"]["sha256"], hashlib.sha256(b"3 passed\n").hexdigest())
        self.assertNotIn("text", ev["check:unit"], "check output stays unless --include-logs")
        self.assertEqual(tk["source"]["capabilities"]["delivery_evidence"], "claude-projects-jsonl")
        raw = read(out)
        for private in (self.tmp, self.home, os.path.expanduser("~"), wt):
            self.assertNotIn(private, raw, "no absolute path leaves")
        self.assertEqual(doc["transfer"]["project"]["roots"], [helpers.git(self.repo, "rev-list", "--max-parents=0",
                                                                           "HEAD")])
        os.rename(self.cfg, self.cfg + ".gone")  # session 1's transcripts and its HOME's agent data are gone
        env2, tasks2, _ = self.fresh_home()
        before = tree_hashes(os.path.join(self.tmp, "fresh"), skip=("tasks",))
        p = self.pai("task", "import", out, env=env2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for needle in ("TRANSFER packet: 1 task(s) from project repo (repository matches)",
                       "continues s1/a/task-a", "base %s is here" % tk["repository"]["base"][:12],
                       "source      claude: readiness from its session record; delivery shown from its claude-projects-jsonl",
                       "destination codex: readiness from the screen only", "installed here",
                       "evidence    check:unit", "next        pai run --continue task-a", "create"):
            self.assertIn(needle, p.stdout)
        self.assertEqual(os.listdir(tasks2), [], "the preview writes nothing")
        self.assertEqual(before, tree_hashes(os.path.join(self.tmp, "fresh"), skip=("tasks",)))
        self.assert_ok(self.pai("task", "import", out, "--write", env=env2))
        self.assertEqual(tree_hashes(os.path.join(self.tmp, "fresh"), skip=("tasks",)), before,
                         "import only creates the task note")
        p = self.pai("run", "--continue", "task-a", "--agent", "codex", "--yes", "--wait", "8", "--run-id", "s2", env=env2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        got = self.tagged("s2")
        self.assertEqual((len(self.tagged("s1")), len(got)), (1, 1))
        rows, _ = self.status("s2", env=env2)
        self.assertEqual(rows["a"]["agent"]["name"], "codex")
        self.assertEqual(rows["a"]["dispatch"]["state"], "delivered")
        self.assertEqual(helpers.git(rows["a"]["worktree"], "rev-parse", "HEAD"), tk["repository"]["head"])

    def test_codex_to_claude_reverse_direction(self):
        """R2-016: a Codex stand-in starts, a Claude stand-in finishes, through a packet."""
        self.session_one(agent="codex")
        rows, _ = self.status("s1")
        self.assertEqual(rows["a"]["agent"]["name"], "codex")
        self.hand_off()
        out, doc, _ = self.packet()
        tk = doc["transfer"]["tasks"][0]
        self.assertEqual(tk["source"]["agent"], "codex")
        self.assertEqual(tk["source"]["capabilities"]["readiness"], "screen")
        env2, _, _ = self.fresh_home(agent="claude")
        p = self.pai("task", "import", out, env=env2)
        self.assertIn("destination claude: readiness from its session record", p.stdout)
        self.assertIn("readiness differs from the source", p.stdout)
        self.assert_ok(self.pai("task", "import", out, "--write", env=env2))
        p = self.pai("run", "--continue", "task-a", "--agent", "claude", "--yes", "--wait", "8", "--run-id", "s2",
                     env=env2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows, _ = self.status("s2", env=env2)
        self.assertEqual((rows["a"]["agent"]["name"], rows["a"]["dispatch"]["state"]), ("claude", "delivered"))
        self.assertEqual((len(self.tagged("s1")), len(self.tagged("s2"))), (1, 1))
        self.assertIn("This continues an earlier session (s1/a/task-a)", self.tagged("s2")[0]["text"])

    def test_refused_when_the_destination_is_another_project_or_repository(self):
        """R2-019."""
        self.session_one()
        self.hand_off()
        out, doc, _ = self.packet()
        # the same repository registered under another project name
        env2, tasks2, _ = self.fresh_home(project="elsewhere")
        p = self.pai("task", "import", out, env=env2)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("this packet was made for project repo, but project elsewhere is selected here", p.stdout)
        self.assertIn("--other-project", p.stdout)
        p = self.pai("task", "import", out, "--write", env=env2)
        self.assertEqual(p.returncode, 1)
        self.assertIn("nothing was written", p.stdout)
        self.assertEqual(os.listdir(tasks2), [])
        self.assert_ok(self.pai("task", "import", out, "--other-project", env=env2))
        self.assertEqual(os.listdir(tasks2), [], "a preview writes nothing")
        other = tasks2
        # another repository
        doc2 = json.loads(read(out))
        doc2["transfer"]["project"]["roots"] = ["0" * 40]
        bad = os.path.join(self.tmp, "other-repo.json")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump(doc2, f)
        p = self.pai("task", "import", bad, "--write", env=env2)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("comes from another repository", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(tasks2, "task-a.md")))
        self.assertFalse(os.path.exists(os.path.join(other, "task-a.md")))

    def test_a_compatible_set_of_lanes_from_one_run(self):
        """R2-032: the handed-off lanes of one run travel together; one lane can be chosen; notes
        from different runs are refused."""
        self.note("task-b", priority=5, body=BODY)
        p = self.pai("run", "--workers", "2", "--agent", "claude", "--yes", "--wait", "8", "--run-id", "s1")
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        rows, _ = self.status("s1")
        self.assertEqual(sorted(rows), ["a", "b"])
        for lane in ("a", "b"):
            helpers.commit(rows[lane]["worktree"], "STEP-1.md", "step 1 done\n", "step 1: STEP-1.md")
        self.assert_ok(self.pai("cleanup", "--run", "s1", "--apply", "--stop-running"))
        self.assert_ok(self.pai("recover", "--run", "s1", "--continue", "a", "--write"))
        p = self.pai("task", "export", "--transfer", "--run", "s1", "--lane", "a", "--lane", "b")
        self.assertEqual(p.returncode, 2)
        self.assertIn("lane b of run s1 is not handed off", p.stdout)
        self.assert_ok(self.pai("recover", "--run", "s1", "--continue", "b", "--write"))
        out = os.path.join(self.tmp, "both.json")
        self.assert_ok(self.pai("task", "export", "--transfer", "--run", "s1", "--out", out))
        doc = json.loads(read(out))
        self.assertEqual([(t["card"], t["task"]) for t in doc["transfer"]["tasks"]],
                         [("task-a", "s1/a/task-a"), ("task-b", "s1/b/task-b")])
        p = self.pai("task", "export", "--transfer", "--run", "s1", "--lane", "b")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("task-b   continues s1/b/task-b", p.stdout)
        self.assertNotIn("task-a   continues", p.stdout)
        other = '{"agent": "claude", "base": "%s", "branch": "pai/s9/a", "head": "%s", "id": "s9/a@x", "lane": "a", ' \
                '"run": "s9", "from": "s9/a/task-c", "completed": []}' % ("1" * 40, "2" * 40)
        self.note("task-c", body="Other.\n\n## Continuation from s9/a (x)\n\n<!-- pai-continuation v1 %s -->" % other)
        p = self.pai("task", "export", "task-a", "task-c", "--transfer")
        self.assertEqual(p.returncode, 2)
        self.assertIn("the notes come from different runs (s1, s9): export one run at a time", p.stdout)

    def test_no_packet_for_a_lane_that_was_not_handed_off_or_already_continued(self):
        """R2-032: lane selection; incompatible selections are refused with the reason."""
        self.session_one()
        p = self.pai("task", "export", "--transfer", "--run", "s1")
        self.assertEqual(p.returncode, 2)
        self.assertIn("has no handed-off lane", p.stdout)
        p = self.pai("task", "export", "task-a", "--transfer")
        self.assertEqual(p.returncode, 2)
        self.assertIn("has no continuation to transfer", p.stdout)
        self.hand_off()
        p = self.pai("task", "export", "--transfer", "--run", "s1", "--lane", "a")
        self.assertEqual(p.returncode, 0, p.stdout)
        p = self.pai("task", "export", "--transfer", "--run", "s1", "--lane", "b")
        self.assertEqual(p.returncode, 2)
        self.assertIn("lane b has no task record in run s1", p.stdout)
        self.assert_ok(self.pai("run", "--continue", "task-a", "--yes", "--wait", "8", "--run-id", "s2"))
        p = self.pai("task", "export", "task-a", "--transfer")
        self.assertEqual(p.returncode, 2)
        self.assertIn("was already continued here", p.stdout)
        self.assertEqual(len(self.tagged("s2")), 1)


@helpers.needs_tmux
@needs_311
class DataAndPermissions(TwoAgents):
    def test_evidence_hashes_are_verified_and_an_edited_log_is_refused(self):
        """R2-037, R2-038, R2-035."""
        self.session_one()
        self.assert_ok(self.pai("pr", "check", "--run", "s1", "a"))
        self.hand_off()
        logs = [os.path.join(self.home, "reviews", f) for f in os.listdir(os.path.join(self.home, "reviews"))
                if f.endswith(".unit.log")]
        self.assertEqual(len(logs), 1)
        with open(logs[0], "a") as f:
            f.write("0 failed (edited later)\n")
        out = os.path.join(self.tmp, "p.json")
        p = self.pai("task", "export", "task-a", "--transfer", "--out", out)
        self.assertEqual(p.returncode, 1)
        self.assertIn("the output of check unit changed after `pai pr check` recorded it", p.stdout)
        self.assertIn("--without check:unit", p.stdout)
        self.assertIn("pai pr check --run s1 a", p.stdout)
        self.assertFalse(os.path.exists(out))
        p = self.pai("task", "export", "task-a", "--transfer", "--out", out, "--without", "check:unit")
        self.assertEqual(p.returncode, 0, p.stdout)
        doc = json.loads(read(out))
        tk = doc["transfer"]["tasks"][0]
        self.assertNotIn("check:unit", {e["id"] for e in tk["evidence"]})
        left = [o for o in tk["omitted"] if o["what"] == "check unit"][0]
        self.assertEqual(left["impact"], "limits")
        self.assertIn("LIMITS the next session", p.stdout)
        self.assertIn("BLOCKS the next session until fixed", p.stdout, "the head is on no remote-tracking branch")
        os.remove(logs[0])
        p = self.pai("task", "export", "task-a", "--transfer")
        self.assertEqual(p.returncode, 1)
        self.assertIn("is gone", p.stdout)

    def test_privacy_defaults_and_stable_serialization(self):
        """R2-031, R2-033, R2-034, R2-040."""
        wt = self.session_one()
        self.hand_off()
        path = os.path.join(self.tasks, "task-a.md")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nSee /home/ada/notes/plan.md and http://localhost:8080/board for context.\n")
        p1 = self.pai("task", "export", "task-a", "--transfer")
        p2 = self.pai("task", "export", "task-a", "--transfer")
        self.assertEqual(p1.stdout, p2.stdout, "the same state gives the same preview")
        self.assertIn("PRIVATE  card task-a, description", p1.stdout)
        self.assertIn("home folder path '/home/ada/notes/plan.md'", p1.stdout)
        self.assertIn("local or private network link 'http://localhost:8080/board'", p1.stdout)
        self.assertIn("left as written (pai does not rewrite the task)", p1.stdout)
        for line in ("stays    raw agent transcripts and session records (never exported) -> no effect",
                     "stays    launcher configuration, agent permissions and approved check commands",
                     "leaves   the note: title"):
            self.assertIn(line, p1.stdout)
        a, doc_a, _ = self.packet(name="a.json")
        b, doc_b, _ = self.packet(name="b.json")
        self.assertEqual(sha256(a), sha256(b), "byte-identical packets from the same state")
        raw = read(a)
        self.assertEqual(raw, json.dumps(doc_a, indent=2, sort_keys=True, ensure_ascii=False) + "\n", "canonical JSON")
        self.assertIn("/home/ada/notes/plan.md", raw, "the task's own words are kept")
        for k in ("worker", "worktree", "rc_run", "base_sha", "remote"):
            self.assertNotIn('"%s"' % k, raw)
        self.assertNotIn(wt, raw)
        with open(path, "a", encoding="utf-8") as f:
            f.write("Session: https://claude.ai/code/session_01ABCDEFGHIJKLMNOPQRSTUV\n")
        p = self.pai("task", "export", "task-a", "--transfer", "--out", os.path.join(self.tmp, "c.json"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("looks like a Claude session link", p.stdout)
        self.assertNotIn("session_01ABCDEFGHIJKLMNOPQRSTUV", p.stdout, "masked in the preview")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "c.json")))

    def test_include_logs_is_privacy_filtered(self):
        self.set_config({("review.checks", "unit"): [sys.executable, "-c", "print('3 passed; token ghp_' + 'Ab12Cd34' * 5)"]})
        self.session_one()
        self.assert_ok(self.pai("pr", "check", "--run", "s1", "a"))
        self.hand_off()
        out, doc, _ = self.packet("--include-logs")
        e = [e for e in doc["transfer"]["tasks"][0]["evidence"] if e["id"] == "check:unit"][0]
        self.assertEqual(e["removed"], 1)
        self.assertIn("3 passed; token <removed: GitHub token>", e["text"])
        self.assertNotIn("Ab12Cd34Ab12", read(out))

    def test_imported_data_cannot_change_configuration_or_policy(self):
        """R2-039: import creates task notes only; a card can name checks, never commands; the
        transfer object changes nothing on the destination."""
        self.session_one()
        self.hand_off()
        out, doc, _ = self.packet()
        doc["cards"][0]["fields"] = {"checks": ["rm-everything"], "model": "x"}
        doc["transfer"]["tasks"][0]["source"]["capabilities"] = {"backend": "claude", "known": True,
                                                                 "readiness": "session-record", "delivery_evidence": "x",
                                                                 "remote_control": True}
        evil = os.path.join(self.tmp, "evil.json")
        with open(evil, "w", encoding="utf-8") as f:
            f.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        env2, tasks2, project = self.fresh_home()
        cfg_before = tree_hashes(os.path.join(self.tmp, "fresh", "wb"))
        repo_before = helpers.git(self.repo, "status", "--porcelain", "--ignored")
        refs_before = helpers.git(self.repo, "for-each-ref")
        self.assert_ok(self.pai("task", "import", evil, "--write", env=env2))
        self.assertEqual(tree_hashes(os.path.join(self.tmp, "fresh", "wb")), cfg_before, "project config unchanged")
        self.assertEqual(helpers.git(self.repo, "status", "--porcelain", "--ignored"), repo_before)
        self.assertEqual(helpers.git(self.repo, "for-each-ref"), refs_before)
        self.assertEqual(sorted(os.listdir(tasks2)), ["task-a.md"])
        p = self.pai("run", "--continue", "task-a", "--agent", "claude", "--yes", "--wait", "8", "--run-id", "s2",
                     env=env2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.pai("pr", "check", "--run", "s2", "a", env=env2)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("check 'rm-everything' is not an approved command in the manifest", p.stdout + p.stderr)

    def test_packet_validation_errors_are_actionable(self):
        """R2-078: unsupported versions and edited packets are refused before anything is written."""
        self.session_one()
        self.hand_off()
        out, doc, _ = self.packet()
        env2, tasks2, _ = self.fresh_home()
        cases = []
        d = json.loads(json.dumps(doc))
        d["transfer"]["version"] = 2
        cases.append((d, "unsupported transfer packet version 2: this pai (transfer version 1) cannot read it"))
        d = json.loads(json.dumps(doc))
        d["version"] = 7
        cases.append((d, "unsupported format version 7: this pai reads version 1"))
        d = json.loads(json.dumps(doc))
        d["cards"][0]["description"] = [l.replace("step 1: STEP-1.md", "step 1 and 2 done") for l in
                                        d["cards"][0]["description"]]
        cases.append((d, "does not match the sha256 recorded at export"))
        d = json.loads(json.dumps(doc))
        d["surprise"] = True
        cases.append((d, "unknown top-level field(s) surprise"))
        for i, (d, needle) in enumerate(cases):
            path = os.path.join(self.tmp, "case%d.json" % i)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f)
            p = self.pai("task", "import", path, "--write", env=env2)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn(needle, p.stdout + p.stderr)
            self.assertIn("ask for a fresh export" if "sha256" in needle else "", p.stdout + p.stderr)
        self.assertEqual(os.listdir(tasks2), [])


if __name__ == "__main__":
    unittest.main()
