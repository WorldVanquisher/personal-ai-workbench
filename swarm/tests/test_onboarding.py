"""A new user's first run, and the task-note command, with fakes only.

The acceptance harness (tools/onboarding_acceptance.py) builds the archives and goes from a
verified download to a first delivered task in a fresh HOME; here it runs in a temporary
folder. `pai task new --from` imports are checked on their own."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, needs_311  # noqa: E402

ROOT = os.path.dirname(helpers.SWARM)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import onboarding_acceptance as oa  # noqa: E402

# Ubuntu's /etc/bash.bashrc defines this hook; /usr/lib/command-not-found then prints a hint
# and exits 127. Reproduced here, so the check does not depend on the host's distribution.
UBUNTU_HOOK = """command_not_found_handle() {
    if [ -x "$HOME/command-not-found" ]; then "$HOME/command-not-found" -- "$1"; return $?; fi
    printf '%s: command not found\\n' "$1" >&2; return 127
}
"""
UBUNTU_HELPER = """#!/bin/sh
shift
printf "Command '%s' not found, but can be installed with:\\n\\nsudo apt install %s\\n" "$1" "$1" >&2
exit 127
"""


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class MissingPai(unittest.TestCase):
    """The acceptance checks that a fresh shell cannot find `pai` test the exit status (127),
    not bash's own wording: Ubuntu's command-not-found hook words it differently."""

    def fresh_shell(self, bashrc, helper=None):
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, ".bashrc"), "w") as f:
                f.write(bashrc)
            if helper:
                path = os.path.join(home, "command-not-found")
                with open(path, "w") as f:
                    f.write(helper)
                os.chmod(path, 0o755)
            env = {"HOME": home, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb"}
            return oa.sh(["bash", "-ic", "pai --version"], env, cwd=home)

    def test_ubuntu_command_not_found_hook(self):
        rc, out, err = self.fresh_shell(UBUNTU_HOOK, UBUNTU_HELPER)
        self.assertIn("Command 'pai' not found, but can be installed with", err, "the fixture behaves like Ubuntu")
        self.assertNotIn("command not found", err, "the old wording check would have failed here")
        self.assertTrue(oa.pai_missing(rc), "exit %s; %s" % (rc, err))

    def test_plain_bash_and_a_found_command(self):
        rc, _, err = self.fresh_shell("")
        self.assertTrue(oa.pai_missing(rc), "exit %s; %s" % (rc, err))
        self.assertIn("pai: command not found", err)
        self.assertFalse(oa.pai_missing(0))
        self.assertFalse(oa.pai_missing(1), "pai ran and failed: that is not a missing command")


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
            with open(os.path.join(work, "onboarding-results.json")) as f:
                res = json.load(f)
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
        with open(os.path.join(self.tasks, "fix-login.md")) as f:
            text = f.read()
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
