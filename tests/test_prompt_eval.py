"""pai prompt eval: output contracts, PASS/FAIL per check, JEV beside them (mock), calibration
(R6-PRM-021, 022, 025-027). No network."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pai import cli, prompteval, promptjev, promptlib  # noqa: E402
from tests.test_prompt_jev import FakeJev  # noqa: E402

SHA = "ab" * 32
GOOD_V2 = """---
format: pai-handoff/2
status: sealed
approved: false
project: pai
round: R9
lane: docs
checkpoint: cp-A
base_sha: %s
head_sha: %s
queue: /work/queue
queue_sha256: %s
---

## Summary

Did things.

## State

| State | Value | Source |
|---|---|---|
| Implementation | 3 files | real run |
| Validation | 12 tests OK | real run |
| Commit | none (owner-only) | real run |
| Merge | not merged (owner-only) | real run |
| Deployment | none | real run |

## Items

| ID | state | evidence |
|---|---|---|
| R9-DOC-001 | done | a.md |
| R9-DOC-002 | blocked | needs key |
| R9-DOC-003 | todo | - |

Totals: todo 1, doing 0, done 1, prototype 0, blocked 1, owner-only 0 (3 items)

## Evidence

- a.md  %s  sha256sum a.md

## Decisions within authority

- chose X; reverse by Y.

## Decisions for the owner

- none

## Blockers

- needs the key: `set -a; . ~/.config/pai/jev.env; set +a`

## Next

- R9-DOC-003
""" % (SHA[:40], SHA[:40], SHA, SHA)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-prompt-eval-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(promptjev._jev.clear)
        os.environ["PAI_JEV_USAGE_LOG"] = os.path.join(self.tmp, "usage.jsonl")
        self.addCleanup(os.environ.pop, "PAI_JEV_USAGE_LOG", None)

    def write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def run_cli(self, *args):
        buf = StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["prompt", "eval", *args])
        return rc, buf.getvalue()

    def checks(self, text, contract="handoff-v2"):
        return {c["id"]: c for c in prompteval.run_checks(text, prompteval.load_contract(contract))}


class Contracts(Base):
    def test_every_packaged_contract_loads_and_names_a_library_prompt(self):
        names = sorted(f[:-5] for f in os.listdir(prompteval.CONTRACTS) if f.endswith(".json"))
        self.assertEqual(names, ["decision-sheet", "first-reply", "handoff-v1", "handoff-v2", "intake", "next-pr",
                                 "request", "research-report", "restart-reply"])
        lib = set(promptlib.library_files(promptlib.PACKAGED)) | {"SESSION_START"}
        for n in names:
            c = prompteval.load_contract(n)
            self.assertIn(c["source_prompt"], lib, n)
            for q in c.get("jev", []):
                if q.get("maps_to"):
                    self.assertIn(q["maps_to"], [k["id"] for k in c["checks"]], (n, q["id"]))

    def test_bad_contracts_are_refused(self):
        bad = self.write("bad.json", json.dumps({"format": "pai-contract/1", "name": "bad",
                                                 "checks": [{"id": "a", "type": "nope"}]}))
        with self.assertRaises(promptlib.PromptError):
            prompteval.load_contract(bad)
        with self.assertRaises(promptlib.PromptError):
            prompteval.load_contract("no-such-contract")


class Checks(Base):
    def test_a_complete_v2_handoff_passes_everything(self):
        failed = [c for c in self.checks(GOOD_V2).values() if not c["ok"]]
        self.assertEqual(failed, [])

    def test_totals_must_agree_with_rows(self):
        c = self.checks(GOOD_V2.replace("Totals: todo 1,", "Totals: todo 0,"))
        self.assertFalse(c["items-totals"]["ok"])
        self.assertIn("todo 0≠1", c["items-totals"]["detail"])
        c = self.checks(GOOD_V2.replace("Totals: todo 1", "Total todo 1"))
        self.assertIn("no Totals: line", c["items-totals"]["detail"])

    def test_truncated_digest_fails(self):
        c = self.checks(GOOD_V2.replace("- a.md  %s" % SHA, "- ~/x (SHA256SUMS digest d4ffc8fae2f1d768)"))
        self.assertFalse(c["evidence-sha"]["ok"])
        self.assertIn("truncated", c["evidence-sha"]["detail"])

    def test_state_rows_need_a_source_label(self):
        c = self.checks(GOOD_V2.replace("| Commit | none (owner-only) | real run |", "| Commit | none (owner-only) | |"))
        self.assertFalse(c["state-labels"]["ok"])
        self.assertIn("Commit", c["state-labels"]["detail"])

    def test_regression_none_supplied_is_not_a_blocker_answer(self):
        # calibration 2026-11-22 (PROMPT-BASELINE.md §4.2): check v2 took "None supplied" for "none"
        text = GOOD_V2.replace("- needs the key: `set -a; . ~/.config/pai/jev.env; set +a`",
                               "- None supplied (not the same as none existing).")
        self.assertFalse(self.checks(text)["blockers-cmd"]["ok"])
        self.assertTrue(self.checks(GOOD_V2.replace("- needs the key: `set -a; . ~/.config/pai/jev.env; set +a`",
                                                    "- none"))["blockers-cmd"]["ok"])

    def test_v1_handoff_problems(self):
        v1 = ("---\nstatus: draft\napproved: false\ntask: T-1\nproject: flh\n---\n\n## Summary\n\nx\n\n## State\n\n"
              "| State | Value |\n|---|---|\n| Implementation | x |\n| Validation | y |\n| Commit | none |\n"
              "| Merge | UNKNOWN (not supplied) |\n| Deployment | none |\n\n## Evidence\n\n- ~/flh-handoff/x (tar 92634054)\n\n"
              "## Blockers\n\n- None supplied (not the same as none existing).\n")
        c = self.checks(v1, "handoff-v1")
        self.assertTrue(c["frontmatter"]["ok"] and c["state"]["ok"])
        for k in ("merge-known", "blockers-stated", "evidence-in-vault", "evidence-sha", "items-totals", "labels-any"):
            self.assertFalse(c[k]["ok"], k)

    def test_first_reply_needs_five_full_ids(self):
        reply = ("**读过的文件**\n- docs/a.md、pai/b.py、tools/c.py\n**已有答案的决策**\n- D1\n"
                 "**先做的 5 个 ID**：R6-PRM-001、002、004、003、013\n**owner-only 或被阻塞**\n- none\n**依赖其他线的地方**\n- none\n")
        c = self.checks(reply, "first-reply")
        self.assertFalse(c["first-ids"]["ok"])
        full = reply.replace("R6-PRM-001、002、004、003、013", "R6-PRM-001、R6-PRM-002、R6-PRM-004、R6-PRM-003、R6-PRM-013")
        self.assertTrue(all(x["ok"] for x in self.checks(full, "first-reply").values()))

    def test_decision_sheet_fields(self):
        text = ("- [ ] **D1 x**\n  - 问题：a\n  - 选项：A；B\n  - 取舍：…\n  - 推荐：A\n  - 默认做法：按推荐执行\n  - 阻塞：none\n"
                "- [ ] **D2 y**\n  - 问题：b\n  - 推荐：B\n")
        c = self.checks(text, "decision-sheet")
        self.assertFalse(c["fields"]["ok"])
        self.assertIn("D2", c["fields"]["detail"])


class BlockE(Base):
    def test_evidence_lines_each_need_a_full_sha256(self):
        c = self.checks(GOOD_V2.replace("## Decisions within", "- see the cp folder for more\n\n## Decisions within"))
        self.assertFalse(c["evidence-sha"]["ok"])
        self.assertIn("see the cp folder", c["evidence-sha"]["detail"])
        c = self.checks(GOOD_V2.replace("## Decisions within", "- none: nothing else\n\n## Decisions within"))
        self.assertTrue(c["evidence-sha"]["ok"])

    def test_totals_must_name_all_five_states(self):
        c = self.checks(GOOD_V2.replace("Totals: todo 1, doing 0, done 1, prototype 0, blocked 1, owner-only 0 (3 items)", "Totals: done 1, blocked 1, todo 1"))
        self.assertFalse(c["totals-five"]["ok"])

    def plan(self, n_doc, n_cor=30, missing=False):
        lines = ["## JEV", "x", "## Lanes", "| lane | code | files | n |", "|---|---|---|---|", "| docs | DOC | docs/ | %d |" % n_doc, ""]
        for code, n in (("DOC", n_doc), ("COR", n_cor)):
            for i in range(1, n + 1):
                lines += ["R7-%s-%03d  Do item %d" % (code, i, i), "            完成标准：test passes",
                          "            owner-only：否" if not (missing and i == 2) else "", "            依赖：无"]
        lines += ["## 决策单", "- [ ] **D1 x**", "  - 问题：a", "  - 选项：A；B", "  - 取舍：t", "  - 推荐：A", "  - 默认做法：不做",
                  "  - 阻塞：none", "## 没读到", "none"]
        return "\n".join(lines)

    def test_intake_items_fields_and_lane_size(self):
        fm = "---\ntype: pai-intake\nround: R7-20261201\nid_prefix: R7\n---\n"
        self.assertTrue(all(x["ok"] for x in self.checks(fm + self.plan(30), "intake").values()))
        c = self.checks(self.plan(12), "intake")
        self.assertIn("DOC has 12", c["items"]["detail"])
        c = self.checks(self.plan(30, missing=True), "intake")
        self.assertIn("R7-DOC-002: owner-only", c["items"]["detail"])

    def test_intake_ids_must_use_the_round_prefix(self):
        plan = "---\ntype: pai-intake\nround: R7-20261201\nid_prefix: R7\n---\n" + self.plan(30)
        self.assertTrue(self.checks(plan, "intake")["id-prefix"]["ok"])
        wrong = plan.replace("R7-COR-", "R6-COR-")
        c = self.checks(wrong, "intake")["id-prefix"]
        self.assertFalse(c["ok"])
        self.assertIn("prefix(es) R6, expected R7", c["detail"])
        self.assertFalse(self.checks(self.plan(30), "intake")["id-prefix"]["ok"], "no front matter")

    def test_request_choice_question_is_recorded(self):
        promptjev._jev.append(FakeJev(score=0.9))
        req = self.write("req.md", "---\ntype: pai-request\ntemplate: pai-request/1\nstatus: inbox\nproject: demo\npriority: normal\n"
                         "budget: 5\n---\n# R\n\n## 需求\n\nRename the key timeout_s to timeout_seconds in cli/config.py and update the tests.\n")
        rc, text = self.run_cli(req, "--contract", "request", "--jev", "--json")
        self.assertEqual(rc, 0, text)
        j = json.loads(text)["results"][0]["jev"]
        self.assertEqual(j["choices"]["tier"], ["haiku"])
        self.assertEqual(j["answers"]["multi_lane"], [0.9])


class BlockF(Base):
    """Owner decisions PRM-D3..D7 (2026-11-22) in contract handoff-v2 v5."""

    def test_d3_totals_must_be_the_queue_table_line(self):
        c = self.checks(GOOD_V2.replace("Totals: todo 1, doing 0, done 1, prototype 0, blocked 1, owner-only 0 (3 items)",
                                        "Totals: done 1, prototype 0, blocked 1, owner-only 0, todo 1"))
        self.assertTrue(c["items-totals"]["ok"])
        self.assertFalse(c["items-from-queue"]["ok"], "hand-written Totals are not the pasted queue table")

    def test_d4_evidence_outside_the_cp_folder_needs_the_5mb_note(self):
        line = "- ~/flh-handoff/big.tar  %s  sha256sum big.tar" % SHA
        c = self.checks(GOOD_V2.replace("## Decisions within", line + "\n\n## Decisions within"))
        self.assertFalse(c["evidence-local"]["ok"])
        c = self.checks(GOOD_V2.replace("## Decisions within", line + "  >5 MB\n\n## Decisions within"))
        self.assertTrue(c["evidence-local"]["ok"])

    def test_d5_state_has_exactly_three_columns(self):
        c = self.checks(GOOD_V2.replace("| State | Value | Source |", "| State | Value |"))
        self.assertFalse(c["state-columns"]["ok"])

    def test_d6_unknown_for_not_done_and_blockers_without_command(self):
        c = self.checks(GOOD_V2.replace("| Merge | not merged (owner-only) | real run |", "| Merge | UNKNOWN | real run |"))
        self.assertFalse(c["not-done-not-unknown"]["ok"])
        c = self.checks(GOOD_V2.replace("- needs the key: `set -a; . ~/.config/pai/jev.env; set +a`",
                                        "- needs the key: `set -a` first\n- the owner should look at it"))
        self.assertFalse(c["blockers-cmd"]["ok"])
        self.assertIn("the owner should look at it", c["blockers-cmd"]["detail"])

    def test_d7_abbreviated_ids(self):
        c = self.checks(GOOD_V2.replace("- R9-DOC-003\n", "- R9-DOC-003、004\n"))
        self.assertFalse(c["full-ids"]["ok"])


class Behaviour(Base):
    """Deterministic behaviour metrics (R6-PRM-070, 073-075)."""

    def cp(self, handoff=GOOD_V2, next_pr=None, changed=None):
        d = os.path.join(self.tmp, "cp-X")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "HANDOFF.md"), "w", encoding="utf-8") as f:
            f.write(handoff)
        if next_pr is not None:
            with open(os.path.join(d, "NEXT-PR.md"), "w", encoding="utf-8") as f:
                f.write(next_pr)
        if changed is not None:
            with open(os.path.join(d, "changed-paths.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(changed) + "\n")
        return os.path.join(d, "HANDOFF.md")

    def metrics(self, path):
        with open(path, encoding="utf-8") as f:
            return prompteval.behaviour(path, f.read())

    def test_good_checkpoint(self):
        npr = "## Branch\n\nx\n\n## Commits\n\n    a.md\n    b.py\n\n## PR body\n"
        m = self.metrics(self.cp(GOOD_V2.replace("- R9-DOC-003\n", "- Next ID: R9-DOC-003; RESTART: `cat RESTART.md`\n"),
                                 npr, ["a.md", "b.py"]))
        self.assertEqual(m, {"stop_ok": 1.0, "placeholders": 0, "handoff_complete": 1.0, "labelled_share": 1.0,
                             "next_pr_exec": 1.0})

    def test_undocumented_stop_unlabelled_numbers_and_missing_paths(self):
        text = GOOD_V2.replace("Did things.", "Did things; 40 tests OK.").replace("| Validation | 12 tests OK | real run |",
                                                                              "| Validation | 12 tests OK | |")
        m = self.metrics(self.cp(text, "## Commits\n\n    a.md\n    c.md\n", ["a.md"]))
        self.assertEqual(m["stop_ok"], 0.0, "todo 1 and Next has no RESTART pointer")
        self.assertEqual(m["labelled_share"], 0.0)
        self.assertEqual(m["next_pr_exec"], 0.5)

    def test_regression_restart_pointer_is_case_insensitive(self):
        # 2026-11-22: docs cp-B wrote "Restart: `pai queue restart ...`" and was counted as an undocumented stop
        m = self.metrics(self.cp(GOOD_V2.replace("- R9-DOC-003\n", "- Next ID: R9-DOC-003. Restart: `pai queue restart q`\n")))
        self.assertEqual(m["stop_ok"], 1.0)

    def test_placeholders_and_totals_disagreement(self):
        m = self.metrics(self.cp(GOOD_V2.replace("Did things.", "Base {{BASE_SHA}} and {{NAME}}.").replace("Totals: todo 1,", "Totals: todo 0,")))
        self.assertEqual(m["placeholders"], 1, "only library-declared names count; prose {{NAME}} does not")
        self.assertEqual(m["handoff_complete"], 0.5)
        self.assertEqual(m["stop_ok"], 1.0, "Totals says todo 0")

    def test_warnings_never_fail_and_note_is_managed(self):
        path = self.cp(GOOD_V2)
        note = os.path.join(self.tmp, "PAI", "eval", "R9.md")
        rc, text = self.run_cli(path, "--contract", "handoff-v2", "--behavior", "--round", "R9", "--note", note)
        self.assertEqual(rc, 0, "behaviour warnings never fail an artifact whose checks pass")
        self.assertIn("WARN stop_ok 0.000 < 1.000", text)
        with open(note, encoding="utf-8") as f:
            body = f.read()
        self.assertTrue(body.startswith("---\ntype: pai-eval\n"))
        self.assertIn("round: R9", body)
        self.assertIn("behaviour_warnings: 1", body)
        self.assertIn("| artifact | pass | score | behaviour warnings |", body)
        rc, _ = self.run_cli(path, "--contract", "handoff-v2", "--note", note)
        self.assertEqual(rc, 0, "a PAI-managed note is replaced")
        own = self.write("own.md", "# my notes\n")
        from contextlib import redirect_stderr
        with redirect_stderr(StringIO()):
            rc, _ = self.run_cli(path, "--contract", "handoff-v2", "--note", own)
        self.assertEqual(rc, 1, "a refusal")
        with open(own, encoding="utf-8") as f:
            self.assertEqual(f.read(), "# my notes\n", "never overwrites a note PAI did not write")


class EvalCommand(Base):
    def test_exit_codes_summary_and_out_files(self):
        good, bad = self.write("good.md", GOOD_V2), self.write("bad.md", "# nothing\n")
        out = os.path.join(self.tmp, "_eval")
        rc, text = self.run_cli(good, bad, "--contract", "handoff-v2", "--out", out)
        self.assertEqual(rc, 1)
        self.assertIn("PASS 19/19", text)
        self.assertIn("mean pass rate", text)
        files = sorted(os.listdir(out))
        self.assertIn("summary-handoff-v2.md", files)
        with open(os.path.join(out, [f for f in files if f.endswith("good.handoff-v2.json")][0]), encoding="utf-8") as fh:
            r = json.load(fh)
        self.assertEqual(r["format"], "pai-prompt-eval/1")
        self.assertEqual((r["passed"], r["total"], r["contract_version"]), (19, 19, 6))
        self.assertEqual(len(r["artifact_sha256"]), 64)
        rc, _ = self.run_cli(good, "--contract", "handoff-v2")
        self.assertEqual(rc, 0)

    def test_jev_is_asked_per_section_repeated_and_never_changes_pass_fail(self):
        f = FakeJev(score=lambda s: 0.9 if "| Implementation |" in s else 0.1)
        promptjev._jev.append(f)
        good = self.write("good.md", GOOD_V2)
        rc, text = self.run_cli(good, "--contract", "handoff-v2", "--jev", "--repeat", "2", "--json")
        self.assertEqual(rc, 0, "low JEV scores never fail an artifact whose checks pass")
        data = json.loads(text)
        j = data["results"][0]["jev"]
        self.assertEqual(j["answers"]["numbers_labeled"], [0.9, 0.9], "asked about the State section")
        self.assertEqual(j["answers"]["blockers_actionable"], [0.1, 0.1])
        self.assertEqual(len(f.states), 3 * 2, "one request per section, repeated")
        self.assertTrue(all(s.startswith(("\n|", "\n- ")) for s in f.states))
        cal = data["calibration"]
        self.assertEqual(cal["numbers_labeled"]["agree"], 1)
        self.assertEqual(cal["blockers_actionable"]["disagree"][0]["check_ok"], True)

    def test_without_jev_client_it_is_skipped(self):
        promptjev._jev.append(FakeJev(key=False))
        good = self.write("good.md", GOOD_V2)
        rc, text = self.run_cli(good, "--contract", "handoff-v2", "--jev")
        self.assertEqual(rc, 0)
        self.assertIn("jev skipped", text)

    def test_missing_artifact_is_a_refusal(self):
        buf = StringIO()
        with redirect_stdout(buf):
            from contextlib import redirect_stderr
            with redirect_stderr(StringIO()):
                rc = cli.main(["prompt", "eval", os.path.join(self.tmp, "nope.md"), "--contract", "handoff-v2"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
