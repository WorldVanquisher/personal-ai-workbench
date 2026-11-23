"""tools/doc_check.py: commands and flags against the real `pai --help`, links and anchors."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import doc_check  # noqa: E402


class Slug(unittest.TestCase):
    def test_github_rules(self):
        self.assertEqual(doc_check.slug("6. Your first round: one worker"), "6-your-first-round-one-worker")
        self.assertEqual(doc_check.slug("3. Check your machine: `pai doctor`"), "3-check-your-machine-pai-doctor")
        self.assertEqual(doc_check.slug("(a) Upgrade pai"), "a-upgrade-pai")
        self.assertEqual(doc_check.slug("trusted root（已信任的根目录）"), "trusted-root已信任的根目录")
        self.assertEqual(doc_check.slug("(b) 每一轮的刷新"), "b-每一轮的刷新")

    def test_repeated_headings_are_numbered_and_code_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# T\n\n## Notes\n\n```\n## not a heading\n```\n\n## Notes\n")
            self.assertEqual(doc_check.anchors(p), {"t", "notes", "notes-1"})


class Commands(unittest.TestCase):
    def test_only_commands_in_code_and_at_a_command_position(self):
        text = ("pai never answers in prose\n\n`pai queue init q --from x --write`\n\n```\n"
                "info  project  … without pai defaults yet   -> pai init\n$ pai status --brief\n"
                "cd x && pai run --dry-run   # a comment --not-a-flag\n```\n")
        got = [(w, f) for _, _, w, f in doc_check.commands(text)]
        self.assertEqual(got, [(["queue", "init", "q"], ["--from", "--write"]), (["status"], ["--brief"]),
                               (["run"], ["--dry-run"])])


class CheckFile(unittest.TestCase):
    def run_on(self, text, extra=None):
        with tempfile.TemporaryDirectory() as d:
            for name, body in (extra or {}).items():
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(body)
            p = os.path.join(d, "m.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return doc_check.check_file(p, d, doc_check.Pai(ROOT))

    def test_real_commands_and_flags_pass(self):
        problems, notes = self.run_on("```sh\npai queue init q --from items.txt --write\npai pr plan a\n"
                                      "pai doctor --agents\npai --version\n```\n")
        self.assertEqual((problems, notes), ([], []))

    def test_a_flag_of_pais_own_init_found_through_its_positional_routing(self):
        problems, _ = self.run_on("`pai init PROJECT --repo R --vault V --memory M --inbox I`\n\n`pai init demo --bogus-x`\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("--bogus-x", problems[0])

    def test_an_unknown_command_and_an_unknown_flag_fail(self):
        problems, _ = self.run_on("`pai nosuchcommand --x`\n\n`pai queue init q --from x --bogus-flag`\n")
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("unknown command `pai nosuchcommand`", problems[0])
        self.assertIn("`pai queue init` has no option --bogus-flag", problems[1])

    def test_pending_commands_are_notes_until_they_exist(self):
        problems, notes = self.run_on("<!-- doc_check: pending pai nosuchcommand, pai queue -->\n\n"
                                      "`pai nosuchcommand new --x`\n\n`pai queue status q`\n")
        self.assertEqual(problems, [])
        self.assertTrue(notes[0].startswith("PENDING") and "nosuchcommand" in notes[0], notes)
        self.assertTrue(notes[1].startswith("WARNING") and "pai queue" in notes[1], notes)

    def test_a_pending_subcommand_of_an_existing_command_stays_pending(self):
        problems, notes = self.run_on("<!-- doc_check: pending pai queue frobnicate -->\n\n"
                                      "`pai queue frobnicate q --expect-sha x`\n\n`pai queue status q --bogus`\n")
        self.assertEqual(len(problems), 1, problems)  # only the real command's bad flag
        self.assertIn("`pai queue status` has no option --bogus", problems[0])
        self.assertTrue(notes and notes[0].startswith("PENDING") and "frobnicate" in notes[0], notes)

    def test_a_developer_path_fails_like_the_bundle_build(self):
        problems, _ = self.run_on("Run it in /srv/data/obsidian or in ~/code; not /home/alice/x.\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("absolute developer path '/srv/data/'", problems[0])

    def test_a_pending_flag_of_an_existing_command(self):
        problems, notes = self.run_on("<!-- doc_check: pending pai queue status --frobnicate -->\n\n"
                                      "`pai queue status q --frobnicate`\n\n`pai queue status q --other`\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("--other", problems[0])
        self.assertTrue(any("pai queue status --frobnicate" in n for n in notes), notes)

    def test_a_pending_flag_that_exists_now_is_a_warning(self):
        problems, notes = self.run_on("<!-- doc_check: pending pai queue add --write -->\n\n"
                                      "`pai queue add q --from x --write`\n\n`pai queue add q2 --from y --write`\n")
        self.assertEqual(problems, [])
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].startswith("WARNING") and "pai queue add --write" in notes[0], notes)

    def test_links_and_anchors(self):
        problems, _ = self.run_on(
            "[ok](other.md#second-part) [self](#top) [web](https://example.invalid/x) "
            "[gone](missing.md) [bad](other.md#nope) `[in code](missing2.md)`\n\n# Top\n",
            {"other.md": "# Other\n\n## Second part\n"})
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("broken link missing.md", problems[0])
        self.assertIn("broken anchor other.md#nope", problems[1])


class Parity(unittest.TestCase):
    EN = "# T\n\nIntro.\n\n## 1. Install\n\n```sh\nx\n```\n\n| a | b |\n|---|---|\n\n## 2a. More\n\n### (b) Sub\n"

    def pair(self, zh):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d))
        en_p, zh_p = os.path.join(d, "X.md"), os.path.join(d, "X.zh-CN.md")
        for p, body in ((en_p, self.EN), (zh_p, zh)):
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
        return doc_check.parity(en_p, zh_p, d)

    def test_a_faithful_translation_passes_even_with_different_paragraphs(self):
        zh = "# 标题\n\n介绍。\n\n- 一\n- 二\n\n## 1. 安装\n\n```sh\nx\n```\n\n| 甲 | 乙 |\n|---|---|\n\n## 2a. 更多\n\n### (b) 小节\n"
        self.assertEqual(self.pair(zh), [])

    def test_a_missing_heading_or_a_different_number_fails(self):
        self.assertIn("headings differ", self.pair("# 标题\n\n## 1. 安装\n\n```sh\nx\n```\n\n| 甲 |\n|---|\n")[0])
        self.assertIn("headings differ", self.pair(self.EN.replace("## 2a.", "## 3.")
                                                   .replace("Install", "安装"))[0])

    def test_an_extra_code_block_or_a_lost_table_fails(self):
        zh = self.EN.replace("```sh\nx\n```\n", "```sh\nx\n```\n\n```sh\ny\n```\n").replace("| a | b |\n|---|---|\n", "")
        problems = self.pair(zh)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("2 code block(s), X.md has 1", problems[0])
        self.assertIn("0 table(s), X.md has 1", problems[1])


class Fields(unittest.TestCase):
    def test_fields_from_front_matter_blocks_and_opted_in_spans(self):
        with tempfile.TemporaryDirectory() as d:
            plain, marked = os.path.join(d, "plain.md"), os.path.join(d, "marked.md")
            body = ("```\n---\nformat: x\nhead_sha: y\n---\n```\n\nSay `pai: command not found` and `choice:`.\n\n"
                    "| Field | Meaning |\n|---|---|\n| `needs_owner`, `totals` | … |\n")
            with open(plain, "w", encoding="utf-8") as f:
                f.write(body)
            with open(marked, "w", encoding="utf-8") as f:
                f.write("<!-- doc_check: fields -->\n" + body)
            self.assertEqual(set(doc_check.named_fields(plain)), {"format", "head_sha"})
            self.assertEqual(set(doc_check.named_fields(marked)),
                             {"format", "head_sha", "pai", "choice", "needs_owner", "totals"})

    def test_unknown_fields_fail_against_a_schema_and_templates_are_read(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "pai", "templates"))
            with open(os.path.join(d, "pai", "templates", "t.md"), "w", encoding="utf-8") as f:
                f.write("---\nstatus: draft\nmystery_key: 1\n---\nbody\n")
            doc = os.path.join(d, "doc.md")
            with open(doc, "w", encoding="utf-8") as f:
                f.write("```\n---\nstatus: open\nchoice: A\n---\n```\n")
            schema = os.path.join(d, "s.json")
            with open(schema, "w", encoding="utf-8") as f:
                f.write('{"fields": {"decision/1": ["status", "choice"]}}')
            problems = doc_check.check_fields([doc], d, schema)
            self.assertEqual(problems, ["pai/templates/t.md:3: template front matter field `mystery_key` is not "
                                        "in the schema s.json"])
            with open(schema, "w", encoding="utf-8") as f:
                f.write('{"kinds": []}')
            with self.assertRaises(ValueError):
                doc_check.check_fields([doc], d, schema)


class CoreSchema(unittest.TestCase):
    def test_pai_frontmatter_schema_1_is_read_with_all_its_field_lists(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "frontmatter.schema.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"format": "pai-frontmatter-schema/1", "kinds": {"decision": {"fields": {"status": {}}, '
                        '"match": {"pai_managed": "decision/1"}, "required": ["source"], "owner_fields": ["choice"]}, '
                        '"lane": {"optional": ["current_id"]}}}')
            self.assertEqual(doc_check.schema_fields(p), {"status", "pai_managed", "source", "choice", "current_id"})
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"kinds": {"decision": []}}')
            with self.assertRaises(ValueError):
                doc_check.schema_fields(p)


class Main(unittest.TestCase):
    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = os.path.join(d, "good.md"), os.path.join(d, "bad.md")
            with open(good, "w") as f:
                f.write("`pai queue status q`\n")
            with open(bad, "w") as f:
                f.write("`pai queue status q --nope`\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(doc_check.main([good, "--root", ROOT]), 0)
                self.assertEqual(doc_check.main([bad, "--root", ROOT]), 1)
            self.assertIn("1 PROBLEM(S)", out.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(doc_check.main([os.path.join(d, "absent.md")]), 2)


if __name__ == "__main__":
    unittest.main()
