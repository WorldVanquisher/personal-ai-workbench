"""tools/worktree_rehearsal.py: the owner-to-worker loop end to end, offline (R4-PAI-002/003/071):
refresh -> verify -> AGENTS.md -> split -> queue -> edit -> preview -> dry run -> publish, against
a local bare remote and the fake gh; stopping after a step and resuming from the files alone."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402

TOOL = os.path.join(os.path.dirname(helpers.SWARM), "tools", "worktree_rehearsal.py")


def run(*args):
    return subprocess.run([sys.executable, "-B", TOOL] + list(args), capture_output=True, text=True, timeout=600,
                          stdin=subprocess.DEVNULL)


@helpers.needs_git
class Rehearsal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-rehearsal-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = os.path.join(self.tmp, "r")

    def test_the_whole_loop_with_the_owner_approved_publish(self):
        p = run("--dir", self.d, "--publish")
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr)
        self.assertNotIn("CHECK FAIL", p.stdout)
        for line in ("the dry run says what it would do and did nothing", "pushed exactly the reviewed commit and opened "
                     "one draft PR", "a retry reuses the PR", "Rehearsal complete: every check held."):
            self.assertIn(line, p.stdout)
        self.assertFalse(os.path.exists(self.d), "the folder is removed unless --keep")

    def test_stop_after_a_step_and_resume_from_the_task_card(self):
        p = run("--dir", self.d, "--stop-after", "queue")
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr)
        self.assertIn("STOPPED after queue", p.stdout)
        sys.path.insert(0, helpers.SWARM)
        import cards
        import notes
        card = notes.Task(os.path.join(self.d, "TASK-CARD.md"))
        self.assertEqual((card.status, card.meta["stopped_after"], card.meta["resume_with"]), ("in-progress", "queue", "edit"))
        c, _ = cards.from_note(card)
        self.assertIn("| edit | NEXT | lane a/src/report/__init__.py changed; NOTES.txt staged (unrelated) |", c["description"])
        self.assertEqual(c["acceptance"], ["every CHECK line of the rehearsal holds", "nothing reaches the network"])
        p = run("--resume", self.d)
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr)
        self.assertIn("(queue: done in an earlier run, skipped)", p.stdout)
        self.assertIn("the dry run says what it would do and did nothing", p.stdout)
        self.assertEqual(notes.Task(os.path.join(self.d, "TASK-CARD.md")).meta["stopped_after"], "dryrun")
        self.assertEqual(run("--resume", os.path.join(self.tmp, "nothing")).returncode, 2)


if __name__ == "__main__":
    unittest.main()
