"""A tmux-free slice for native Windows (1.0 readiness 057, prototype): portable task cards.

Task cards are pure file work: notes in, one card file out, and back, with no tmux, no process
control and no POSIX-only module. This test is in the core suite, which CI also runs on
windows-latest, so the slice is checked there whenever the owner runs CI. In this worker's run it
ran on Linux only. Nothing here makes the workbench run on native Windows: `pai task` there still
gets the WSL message (an opt-in would be a separate, owner-approved change)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWARM = ROOT / "swarm"


class CardsWithoutTmux(unittest.TestCase):
    def test_cards_and_notes_load_without_posix_only_modules(self):
        code = ("import sys; sys.path.insert(0, %r); import cards, notes; "
                "bad = sorted({'fcntl', 'termios', 'pty', 'tty', 'resource', 'grp', 'pwd'} & set(sys.modules)); "
                "print(','.join(bad))" % str(SWARM))
        p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "", "cards.py and notes.py import nothing POSIX-only")

    def test_round_trip_with_windows_style_paths_refused(self):
        sys.path.insert(0, str(SWARM))
        try:
            import cards
        finally:
            sys.path.remove(str(SWARM))
        tmp = Path(tempfile.mkdtemp(prefix="pai cards é "))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "tasks").mkdir()
        (tmp / "tasks" / "fix-export.md").write_text(
            "---\nstatus: todo\npriority: 2\npaths: [src/export.py]\n---\n# Corriger l'export\n\n修复导出。\n\n"
            "## Acceptance criteria\n\n- tests pass\n", encoding="utf-8")
        doc, _ = cards.export(str(tmp / "tasks"), project="demo", repo=str(tmp / "repo"), out_dir=str(tmp / "share"))
        self.assertEqual(doc["project"]["repository"], "../repo", "relative, with / on every system")
        text = cards.dump(doc)
        other = tmp / "other"
        other.mkdir()
        for state, c, path in cards.import_plan(cards.load(text), str(other)):
            self.assertEqual(state, "create")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(cards.to_note(c))
        self.assertEqual(cards.dump(cards.export(str(other), project="demo", repo=str(tmp / "repo"),
                                                 out_dir=str(tmp / "share"))[0]), text)
        for bad in ("C:/Users/x", "C:\\Users\\x", "\\\\server\\share", "src\\x.py"):
            with self.assertRaises(cards.CardError, msg=bad):
                cards.check_rel(bad, "paths entry")


if __name__ == "__main__":
    unittest.main()
