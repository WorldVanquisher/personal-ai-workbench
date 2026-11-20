"""tools/command_inventory.py: every `pai ...` example in the help texts and the shipped documents
parses with the parser that would receive it, and the inventory lists every routed command."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(ROOT, "tools", "command_inventory.py")


def run(*args):
    return subprocess.run([sys.executable, "-B", TOOL, *args], capture_output=True, text=True, timeout=120)


@unittest.skipIf(sys.version_info < (3, 11), "the workbench needs Python 3.11")
class Inventory(unittest.TestCase):
    def test_every_example_in_help_and_docs_parses(self):
        p = run("--check-examples")
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr)
        self.assertIn(" 0 FAILED", p.stdout)

    def test_a_wrong_example_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            doc = os.path.join(d, "doc.md")
            with open(doc, "w", encoding="utf-8") as f:
                f.write("```sh\npai status --jsn\npai task export --out cards.json\npai queue mark q.json R2-001 done "
                        "--evidence x\n```\nSee `pai recover --contine a`.\n")
            p = run("--check-examples", doc)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("FAIL     ", p.stdout)
        self.assertIn("unrecognized arguments: --jsn", p.stdout)
        self.assertIn("--contine", p.stdout)
        self.assertRegex(p.stdout, r"ok\s+.*pai task export --out cards.json")
        self.assertRegex(p.stdout, r"ok\s+.*pai queue mark q.json R2-001 done")

    def test_inventory_routes_every_workbench_command(self):
        p = run("--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("NOT ROUTED", p.stdout)
        for cmd in ("pai queue restart", "pai recover", "pai task import", "pai run", "pai guide"):
            self.assertIn('"%s"' % cmd, p.stdout)


if __name__ == "__main__":
    unittest.main()
