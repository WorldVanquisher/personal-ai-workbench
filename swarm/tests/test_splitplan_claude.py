"""splitplan sees a lane whose managed section was written with `--file CLAUDE.md` (R6-COR-015).
Before: `ownership()` read only AGENTS.md, so such a lane owned nothing and a new split could
hand its folder to another worker without a conflict."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import agentsmd  # noqa: E402
import splitplan  # noqa: E402


@helpers.needs_git
class ClaudeMdLane(helpers.Fixture):
    def setUp(self):
        super().setUp()
        for d in ("src api", "web"):
            os.makedirs(os.path.join(self.upstream, d))
            with open(os.path.join(self.upstream, d, "x.txt"), "w") as f:
                f.write("x\n")
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-qm", "dirs")
        git(self.upstream, "push", "-q", "origin", "main")
        git(self.repo, "pull", "-q", "origin", "main")
        self.a = os.path.join(self.tmp, "lane a")
        self.b = os.path.join(self.tmp, "lane b")
        git(self.repo, "worktree", "add", "-q", "-b", "lane/a", self.a)
        git(self.repo, "worktree", "add", "-q", "-b", "lane/b", self.b)
        agentsmd.write(agentsmd.plan(self.a, "the API", ["src api"], lane="a"))
        agentsmd.write(agentsmd.plan(self.b, "the web", ["web"], lane="b", file="CLAUDE.md"))

    def test_both_files_are_read(self):
        owned, _ = splitplan.ownership(self.repo)
        self.assertEqual(owned["src api"]["lane"], "a")
        self.assertEqual(owned["src api"]["file"], "AGENTS.md")
        self.assertEqual(owned["web"]["lane"], "b", owned)
        self.assertEqual(owned["web"]["file"], "CLAUDE.md")

    def test_the_other_lane_is_listed_in_a_preview(self):
        lanes = agentsmd.other_lanes(os.path.realpath(self.a))
        self.assertIn({"lane": "b", "branch": "lane/b", "scope": ["web"]}, lanes)


class UnifiedParent(unittest.TestCase):
    """R6-COR-025: a plan's parent may be the round-lane prefix, so its items are PAI-R6-COR-001, ..."""

    def test_parent_forms(self):
        for good in ("PARENT", "PAI-R6-COR", "R6-COR", "FLH-R6-REL"):
            self.assertTrue(splitplan.PARENT_RE.fullmatch(good), good)
        for bad in ("PAI-R6-COR-001", "-PAI", "PAI--R6", "A-B-C-D-E", "x" * 41):
            self.assertFalse(splitplan.PARENT_RE.fullmatch(bad), bad)
        from unittest import mock
        with mock.patch.object(splitplan, "ownership", return_value=({}, "/repo")):  # no repository needed here
            plan = splitplan.propose("PAI-R6-COR", "- api: Add it | paths: src/ | verify: make test | prereqs: none\n", 1)
        self.assertEqual(plan["slices"][0]["item"], "PAI-R6-COR-001")


if __name__ == "__main__":
    unittest.main()
