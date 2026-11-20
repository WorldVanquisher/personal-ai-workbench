"""`pai guide`: the offline first run with a synthetic project and the fake worker, through the
real launcher, from a fresh HOME. Where the system allows it (`unshare -rn`), the walkthrough runs
with no network at all. Decoy `claude` and `codex` programs on the user's PATH record any start:
the guided run must never reach them."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402

ROOT = os.path.dirname(helpers.SWARM)
PAI = os.path.join(ROOT, "bin", "pai.py")
AGENT = os.path.join(helpers.SWARM, "guided_agent.py")


def no_network_prefix():
    """["unshare", "-rn"] when this system can run a process without network, else []."""
    if not shutil.which("unshare"):
        return []
    p = subprocess.run(["unshare", "-rn", "true"], capture_output=True)
    return ["unshare", "-rn"] if p.returncode == 0 else []


@helpers.needs_tmux
@unittest.skipUnless(sys.version_info >= (3, 11), "pai needs Python 3.11+")
class Guide(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pai-guide-", dir="/tmp" if len(tempfile.gettempdir()) > 40
                                                     else None))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.decoys = os.path.join(self.tmp, "decoys")
        os.makedirs(self.decoys)
        self.started = os.path.join(self.tmp, "real-agent-started")
        for name in ("claude", "codex"):
            path = os.path.join(self.decoys, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\necho %s >> %s\nexit 1\n" % (name, self.started))
            os.chmod(path, 0o755)
        tools = [os.path.dirname(shutil.which(t)) for t in ("git", "tmux", "ps") if shutil.which(t)]
        # the decoys come FIRST on the user's PATH: a real `claude` there would be found before anything else
        self.env = {"HOME": self.home, "PATH": os.pathsep.join(dict.fromkeys([self.decoys] + tools + ["/usr/bin", "/bin"])),
                    "LANG": "C.UTF-8", "TERM": "xterm"}

    def pai(self, *args, prefix=(), timeout=300):
        return subprocess.run(list(prefix) + [sys.executable, "-B", PAI] + list(args), cwd=self.home, env=self.env,
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def test_the_guided_walkthrough_offline_from_a_fresh_home(self):
        demo = os.path.join(self.tmp, "demo")
        prefix = no_network_prefix()
        p = self.pai("guide", "--dir", demo, prefix=prefix)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-3000:])
        for step in ("1. A synthetic project", "2. The plan", "3. Dispatch", "4. Status", "5. Handoff",
                     "6. Where everything landed", "7. Stop the fake worker"):
            self.assertIn(step, out)
        self.assertLess(out.index("DRY RUN"), out.index("$ pai run --yes"), "the plan comes before anything starts")
        self.assertIn("dispatch  delivered", out)
        self.assertIn("Claude's own transcript for this worktree records the prompt", out)
        self.assertIn("the lane's process is the fake worker: ", out)
        self.assertIn("guided demo complete", out)
        self.assertFalse(os.path.exists(self.started), "a real claude or codex was never started")
        self.assertEqual(os.listdir(self.home), [], "the user's HOME is untouched: the demo has its own")
        with open(os.path.join(demo, "PAI-GUIDED-DEMO.txt")) as f:
            self.assertIn("folder: %s" % demo, f.read())
        for rel in ("tasks/guided-first-task.md", "run-log.txt", "lanes/a/HANDOFF.md", "lanes/a/GUIDED-DONE.md"):
            self.assertTrue(os.path.isfile(os.path.join(demo, rel)), rel)
        self.assertIn("run record   %s" % os.path.join(demo, "state"), out)
        print("\n[guide walkthrough ran %s]" % ("with no network (unshare -rn)" if prefix else "WITH network available"),
              file=sys.stderr)

        p = self.pai("guide", "--cleanup", demo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(os.path.exists(demo))
        self.assertEqual(sorted(os.listdir(self.tmp)), ["decoys", "home"], "only the demo folder was removed")

    def test_the_recovery_walkthrough_offline(self):
        demo = os.path.join(self.tmp, "demo-recovery")
        prefix = no_network_prefix()
        p = self.pai("guide", "--recovery", "--dir", demo, prefix=prefix)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-3000:])
        self.assertTrue(os.path.isfile(os.path.join(demo, "state", "guided-crash-once")), "the first start crashed")
        for needle in ("3a. The worker crashed", "lifecycle exited: the agent process crashed (tmux reports exit status 3",
                       "RECOVERY PREVIEW", "action    restart [AUTO]", "DONE     lane a  restart: exited ->",
                       "guided demo complete", "recovery log %s" % os.path.join(demo, "state")):
            self.assertIn(needle, out)
        self.assertLess(out.index("RECOVERY PREVIEW"), out.index("APPLYING"), "the preview comes before any change")
        self.assertFalse(os.path.exists(self.started), "a real claude or codex was never started")
        self.assertEqual(os.listdir(self.home), [])
        self.assertEqual(self.pai("guide", "--cleanup", demo).returncode, 0)
        self.assertFalse(os.path.exists(demo))

    def handoff(self, kind):
        demo = os.path.join(self.tmp, "demo-" + kind)
        prefix = no_network_prefix()
        p = self.pai("guide", "--handoff", kind, "--dir", demo, prefix=prefix, timeout=400)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-4000:])
        with open(os.path.join(demo, "guided-received.jsonl")) as f:
            got = [json.loads(x)["run"] for x in f if x.strip()]
        self.assertEqual(got, ["session-1", "session-2"], "one prompt per session, never a duplicate")
        self.assertIn("was already used by session-2/a; nothing is started twice", out)
        self.assertIn("HANDED OFF a", out)
        self.assertIn("Completed before this session (from the note's continuation): step-1", out)
        self.assertFalse(os.path.exists(self.started), "a real claude or codex was never started")
        self.assertEqual(os.listdir(self.home), [])
        self.assertEqual(self.pai("guide", "--cleanup", demo).returncode, 0)
        self.assertFalse(os.path.exists(demo))
        return out

    def test_the_clean_handoff_from_claude_to_codex_in_a_fresh_home_offline(self):
        out = self.handoff("clean")
        for needle in ("$ pai task export guided-handoff --transfer", "TRANSFER PREVIEW (nothing written)",
                       "TRANSFER packet: 1 task(s) from project", "(repository matches)",
                       "source      claude: readiness from its session record",
                       "destination codex: readiness from the screen only", "head ", "is here",
                       "Session: codex (fake), run session-2", "delivered: Codex's own rollout file"):
            self.assertIn(needle, out)
        self.assertNotIn("BLOCKS", out, "the branch was pushed to the demo's origin first")

    def test_the_reverse_handoff_from_codex_to_claude_in_a_fresh_home_offline(self):
        out = self.handoff("reverse")
        for needle in ("Session 1 (fake Codex) does step 1", "source      codex: readiness from the screen only",
                       "destination claude: readiness from its session record", "Session: claude (fake), run session-2",
                       "from a fake Codex session to a fresh Claude session in a fresh HOME via a transfer packet"):
            self.assertIn(needle, out)

    def test_the_interrupted_handoff_carries_the_open_decision_offline(self):
        out = self.handoff("interrupted")
        self.assertIn("REFUSED    session 1's delivery is unknown", out)
        self.assertIn("Open decisions I was given: OPEN: it is not known whether session 1 received this task", out)

    def test_cleanup_removes_only_a_folder_pai_guide_made(self):
        other = os.path.join(self.tmp, "mine")
        os.makedirs(other)
        with open(os.path.join(other, "notes.md"), "w") as f:
            f.write("# mine\n")
        p = self.pai("guide", "--cleanup", other)
        self.assertEqual(p.returncode, 2)
        self.assertIn("not a folder made by `pai guide`", p.stdout)
        self.assertTrue(os.path.isfile(os.path.join(other, "notes.md")))
        with open(os.path.join(other, "PAI-GUIDED-DEMO.txt"), "w") as f:  # a marker naming another folder
            f.write("PAI guided demo (disposable)\nfolder: /somewhere/else\n")
        self.assertEqual(self.pai("guide", "--cleanup", other).returncode, 2)
        self.assertTrue(os.path.isfile(os.path.join(other, "notes.md")))
        p = self.pai("guide", "--dir", other)
        self.assertEqual(p.returncode, 2, "an existing folder is never used")
        self.assertIn("exists", p.stdout)

    def test_the_fake_worker_refuses_to_run_outside_a_guided_demo(self):
        p = subprocess.run([sys.executable, "-B", AGENT], cwd=self.home, env=dict(self.env, CLAUDE_CONFIG_DIR=self.tmp),
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 2)
        self.assertIn("only runs inside a `pai guide` demo", p.stdout)


if __name__ == "__main__":
    unittest.main()
