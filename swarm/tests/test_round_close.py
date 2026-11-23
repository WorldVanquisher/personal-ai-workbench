"""pai round close: checklist first; reclaim only clean, idle worktrees; changes, processes,
missing folders, other open rounds and other clones are handled as r6-worktrees.sh did;
branches are kept; ROUND.json is archived once no lane worktree is left."""
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_round_new import RoundFixture, git, needs_git  # noqa: E402
import round as rnd  # noqa: E402
import round_close as rcl  # noqa: E402


@needs_git
class Close(RoundFixture):
    def setUp(self):
        super().setUp()
        os.environ["PAI_ROUND_TMUX_SOCKET"] = os.path.join(self.tmp, "no-tmux-here")
        self.addCleanup(os.environ.pop, "PAI_ROUND_TMUX_SOCKET", None)
        code, out, err = self.new("--lane", "alpha", "--lane", "beta", "--lane", "gamma", "--lane", "delta")
        self.assertEqual(code, 0, err)
        self.path = os.path.join(self.vault, "Agent Inbox", "R9-TEST", "ROUND.json")
        self.r = rnd.load_round(self.path)
        self.wt = {ln["name"]: ln["worktree"] for ln in self.r["lanes"]}

    def close(self, *extra, write=False):
        return self.run_round("close", self.path, *(list(extra) + (["--write"] if write else [])))

    def items(self, **kw):
        return {os.path.basename(it["path"]): it for it in rcl.plan(rnd.load_round(self.path), **kw)}

    def test_dirty_busy_missing_and_clean(self):
        with open(os.path.join(self.wt["beta"], "notes.txt"), "w") as f:
            f.write("x\n")
        p = subprocess.Popen(["sleep", "60"], cwd=self.wt["gamma"])
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        shutil.rmtree(self.wt["delta"])
        it = self.items()
        self.assertEqual(it["alpha"]["action"], "remove")
        self.assertEqual(it["beta"]["action"], "keep")
        self.assertIn("notes.txt", it["beta"]["why"])
        self.assertEqual(it["gamma"]["action"], "keep")
        self.assertIn("in use by %d" % p.pid, it["gamma"]["why"])
        self.assertEqual(it["delta"]["action"], "prune")
        code, out, _ = self.close()
        self.assertEqual(code, 1)  # something would be kept
        self.assertIn("验收清单", out)
        self.assertIn("would remove", out)
        self.assertTrue(os.path.isdir(self.wt["alpha"]))  # preview changed nothing
        code, out, _ = self.close(write=True)
        self.assertFalse(os.path.exists(self.wt["alpha"]))
        self.assertTrue(os.path.isdir(self.wt["beta"]) and os.path.isdir(self.wt["gamma"]))
        for lane in ("alpha", "beta", "gamma", "delta"):  # every branch survives
            self.assertTrue(git(self.repo, "rev-parse", "--verify", "refs/heads/worker/R9-TEST/" + lane))
        self.assertNotIn(os.path.realpath(self.wt["delta"]), git(self.repo, "worktree", "list"))
        self.assertEqual(rnd.load_round(self.path)["status"], "open")
        self.assertIn("stays open", out)

    def test_all_clean_archives_the_round(self):
        code, out, err = self.close(write=True)
        self.assertEqual(code, 0, out + err)
        r = rnd.load_round(self.path)
        self.assertEqual(r["status"], "closed")
        arch = os.listdir(os.path.join(self.vault, "Agent Inbox", "R9-TEST", "archive"))
        self.assertEqual(len(arch), 1)
        self.assertIn("archived", out)

    def test_only_re_limits_the_candidates(self):
        it = self.items(only_re=r"/(alpha|beta)$")
        self.assertEqual(it["alpha"]["action"], "remove")
        self.assertEqual(it["gamma"]["action"], "keep")
        self.assertEqual(it["gamma"]["why"], "not matched by --only-re")
        code, _, err = self.close("--only-re", "(")
        self.assertEqual(code, 2)
        self.assertIn("--only-re", err)

    def test_another_open_round_is_protected_and_old_worktrees_are_candidates(self):
        code, _, err = self.run_round("new", "widget", "R6-OTHER", "--github", "acme/widget", "--search", self.search,
                                      "--trusted-root", os.path.join(self.tmp, "workers", "{round}"),
                                      "--work-root", os.path.join(self.tmp, "work", "{round}"), "--lane", "x", "--write")
        self.assertEqual(code, 0, err)
        old = os.path.join(self.tmp, "old-wt")
        git(self.repo, "worktree", "add", "-q", "-b", "old/feature", old, self.base)
        it = self.items(all_worktrees=True)
        self.assertEqual(it["x"]["action"], "keep")
        self.assertIn("open round R6-OTHER", it["x"]["why"])
        self.assertEqual(it["old-wt"]["action"], "remove")
        self.assertNotIn("old-wt", self.items())  # without --all-worktrees only this round's lanes
        self.assertNotIn(os.path.basename(self.repo), self.items(all_worktrees=True))  # never the main checkout

    def test_other_clones_are_not_touched(self):
        second = self.clone("widget-copy")
        side = os.path.join(self.tmp, "side-wt")
        git(second, "worktree", "add", "-q", "-b", "side", side)
        self.close("--all-worktrees", write=True)
        self.assertTrue(os.path.isdir(side))
        self.assertIn(os.path.realpath(side), git(second, "worktree", "list"))

    def test_checklist_reports_per_lane(self):
        r = rnd.load_round(self.path)
        rows = [{"state": "idle", "worktree": "clean", "checkpoint": {"name": "cp-A", "handoff": True}, "queue": None}
                for _ in r["lanes"]]
        cl = dict(rcl.checklist(r, rows))
        labels = {label: ok for label, ok, _ in cl["alpha"]}
        self.assertTrue(labels["worktree 干净"])
        self.assertTrue(labels["最新 checkpoint 有 HANDOFF.md"])
        self.assertFalse(labels["分支上有提交"])
        self.assertFalse(labels["有 NEXT-PR.md"])


if __name__ == "__main__":
    unittest.main()
