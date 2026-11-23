"""pai prompt render / lint / diff / init and the pai init hook (R6-PRM-011, 013-016). Core command:
standard library, offline, no workbench."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAI = os.path.join(ROOT, "bin", "pai.py")
sys.path.insert(0, ROOT)
from pai import promptlib  # noqa: E402

LIB = promptlib.PACKAGED
WORKER_VARS = ["PROJECT=pai", "REPO_URL=https://example.invalid/r.git", "BASE_SHA=" + "a" * 40,
               "WORKTREE=/w/docs", "BRANCH=worker/R9/docs", "WORK_DIR=/work/docs", "VAULT=/v"]


def pai(*args, cwd=None, env=None):
    e = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    e.pop("TYPESAFE_API_KEY", None)
    e.update(env or {})
    return subprocess.run([sys.executable, "-B", PAI, *args], capture_output=True, text=True, cwd=cwd, env=e)


def prompt_file(body, name="t", vars_="RUN_ID,LANE", kind="other", extra=""):
    return ("<!-- pai-prompts/1 begin name=%s version=1 kind=%s vars=%s%s -->\n%s\n<!-- pai-prompts/1 end -->\n"
            % (name, kind, vars_, extra, body))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pai-prompt-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, rel, text):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        return p


class Render(Base):
    def render(self, *names, var=(), out="out.md", rnd="R9", lane="docs", extra=()):
        args = ["prompt", "render", *names, "--round", rnd, "--lane", lane, "--out", str(self.tmp / out), *extra]
        for v in var:
            args += ["--var", v]
        return pai(*args)

    def test_packaged_worker_base_renders_and_prints_its_sha256(self):
        r = self.render("worker-base", var=WORKER_VARS)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.tmp / "out.md").read_text(encoding="utf-8")
        digest, path = r.stdout.splitlines()[0].split("  ", 1)
        self.assertEqual(digest, hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(path, str(self.tmp / "out.md"))
        self.assertNotIn("pai-prompts/", text)
        self.assertIn("R9", text)
        self.assertIn("worker/R9/docs", text)

    def test_missing_values_are_all_listed_and_nothing_is_written(self):
        r = self.render("worker-base", var=WORKER_VARS[:2])
        self.assertEqual(r.returncode, 1)
        for name in ("BASE_SHA", "WORKTREE", "BRANCH", "WORK_DIR", "VAULT"):
            self.assertIn(name, r.stderr)
        self.assertIn("--var VAULT=...", r.stderr)
        self.assertFalse((self.tmp / "out.md").exists())

    def test_regression_r6_prose_about_placeholders_is_not_a_placeholder(self):
        # R6 (2026-11-22): `grep -nE '\{\{[A-Z_]+\}\}'` over the filled prompt flagged the start-check
        # rule that *describes* the placeholder syntax. An escaped placeholder is prose: it renders
        # literally, needs no value, and a rendered-file lint does not call it unfilled.
        src = self.write("p.md", prompt_file("Round {{RUN_ID}}, lane {{LANE}}.\n"
                                             "1. If this prompt still contains a placeholder (written like \\{{NAME}}), stop."))
        r = self.render(str(src))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.tmp / "out.md").read_text(encoding="utf-8")
        self.assertIn("written like {{NAME}}), stop", text)
        self.assertIn("Round R9, lane docs.", text)
        lint = pai("prompt", "lint", "--rendered", "--kind", "other", str(self.tmp / "out.md"))
        self.assertNotIn("unfilled", lint.stdout)
        self.assertEqual(lint.returncode, 0, lint.stdout)
        # the packaged worker-base carries exactly such a rule
        self.assertIn("\\{{NAME}}", (Path(LIB) / "worker-base.md").read_text(encoding="utf-8"))

    def test_a_real_leftover_in_a_rendered_file_is_reported(self):
        out = self.write("filled.md", "Base {{BASE_SHA}} and round R9\n## 开工检查\n")
        r = pai("prompt", "lint", "--rendered", str(out))
        self.assertEqual(r.returncode, 1)
        self.assertIn("unfilled placeholder(s): BASE_SHA", r.stdout)

    def test_undeclared_placeholder_is_refused(self):
        src = self.write("p.md", prompt_file("{{RUN_ID}} {{LANE}} {{SECRET_PATH}}"))
        r = self.render(str(src), var=["SECRET_PATH=x"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("SECRET_PATH are not declared", r.stderr)

    def test_values_are_substituted_once(self):
        src = self.write("p.md", prompt_file("{{RUN_ID}} / {{LANE}}"))
        r = self.render(str(src), lane="{{RUN_ID}}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / "out.md").read_text(encoding="utf-8"), "R9 / {{RUN_ID}}\n")

    def test_empty_value_and_bad_var_are_refused(self):
        src = self.write("p.md", prompt_file("{{RUN_ID}} {{LANE}}"))
        self.assertIn("empty value for LANE", self.render(str(src), lane=" ").stderr)
        r = self.render(str(src), var=["lower=x"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a placeholder name", r.stderr)

    def test_existing_output_needs_force_unless_identical(self):
        src = self.write("p.md", prompt_file("{{RUN_ID}} {{LANE}}"))
        self.assertEqual(self.render(str(src)).returncode, 0)
        self.assertEqual(self.render(str(src)).returncode, 0)  # same content: fine
        r = self.render(str(src), lane="other")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--force", r.stderr)
        self.assertEqual(self.render(str(src), lane="other", extra=["--force"]).returncode, 0)

    def test_parts_are_joined_in_order_and_outside_text_is_kept(self):
        a = self.write("a.md", "Project note above.\n" + prompt_file("A {{RUN_ID}}", name="a") + "Local rule below.\n")
        b = self.write("b.md", prompt_file("B {{LANE}}", name="b"))
        r = self.render(str(a), str(b))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / "out.md").read_text(encoding="utf-8"),
                         "Project note above.\n\nA R9\n\nLocal rule below.\n\nB docs\n")

    def test_vars_file_and_var_precedence(self):
        src = self.write("p.md", prompt_file("{{RUN_ID}} {{LANE}} {{X}}", vars_="RUN_ID,LANE,X"))
        vf = self.write("v.json", json.dumps({"X": "from-file"}))
        self.assertEqual(self.render(str(src), extra=["--vars-file", str(vf)]).returncode, 0)
        self.assertTrue((self.tmp / "out.md").read_text(encoding="utf-8").endswith("from-file\n"))
        self.assertEqual(self.render(str(src), var=["X=cli"], extra=["--vars-file", str(vf), "--force"]).returncode, 0)
        self.assertTrue((self.tmp / "out.md").read_text(encoding="utf-8").endswith("cli\n"))

    def test_broken_markers_are_refused(self):
        src = self.write("p.md", "<!-- pai-prompts/1 begin name=p version=1 vars=RUN_ID -->\n{{RUN_ID}}\n")
        r = self.render(str(src))
        self.assertEqual(r.returncode, 1)
        self.assertIn("exactly one", r.stderr)


class SplitLanes(Base):
    """pai prompt render --split-lanes: the r6-prepare.sh behaviour (R6-PRM-077)."""
    R6 = ("# Round\n\nintro\n\n```text\nPart 0 for {{RUN_ID}} at {{BASE_SHA}}; stop if a placeholder like \\{{NAME}} is left.\n```\n\n"
          "```text\n线名：docs。线缩写：DOC。\nR-DOC-001 a\n```\n\n```text\n线名：core。线缩写：COR。\nR-COR-001 b\n```\n")

    def test_lanes_get_part0_plus_their_part(self):
        src = self.write("r6.md", self.R6)
        out = self.tmp / "split"
        r = pai("prompt", "render", str(src), "--round", "R9", "--var", "BASE_SHA=abc", "--split-lanes", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(out)), ["core.txt", "docs.txt"])
        docs = (out / "docs.txt").read_text(encoding="utf-8")
        self.assertEqual(docs, "Part 0 for R9 at abc; stop if a placeholder like {{NAME}} is left.\n\n线名：docs。线缩写：DOC。\nR-DOC-001 a\n")
        self.assertEqual(len(r.stdout.splitlines()), 2)
        self.assertIn(hashlib.sha256(docs.encode()).hexdigest(), r.stdout)

    def test_leftover_and_bad_lane_line_are_refused(self):
        src = self.write("r6.md", self.R6)
        r = pai("prompt", "render", str(src), "--round", "R9", "--split-lanes", str(self.tmp / "s"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no value for BASE_SHA", r.stderr)
        self.assertFalse((self.tmp / "s").exists())
        bad = self.write("bad.md", self.R6.replace("线名：core。", "lane: core"))
        r = pai("prompt", "render", str(bad), "--round", "R9", "--var", "BASE_SHA=a", "--split-lanes", str(self.tmp / "s"))
        self.assertIn("does not start with 线名", r.stderr)

    def test_single_block_is_one_file_and_lane_is_needed_otherwise(self):
        one = self.write("one.md", "```text\nonly {{RUN_ID}}\n```\n")
        r = pai("prompt", "render", str(one), "--round", "R9", "--split-lanes", str(self.tmp / "o"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / "o" / "all.txt").read_text(encoding="utf-8"), "only R9\n")
        r = pai("prompt", "render", "worker-base", "--round", "R9")
        self.assertIn("--lane is required", r.stderr)


class RenderText(unittest.TestCase):
    """The round lane's call (NOTES-FOR-prompts.md): render(text, values) -> (rendered, leftover)."""

    def test_plain_text_fills_known_and_reports_leftovers_without_raising(self):
        out, left = promptlib.render("Run {{RUN_ID}} base {{BASE_SHA}}; stop if a placeholder like \\{{NAME}} is left.",
                                     {"RUN_ID": "R9"})
        self.assertEqual(out, "Run R9 base {{BASE_SHA}}; stop if a placeholder like {{NAME}} is left.")
        self.assertEqual(left, ["BASE_SHA"])

    def test_library_text_drops_markers(self):
        out, left = promptlib.render(prompt_file("A {{RUN_ID}} {{LANE}}"), {"RUN_ID": "R9", "LANE": "x"})
        self.assertEqual((out, left), ("A R9 x\n", []))


class Lint(Base):
    def test_packaged_library_passes(self):
        r = pai("prompt", "lint")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("9 file(s), 0 error(s)", r.stdout)

    def findings(self, text, name="t", rendered=False):
        p = promptlib.Prompt(str(self.write(name + ".md", text)), text)
        return promptlib.lint(p, rendered=rendered)

    def codes(self, found):
        return {(lvl, code) for lvl, code, _ in found}

    def test_worker_kind_requires_its_five_parts(self):
        found = self.findings(prompt_file("==== 规则 ====\n- a {{RUN_ID}} {{LANE}}", kind="worker"))
        missing = " ".join(m for lvl, code, m in found if code == "section")
        for part in ("start-of-work check", "stop rule", "evidence labels", "handoff format"):
            self.assertIn(part, missing)
        self.assertNotIn("missing the rules part", missing)

    def test_english_headings_count(self):
        body = ("## Start-of-work check\n## Rules\n## Stop rule\n## Evidence labels\n## Handoff\n{{RUN_ID}} {{LANE}}")
        self.assertFalse([f for f in self.findings(prompt_file(body, kind="worker")) if f[1] == "section"])

    def test_forbidden_content(self):
        body = ("{{RUN_ID}} {{LANE}}\nsee https://claude.ai/code/session_01ABC\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
                "read /home/alice/notes.md")
        codes = {c for _, c, _ in self.findings(prompt_file(body))}
        self.assertTrue({"session-link", "ai-attribution", "private-path"} <= codes)
        # a rendered prompt is expected to contain host paths
        rendered = {c for _, c, _ in self.findings("read /home/alice/notes.md\n", rendered=True)}
        self.assertNotIn("private-path", rendered)

    def test_placeholder_checks(self):
        found = self.findings(prompt_file("{{RUN_ID}} {{OTHER}} {{lower}} \\{{LANE}}", vars_="RUN_ID,LANE,UNUSED"))
        msgs = " | ".join(m for _, _, m in found)
        self.assertIn("used but not declared in vars=: OTHER", msgs)
        self.assertIn("declared but never used: LANE, UNUSED", msgs)
        self.assertIn("'{{lower}}' looks like a placeholder", msgs)
        self.assertIn("escaped \\{{LANE}} names a declared variable", msgs)

    def test_header_and_length(self):
        found = self.findings(prompt_file("x" * 7000 + "\n{{RUN_ID}} {{LANE}}", name="wrong", kind="nope"), name="t")
        codes = self.codes(found)
        self.assertIn(("error", "header"), codes)
        self.assertIn(("warn", "length"), codes)
        big = self.findings(prompt_file("y\n" * 7000 + "{{RUN_ID}} {{LANE}}"), name="t")
        self.assertIn(("error", "length"), self.codes(big))

    def test_json_output_and_exit_code(self):
        bad = self.write("bad.md", prompt_file("no parts {{RUN_ID}} {{LANE}}", name="bad", kind="worker"))
        r = pai("prompt", "lint", str(bad), "--json")
        self.assertEqual(r.returncode, 1)
        data = json.loads(r.stdout)
        self.assertEqual(data["format"], "pai-prompt-lint/1")
        self.assertGreaterEqual(data["errors"], 4)

    def test_v2_files_must_state_the_owner_rules_and_ids_in_full(self):
        body = "==== 开工检查 ====\n==== 规则 ====\n==== 停止 ====\n==== 证据 ====\n==== 交接 ====\n- 先做 R6-PRM-001、002 {{RUN_ID}} {{LANE}}"
        v2 = prompt_file(body, kind="worker").replace("pai-prompts/1", "pai-prompts/2")
        msgs = " | ".join(m for _, _, m in self.findings(v2))
        for rule in ("D6 not-done wording", "D6 blockers end with a command", "D7 full item IDs"):
            self.assertIn(rule, msgs)
        self.assertIn("abbreviates item IDs (R6-PRM-001、002)", msgs)
        v1 = prompt_file(body, kind="worker")
        self.assertNotIn("D6 not-done", " ".join(m for _, _, m in self.findings(v1)), "v1 files keep the v1 rules")

    def test_jev_threshold_default_is_045(self):
        a = promptlib.add_parser.__globals__["argparse"].ArgumentParser()
        sub = a.add_subparsers(dest="cmd")
        promptlib.add_parser(sub)
        self.assertEqual(a.parse_args(["prompt", "lint", "--jev"]).jev_below, 0.45)

    def test_rules_are_split_with_continuations(self):
        p = promptlib.Prompt("t.md", prompt_file("## Rules\n- one\n  more of one\n- two\n\nplain\n1. three"))
        self.assertEqual([t for _, _, t in promptlib.rules(p)], ["one more of one", "two", "three"])
        self.assertEqual(promptlib.rules(p)[0][1], "Rules")


class Install(Base):
    def init(self, dest, *extra):
        return pai("prompt", "init", "--dest", str(dest), *extra)

    def test_v1_project_copy_upgrades_to_format_2(self):
        dest = self.tmp / "dest"
        old = self.tmp / "oldlib"
        old.mkdir()
        (old / "x.md").write_text(prompt_file("a {{RUN_ID}}", name="x", vars_="RUN_ID"), encoding="utf-8")
        self.assertEqual(self.init(dest, "--library", str(old), "--write").returncode, 0)
        new = self.tmp / "newlib"
        new.mkdir()
        (new / "x.md").write_text(prompt_file("a {{RUN_ID}}", name="x", vars_="RUN_ID").replace("pai-prompts/1", "pai-prompts/2"),
                                  encoding="utf-8")
        r = self.init(dest, "--library", str(new), "--write")
        self.assertIn("(format pai-prompts/1 -> /2)", r.stdout)
        self.assertIn("<!-- pai-prompts/2 begin name=x", (dest / "x.md").read_text(encoding="utf-8"))

    def test_preview_then_write_then_same(self):
        dest = self.tmp / "vault" / "PAI" / "prompts"
        r = self.init(dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("9 file(s) would change", r.stdout)
        self.assertFalse(dest.exists())
        self.assertEqual(self.init(dest, "--write").returncode, 0)
        files = sorted(os.listdir(dest))
        self.assertIn("worker-base.md", files)
        p = promptlib.read(str(dest / "worker-base.md"))
        self.assertEqual(p.attrs["sha256"], promptlib.sha256(p.body))
        self.assertIn("0 file(s) would change", self.init(dest).stdout)

    def lib_with(self, body, version=1):
        lib = self.tmp / "lib"
        lib.mkdir(exist_ok=True)
        (lib / "x.md").write_text(prompt_file(body, name="x", vars_="RUN_ID").replace("version=1", "version=%d" % version),
                                  encoding="utf-8")
        return lib

    def test_upgrade_replaces_only_the_managed_block(self):
        dest = self.tmp / "dest"
        lib = self.lib_with("old {{RUN_ID}}")
        self.assertEqual(self.init(dest, "--library", str(lib), "--write").returncode, 0)
        f = dest / "x.md"
        f.write_bytes(b"Project preface.\n" + f.read_bytes() + b"\nOur own rule: keep it.\n")
        lib = self.lib_with("new {{RUN_ID}}", version=2)
        r = self.init(dest, "--library", str(lib), "--write")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("v1 -> v2", r.stdout)
        text = f.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("Project preface.\n<!-- pai-prompts/1 begin name=x version=2"))
        self.assertIn("\nnew {{RUN_ID}}\n", text)
        self.assertNotIn("old", text)
        self.assertTrue(text.endswith("\nOur own rule: keep it.\n"))

    def test_hand_edit_inside_the_block_is_never_overwritten(self):
        dest = self.tmp / "dest"
        lib = self.lib_with("old {{RUN_ID}}")
        self.init(dest, "--library", str(lib), "--write")
        f = dest / "x.md"
        edited = f.read_text(encoding="utf-8").replace("old", "my edit")
        f.write_text(edited, encoding="utf-8")
        r = self.init(dest, "--library", str(self.lib_with("new {{RUN_ID}}", version=2)), "--write")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertEqual(f.read_text(encoding="utf-8"), edited)
        d = pai("prompt", "diff", "--dest", str(dest), "--library", str(lib))
        self.assertIn("HAND-EDITED", d.stdout)
        self.assertIn("-my edit {{RUN_ID}}", d.stdout)

    def test_broken_markers_in_the_project_copy_are_a_conflict(self):
        dest = self.tmp / "dest"
        lib = self.lib_with("v {{RUN_ID}}")
        self.init(dest, "--library", str(lib), "--write")
        f = dest / "x.md"
        f.write_text(f.read_text(encoding="utf-8") + "<!-- pai-prompts/1 end -->\n", encoding="utf-8")
        before = f.read_bytes()
        r = self.init(dest, "--library", str(self.lib_with("w {{RUN_ID}}", version=2)), "--write")
        self.assertIn("markers need fixing", r.stdout)
        self.assertEqual(f.read_bytes(), before)

    def test_crlf_project_file_stays_crlf(self):
        dest = self.tmp / "dest"
        self.init(dest, "--library", str(self.lib_with("a {{RUN_ID}}")), "--write")
        f = dest / "x.md"
        f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n") + b"mine\r\n")
        self.init(dest, "--library", str(self.lib_with("b {{RUN_ID}}", version=2)), "--write")
        data = f.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(b"b {{RUN_ID}}\r\n", data)
        self.assertTrue(data.endswith(b"mine\r\n"))

    def test_diff_lists_project_only_files_and_missing_ones(self):
        dest = self.tmp / "dest"
        dest.mkdir()
        (dest / "ours.md").write_text("# ours\n", encoding="utf-8")
        out = pai("prompt", "diff", "--dest", str(dest)).stdout
        self.assertIn("== ours: only in the project", out)
        self.assertIn("== worker-base: not installed", out)


class Templates(Base):
    """Obsidian templates (R6-PRM-043/044/048): core Templates plugin variables are lower case, PAI
    placeholders are capitals; neither pattern matches the other."""

    def test_variables_and_placeholders_never_collide(self):
        for var in ("{{date}}", "{{time}}", "{{title}}", "{{date:YYYY-MM-DD}}", "{{time:HH:mm}}"):
            self.assertFalse(promptlib.PLACEHOLDER_RE.search(var), var)
            self.assertTrue(promptlib.OBSIDIAN_VAR_RE.fullmatch(var), var)
        for ph in ("{{RUN_ID}}", "{{VAULT}}", "{{DATE}}", "{{TITLE}}"):
            self.assertFalse(promptlib.OBSIDIAN_VAR_RE.fullmatch(ph), ph)
            self.assertTrue(promptlib.PLACEHOLDER_RE.fullmatch(ph), ph)
        # rendering a PAI prompt leaves Obsidian variables alone, and vice versa nothing in a template is a PAI placeholder
        out, left = promptlib.render("{{date:YYYY-MM-DD}} {{title}} {{RUN_ID}}", {"RUN_ID": "R9"})
        self.assertEqual((out, left), ("{{date:YYYY-MM-DD}} {{title}} R9", []))
        for fn in os.listdir(promptlib.TEMPLATES):
            text = (Path(promptlib.TEMPLATES) / fn).read_text(encoding="utf-8")
            self.assertEqual(promptlib.PLACEHOLDER_RE.findall(text), [], fn)

    def test_packaged_templates_lint_clean_against_the_draft_schema(self):
        schema = self.write("schema.json", json.dumps({"format": "pai-frontmatter-schema/1", "types": {
            "pai-request": {"fields": {k: {} for k in ("type", "template", "status", "project", "priority", "budget", "created")}},
            "pai-decision": {"fields": {k: {} for k in ("type", "template", "status", "project", "round", "id", "default",
                                                         "blocks", "created")}},
            "pai-view": {"fields": {k: {} for k in ("type", "template", "project", "round", "created")}}}}))
        r = pai("prompt", "lint", "--templates", "--schema", str(schema))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("3 template(s), 0 error(s)", r.stdout)

    def test_template_lint_catches_problems(self):
        bad = self.write("bad.md", "---\ntype: pai-request\nstatus: weird\nextra: 1\n---\n{{RUN_ID}} {{author}} <% tp.date %>\n")
        schema = self.write("s.json", json.dumps({"format": "pai-frontmatter-schema/1", "types": {"pai-request": {"fields": {
            "type": {}, "status": {"values": ["inbox"]}, "project": {"required": True}}}}}))
        r = pai("prompt", "lint", "--templates", "--schema", str(schema), str(bad))
        self.assertEqual(r.returncode, 1)
        for msg in ("template: pai-<kind>", "PAI placeholders (RUN_ID)", "{{author}} is not a core Templates variable",
                    "Templater syntax", "'extra' is not in the schema", "status: 'weird' is not one of inbox",
                    "required field 'project' is missing"):
            self.assertIn(msg, r.stdout)

    def test_install_never_overwrites_the_owners_edit_or_file(self):
        dest = self.tmp / "vault" / "99 Templates" / "PAI"
        dest.mkdir(parents=True)
        (dest / "decision.md").write_text("owner's own decision template\n", encoding="utf-8")
        r = pai("prompt", "templates", "--dest", str(dest), "--write")
        self.assertEqual(r.returncode, 1)
        self.assertIn("PAI did not write it; left alone", r.stdout)
        self.assertEqual((dest / "decision.md").read_text(encoding="utf-8"), "owner's own decision template\n")
        self.assertTrue((dest / "request.md").is_file())
        man = json.loads((dest / ".pai-templates.json").read_text(encoding="utf-8"))
        self.assertEqual(man["files"]["request.md"]["template"], "pai-request/1")
        self.assertNotIn("decision.md", man["files"])
        (dest / "request.md").write_text((dest / "request.md").read_text(encoding="utf-8") + "my line\n", encoding="utf-8")
        r = pai("prompt", "templates", "--dest", str(dest), "--write")
        self.assertIn("changed since PAI wrote it", r.stdout)
        self.assertTrue((dest / "request.md").read_text(encoding="utf-8").endswith("my line\n"))


class PaiInitHook(Base):
    def make(self):
        repo, vault = self.tmp / "repo", self.tmp / "vault"
        (vault / "Agent Inbox" / "MEM-1").mkdir(parents=True)
        repo.mkdir()
        (self.tmp / "cfg").mkdir(exist_ok=True)
        (repo / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        (vault / "Agent Inbox" / "MEM-1" / "status.md").write_text("# S\n", encoding="utf-8")
        return repo, vault

    def args(self, repo, vault, *extra):
        return ["--config", str(self.tmp / "cfg" / "pai.toml"), "init", "demo", "--repo", str(repo), "--vault", str(vault),
                "--memory", "Agent Inbox/MEM-1", "--inbox", "Agent Inbox", *extra]

    def test_preview_lists_and_write_installs_into_the_vault(self):
        repo, vault = self.make()
        r = pai(*self.args(repo, vault))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# Prompt library (pai-prompts/2) -> %s" % (vault / "PAI/prompts"), r.stdout)
        self.assertFalse((vault / "PAI").exists())
        r = pai(*self.args(repo, vault, "--write"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((vault / "PAI" / "prompts" / "COORDINATOR.md").is_file())
        self.assertTrue((vault / "99 Templates" / "PAI" / "request.md").is_file())
        self.assertTrue((vault / "99 Templates" / "PAI" / ".pai-templates.json").is_file())
        self.assertLess(r.stdout.index("wrote 9 file(s)"), r.stdout.index("# Wrote "))

    def test_custom_folder_and_skip(self):
        repo, vault = self.make()
        r = pai(*self.args(repo, vault, "--prompts", "Lib/p", "--write"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((vault / "Lib" / "p" / "worker-base.md").is_file())
        os.remove(self.tmp / "cfg" / "pai.toml")
        r = pai(*self.args(repo, vault, "--prompts", "", "--write"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Prompt library", r.stdout)

    def test_existing_configuration_refusal_installs_nothing(self):
        repo, vault = self.make()
        self.assertEqual(pai(*self.args(repo, vault, "--prompts", "", "--write")).returncode, 0)
        r = pai(*self.args(repo, vault, "--write"))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((vault / "PAI").exists())


if __name__ == "__main__":
    unittest.main()
