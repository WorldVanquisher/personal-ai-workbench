"""rc.assign_tasks never restarts a handed-off note as a new task (R6-COR-013).

A continuation (`pai recover --continue --write`) sets the note back to `status: todo` and records
`handed_off_from`. Before this fix `rc.assign_tasks` chose notes only by `status == "todo"`, so a
plain `pai run` / `pai next` could start the note again on a fresh base."""
import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import rc  # noqa: E402


def note(d, name, priority, extra=""):
    with open(os.path.join(d, name + ".md"), "w") as f:
        f.write("---\nstatus: todo\npriority: %d\n%s---\n# %s\n\nDo %s.\n" % (priority, extra, name, name))


class AssignTasks(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="pai-assign-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        note(self.d, "handed", 9, "handed_off_from: run1/api@0123456789ab\n")
        note(self.d, "fresh", 1)

    def manifest(self, task=None, continues=None):
        w = types.SimpleNamespace(lane="api", task=task, note=None)
        return types.SimpleNamespace(tasks_dir=self.d, workers=[w], data={"continues": continues} if continues else {}), w

    def test_a_plain_run_skips_the_handed_off_note_even_with_a_higher_priority(self):
        m, w = self.manifest()
        self.assertEqual(rc.assign_tasks(m), [])
        self.assertEqual(w.task, "fresh")

    def test_naming_a_handed_off_note_without_continue_is_refused(self):
        m, w = self.manifest(task="handed")
        problems = rc.assign_tasks(m)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("was handed off (run1/api@0123456789ab)", problems[0])
        self.assertIn("pai run --continue", problems[0])
        self.assertIsNone(w.note)

    def test_run_continue_may_take_it(self):
        m, w = self.manifest(task="handed", continues={"id": "run1/api@0123456789ab", "head": "0" * 40})
        self.assertEqual(rc.assign_tasks(m), [])
        self.assertEqual(w.note.name, "handed")
        m, w = self.manifest(continues={"id": "run1/api@0123456789ab", "head": "0" * 40})
        self.assertEqual(rc.assign_tasks(m), [])
        self.assertEqual(w.task, "handed")


if __name__ == "__main__":
    unittest.main()
