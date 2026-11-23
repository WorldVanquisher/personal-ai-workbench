"""JEV for the prompt library with a fake workbench client (R6-PRM-018, 027): no network, no key."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pai import promptjev, promptlib  # noqa: E402


class FakeJev:
    """Stands in for swarm/jev.py: available(), ask(state, questions, timeout), last_usage."""

    def __init__(self, score=0.9, fail=False, key=True):
        self.score, self.fail, self.key, self.states, self.last_usage = score, fail, key, [], None

    def available(self):
        return self.key

    def ask(self, state, questions, timeout=None):
        self.states.append(state)
        if self.fail:
            raise RuntimeError("Jev HTTP 500: boom")
        self.last_usage = {"input_tokens": 350, "output_tokens": 20}
        s = self.score(state) if callable(self.score) else self.score
        return {q: ({"choice": sorted(spec["criteria"])[0], "confidence": s} if spec.get("type") == "choice" else {"noul": s})
                for q, spec in questions.items()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-prompt-jev-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = os.path.join(self.tmp, "usage.jsonl")
        os.environ["PAI_JEV_USAGE_LOG"] = self.log
        self.addCleanup(os.environ.pop, "PAI_JEV_USAGE_LOG", None)
        self.addCleanup(promptjev._jev.clear)

    def fake(self, **kw):
        promptjev._jev.clear()
        f = FakeJev(**kw)
        promptjev._jev.append(f)
        return f

    def usage(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f]


class ScoreRules(Base):
    RULES = [("x#1", "Rules", "Write to {{VAULT}}/Agent Inbox/{{RUN_ID}}/."), ("x#2", "Rules", "Be good.")]

    def test_scores_low_rules_and_logs_usage_without_text(self):
        f = self.fake(score=lambda s: 0.2 if "Be good" in s else 0.9)
        r = promptjev.score_rules(self.RULES, below=0.6)
        self.assertEqual([x["score"] for x in r["scores"]], [0.9, 0.2])
        self.assertEqual([x["id"] for x in r["low"]], ["x#2"])
        self.assertIn("2 rule(s) scored, 0 failed, mean 0.55, 1 below 0.60", r["summary"])
        # placeholders are filled with sample values before asking (JEV-QUESTIONS.md §1.2)
        self.assertIn("/vault/Agent Inbox/R7-20261201/", f.states[0])
        self.assertNotIn("{{", f.states[0])
        u = self.usage()
        self.assertEqual(len(u), 2)
        self.assertEqual(u[0]["usage"], {"input_tokens": 350, "output_tokens": 20})
        self.assertEqual(u[0]["purpose"], "lint-rule")
        self.assertNotIn("Agent Inbox", json.dumps(u), "the log never holds the text sent")

    def test_failures_are_counted_not_raised(self):
        self.fake(fail=True)
        r = promptjev.score_rules(self.RULES)
        self.assertIn("0 rule(s) scored, 2 failed", r["summary"])
        self.assertEqual([x["ok"] for x in self.usage()], [False, False])
        self.assertEqual(self.usage()[0]["error"], "RuntimeError")

    def test_without_a_key_it_is_skipped(self):
        self.fake(key=False)
        self.assertIn("skipped", promptjev.score_rules(self.RULES)["summary"])
        self.assertFalse(os.path.exists(self.log))

    def test_escaped_names_stay_prose(self):
        self.assertEqual(promptjev.sample("{{LANE}} and \\{{NAME}} and {{OTHER}}"), "docs and \\{{NAME}} and other")

    def test_out_of_range_answers_are_not_scores(self):
        self.assertIsNone(promptjev.noul({"q": {"noul": 1.5}}, "q"))
        self.assertIsNone(promptjev.noul({}, "q"))


class LintJev(Base):
    def test_lint_jev_json_lists_scores_and_never_fails_on_them(self):
        self.fake(score=0.1)
        from io import StringIO
        from contextlib import redirect_stdout
        from pai import cli
        buf = StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["prompt", "lint", "restart", "--jev", "--json"])
        self.assertEqual(rc, 0, "low JEV scores never fail lint")
        data = json.loads(buf.getvalue())
        j = list(data["files"].values())[0]["jev"]
        self.assertEqual(len(j["low"]), len(promptlib.rules(promptlib.read(os.path.join(promptlib.PACKAGED, "restart.md")))))


if __name__ == "__main__":
    unittest.main()


class Optimize(Base):
    """pai prompt optimize --dry-run (R6-PRM-039): candidates, JEV gate, budget, never writes."""

    def lib(self, body, kind="other"):
        d = os.path.join(self.tmp, "lib")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "x.md"), "w", encoding="utf-8") as f:
            f.write("<!-- pai-prompts/1 begin name=x version=1 kind=%s vars= -->\n%s\n<!-- pai-prompts/1 end -->\n" % (kind, body))
        return d

    def run_opt(self, *args):
        from io import StringIO
        from contextlib import redirect_stdout
        from pai import cli
        buf = StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["prompt", "optimize", "--dry-run", *args])
        return rc, buf.getvalue()

    BODY = ("## 规则\n- 清空之前必须先做两件事：往 COORDINATOR-LOG.md 追加当前状态和下一步；跑 pai round brief 生成接手包给新会话。\n"
            "- 在 worktree 上测的结果统一写 \"lane tree; rerun on merged main needed\"，不要写成 main 的结果，也不要省略。")

    def test_lead_in_stays_parent_and_quoted_semicolons_are_not_split(self):
        p = promptlib.read(os.path.join(self.lib(self.BODY), "x.md"))
        from pai import promptopt
        c = promptopt.split_candidates(p)
        self.assertEqual(len(c), 1, "the quoted 'lane tree; rerun' is not a split point")
        self.assertEqual(c[0][1][0], "- 清空之前必须先做两件事：")
        self.assertTrue(c[0][1][1].startswith("  - 往 COORDINATOR-LOG.md"))

    def test_jev_gate_budget_and_nothing_written(self):
        d = self.lib(self.BODY)
        before = promptlib.read(os.path.join(d, "x.md")).text
        f = self.fake(score=lambda s: 0.4 if "两件事：往" in s else 0.8)
        rc, out = self.run_opt("x", "--library", d, "--jev", "--budget-requests", "10")
        self.assertEqual(rc, 0)
        self.assertIn("PROPOSE  split     0.40 -> 0.80", out)
        self.assertIn("+  - 往 COORDINATOR-LOG.md", out)
        self.assertIn("dry run: nothing written; 3 JEV request(s) of 10 allowed", out)
        self.assertIn("SECTION: 规则 > 清空之前必须先做两件事：", f.states[1], "children carry the lead-in")
        self.assertEqual(promptlib.read(os.path.join(d, "x.md")).text, before)
        rc, out = self.run_opt("x", "--library", d, "--jev", "--budget-requests", "1")
        self.assertIn("keep     split", out.replace("unscored", "keep    "))
        self.assertIn("1 JEV request(s) of 1 allowed", out)

    def test_without_jev_candidates_are_listed_unscored(self):
        rc, out = self.run_opt("x", "--library", self.lib(self.BODY))
        self.assertIn("unscored", out)
        self.assertNotIn("+  - 往", out)

    def test_missing_worker_part_gets_a_skeleton(self):
        rc, out = self.run_opt("x", "--library", self.lib("==== 规则 ====\n- a", kind="worker"))
        self.assertIn("+==== 停止规则 ====", out)
        self.assertIn("TODO（人来写）", out)

    def test_dry_run_is_required(self):
        from pai import cli
        from contextlib import redirect_stderr
        from io import StringIO
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.main(["prompt", "optimize"])
