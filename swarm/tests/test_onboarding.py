"""A new user's first run, and the task-note command, with fakes only.

The acceptance harness (tools/onboarding_acceptance.py) builds the archives and goes from a
verified download to a first delivered task in a fresh HOME; here it runs in a temporary
folder. `pai task new --from` imports are checked on their own."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311  # noqa: E402

ROOT = os.path.dirname(helpers.SWARM)


@helpers.needs_tmux
@needs_311
class FirstRun(unittest.TestCase):
    def test_from_archive_to_first_delivered_task_in_a_fresh_home(self):
        with tempfile.TemporaryDirectory() as t:
            work = os.path.join(t, "w")
            p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "onboarding_acceptance.py"),
                                "--work", work], capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL,
                               env={k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")})
            self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr[-2000:])
            res = json.load(open(os.path.join(work, "onboarding-results.json")))
            self.assertTrue(res["passed"])
            self.assertEqual(res["manual_prompts_answered_with_text"], 1, res["prompts"])


@needs_311
@unittest.skipUnless(helpers.shutil.which("git"), "needs git")
class Tasks(WB):
    def setUp(self):
        super().setUp()
        self.init()

    def new(self, *args):
        return self.pai("task", "new", *args)

    def test_import_keeps_the_notes_own_front_matter_and_adds_what_the_queue_needs(self):
        src = os.path.join(self.tmp, "Fix login.md")
        with open(src, "w") as f:
            f.write("---\npriority: 4\nchecks: [verify]\nowner: me # mine\n---\nThe login button does nothing.\n")
        p = self.new("--from", src)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PREVIEW (nothing written)", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tasks, "fix-login.md")))
        self.assert_ok(self.new("--from", src, "--write"))
        text = open(os.path.join(self.tasks, "fix-login.md")).read()
        self.assertTrue(text.startswith("---\npriority: 4\nchecks: [verify]\nowner: me # mine\nstatus: todo\n---\n"), text)
        self.assertIn("# Fix login\n\nThe login button does nothing.\n", text)
        out = self.assert_ok(self.pai("task", "list"))
        self.assertIn("fix-login", out)
        self.assertIn("1 todo", out)

    def test_import_with_a_heading_and_another_status_is_kept_but_flagged(self):
        src = os.path.join(self.tmp, "n.md")
        with open(src, "w") as f:
            f.write("---\nstatus: inbox\n---\n# Tidy the docs\n\nShorter README.\n")
        p = self.new("--from", src, "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("will not take it until it is todo", p.stdout)
        with open(src) as f, open(os.path.join(self.tasks, "tidy-the-docs.md")) as g:
            self.assertEqual(f.read(), g.read(), "a complete note is imported unchanged")
        self.assertIn("The queue is empty", self.assert_ok(self.pai("task", "list")))

    def test_refusals_change_nothing_and_say_what_to_do(self):
        self.assert_ok(self.new("Add a flag", "--write"))
        p = self.new("Add a flag", "--body", "other", "--write")
        self.assertEqual(p.returncode, 1)
        self.assertIn("already exists; nothing was written", p.stdout)
        p = self.new()
        self.assertEqual(p.returncode, 2)
        self.assertIn('pai task new "', p.stderr)
        self.set_config({("run", "tasks"): None})
        p = self.new("x")
        self.assertEqual(p.returncode, 2)
        self.assertIn("pai init --update --tasks", p.stderr)


if __name__ == "__main__":
    unittest.main()
