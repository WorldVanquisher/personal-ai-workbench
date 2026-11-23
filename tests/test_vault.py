"""The vault as control surface (D9 (a)): one vault resolver (R6-COR-043), `pai vault lint` against
the front matter schema (R6-COR-039), decisions (R6-COR-041), Syncthing conflicts (R6-COR-042) and
`pai vault wait` (R6-COR-044). Synthetic vaults only."""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pai import handoff, vault  # noqa: E402
from test_pai import run_cli  # noqa: E402

V2 = """---
format: pai-handoff/2
status: draft
approved: false
project: demo
round: R6
lane: core
checkpoint: cp-A
base_sha: %s
head_sha: %s
queue: none
queue_sha256: none
---

## Summary

ok
""" % ("a" * 40, "b" * 40)
DECISION = """---
pai_managed: "decision/1"
status: "open"
options: ["(a) Obsidian", "(b) web"]
choice: "%s"
source: "30 Projects/sheet.md"
source_sha: "b578b6050b861690"
pai_sha256: "%s"
---

<!-- pai:managed:begin pai-vault/1 -->
question
<!-- pai:managed:end -->
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.v = Path(self.tmp.name) / "vault"
        (self.v / "Agent Inbox" / "R6" / "core" / "cp-A").mkdir(parents=True)
        (self.v / "PAI" / "decisions").mkdir(parents=True)

    def write(self, rel, text):
        p = self.v / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def findings(self, **kw):
        return [(r["level"], r["path"], r["message"]) for r in vault.lint(str(self.v), decision_check=None, **kw)
                if r["level"] != "INFO"]


class Resolve(Base):
    def test_order_and_sources(self):
        other = Path(self.tmp.name) / "other"
        (other / "Agent Inbox").mkdir(parents=True)
        path, src, notes = vault.resolve(argument=str(self.v), env={"VAULT": str(other)})
        self.assertEqual((path, src), (os.path.realpath(self.v), "argument"))
        self.assertTrue(any("VAULT names another folder" in n for n in notes))
        self.assertEqual(vault.resolve(env={"VAULT": str(other)})[1], "VAULT")
        script = self.v / "99 Templates" / "r6-prepare.sh"
        self.assertEqual(vault.resolve(script=str(script), env={})[:2], (os.path.realpath(self.v), "script location"))
        self.assertEqual(vault.resolve(config_vault=str(self.v), argument=str(self.v), env={})[1], "configuration")

    def test_config_and_argument_that_disagree_are_refused(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        with self.assertRaises(vault.VaultError) as cm:
            vault.resolve(config_vault=str(self.v), argument=str(other), env={})
        self.assertIn("pai uses one vault", str(cm.exception))

    def test_container_path_and_nothing_given(self):
        with self.assertRaises(vault.VaultError) as cm:
            vault.resolve(argument="/vaults/MainVault", env={})
        self.assertIn("container path", str(cm.exception))
        with self.assertRaises(vault.VaultError):
            vault.resolve(env={})

    def test_config_load_uses_it(self):
        from pai import config
        with self.assertRaises(config.ConfigError) as cm:
            config.from_dict("demo", {"repo": self.tmp.name, "vault": "/vaults/MainVault", "memory": "M", "inbox": "I"})
        self.assertIn("container path", str(cm.exception))


class Lint(Base):
    def test_owner_notes_are_not_judged_and_a_good_handoff_passes(self):
        self.write("Agent Inbox/notes.md", "---\ntitle: mine\napproved: maybe\n---\n\nowner text\n")
        self.write("PAI/Home.md", "# no front matter\n")
        self.write("Agent Inbox/R6/core/cp-A/HANDOFF.md", V2)
        self.assertEqual(self.findings(), [])
        info = [r["message"] for r in vault.lint(str(self.v), decision_check=None) if r["level"] == "INFO"]
        self.assertIn("PAI notes checked: handoff 1", info)

    def test_handoff_problems(self):
        self.write("Agent Inbox/R6/core/cp-A/HANDOFF.md", V2.replace("status: draft", "status: done")
                   .replace("base_sha: " + "a" * 40, "base_sha: c8bc51e").replace("queue: none\n", ""))
        msgs = [m for _, _, m in self.findings()]
        self.assertIn("handoff: `queue` is missing", msgs)
        self.assertTrue(any("`status: done` is not one of" in m for m in msgs))
        self.assertTrue(any("`base_sha: c8bc51e` does not have the expected form" in m for m in msgs))

    def test_a_sealed_handoff_whose_body_changed_and_an_unrecorded_approval(self):
        sealed = handoff.with_front_matter(V2, {"status": "sealed", "sealed_sha256": handoff.body_sha(V2),
                                                "approval": "pending", "approved": "true"})
        p = self.write("Agent Inbox/R6/core/cp-A/HANDOFF.md", sealed)
        self.assertTrue(any("not recorded yet" in m for _, _, m in self.findings()))
        p.write_text(sealed.replace("ok\n", "ok, edited\n"), encoding="utf-8")
        self.assertTrue(any("approval is stale" in m for _, _, m in self.findings()))

    def test_decision_choice_must_be_an_option(self):  # R6-COR-041
        self.write("PAI/decisions/R6-D9.md", DECISION % ("(c) neither", "0" * 64))
        self.assertTrue(any("choice '(c) neither' is not one of the options" in m for _, _, m in self.findings()))
        self.write("PAI/decisions/R6-D9.md", DECISION % ("(a) Obsidian", "0" * 64))
        self.assertEqual(self.findings(), [])
        self.write("PAI/decisions/R6-D9.md", (DECISION % ("", "0" * 64)).replace('["(a) Obsidian", "(b) web"]', "[]"))
        self.assertTrue(any("nothing to choose from" in m for _, _, m in self.findings()))

    def test_sync_conflicts_are_reported_never_deleted(self):  # R6-COR-042
        p = self.write("PAI/decisions/R6-D9.sync-conflict-20261122-101010-ABCDEFG.md", "x")
        q = self.write("Agent Inbox/R6/core/cp-A/HANDOFF.sync-conflict-20261122-1-X.md", "y")
        rows = [r for r in vault.lint(str(self.v), decision_check=None) if r["level"] == "CONFLICT"]
        self.assertEqual(len(rows), 2)
        self.assertIn("pai never deletes it", rows[0]["message"])
        self.assertTrue(p.exists() and q.exists())

    def test_cli_exit_codes(self):
        self.write("Agent Inbox/R6/core/cp-A/HANDOFF.md", V2)
        rc, out, _ = run_cli("vault", "lint", "--vault", str(self.v))
        self.assertEqual(rc, 0, out)
        self.write("PAI/x.sync-conflict-1.md", "z")
        rc, out, _ = run_cli("vault", "lint", "--vault", str(self.v), "--json")
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["findings"][0]["level"], "CONFLICT")


class Wait(Base):
    def test_waits_for_the_expected_sha_then_times_out(self):  # R6-COR-044
        target = self.v / "Agent Inbox" / "note.md"
        want = hashlib.sha256(b"synced\n").hexdigest()
        t = [0.0]
        steps = {1.0: b"partial", 2.0: b"synced\n"}

        def sleep(s):
            t[0] += s
            if t[0] in steps:
                target.write_bytes(steps[t[0]])
        ok, last = vault.wait(str(target), want[:16], timeout=10, interval=0.5, clock=lambda: t[0], sleep=sleep)
        self.assertEqual((ok, last, t[0]), (True, want, 2.0))
        ok, last = vault.wait(str(target), "0" * 16, timeout=1.5, interval=0.5, clock=lambda: t[0], sleep=sleep)
        self.assertEqual((ok, last), (False, want))
        with self.assertRaises(vault.VaultError):
            vault.wait(str(target), "abc", 1)

    def test_cli(self):
        target = self.write("Agent Inbox/n.md", "x\n")
        sha = hashlib.sha256(b"x\n").hexdigest()
        rc, out, _ = run_cli("vault", "wait", str(target), "--sha", sha[:12], "--timeout", "0")
        self.assertEqual(rc, 0, out)
        rc, out, _ = run_cli("vault", "wait", str(target) + ".missing", "--sha", sha[:12], "--timeout", "0")
        self.assertEqual(rc, 1)
        self.assertIn("TIMEOUT", out)


class SchemaDoc(unittest.TestCase):
    def test_the_published_schema_is_the_one_lint_uses(self):
        with open(ROOT / "docs" / "frontmatter.schema.json", encoding="utf-8") as f:
            self.assertEqual(f.read(), vault.schema_json(), "regenerate: pai vault schema > docs/frontmatter.schema.json")
        rc, out, _ = run_cli("vault", "schema")
        self.assertEqual(json.loads(out)["format"], "pai-frontmatter-schema/1")


SWARM = ROOT / "swarm"


class FieldsSeenInTheOwnersVault(unittest.TestCase):
    """R6-FIN-009 (docs NOTES-FOR-core N7): lane `dialog` and `queued`, Home `title`; the real vault's notes carry
    them (pai vault lint on MainVault, read-only, 2026-11-23: no field problem)."""

    def test_the_schema_and_its_json_copy_list_them(self):
        def names(kind, s):
            k = s["kinds"][kind]
            return set(k.get("required", [])) | set(k.get("fields", {})) | set(k.get("optional", [])) | \
                set(k.get("owner_fields", []))
        with open(ROOT / "docs" / "frontmatter.schema.json", encoding="utf-8") as f:
            shipped = json.load(f)
        for s in (vault.SCHEMA, shipped):
            self.assertLessEqual({"dialog", "queued"}, names("lane", s))
            self.assertIn("title", names("home", s))


@unittest.skipUnless((SWARM / "round_vault.py").exists() and (SWARM / "decide.py").exists(),
                     "the round lane's decision notes are not in this tree")
class DecisionShaWithTheRoundLanesCode(unittest.TestCase):
    """R6-COR-041 rerun on the merged tree (R6-FIN-010): the stub is gone; the notes are written by
    swarm/round_vault.py from a real decision sheet and lint uses the round lane's own checks."""

    def setUp(self):
        import importlib
        import shutil
        sys.path[:0] = [str(SWARM), str(SWARM / "tests")]
        try:
            self.rv = importlib.import_module("round_vault")
            make_round = importlib.import_module("test_round_board").make_round
        finally:
            del sys.path[:2]
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.r = make_round(tmp)
        self.vault = self.r["vault"]
        self.sheet = os.path.join(self.vault, "sheet.md")
        self.write(self.sheet, '- [ ] **D7 预算**：$5 够不够。\n  - 选项：A $5；B $10, or "more"\n  - 推荐：A\n'
                               '- [ ] **D1 定位**：PAI 是"润滑层"，不重写 Claude Code。\n  - 选项：同意；不同意\n')
        self.rv.decision_notes(self.r, lambda s: None)
        self.note = os.path.join(self.vault, "PAI", "decisions", "R9-D7.md")
        self.assertIsNotNone(vault._round_decision_check(self.vault), "the real check, not a stub")

    @staticmethod
    def write(path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def problems(self):
        return [(r["path"], r["message"]) for r in vault.lint(self.vault, roots=("PAI",)) if r["level"] == "PROBLEM"]

    def test_unedited_notes_pass_even_with_quotes_and_commas(self):
        self.assertEqual(self.problems(), [])  # before: "the PAI-written part was edited" for both notes

    def test_a_sheet_saved_with_crlf_line_ends_passes(self):
        # an editor on Windows saves \r\n; the round lane hashes the bytes, so lint must read them the same way
        text = self.read(self.sheet)
        with open(self.sheet, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)
        self.rv.decision_notes(self.r, lambda s: None)
        self.assertEqual(self.problems(), [])

    def test_a_short_choice_the_round_lane_accepts_passes(self):
        self.write(self.note, self.read(self.note).replace('choice: ""', "choice: A", 1))
        self.assertEqual(self.problems(), [])  # before: "choice 'A' is not one of the options"
        self.write(self.note, self.read(self.note).replace("choice: A", "choice: C", 1))
        self.assertEqual(len(self.problems()), 1)

    def test_an_edited_note_and_a_changed_question_are_reported(self):
        text = self.read(self.note)
        self.write(self.note, text.replace("$5 够不够。\n\n|", "$6 够不够。\n\n|", 1))
        self.assertIn("the PAI-written part was edited", self.problems()[0][1])
        self.write(self.note, text)
        self.write(self.sheet, self.read(self.sheet).replace("$5 够不够", "$7 够不够"))
        self.assertIn("the question changed in sheet.md", self.problems()[0][1])


@unittest.skipUnless((SWARM / "round_vault.py").exists() and (ROOT / "pai" / "prompteval.py").exists(),
                     "the round and prompts lanes' note writers are not in this tree")
class EveryWriterMatchesTheSchema(unittest.TestCase):
    """R6-COR-078 (F3): the notes the round lane (lane, ROUND, decision, Home, integration, log) and the
    prompts lane (eval) really write are recognised by the schema, carry only keys it lists, and lint
    reports no PROBLEM for them. Synthetic round; the writers are the real code."""

    def test_generated_notes(self):
        import importlib
        import shutil
        sys.path[:0] = [str(SWARM), str(SWARM / "tests")]
        try:
            rv, rb, ri = (importlib.import_module(m) for m in ("round_vault", "round_board", "round_integrate"))
            make_round = importlib.import_module("test_round_board").make_round
            rows = importlib.import_module("test_round_vault").rows
            from pai import prompteval
        finally:
            del sys.path[:2]
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        r = make_round(tmp)
        r["_path"], r["status"] = os.path.join(r["state_dir"], "ROUND.json"), "open"
        with open(os.path.join(r["vault"], "sheet.md"), "w", encoding="utf-8") as f:
            f.write("- [ ] **D7 Budget**: is $5 enough.\n  - 选项：A $5；B $10\n  - 推荐：A\n  - 默认：A\n")
        with open(r["_path"], "w", encoding="utf-8") as f:
            json.dump(r, f)
        quiet = lambda s: None  # noqa: E731
        rv.write_vault(r, rows(), None, quiet)
        rb.write_daily(r, "2026-11-22", [["20:00", "R9", "go", "because"]], quiet)
        rep = {"order": ["alpha"], "patches": [], "conflicts": [], "hot_files": [], "test": {"exit": 0},
               "at": "2026-11-22T20:00:00Z"}
        orig = ri.render
        ri.render = lambda r_, rep_: "\n" + rv.BEGIN + "\n# x\n" + rv.END + "\n"
        try:
            ri.write(r, rep, quiet)
        finally:
            ri.render = orig
        res = {"artifact": "a.md", "passed": 1, "total": 1, "score": 1.0, "behaviour": {}}
        prompteval.write_note(os.path.join(r["vault"], "PAI", "eval-R9.md"), [res], {"name": "c", "version": 1}, "R9", [])
        seen = {}
        for d, _, files in os.walk(r["vault"]):
            for name in files:
                if name.endswith(".md"):
                    with open(os.path.join(d, name), encoding="utf-8") as f:
                        fm = vault.front_matter(f.read())
                    kind = vault.kind_of(fm) if fm else None
                    if kind and kind != "handoff":
                        seen[kind] = name
                        self.assertEqual(set(fm) - vault.known_fields(kind), set(), "%s (%s): keys not in the schema"
                                         % (name, kind))
        self.assertEqual(sorted(seen), ["decision", "eval", "home", "integration", "lane", "log", "round"])
        problems = [x for x in vault.lint(r["vault"], decision_check=None) if x["level"] != "INFO"]
        self.assertEqual(problems, [])



class EmptyVault(unittest.TestCase):
    def test_none_yet_says_what_counts(self):  # R6-COR-090
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "PAI", "prompts"))
            msg = [x["message"] for x in vault.lint(d, decision_check=None) if x["level"] == "INFO"][-1]
        self.assertIn("none yet", msg)
        self.assertIn("pai vault schema", msg)

if __name__ == "__main__":
    unittest.main()
