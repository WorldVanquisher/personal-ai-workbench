"""Tests for init, doctor, the handoff template and the release tooling. All data is synthetic.

Run: python3 -B -m unittest discover -s tests -v   (from the repository root)
"""

import io
import json
import os
import ntpath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tokenize
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_bundle  # noqa: E402
import bundle_smoke  # noqa: E402
from pai import cli, config, doctor, handoff  # noqa: E402


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(list(args))
    return rc, out.getvalue(), err.getvalue()


def make_data(tmp):
    repo, vault = Path(tmp) / "repo dir", Path(tmp) / "vault dir"
    (repo / "docs").mkdir(parents=True)
    (vault / "Agent Inbox" / "MEM-1").mkdir(parents=True)
    (vault / "Projects").mkdir()
    (repo / "AGENTS.md").write_text("# Rules\n\n## Working Rules\n\n- synthetic\n")
    (vault / "Agent Inbox" / "MEM-1" / "status.md").write_text("# S\n\n## Human decisions in force\n\n- frozen\n")
    (vault / "Projects" / "Demo.md").write_text("# Demo\n")
    return repo, vault


def init_args(cfg, repo, vault, *extra):
    return ["--config", str(cfg), "init", "demo", "--repo", str(repo), "--vault", str(vault),
            "--memory", "Agent Inbox/MEM-1", "--inbox", "Agent Inbox", "--markdown-root", "Projects",
            "--project-note", "Projects/Demo.md", *extra]


class InitTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.repo, self.vault = make_data(self.tmp)
        # Windows forbids '"' in file names; elsewhere it exercises TOML string escaping.
        self.cfg = self.tmp / ("cfg 'quoted' dir" if os.name == "nt" else 'cfg "quoted" dir') / "pai.toml"
        self.cfg.parent.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def test_preview_writes_nothing_then_write_then_refuse(self):
        rc, out, _ = run_cli(*init_args(self.cfg, self.repo, self.vault))
        self.assertEqual(rc, 0)
        self.assertIn("DRY RUN", out)
        self.assertFalse(self.cfg.exists())
        rc, out, _ = run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))
        self.assertEqual(rc, 0)
        if os.name == "nt":  # POSIX modes are not Windows ACLs, and pai says so
            self.assertIn("does not apply on Windows", out)
        else:
            self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o600)
            self.assertIn("mode 0600", out)
        before = self.cfg.read_bytes()
        rc, _, err = run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))
        self.assertEqual(rc, 2)
        self.assertIn("never overwrites", err)
        self.assertEqual(self.cfg.read_bytes(), before)
        p = config.load("demo", str(self.cfg))  # paths with spaces round-trip through TOML
        self.assertEqual(p.repo, os.path.realpath(self.repo))

    def test_write_is_exclusive_even_after_the_existence_check(self):
        from unittest import mock
        from pai import initcfg
        self.cfg.write_text("existing = true\n")
        with mock.patch("pai.initcfg.os.path.lexists", return_value=False):  # simulate the race
            with self.assertRaisesRegex(config.ConfigError, "never overwrites"):
                initcfg.write(str(self.cfg), "new = true\n")
        self.assertEqual(self.cfg.read_text(), "existing = true\n")

    def test_actionable_errors(self):
        cases = [
            (init_args(self.cfg, "relative/repo", self.vault), "absolute"),
            (init_args(self.cfg, self.repo, self.tmp / "no-vault"), "vault does not exist"),
            (["--config", str(self.cfg), "init", "Bad Key!", "--repo", str(self.repo), "--vault", str(self.vault),
              "--memory", "m", "--inbox", "Agent Inbox"], "project key"),
            (["--config", str(self.cfg), "init", "demo", "--repo", str(self.repo), "--vault", str(self.vault),
              "--memory", "Agent Inbox/MEM-1", "--inbox", "No Inbox"], "create that folder"),
            (["--config", str(self.tmp / "missing-dir" / "x.toml"), *init_args(self.cfg, self.repo, self.vault, "--write")[2:]],
             "does not exist"),
        ]
        for args, msg in cases:
            rc, _, err = run_cli(*args)
            self.assertEqual(rc, 2, args)
            self.assertIn(msg, err, args)
        self.assertFalse(self.cfg.exists())

    def test_init_does_not_scan(self):
        # A file outside the given paths is never read: the preview lists only registered sources.
        (self.tmp / "secret-notes.md").write_text("PRIVATE")
        rc, out, _ = run_cli(*init_args(self.cfg, self.repo, self.vault))
        self.assertEqual(rc, 0)
        self.assertNotIn("PRIVATE", out)
        self.assertNotIn("secret-notes", out)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.repo, self.vault = make_data(self.tmp)
        self.cfg = self.tmp / "pai.toml"

    def tearDown(self):
        self._t.cleanup()

    def test_missing_config_is_a_problem(self):
        r = doctor.run(str(self.cfg))
        self.assertEqual(r.problems, 1)
        self.assertIn("pai init", "\n".join(r.lines))

    def test_ok_then_missing_root(self):
        run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))
        r = doctor.run(str(self.cfg), agents=True)
        text = "\n".join(r.lines)
        self.assertEqual(r.problems, 0, text)
        self.assertIn("development checkout", text)
        self.assertIn("not launched", text)
        shutil.rmtree(self.vault)
        r = doctor.run(str(self.cfg))
        self.assertEqual(r.problems, 1, r.lines)
        self.assertIn("vault not found", "\n".join(r.lines))


class TemplateTests(unittest.TestCase):
    def test_user_text_with_placeholders_is_literal(self):
        with tempfile.TemporaryDirectory() as t:
            repo, vault = make_data(t)
            cfg = Path(t) / "c.toml"
            run_cli(*init_args(cfg, repo, vault, "--write"))
            path = handoff.create(config.load("demo", str(cfg)), "TPL-1", summary="cost $summary ${created} $$",
                                  evidence=["${task_id}"])
            text = Path(path).read_text()
            self.assertIn("cost $summary ${created} $$", text)
            self.assertIn("- ${task_id}", text)
            self.assertIn("task: TPL-1", text)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def copy_source(self):
        src = self.tmp / "src"
        entries = build_bundle.read_allowlist(str(ROOT))
        for rel in entries + ["release/allowlist.txt"]:
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, src / rel)
        return src

    def test_portable_policy_allows_only_the_runtime_handoff_template(self):
        # Emulate Windows normcase on Linux; policy must not use host-normalizing fnmatch.
        with mock.patch("fnmatch.os.path.normcase", side_effect=ntpath.normcase):
            build_bundle.check_entries(str(ROOT), build_bundle.read_allowlist(str(ROOT)))
            for rel in ("HANDOFF.md", "Inbox/task/handoff.md", "pai/templates/HANDOFF.md",
                        "pai/other/handoff.md", "PAI/Templates/handoff.md", "PRIVATE-TOKEN.md",
                        "Credentials.json", "private.KEY", "PAI.LOCAL.TOML", "Agent Inbox/report.md"):
                with self.subTest(rel=rel), self.assertRaisesRegex(build_bundle.BuildError, "forbidden"):
                    build_bundle.check_entries(str(ROOT), [rel])

    def test_deterministic_and_allowlisted(self):
        r1, version, n = build_bundle.build(str(ROOT), str(self.tmp / "o1"), 0)
        r2, *_ = build_bundle.build(str(ROOT), str(self.tmp / "o2"), 0)
        self.assertEqual(r1["tar.gz"][1], r2["tar.gz"][1])
        self.assertEqual(r1["zip"][1], r2["zip"][1])
        allow = build_bundle.read_allowlist(str(ROOT))
        with tarfile.open(r1["tar.gz"][0]) as t:
            files = sorted(m.name.split("/", 1)[1] for m in t.getmembers() if m.isfile())
            tar_bytes = {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}
            manifest = json.loads(tar_bytes[f"pai-workbench-{version}/MANIFEST.json"])
            modes = {m.name.split("/", 1)[1]: m.mode for m in t.getmembers() if m.isfile()}
        with zipfile.ZipFile(r1["zip"][0]) as z:
            zip_bytes = {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}
            zip_modes = {i.filename.split("/", 1)[1]: (i.external_attr >> 16) & 0o777 for i in z.infolist() if not i.is_dir()}
            self.assertTrue(all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist()))  # epoch 0, clamped
        self.assertEqual(files, sorted(allow + ["MANIFEST.json", "SHA256SUMS"]))
        self.assertEqual(tar_bytes, zip_bytes)  # one allowlist, identical contents in both formats
        self.assertEqual(modes, zip_modes)
        self.assertEqual(modes["bin/pai"], 0o755)
        self.assertEqual(modes["bin/pai.cmd"], 0o644)
        self.assertEqual(manifest["version"], version)
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(manifest["runtime_dependencies"], [])
        for required in ("LICENSE", "bin/pai", "bin/pai.cmd", "bin/pai.py", "INSTALL.md", "SESSION_START.md",
                         "pai.example.toml", "pai/templates/handoff.md"):
            self.assertIn(required, files)
        for bad in ("pai.local.toml", "trial-evidence", ".git", "__pycache__", "tests/", "tools/", "dist/", ".pyc",
                    "HANDOFF", "context-"):
            self.assertFalse(any(bad in f for f in files), bad)
        for name in os.listdir(self.tmp / "o1"):
            path = self.tmp / "o1" / name
            if name.endswith(".sha256"):
                digest, archive = path.read_text(encoding="utf-8").split()
                self.assertEqual(digest, build_bundle.sha256_bytes((self.tmp / "o1" / archive).read_bytes()))
        with self.assertRaises(build_bundle.BuildError):  # never overwrites
            build_bundle.build(str(ROOT), str(self.tmp / "o1"), 0)

    def test_refuses_when_any_artifact_exists_and_writes_nothing(self):
        out = self.tmp / "partial"
        out.mkdir()
        version = build_bundle.read_version(str(ROOT))
        existing = out / f"pai-workbench-{version}.zip.sha256"
        existing.write_text("published\n")
        with self.assertRaisesRegex(build_bundle.BuildError, "never overwritten"):
            build_bundle.build(str(ROOT), str(out), 0)
        self.assertEqual(sorted(os.listdir(out)), [existing.name])
        self.assertEqual(existing.read_text(), "published\n")

    def test_line_endings_enforced(self):
        src = self.copy_source()
        cmd = src / "bin" / "pai.cmd"
        cmd.write_bytes(cmd.read_bytes().replace(b"\r\n", b"\n"))
        with self.assertRaisesRegex(build_bundle.BuildError, "CRLF"):
            build_bundle.build(str(src), str(self.tmp / "o"), 0)
        cmd.write_bytes((ROOT / "bin" / "pai.cmd").read_bytes())
        launcher = src / "bin" / "pai"
        launcher.write_bytes(launcher.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(build_bundle.BuildError, "LF line endings"):
            build_bundle.build(str(src), str(self.tmp / "o"), 0)

    def test_refusals(self):
        src = self.copy_source()
        build_bundle.build(str(src), str(self.tmp / "ok"), 0)  # the copy builds

        (src / "pai" / "new_module.py").write_text("x = 1\n")
        with self.assertRaisesRegex(build_bundle.BuildError, "not in the allowlist"):
            build_bundle.build(str(src), str(self.tmp / "o"), 0)
        (src / "pai" / "new_module.py").unlink()

        allow = src / "release" / "allowlist.txt"
        original = allow.read_text()
        (src / "pai.local.toml").write_text("x = 1\n")
        allow.write_text(original + "pai.local.toml\n")
        with self.assertRaisesRegex(build_bundle.BuildError, "forbidden"):
            build_bundle.build(str(src), str(self.tmp / "o"), 0)
        allow.write_text(original + "pai/*.py\n")
        with self.assertRaisesRegex(build_bundle.BuildError, "plain relative path"):
            build_bundle.build(str(src), str(self.tmp / "o"), 0)
        allow.write_text(original)

        readme = src / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(text + "\nsee /home/alice/notes\n", encoding="utf-8")
        with self.assertRaisesRegex(build_bundle.BuildError, "developer path"):
            build_bundle.build(str(src), str(self.tmp / "o"), 0)
        readme.unlink()
        try:
            os.symlink(self.tmp / "elsewhere.md", readme)
        except OSError as e:
            self.skipTest(f"cannot create symlinks here: {e}")
        (self.tmp / "elsewhere.md").write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(build_bundle.BuildError, "symlink"):
            build_bundle.build(str(src), str(self.tmp / "o"), 0)

    def test_bundle_smoke_end_to_end(self):
        archives, *_ = build_bundle.build(str(ROOT), str(self.tmp / "dist"), 0)
        # The downloaded-style bundle for this platform first; the other format is checked against it.
        order = ["zip", "tar.gz"] if os.name == "nt" else ["tar.gz", "zip"]
        p = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "bundle_smoke.py"),
                            *(archives[f][0] for f in order), "--work", str(self.tmp / "smoke work")],
                           env=dict(os.environ, PYTHONIOENCODING="cp1252:strict"),
                           capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr[-2000:])
        self.assertIn("6 concurrent handoffs → 6 distinct new files", p.stdout)
        self.assertIn("dossier é 日本", p.stdout)
        report = json.loads((self.tmp / "smoke work" / "smoke-results.json").read_text(encoding="utf-8"))
        self.assertEqual(report["fail"], 0)
        self.assertGreaterEqual(report["pass"], 40)
        self.assertTrue(any("handoffs →" in r["check"] for r in report["results"]))
        self.assertTrue(any("日本" in r["check"] for r in report["results"]))


class SmokeEncodingTests(unittest.TestCase):
    def test_legacy_streams_emit_utf8_for_checks_diagnostics_and_stderr(self):
        out_bytes, err_bytes = io.BytesIO(), io.BytesIO()
        with io.TextIOWrapper(out_bytes, encoding="cp1252", errors="strict") as out, \
                io.TextIOWrapper(err_bytes, encoding="cp1252", errors="strict") as err:
            with mock.patch.object(bundle_smoke.sys, "stdout", out), \
                    mock.patch.object(bundle_smoke.sys, "stderr", err), \
                    mock.patch.object(bundle_smoke, "RESULTS", []):
                bundle_smoke.configure_output()
                bundle_smoke.check("handoffs → 日本", True)
                bundle_smoke.check("failure → 日本", False, "détails → 日本")
                bundle_smoke.skip("skip → 日本", "raison → 日本")
                print("stderr → 日本", file=sys.stderr, flush=True)
                self.assertEqual(out_bytes.getvalue().decode("utf-8"),
                                 "PASS  handoffs → 日本\nFAIL  failure → 日本  — détails → 日本\n"
                                 "SKIP  skip → 日本  — raison → 日本\n")
                self.assertEqual(err_bytes.getvalue().decode("utf-8"), "stderr → 日本\n")

    def test_runner_decodes_piped_utf8_from_both_streams(self):
        text = "handoffs → é 日本\n"
        code = (f"import sys; data = {ascii(text)}.encode('utf-8'); "
                "sys.stdout.buffer.write(data); sys.stderr.buffer.write(data)")
        run = bundle_smoke.Runner(str(ROOT), dict(os.environ, PYTHONIOENCODING="cp1252:strict"),
                                  str(ROOT), command=[sys.executable, "-B", "-c", code])
        rc, out, err = run()
        self.assertEqual((rc, out, err), (0, text, text))


class AliasPathTests(unittest.TestCase):
    """Regression for macOS CI: /var is a symlink to /private/var, so a fixture path and the
    canonical path pai returns differ as strings. This reproduces that with a symlink alias."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        real = Path(self._t.name) / "real root"
        real.mkdir()
        self.alias = Path(self._t.name) / "alias root"
        try:
            os.symlink(real, self.alias, target_is_directory=True)
        except OSError as e:
            self._t.cleanup()
            self.skipTest(f"cannot create symlinks here: {e}")
        self.repo, self.vault = make_data(self.alias)  # every fixture path goes through the alias
        self.cfg = self.alias / "pai.toml"
        run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))
        self.inbox = str(self.vault / "Agent Inbox")

    def tearDown(self):
        self._t.cleanup()

    def concurrent(self, n=6):
        """Run n writers concurrently. Returns ({writer index: path}, errors).

        Each writer records its own result under its index, so writer identity never
        depends on thread completion order.
        """
        import threading
        project = config.load("demo", str(self.cfg))
        by_writer, errs, lock = {}, [], threading.Lock()

        def make(i):
            try:
                path = handoff.create(project, "ALIAS-1", summary=f"writer {i}")
                with lock:
                    by_writer[i] = path
            except Exception as e:  # pragma: no cover
                with lock:
                    errs.append(repr(e))

        ts = [threading.Thread(target=make, args=(i,)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return by_writer, errs

    def test_alias_paths_pass_resolved_containment(self):
        import bundle_smoke
        by_writer, errs = self.concurrent()
        paths = list(by_writer.values())
        # The alias case is really exercised: the old string-prefix check rejects these paths.
        self.assertTrue(paths and not any(p.startswith(self.inbox) for p in paths), paths)
        self.assertEqual(bundle_smoke.verify_concurrent_handoffs(paths, errs, self.inbox,
                                                                 [f"writer {i}" for i in range(6)]), [])

    def test_resolved_containment_still_rejects_real_problems(self):
        import bundle_smoke
        want = [f"writer {i}" for i in range(6)]
        by_writer, errs = self.concurrent()
        self.assertEqual((sorted(by_writer), errs), (list(range(6)), []))
        paths = [by_writer[i] for i in range(6)]  # paths[i] is writer i's file, whatever the completion order
        v = bundle_smoke.verify_concurrent_handoffs
        self.assertTrue(any("writer(s) failed" in x for x in v(paths, ["boom"], self.inbox, want)))
        self.assertTrue(any("successful writers" in x for x in v(paths[:5], [], self.inbox, want)))
        self.assertTrue(any("same file" in x for x in v(paths[:5] + paths[:1], [], self.inbox, want)))
        outside = Path(self._t.name) / "elsewhere" / "X-1" / "HANDOFF.md"
        outside.parent.mkdir(parents=True)
        outside.write_text(Path(paths[0]).read_text())
        self.assertTrue(any("directly under the inbox" in x for x in v(paths[1:] + [str(outside)], [], self.inbox, want)))
        nested = Path(self.inbox) / "deep" / "X-2" / "HANDOFF.md"
        nested.parent.mkdir(parents=True)
        nested.write_text(Path(paths[0]).read_text())
        self.assertTrue(any("directly under the inbox" in x for x in v(paths[1:] + [str(nested)], [], self.inbox, want)))
        self.assertIn("## Summary\n\nwriter 1\n", Path(by_writer[1]).read_text())
        Path(by_writer[1]).write_text(Path(by_writer[0]).read_text())  # writer 0's file over writer 1's
        problems = v(paths, [], self.inbox, want)
        self.assertTrue(any("'writer 0' found 2 times" in x for x in problems), problems)
        self.assertTrue(any("'writer 1' found 0 times" in x for x in problems), problems)

    def test_bundle_smoke_through_symlinked_work_dir(self):
        archives, *_ = build_bundle.build(str(ROOT), str(Path(self._t.name) / "dist"), 0)
        work = self.alias / "smoke work"
        p = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "bundle_smoke.py"),
                            archives["zip" if os.name == "nt" else "tar.gz"][0], "--work", str(work)],
                           env=dict(os.environ, PYTHONIOENCODING="cp1252:strict"),
                           capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr[-2000:])
        report = json.loads((work / "smoke-results.json").read_text(encoding="utf-8"))
        self.assertEqual(report["fail"], 0)


class CompatTests(unittest.TestCase):
    @unittest.skipIf(sys.version_info < (3, 12), "tokenizer check needs 3.12+; on 3.11 the import itself is the test")
    def test_fstrings_are_python311_compatible(self):
        """3.12+ accepts quote reuse and backslashes inside f-string expressions; 3.11 does not."""
        files = list((ROOT / "pai").glob("*.py")) + list((ROOT / "tools").glob("*.py"))
        for f in files:
            stack = []
            with open(f, "rb") as fh:
                for tok in tokenize.tokenize(fh.readline):
                    if tok.type == tokenize.FSTRING_START:
                        quote = tok.string.lstrip("rRfFbB")
                        if stack and stack[-1] == quote[0]:
                            self.fail(f"{f.name}:{tok.start[0]} nested f-string reuses the enclosing quote")
                        stack.append(quote[0])
                    elif tok.type == tokenize.FSTRING_END:
                        stack.pop()
                    elif stack and tok.type == tokenize.STRING:
                        q = tok.string.lstrip("rRbBuU")[0]
                        if q == stack[-1]:
                            self.fail(f"{f.name}:{tok.start[0]} string inside an f-string reuses the enclosing quote")
                    elif stack and tok.type == tokenize.OP and tok.string == "\\":
                        self.fail(f"{f.name}:{tok.start[0]} backslash inside an f-string expression")

    def test_runtime_has_no_process_or_network_imports(self):
        import ast
        banned = {"subprocess", "socket", "urllib", "http", "requests", "ssl", "asyncio"}
        for f in (ROOT / "pai").glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module else [])
                self.assertFalse(banned & set(names), f"{f.name}: {banned & set(names)}")


if __name__ == "__main__":
    unittest.main()
