"""pai/commands.py, the command registry (R6-COR-049): features add parsers and run themselves, and
pai/cli.py only registers them."""

import argparse
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pai import cli, commands  # noqa: E402


class Registry(unittest.TestCase):
    def setUp(self):
        self.saved = list(commands.FEATURES)
        self.addCleanup(commands.FEATURES.__setitem__, slice(None), self.saved)

    def test_cli_registers_the_features_in_order(self):
        names = [f.name for f in self.saved]  # core's four first; other lanes' features follow (R6-COR-081)
        self.assertEqual(names[:4], ["handoff-v2", "vault", "context-worktree", "secrets"])

    def test_add_dispatch_and_replace(self):
        commands.FEATURES[:] = []
        seen = []
        commands.use(commands.Feature("x", lambda sub, ps: sub.add_parser("x"),
                                      lambda a, lp: (seen.append(lp()), 7)[1] if a.cmd == "x" else None))
        commands.use(commands.Feature("x", lambda sub, ps: sub.add_parser("x"),
                                      lambda a, lp: 3 if a.cmd == "x" else None))  # replaces, not duplicates
        self.assertEqual(len(commands.FEATURES), 1)
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd")
        sub.add_parser("y")
        commands.add_all(sub, {})
        self.assertEqual(commands.dispatch(p.parse_args(["x"]), lambda: "project"), 3)
        self.assertIsNone(commands.dispatch(p.parse_args(["y"]), lambda: "project"))

    def test_the_usage_line_lists_every_command(self):  # R6-COR-080 (F5)
        p = cli._parser()
        usage = " ".join(p.format_usage().split())
        sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        listed = usage[usage.index("{") + 1:usage.index("}")].split(",")
        self.assertEqual(listed, list(sub.choices), "generated from argparse, not written by hand")
        registered = [c for f in commands.FEATURES for c in commands.ADDED.get(f.name, [])]
        self.assertTrue({"vault", "secrets"} <= set(registered), registered)
        for name in registered + ["init", "doctor", "install", "check", "context", "handoff", "queue"]:
            self.assertIn(name, listed)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
            p.parse_args(["no-such-command"])
        self.assertIn("vault", err.getvalue(), "the error lists the registered commands too")

    def test_hooks_for_init(self):  # R6-COR-081: the shape the prompt library uses
        commands.FEATURES[:] = []
        seen = []
        feat = commands.Feature(
            "lib", add=lambda sub, ps: (sub.add_parser("lib"), ps["init"].add_argument("--lib-dir", default="L")),
            run=lambda a, lp: 0 if a.cmd == "lib" else None,
            hooks={"init before-write": lambda a: seen.append(("before", a.lib_dir)),
                   "init after-write": lambda a: seen.append(("after", a.lib_dir))})
        commands.use(feat)
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd")
        init = sub.add_parser("init")
        commands.add_all(sub, {"init": init})
        self.assertEqual(commands.ADDED["lib"], ["lib"])
        a = p.parse_args(["init", "--lib-dir", "X"])
        commands.hook("init before-write", a)
        commands.hook("init after-write", a)
        self.assertEqual(seen, [("before", "X"), ("after", "X")])
        self.assertEqual(commands.dispatch(p.parse_args(["lib"]), lambda: None), 0)
        with self.assertRaises(ValueError):
            commands.hook("init during", a)
        with self.assertRaises(ValueError):
            commands.Feature("bad", None, None, hooks={"befor-write": print})

    def test_pai_init_calls_the_hooks(self):  # R6-COR-081
        text = (Path(cli.__file__)).read_text(encoding="utf-8")
        init = text[text.index("def _init("):text.index("def main(")]
        self.assertLess(init.index('commands.hook("init before-write", a)'), init.index("if not a.write:"))
        self.assertLess(init.index("initcfg.write(path, text)"), init.index('commands.hook("init after-write", a)'))

    def test_cli_py_holds_registrations_not_command_code(self):
        text = (Path(cli.__file__)).read_text(encoding="utf-8")
        self.assertEqual(text.count("commands.use("), len(self.saved), "one line per registered feature")
        for moved in ('add_parser("seal"', 'add_parser("vault"', "def _seal", "def _vault"):
            self.assertNotIn(moved, text)



class FreshHomeWording(unittest.TestCase):
    """R6-COR-090: what a new user read in a fresh HOME and could not act on."""

    def test_secrets_where_names_nothing_an_installed_pai_lacks(self):
        for row in commands.secrets_where(env={}, home="/nonexistent"):
            self.assertNotIn("tools/", row["where"], "tools/ is not packaged")
        where = {r["name"]: r["where"] for r in commands.secrets_where(env={}, home="/nonexistent")}
        self.assertIn("anthropic-workspace-id: <workspace id>", where["ANTHROPIC_CUSTOM_HEADERS"])
        self.assertIn("secret-tool", where["TYPESAFE_API_KEY"])

    def test_the_init_example_does_not_trigger_its_own_layout_warning(self):
        import re
        memory = re.search(r'--memory "([^"]+)"', cli.INIT_EPILOG).group(1)
        inbox = re.search(r'--inbox "([^"]+)"', cli.INIT_EPILOG).group(1)
        self.assertFalse(memory.startswith(inbox + "/"), (memory, inbox))

if __name__ == "__main__":
    unittest.main()


class ContextWorktree(unittest.TestCase):
    """`pai context --worktree PATH` (R6-COR-061): the lane's L2 comes from that worktree's CLAUDE.local.md
    (and its own AGENTS.md), never from the main checkout's."""

    def setUp(self):
        import tempfile
        from test_pai import Fixture
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)
        (self.f.repo / "AGENTS.md").write_text("# Rules\n\n## Main\n\nMAIN CHECKOUT RULE\n", encoding="utf-8")
        gd = self.f.repo / ".git" / "worktrees" / "a"
        gd.mkdir(parents=True)
        self.wt = Path(self.tmp.name) / "lane a"
        self.wt.mkdir()
        (self.wt / ".git").write_text("gitdir: %s\n" % gd, encoding="utf-8")
        (self.wt / "AGENTS.md").write_text("# Rules\n\n## Lane\n\nWORKTREE AGENTS RULE\n", encoding="utf-8")
        (self.wt / "CLAUDE.local.md").write_text(
            "<!-- pai:managed:begin pai-agents/1 -->\n## This worktree (pai)\n\nL2 LANE RULE\n<!-- pai:managed:end -->\n",
            encoding="utf-8")

    def run_ctx(self, *extra):
        from test_pai import run_cli
        return run_cli("--config", str(self.f.cfg), "context", "demo", "--task", "release", *extra)

    def test_l2_from_the_worktree(self):
        rc, out, err = self.run_ctx("--worktree", str(self.wt))
        self.assertEqual(rc, 0, err)
        import hashlib
        self.assertIn("L2 LANE RULE", out, "the L2 section is required context")
        wt_sha = hashlib.sha256((self.wt / "AGENTS.md").read_bytes()).hexdigest()[:12]
        self.assertIn("| instructions | `AGENTS.md` | ok | ", out)
        self.assertIn(wt_sha, out, "AGENTS.md is the worktree's copy")
        self.assertNotIn("MAIN CHECKOUT RULE", out)
        self.assertLess(out.index("`CLAUDE.local.md`"), out.index("`AGENTS.md`"), "L2 first")
        rc, out, _ = self.run_ctx()
        main_sha = hashlib.sha256((self.f.repo / "AGENTS.md").read_bytes()).hexdigest()[:12]
        self.assertIn(main_sha, out, "without --worktree the main checkout's AGENTS.md is read, as before")
        self.assertNotIn("CLAUDE.local.md", out)

    def test_not_a_worktree_of_the_project(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        rc, _, err = self.run_ctx("--worktree", str(other))
        self.assertEqual(rc, 2)
        self.assertIn("is not a linked worktree", err)


class ClaudeMdSymlink(unittest.TestCase):
    """R6-COR-064/065: doctor warns about CLAUDE.md as a symbolic link with core.symlinks=false, or already
    checked out as a text file holding a path, and suggests the @AGENTS.md import."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / ".git").mkdir()
        (self.repo / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")

    def config(self, symlinks):
        (self.repo / ".git" / "config").write_text("[core]\n\tbare = false\n\tsymlinks = %s\n" % symlinks, encoding="utf-8")

    def findings(self):
        from pai import handoff
        return handoff.claude_md_findings(str(self.repo))

    def test_symlink_with_symlinks_false(self):
        self.config("false")
        try:
            (self.repo / "CLAUDE.md").symlink_to("AGENTS.md")
        except OSError:
            self.skipTest("cannot create a symbolic link here")
        f = self.findings()
        self.assertEqual(len(f), 1)
        self.assertIn("core.symlinks=false", f[0])
        self.assertIn("`@AGENTS.md`", f[0])

    def test_link_checked_out_as_text(self):
        self.config("false")
        (self.repo / "CLAUDE.md").write_text("AGENTS.md", encoding="utf-8")
        self.assertIn("only holds the path 'AGENTS.md'", self.findings()[0])

    def test_import_line_and_real_files_are_fine(self):
        (self.repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        self.assertEqual(self.findings(), [])
        (self.repo / "CLAUDE.md").write_text("# Claude\n\nUse small commits.\n", encoding="utf-8")
        self.assertEqual(self.findings(), [])

    def test_doctor_reports_it(self):
        import tempfile
        from test_pai import Fixture, run_cli
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d)
            (fx.repo / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
            (fx.repo / "CLAUDE.md").write_text("AGENTS.md", encoding="utf-8")
            rc, out, _ = run_cli("--config", str(fx.cfg), "doctor", "demo")
            self.assertIn("CLAUDE.md only holds the path 'AGENTS.md'", out)


class SecretsWhere(unittest.TestCase):
    """R6-COR-069: where each key belongs and whether it is set; never its value."""

    def test_presence_and_modes_without_values(self):
        import json
        import os
        import tempfile
        from unittest import mock
        from test_pai import run_cli
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".config", "pai"))
            path = os.path.join(home, ".config", "pai", "jev.env")
            with open(path, "w") as f:
                f.write("TYPESAFE_API_KEY=secret-value-123\n")
            os.chmod(path, 0o644)
            rows = commands.secrets_where(env={"TYPESAFE_API_KEY": "secret-value-123"}, home=home)
            jev = rows[0]
            self.assertEqual((jev["name"], jev["env"], jev["file_mode"]), ("TYPESAFE_API_KEY", True, "644"))
            self.assertIn("chmod 600", jev["problem"])
            self.assertNotIn("secret-value-123", json.dumps(rows))
            os.chmod(path, 0o600)
            self.assertIsNone(commands.secrets_where(env={}, home=home)[0]["problem"])
            with mock.patch.dict(os.environ, {"HOME": home, "TYPESAFE_API_KEY": "secret-value-123"}):
                rc, out, _ = run_cli("secrets", "where")
            self.assertEqual(rc, 0, out)
            self.assertIn("TYPESAFE_API_KEY          set in the environment", out)
            self.assertNotIn("secret-value-123", out)
