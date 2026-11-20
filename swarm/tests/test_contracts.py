"""Contracts across an upgrade (0.5.0 candidate, R2-071..R2-074): the run state a real pai 0.4.0
wrote (saved fixture) is read by this pai; the documented status client ignores new fields; the
JSON outputs recovery and automation use carry their documented fields; exit codes mean what
`pai --help` says."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_transfer import TwoAgents, read  # noqa: E402
from test_workbench import PAI, needs_311  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "run-0.4.0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
import make_run_040  # noqa: E402


def cdir_hash(path):
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:12]


def lock_hash(path):
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:24]


@helpers.needs_tmux
@needs_311
class Saved040(unittest.TestCase):
    """R2-072: a run written by pai 0.4.0 (git b02acec), saved by fixtures/make_run_040.py."""

    def setUp(self):
        with open(os.path.join(FIX, "meta.json"), encoding="utf-8") as f:
            self.meta = json.load(f)
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="pai-fx040-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.env = make_run_040.env_for(self.root)
        self.env["PAI_WORKBENCH_HOME"] = os.path.join(self.root, "cfg")
        repo = make_run_040.setup_repo(self.root, self.env)
        self.repo = repo
        base = helpers.git(repo, "rev-parse", "HEAD")
        self.assertEqual(base, self.meta["base"], "the fixed dates give the 0.4.0 run's base commit")
        task = json.loads(read(os.path.join(FIX, "state", "tasks", "@CDIR@-r040.json")))
        branch = task["lanes"]["a"]["branch"]
        self.wt = os.path.join(self.root, "lanes", "a")
        helpers.git(repo, "worktree", "add", "-q", "-b", branch, self.wt, base)
        ch, lh = cdir_hash(os.path.join(repo, ".git")), lock_hash(self.wt)
        st = os.stat(self.wt)
        for rel in self.meta["files"]:
            text = read(os.path.join(FIX, rel)).replace("@ROOT@", self.root)
            if rel.endswith("worktrees.json"):
                text = text.replace('"@DEV@"', str(st.st_dev)).replace('"@INO@"', str(st.st_ino))
            dst = os.path.join(self.root, rel.replace("@CDIR@", ch).replace("@LOCK@", lh))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(text)

    def pai(self, *args):
        return subprocess.run([sys.executable, "-B", PAI] + list(args), cwd=self.repo, env=self.env, capture_output=True,
                              text=True, timeout=120, stdin=subprocess.DEVNULL)

    def test_the_file_names_are_computed_as_0_4_0_did(self):
        old = self.meta["root"]
        self.assertEqual(cdir_hash(os.path.join(old, "repo", ".git")), self.meta["names"]["cdir"])
        self.assertEqual(lock_hash(os.path.join(old, "lanes", "a")), self.meta["names"]["lock"])
        sys.path.insert(0, helpers.SWARM)
        import gitprep
        import wtlock
        self.assertEqual(os.path.basename(gitprep.record_path(os.path.join(old, "repo", ".git"), "r040")),
                         "%s-r040.json" % self.meta["names"]["cdir"])
        self.assertTrue(wtlock.lock_path(os.path.join(old, "lanes", "a")).endswith(self.meta["names"]["lock"] + ".json"))

    def test_status_inspect_recover_and_a_handoff_read_0_4_0_state(self):
        def digest():
            return {p: read(os.path.join(d, p)) for d in (os.path.join(self.root, "state"),) for p in sorted(
                os.path.relpath(os.path.join(r, f), d) for r, _, fs in os.walk(d) for f in fs if not f.endswith(".mutex"))}
        before = digest()
        p = self.pai("status", "--json", "--run", "r040")
        self.assertIn(p.returncode, (0, 1), p.stderr)
        d = json.loads(p.stdout)
        self.assertEqual(d["schema"], "pai_status_v1")
        lane = d["lanes"][0]
        self.assertEqual((lane["ids"]["task"], lane["dispatch"]["state"], lane["lifecycle"]["state"]),
                         ("r040/a/first", "delivered", "stopped"))
        self.assertIsNone(lane.get("handed_off"))
        self.assertIsNone(lane.get("continues"))
        p = self.pai("inspect", "a", "--run", "r040", "--json")
        self.assertIn(p.returncode, (0, 1), p.stderr)
        self.assertIn("dispatch", {e["kind"] for e in json.loads(p.stdout)["evidence"]})
        p = self.pai("recover", "--run", "r040")
        self.assertIn("RECOVERY PREVIEW", p.stdout)
        self.assertEqual(digest(), before, "reading 0.4.0 state writes nothing")
        p = self.pai("recover", "--run", "r040", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        d = json.loads(self.pai("status", "--json", "--run", "r040").stdout)
        self.assertEqual(d["lanes"][0]["lifecycle"]["state"], "handed-off")
        p = self.pai("task", "export", "first", "--transfer")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("continues r040/a/first", p.stdout)


CLIENT_RE = re.compile(r"```python\n(import json, subprocess, sys\n.*?)```", re.S)


def add_noise(doc):
    """The same document with new fields everywhere a newer pai might add them."""
    doc = json.loads(json.dumps(doc))
    doc["future_top"] = {"x": [1, 2]}
    doc["summary"]["future_count"] = 7
    for l in doc["lanes"]:
        l["future_lane"] = "new"
        l["ids"]["future_id"] = "x"
        l["lifecycle"]["future_flag"] = True
        l["lifecycle"]["why"] = "worded differently in a later release"
    return doc


@helpers.needs_tmux
@needs_311
class Clients(TwoAgents):
    def client(self):
        text = read(os.path.join(helpers.SWARM, "RUN-STATE.md"))
        code = CLIENT_RE.search(text).group(1)
        ns = {}
        exec(compile(code, "RUN-STATE.md example", "exec"), ns)
        return ns

    def test_the_documented_client_ignores_new_fields(self):
        """R2-073: the example in RUN-STATE.md, run on real output and on output with new fields."""
        self.behavior(trust=["a"])
        self.pai("run", "--workers", "1", "--agent", "claude", "--yes", "--wait", "4", "--run-id", "s1")
        real = json.loads(self.pai("status", "--json").stdout)
        ns = self.client()

        class Done:
            def __init__(self, out):
                self.stdout, self.stderr, self.returncode = out, "", 1

        orig = subprocess.run
        self.addCleanup(setattr, subprocess, "run", orig)  # ns["subprocess"] is this process's module: put it back
        for doc in (real, add_noise(real)):
            subprocess.run = lambda *a, _o=json.dumps(doc), **k: Done(_o)
            try:
                got = ns["lanes_needing_a_person"](self.repo)
            finally:
                subprocess.run = orig
            self.assertEqual(got, [("a", "s1/a/task-a", "waiting-for-trust")])

    def test_recovery_and_inspect_json_carry_the_documented_fields(self):
        """R2-071."""
        self.session_one()
        rec = json.loads(self.pai("recover", "--json", "--run", "s1").stdout)
        self.assertEqual(rec["schema"], "pai_recovery_preview_v1")
        self.assertIs(rec["changed"], False)
        self.assertIn("run", rec)
        for l in rec["lanes"]:
            for k in ("lane", "task", "action", "human", "choices"):
                self.assertIn(k, l)
        ins = json.loads(self.pai("inspect", "a", "--run", "s1", "--json").stdout)
        self.assertEqual(ins["schema"], "pai_status_v1")
        for k in ("run", "lane", "ids", "lifecycle", "evidence"):
            self.assertIn(k, ins)
        for e in ins["evidence"]:
            self.assertEqual(set(e) >= {"kind", "state", "detail", "source"}, True, e)
        self.hand_off()
        log = self.recovery_log()
        self.assertEqual([(e["phase"], e["schema"]) for e in log], [("begin", "pai_recovery_v1"), ("end", "pai_recovery_v1")])
        for k in ("id", "lane", "task", "action", "before"):
            self.assertIn(k, log[0])
        self.assertEqual(log[1]["result"], "done")

    def test_exit_codes_mean_what_the_help_says(self):
        """R2-074."""
        p = self.pai("--help")
        for line in ("exit codes (every command; scripts may rely on them across upgrades):",
                     "  0  done, or a preview with nothing in the way",
                     "  2  usage error, or input that cannot be read; nothing was changed"):
            self.assertIn(line, p.stdout)
        self.assertEqual(self.pai("--version").returncode, 0)
        self.assertEqual(self.pai("status", "--run", "no-such-run").returncode, 2)
        self.assertEqual(self.pai("task", "import", os.path.join(self.tmp, "missing.json")).returncode, 2)
        self.assertEqual(self.pai("queue", "status", os.path.join(self.tmp, "missing.json")).returncode, 2)
        self.assertEqual(self.pai("status", "--jsn").returncode, 2, "an unknown option is a usage error")
        self.session_one()
        p = self.pai("recover", "--run", "s1", "--continue", "a", "--write")
        self.assertEqual(p.returncode, 1, "refused: the agent still runs")
        self.assertIn("REFUSED", p.stdout)
        self.assertEqual(self.pai("task", "export", "task-a").returncode, 0, "a preview with nothing in the way")


if __name__ == "__main__":
    unittest.main()
