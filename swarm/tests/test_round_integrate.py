"""pai round integrate with synthetic lane patches: checkpoint patch vs worktree fallback, order,
conflicts reported and kept going, hot files, dependencies, a merge plan draft, --test on the
merged copy, INTEGRATION.md as a managed note; the repository itself is never changed. Stale data
(a newer queue checkpoint, a lane left out) is flagged STALE with exit 3; an approved resolutions
list (off by default) settles the conflicts it names."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_round_new import RoundFixture, git, needs_git  # noqa: E402
import round as rnd  # noqa: E402
import round_integrate as ri  # noqa: E402

VERIFY = '''import json, os, sys
w = sys.argv[sys.argv.index("--work") + 1]
os.makedirs(w)
ok = open("README.md").read().count("alpha") == 1
import socket
s = socket.socket(socket.AF_UNIX)
s.bind(os.path.join(w, "tmux.sock"))  # verify's onboarding leaves a tmux socket behind
s.close()
json.dump({"steps": [{"step": "core", "result": "PASS" if ok else "FAIL", "seconds": 1, "detail": "1 test; logs in " + w}]},
          open(os.path.join(w, "verify-report.json"), "w"))
print("core PASS" if ok else "core FAIL")
sys.exit(0 if ok else 1)
'''


def diff_new(path, text):
    lines = text.splitlines()
    return "diff --git a/{0} b/{0}\nnew file mode 100644\n--- /dev/null\n+++ b/{0}\n@@ -0,0 +1,{1} @@\n{2}\n".format(
        path, len(lines), "\n".join("+" + l for l in lines))


@needs_git
class Integrate(RoundFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.new("--lane", "alpha", "--lane", "beta", "--lane", "gamma")[0], 0)
        self.path = os.path.join(self.vault, "Agent Inbox", "R9-TEST", "ROUND.json")
        self.r = rnd.load_round(self.path)
        lanes = {l["name"]: l for l in self.r["lanes"]}
        # alpha: a sealed checkpoint patch (README line + tools/verify.py + allowlist)
        cp = os.path.join(lanes["alpha"]["handoff_dir"], "cp-A")
        os.makedirs(cp)
        patch = ("diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n hello\n+alpha\n"
                 + diff_new("tools/verify.py", VERIFY) + diff_new("release/allowlist.txt", "swarm/alpha.py"))
        with open(os.path.join(cp, "cumulative.patch"), "w") as f:
            f.write(patch)
        with open(os.path.join(cp, "HANDOFF.md"), "w") as f:
            f.write("# h\n## Blockers\n- 等 core 的 HANDOFF-V2 定稿\n## Next\nx\n")
        with open(os.path.join(cp, "NEXT-PR.md"), "w") as f:
            f.write("# NEXT-PR\n## Commits\n1. `feat(alpha): add alpha`\n   git add a\n## 合并前要跑\n- tests\n")
        # beta: no patch in its checkpoint, so its worktree is used: the same README line -> conflict
        os.makedirs(os.path.join(lanes["beta"]["handoff_dir"], "cp-A"))
        with open(os.path.join(lanes["beta"]["worktree"], "README.md"), "a") as f:
            f.write("beta\n")
        with open(os.path.join(lanes["beta"]["worktree"], "b.txt"), "w") as f:
            f.write("b\n")
        # gamma: only a proposal that also creates release/allowlist.txt (a hot file)
        os.makedirs(os.path.join(lanes["gamma"]["handoff_dir"], "proposals"))
        with open(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "allowlist.patch"), "w") as f:
            f.write(diff_new("release/allowlist.txt", "swarm/gamma.py"))
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.lines = []

    def run_it(self, **kw):
        return ri.integrate(self.r, log=self.lines.append, **kw)

    def test_plan(self):
        rep = self.run_it(order=["alpha", "beta", "gamma"])
        by = {p["name"]: p for p in rep["patches"]}
        self.assertEqual([p["name"] for p in rep["patches"]], ["alpha-lane", "beta-lane", "gamma-allowlist"])
        self.assertEqual(by["alpha-lane"]["result"], "ok")
        self.assertEqual(by["alpha-lane"]["source"], "checkpoint")
        self.assertTrue(by["beta-lane"]["source"].startswith("worktree"))
        self.assertEqual(by["beta-lane"]["result"], "CONFLICT")
        self.assertEqual(by["beta-lane"]["files"], ["README.md"])
        self.assertEqual(by["gamma-allowlist"]["result"], "ok (line union)")  # a set of lines: merged as a union
        self.assertEqual(by["gamma-allowlist"]["union"]["release/allowlist.txt"]["missing_file"], ["swarm/gamma.py"])
        self.assertEqual(rep["unions"][0]["patch"], "gamma-allowlist")
        tree = rep["tree"]
        with open(os.path.join(tree, "b.txt")) as f:  # the rest of beta still applied
            self.assertEqual(f.read(), "b\n")
        hot = {h["path"]: h["patches"] for h in rep["hot_files"]}
        self.assertEqual(rep["hot_files"][0]["path"], "release/allowlist.txt")  # known hot spot first
        self.assertEqual(hot["README.md"], ["alpha-lane", "beta-lane"])
        self.assertEqual(rep["dependencies"][0]["text"], "等 core 的 HANDOFF-V2 定稿")
        self.assertEqual(rep["pr_plan"][0]["commits"], ["feat(alpha): add alpha"])
        self.assertEqual(rep["pr_plan"][0]["checks"], ["tests"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(rep["test"], None)

    def test_order_and_no_proposals(self):
        rep = self.run_it(order=["beta", "alpha"], proposals="none")
        self.assertEqual([p["name"] for p in rep["patches"]], ["beta-lane", "alpha-lane"])
        self.assertEqual(rep["patches"][1]["result"], "CONFLICT")  # alpha now meets beta's README line
        with self.assertRaises(rnd.RoundError):
            self.run_it(order=["nope"])

    def test_test_runs_verify_on_the_merged_copy(self):
        rep = self.run_it(order=["alpha", "gamma"])
        self.assertEqual(rep["test"], None)
        rep = self.run_it(order=["alpha"], proposals="none", test=True)
        self.assertEqual(rep["test"]["exit"], 0)
        self.assertEqual(rep["test"]["steps"]["core"]["status"], "PASS")
        # verify ran in a short folder (tmux's socket under TMPDIR=<work>/tmp must fit 107 bytes); its logs are kept
        final = os.path.join(os.path.dirname(rep["tree"]), "verify")
        self.assertEqual(rep["test"]["steps"]["core"]["summary"], "1 test; logs in " + final)
        self.assertTrue(os.path.isfile(os.path.join(final, "verify-report.json")))
        self.assertFalse(os.path.exists(os.path.join(final, "tmux.sock")))  # a socket is not copied (it used to crash)

    def test_verify_work_folder_is_short_enough_for_a_tmux_socket(self):
        w = ri.short_work()
        self.addCleanup(os.rmdir, w)
        self.assertLessEqual(len(os.path.join(w, "v", "tmp", "tmux-%d" % os.getuid(), "default")), 107)
        rep = self.run_it(order=["alpha", "beta"], proposals="none", test=True)  # beta's partial apply leaves alpha's line
        self.assertIn(rep["test"]["exit"], (0, 1))

    def test_write_integration_note(self):
        rep = self.run_it(order=["alpha", "beta", "gamma"])
        res = ri.write(self.r, rep, self.lines.append)
        self.assertEqual(res, "created")
        note = os.path.join(self.r["state_dir"], "INTEGRATION.md")
        with open(note, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('pai_managed: "integration/1"', text)
        self.assertIn("## 冲突（1）", text)
        self.assertIn("## 自动合并", text)
        self.assertIn("README.md", text)
        self.assertIn("feat(alpha): add alpha", text)
        self.assertIn("PAI 不会 push", text)
        with open(os.path.join(self.r["state_dir"], "INTEGRATION.json")) as f:
            self.assertEqual(json.load(f)["order"], ["alpha", "beta", "gamma"])

    def test_union_adds_lines_for_files_in_the_copy_and_never_duplicates(self):
        lanes = {l["name"]: l for l in self.r["lanes"]}
        with open(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "allowlist.patch"), "w") as f:
            f.write(diff_new("release/allowlist.txt", "swarm/alpha.py\nREADME.md\ntools/verify.py"))
        rep = self.run_it(order=["alpha", "gamma"])
        u = rep["patches"][-1]["union"]["release/allowlist.txt"]
        self.assertEqual((u["present"], u["added"]), (["swarm/alpha.py"], ["README.md", "tools/verify.py"]))
        with open(os.path.join(rep["tree"], "release", "allowlist.txt")) as f:
            self.assertEqual(f.read().split(), ["swarm/alpha.py", "README.md", "tools/verify.py"])

    def test_a_base_to_both_proposal_is_already_contained(self):
        lanes = {l["name"]: l for l in self.r["lanes"]}
        os.remove(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "allowlist.patch"))
        with open(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "readme-both.patch"), "w") as f:
            f.write("--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n hello\n+alpha\n")  # what alpha already did
        rep = self.run_it(order=["alpha", "gamma"])
        self.assertEqual(rep["patches"][-1]["result"], "ok (already in the merged copy)")
        self.assertEqual(rep["conflicts"], [])
        with open(os.path.join(lanes["gamma"]["handoff_dir"], "proposals", "readme-both.patch"), "w") as f:
            f.write("--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n hello\n+other\n")
        rep = self.run_it(order=["alpha", "gamma"])
        self.assertEqual(rep["patches"][-1]["result"], "CONFLICT")

    def test_cli(self):
        code, out, err = self.run_round("integrate", self.path, "--plan", "--order", "alpha", "--proposals", "none", "--write")
        self.assertEqual(code, 3, out + err)  # beta and gamma are left out: stale
        self.assertIn("0 conflict(s)", out)
        self.assertIn("not in the plan: beta, gamma", out)
        res = self.resolutions({"file": "README.md", "how": "both"})
        code, out, err = self.run_round("integrate", self.path, "--plan", "--order", "alpha,beta,gamma", "--resolutions", res)
        self.assertEqual(code, 0, out + err)
        self.assertIn("ok (resolved: both)", out)
        code, out, err = self.run_round("integrate", self.path)
        self.assertEqual(code, 2)

    # -- stale data (R6-RND-083) ----------------------------------------------------------------
    def queue_event(self, lane, handoff=None, at="2026-11-22T20:36:00Z"):
        ln = {l["name"]: l for l in self.r["lanes"]}[lane]
        os.makedirs(ln["work_dir"], exist_ok=True)
        with open(os.path.join(ln["work_dir"], "queue.log"), "a") as f:
            f.write(json.dumps({"at": at, "event": "checkpoint", "id": "R9-%s-009" % lane.upper(),
                                "handoff": handoff}) + "\n")
        return ln

    def test_a_newer_queue_checkpoint_is_stale(self):
        ln = self.queue_event("alpha", os.path.join(self.r["lanes"][0]["handoff_dir"], "cp-A", "HANDOFF.md"))
        rep = self.run_it()
        self.assertEqual(rep["stale"], [])  # the queue's checkpoint is the folder the plan used
        self.queue_event("alpha", os.path.join(ln["handoff_dir"], "cp-B", "HANDOFF.md"))  # sealed after: not on disk yet
        rep = self.run_it()
        self.assertEqual(len(rep["stale"]), 1)
        self.assertIn("alpha: queue checkpoint R9-ALPHA-009", rep["stale"][0])
        self.assertIn("is cp-B, the plan used cp-A", rep["stale"][0])
        ri.write(self.r, rep, self.lines.append)
        with open(os.path.join(self.r["state_dir"], "INTEGRATION.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertLess(text.index("STALE"), text.index("## 补丁"))  # at the top
        self.assertNotIn("stale:", text.split("---")[1])  # front matter keys stay within core's schema

    def test_a_checkpoint_event_without_a_handoff_path_is_judged_by_time(self):
        self.queue_event("alpha", None, at="2099-01-01T00:00:00Z")
        self.assertIn("is newer than cp-A", self.run_it()["stale"][0])
        self.queue_event("alpha", None, at="2000-01-01T00:00:00Z")  # the newest event wins
        self.assertEqual(self.run_it()["stale"], [])

    def test_the_brief_says_stale_when_a_lane_checkpoints_after_the_integration(self):
        import round_board as rb
        rep = self.run_it()
        ri.write(self.r, rep, self.lines.append)
        self.assertNotIn("STALE", "\n".join(rb.integration_summary(self.r)))
        self.queue_event("beta", None, at="2099-01-01T00:00:00Z")  # F6: a lane sealed its final after the run
        got = rb.integration_summary(self.r)
        self.assertTrue(got[0].startswith("**STALE**"))
        self.assertIn("集成之后 beta 又交了 checkpoint", "\n".join(got))

    def test_final_folders_and_the_queue_sealed_folder_are_used(self):
        ln = {l["name"]: l for l in self.r["lanes"]}["alpha"]
        fj = os.path.join(ln["handoff_dir"], "final-J")  # R6 round lane: final-J was ignored (only cp-* and final)
        os.makedirs(fj)
        with open(os.path.join(fj, "cumulative.patch"), "w") as f:
            f.write(diff_new("f.txt", "final"))
        os.utime(fj, (2e9, 2e9))
        self.assertEqual(ri.lane_patch(self.r, ln, self.tmp)["checkpoint"], "final-J")
        later = os.path.join(ln["handoff_dir"], "cp-K")  # a newer folder the queue has not sealed yet
        os.makedirs(later)
        with open(os.path.join(later, "cumulative.patch"), "w") as f:
            f.write(diff_new("k.txt", "half done"))
        os.utime(later, (2.1e9, 2.1e9))
        self.queue_event("alpha", os.path.join(fj, "HANDOFF.md"))
        self.assertEqual(ri.lane_patch(self.r, ln, self.tmp)["checkpoint"], "final-J")
        rep = self.run_it(order=["alpha", "beta", "gamma"])
        self.assertEqual(rep["stale"], [])
        import round_status as rs
        self.assertEqual(rs.latest_checkpoint(ln["handoff_dir"])["name"], "cp-K")  # status still shows the newest folder

    def test_lanes_left_out_are_stale(self):
        self.assertEqual(self.run_it(order=["alpha", "beta"])["stale"], ["lane(s) in ROUND.json not in the plan: gamma"])

    # -- approved resolutions (R6-RND-084) ------------------------------------------------------
    def resolutions(self, *entries, **top):
        path = os.path.join(self.tmp, "resolutions.json")
        with open(path, "w") as f:
            json.dump(dict({"format": "pai-resolutions", "version": 1, "approved_by": "mastermind",
                            "resolutions": list(entries)}, **top), f)
        return path

    def readme(self, rep):
        with open(os.path.join(rep["tree"], "README.md")) as f:
            return f.read().split()

    def test_off_by_default(self):
        self.assertEqual(self.run_it()["conflicts"][0]["files"], ["README.md"])

    def test_both_keeps_both_sides_and_is_written_down(self):
        res = ri.load_resolutions(self.resolutions({"file": "README.md", "how": "both", "note": "两边都保留", "by": "owner"}))
        rep = self.run_it(resolutions=res)
        beta = rep["patches"][1]
        self.assertEqual(beta["result"], "ok (resolved: both)")
        self.assertEqual(rep["conflicts"], [])
        self.assertEqual(self.readme(rep), ["hello", "alpha", "beta"])
        self.assertEqual(rep["resolved"][0]["patch"], "beta-lane")
        with open(os.path.join(rep["tree"], "b.txt")) as f:  # the rest of beta is still there
            self.assertEqual(f.read(), "b\n")
        ri.write(self.r, rep, self.lines.append)
        with open(os.path.join(self.r["state_dir"], "INTEGRATION.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## 按已审定的解决方式处理", text)
        self.assertIn("**beta-lane** `README.md`：both（两边都保留）", text)
        self.assertIn("审定：mastermind", text)
        self.assertIn(res["sha256"][:12], text)

    def test_ours_and_theirs(self):
        rep = self.run_it(resolutions=ri.load_resolutions(self.resolutions({"file": "README.md", "how": "ours"})))
        self.assertEqual(self.readme(rep), ["hello", "alpha"])
        rep = self.run_it(resolutions=ri.load_resolutions(self.resolutions({"file": "README.md", "how": "theirs"})))
        self.assertEqual(self.readme(rep), ["hello", "beta"])

    def test_file_resolution_checks_its_sha(self):
        good = os.path.join(self.tmp, "README.resolved")
        with open(good, "w") as f:
            f.write("hello\nalpha + beta\n")
        import hashlib
        sha = hashlib.sha256(b"hello\nalpha + beta\n").hexdigest()
        rep = self.run_it(resolutions=ri.load_resolutions(self.resolutions(
            {"file": "README.md", "how": "file", "path": good, "sha256": sha})))
        self.assertEqual(rep["patches"][1]["result"], "ok (resolved: file)")
        self.assertEqual(self.readme(rep), ["hello", "alpha", "+", "beta"])
        with self.assertRaises(rnd.RoundError):
            ri.load_resolutions(self.resolutions({"file": "README.md", "how": "file", "path": good, "sha256": "0" * 64}))

    def test_an_entry_for_another_patch_or_file_does_not_apply(self):
        for e in ({"file": "README.md", "how": "both", "patch": "gamma-allowlist"}, {"file": "b.txt", "how": "both"}):
            rep = self.run_it(resolutions=ri.load_resolutions(self.resolutions(e)))
            self.assertEqual(rep["patches"][1]["result"], "CONFLICT")
            self.assertEqual(rep["resolved"], [])

    def test_malformed_lists_are_refused(self):
        for bad in ({"file": "README.md", "how": "merge"}, {"how": "both"}):
            with self.assertRaises(rnd.RoundError):
                ri.load_resolutions(self.resolutions(bad))
        with self.assertRaises(rnd.RoundError):
            ri.load_resolutions(os.path.join(self.tmp, "missing.json"))


if __name__ == "__main__":
    unittest.main()
