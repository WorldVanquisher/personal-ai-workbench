"""PAI-005: platform-sensitive behaviour. All data is synthetic and temporary.

Windows-only cases (drive paths, junctions, cmd.exe and PowerShell) run on Windows CI and are
skipped elsewhere; the skip is reported by unittest, never counted as a pass.
"""

import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import bundle_smoke  # noqa: E402
from pai import config, context, handoff, initcfg  # noqa: E402
from test_pai import run_cli, symlink_or_skip  # noqa: E402

WINDOWS = os.name == "nt"
LAUNCHER = ROOT / "bin" / ("pai.cmd" if WINDOWS else "pai")
UNICODE_DIR = "dossier é 日本 with spaces"


def make_tree(base):
    """A synthetic repo and vault, with CRLF notes as a Windows editor would save them."""
    repo, vault = base / "repo dir", base / "vault dir"
    (repo / "docs").mkdir(parents=True)
    (vault / "Agent Inbox" / "MEM-1").mkdir(parents=True)
    (vault / "Projects").mkdir()
    crlf = {"encoding": "utf-8", "newline": "\r\n"}
    (repo / "AGENTS.md").write_text("# Règles\n\n## Working Rules\n\n- Synthétique : jamais git.\n", **crlf)
    (vault / "Agent Inbox" / "MEM-1" / "status.md").write_text(
        "---\nstatus: draft\napproved: false\ncreated: 2030-01-01T00:00Z\n---\n# État\n\n"
        "## Human decisions in force\n\n- [HUMAN] Déploiement gelé (freeze) — 日本.\n", **crlf)
    (vault / "Projects" / "Démo.md").write_text("# Démo\n\n## Goal\n\nSynthetic.\n", encoding="utf-8")
    return repo, vault


def init_args(cfg, repo, vault, *extra):
    return ["--config", str(cfg), "init", "demo", "--repo", str(repo), "--vault", str(vault),
            "--memory", "Agent Inbox/MEM-1", "--inbox", "Agent Inbox", "--markdown-root", "Projects",
            "--project-note", "Projects/Démo.md", "--instruction-always", "working rules", *extra]


class ContainmentTests(unittest.TestCase):
    def test_components_not_string_prefixes(self):
        root = os.path.abspath(os.path.join(os.sep, "vault"))
        self.assertTrue(config.inside(root, root))
        self.assertTrue(config.inside(os.path.join(root, "Inbox", "x.md"), root))
        self.assertFalse(config.inside(root + "2", root))           # a string prefix, not a child
        self.assertFalse(config.inside(root + " backup", root))
        self.assertFalse(config.inside(os.path.dirname(root), root))

    def test_relative_paths_refused_in_every_form(self):
        for bad in ("/abs", "\\rooted", "a/../b", "..\\up", "a\\..\\..\\b", "", None):
            with self.assertRaises(config.ConfigError, msg=repr(bad)):
                config._rel(bad, "entry")
        self.assertEqual(config._rel("Agent Inbox/é 日本.md", "entry"), "Agent Inbox/é 日本.md")

    @unittest.skipUnless(WINDOWS, "Windows path semantics")
    def test_windows_drive_case_and_streams(self):
        self.assertTrue(config.inside(r"C:\Vault\Inbox", r"c:\vault"))
        self.assertFalse(config.inside(r"D:\Vault\Inbox", r"C:\Vault"))
        self.assertFalse(config.inside(r"C:\Vault2", r"C:\Vault"))
        self.assertTrue(config.inside(r"\\server\share\vault\x", r"\\server\share\vault"))
        self.assertFalse(config.is_absolute(r"\vault"))       # rooted, but relative to the current drive
        self.assertFalse(config.is_absolute(r"C:vault"))      # drive-relative
        self.assertTrue(config.is_absolute(r"C:\vault"))
        self.assertTrue(config.is_absolute(r"\\server\share\vault"))
        for bad in (r"C:notes", r"C:\notes", "status.md:hidden", "Inbox/a.md:$DATA"):
            with self.assertRaises(config.ConfigError, msg=bad):
                config._rel(bad, "entry")

    def test_permission_note_is_platform_honest(self):
        with mock.patch.object(initcfg.os, "name", "nt"):
            self.assertIn("does not apply on Windows", initcfg.permission_note())
            self.assertIn("ACL", initcfg.permission_note())
        with mock.patch.object(initcfg.os, "name", "posix"):
            self.assertIn("mode 0600", initcfg.permission_note())


class UnicodeWorkflowTests(unittest.TestCase):
    """init → doctor → context → handoff new → handoff list with spaces, Unicode and CRLF."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.base = Path(self._t.name) / UNICODE_DIR
        self.base.mkdir()
        self.repo, self.vault = make_tree(self.base)
        self.cfg = self.base / "config é" / "pai.toml"
        self.cfg.parent.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def test_workflow(self):
        rc, out, err = run_cli(*init_args(self.cfg, self.repo, self.vault))
        self.assertEqual((rc, err), (0, ""))
        self.assertFalse(self.cfg.exists())
        rc, out, err = run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))
        self.assertEqual((rc, err), (0, ""))
        self.assertNotIn(b"\r", self.cfg.read_bytes())  # LF on every platform
        self.assertEqual(config.load("demo", str(self.cfg)).vault, os.path.realpath(self.vault))
        rc, out, err = run_cli("--config", str(self.cfg), "doctor", "demo")
        self.assertEqual(rc, 0, out)
        rc, out, err = run_cli("--config", str(self.cfg), "context", "demo", "--task", "déploiement 日本")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("approved: false", out)                          # CRLF front matter recognized
        self.assertIn("[HUMAN] Déploiement gelé (freeze) — 日本.", out)  # CRLF heading recognized
        self.assertIn("Synthétique : jamais git.", out)
        self.assertNotIn("\r", out)
        rc, path, err = run_cli("--config", str(self.cfg), "handoff", "new", "demo", "--task-id", "UNI-1",
                                "--summary", "résumé 日本 \"quoted\" $x %y%", "--evidence", "é")
        self.assertEqual((rc, err), (0, ""))
        text = Path(path.strip()).read_bytes().decode("utf-8")
        self.assertIn("résumé 日本 \"quoted\" $x %y%", text)
        self.assertNotIn("\r", text)
        rc, out, _ = run_cli("--config", str(self.cfg), "handoff", "list", "demo")
        self.assertIn("task=UNI-1", out)
        self.assertIn("approved=false", out)

    def test_source_escape_and_secret_exclusion_prevent_reads(self):
        outside = self.base / "outside"
        outside.mkdir()
        for name in ("Note.md", "Secret.md"):
            (outside / name).write_text("# N\n\n## Goal\n\nOUTSIDE-CONTENT\n", encoding="utf-8")
        symlink_or_skip(self, outside, self.vault / "Projects" / "linked", directory=True)
        project = config.from_dict("demo", {
            "repo": str(self.repo), "vault": str(self.vault), "memory": "Agent Inbox/MEM-1",
            "inbox": "Agent Inbox", "markdown_roots": ["Projects"],
            "project_notes": ["Projects/linked/Note.md", "Projects/linked/Secret.md"],
        })
        sources = [s for s in project.sources if s.kind == "vault-note"]
        self.assertEqual([s.status for s in sources], ["refused", "excluded"])
        self.assertIn("escapes its root", sources[0].reason)
        with mock.patch("pai.context.open", wraps=open) as reads:
            out = context.build(project, "goal")
        self.assertNotIn("OUTSIDE-CONTENT", out)
        self.assertFalse(any(Path(call.args[0]).resolve().is_relative_to(outside.resolve())
                             for call in reads.call_args_list))

    def test_source_labels_use_portable_separators(self):
        run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))
        project = config.load("demo", str(self.cfg))
        self.assertTrue(all("\\" not in s.label for s in project.sources))
        self.assertIn("Agent Inbox/MEM-1/status.md", [s.label for s in project.sources])

    def test_concurrent_handoffs_in_unicode_inbox(self):
        run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))
        project = config.load("demo", str(self.cfg))
        paths, errors, lock = [], [], threading.Lock()

        def make(i):
            try:
                p = handoff.create(project, "SAME-1", summary=f"écrivain {i}")
                with lock:
                    paths.append(p)
            except Exception as e:  # pragma: no cover
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=make, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(paths)), 16)
        inbox = Path(project.inbox).resolve()
        for p in paths:
            self.assertEqual(Path(p).resolve().parent.parent, inbox)
        self.assertEqual(sorted(Path(p).read_text(encoding="utf-8").split("## Summary\n\n")[1].split("\n")[0]
                                for p in paths), sorted(f"écrivain {i}" for i in range(16)))


@unittest.skipUnless(WINDOWS, "junctions are a Windows reparse point")
class JunctionTests(unittest.TestCase):
    def setUp(self):
        import _winapi
        self.junction = _winapi.CreateJunction
        self._t = tempfile.TemporaryDirectory()
        self.base = Path(self._t.name)
        self.repo, self.vault = make_tree(self.base)
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "Secret.md").write_text("# S\n\n## Goal\n\nOUTSIDE-CONTENT\n", encoding="utf-8")
        self.cfg = self.base / "pai.toml"
        run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))

    def tearDown(self):
        self._t.cleanup()

    def test_junction_source_is_refused(self):
        # Secret.md exercises the credential denylist, not junction containment.
        (self.outside / "Note.md").write_text("# N\n\n## Goal\n\nOUTSIDE-CONTENT\n", encoding="utf-8")
        self.junction(str(self.outside), str(self.vault / "Projects" / "linked"))
        p = config.from_dict("demo", {"repo": str(self.repo), "vault": str(self.vault), "memory": "Agent Inbox/MEM-1",
                                      "inbox": "Agent Inbox", "markdown_roots": ["Projects"],
                                      "project_notes": ["Projects/linked/Note.md", "Projects/linked/Secret.md"]})
        src = [s for s in p.sources if s.kind == "vault-note"][0]
        self.assertEqual(src.status, "refused")
        self.assertIn("escapes its root", src.reason)
        self.assertEqual([s.status for s in p.sources if s.kind == "vault-note"], ["refused", "excluded"])
        self.assertNotIn("OUTSIDE-CONTENT", context.build(p, "goal"))

    def test_junction_in_inbox_is_refused_by_discovery(self):
        project = config.load("demo", str(self.cfg))
        handoff.create(project, "OK-1", summary="INSIDE")
        evil = self.outside / "EVIL-1"
        evil.mkdir()
        good = next(Path(project.inbox).glob("OK-1-*")) / "HANDOFF.md"
        (evil / "HANDOFF.md").write_text(good.read_text(encoding="utf-8").replace("INSIDE", "ESCAPED"), encoding="utf-8")
        self.junction(str(evil), str(Path(project.inbox) / "EVIL-1"))
        reports, issues = handoff.discover(project)
        self.assertEqual([r["meta"]["task"] for r in reports], ["OK-1"])
        self.assertTrue(any("EVIL-1" in x and "REFUSED" in x for x in issues), issues)

    def test_junction_as_inbox_is_refused(self):
        moved = self.vault / "Agent Inbox"
        shutil.move(str(moved), str(self.outside / "inbox"))
        self.junction(str(self.outside / "inbox"), str(moved))
        with self.assertRaisesRegex(config.ConfigError, "inside the vault"):
            config.load("demo", str(self.cfg))


def clean_env(**extra):
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "SYSTEMDRIVE", "HOME", "USERPROFILE")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(extra)
    return env


class LauncherTests(unittest.TestCase):
    """The source-tree launchers, from an unrelated directory holding a decoy pai/ package."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.base = Path(self._t.name) / UNICODE_DIR
        self.cwd = self.base / "unrelated cwd"
        (self.cwd / "pai").mkdir(parents=True)
        for name in ("__init__.py", "__main__.py"):
            (self.cwd / "pai" / name).write_text("raise SystemExit('DECOY')\n")
        self.repo, self.vault = make_tree(self.base)
        self.cfg = self.base / "pai.toml"
        run_cli(*init_args(self.cfg, self.repo, self.vault, "--write"))
        self.env = clean_env(PAI_PYTHON=sys.executable, PAI_CONFIG=str(self.cfg))

    def tearDown(self):
        self._t.cleanup()

    def run_pai(self, *args, command=None, env=None, raw=None):
        """Run the launcher (or command); raw is a verbatim Windows command line for cmd.exe."""
        p = subprocess.run(raw or [*(command or [str(LAUNCHER)]), *args], cwd=self.cwd, env=env or self.env,
                           capture_output=True, timeout=120)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    def shell_env(self):
        """cmd.exe and PowerShell need their usual environment to start."""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAI_")}
        return dict(env, PAI_PYTHON=sys.executable, PAI_CONFIG=str(self.cfg))

    def test_arguments_and_exit_codes_pass_through(self):
        task = "spaces  two 'single' ü 日本 é ; ! ^ & | ( )"
        if not WINDOWS:
            task += ' "double" $HOME * %PATH% \\back'
        rc, out, err = self.run_pai("context", "demo", "--task", task, "--budget", "4000")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("DECOY", out + err)
        self.assertIn(f"- **Task:** {task}\n", out.replace("\r\n", "\n"))
        rc, out, err = self.run_pai("context", "unknown-project", "--task", "x")
        self.assertEqual(rc, 2)
        self.assertIn("not registered", err)

    def test_script_entry_point(self):
        rc, out, err = self.run_pai("--version", command=[sys.executable, str(ROOT / "bin" / "pai.py")])
        self.assertEqual((rc, out.strip()), (0, f"pai {__import__('pai').__version__}"), err)

    def test_missing_interpreter_is_actionable(self):
        env = dict(self.env, PAI_PYTHON=str(self.base / ("absent python" + (".exe" if WINDOWS else ""))))
        rc, out, err = self.run_pai("--version", env=env)
        self.assertNotEqual(rc, 0)
        self.assertIn("PAI_PYTHON", err)
        self.assertIn("3.11", err)

    @unittest.skipIf(WINDOWS, "POSIX launcher")
    def test_posix_symlinked_launcher(self):
        link = self.base / "pai-link"
        os.symlink(LAUNCHER, link)
        rc, out, err = self.run_pai("--version", command=[str(link)])
        self.assertEqual(rc, 0, err)

    @unittest.skipUnless(WINDOWS, "cmd.exe")
    def test_cmd_exe(self):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        line = f'"{comspec}" /d /s /c ""{LAUNCHER}" context demo --task "cmd ü 日本 & more (x)" --budget 4000"'
        rc, out, err = self.run_pai(raw=line, env=self.shell_env())
        self.assertEqual(rc, 0, err)
        self.assertIn("- **Task:** cmd ü 日本 & more (x)", out)
        rc, _, err = self.run_pai(raw=f'"{comspec}" /d /s /c ""{LAUNCHER}" context nope --task x"', env=self.shell_env())
        self.assertEqual(rc, 2, err)

    @unittest.skipUnless(WINDOWS, "PowerShell")
    def test_powershell(self):
        shells = [s for s in ("pwsh", "powershell") if shutil.which(s)]
        if not shells:
            self.skipTest("no PowerShell found")
        for shell in shells:
            script = (f"& '{LAUNCHER}' handoff new demo --task-id PS-1 --summary 'PowerShell ü 日本 a b'; "
                      f"if ($LASTEXITCODE -ne 0) {{ exit 10 }}; "
                      f"& '{LAUNCHER}' context nope --task x 2>$null; exit $LASTEXITCODE")
            rc, out, err = self.run_pai("-NoProfile", "-NonInteractive", "-Command", script,
                                        command=[shutil.which(shell)], env=self.shell_env())
            self.assertEqual(rc, 2, f"{shell}: {out} {err}")  # the second call's exit code reaches the caller
        texts = [p.read_text(encoding="utf-8") for p in Path(config.load("demo", str(self.cfg)).inbox).glob("PS-1-*/HANDOFF.md")]
        self.assertEqual(len(texts), len(shells))
        self.assertTrue(all("PowerShell ü 日本 a b" in t for t in texts))

    def test_old_python_guard(self):
        code = ("import runpy, sys; sys.version_info = (3, 10, 4, 'final', 0); "
                f"sys.argv = [{str(ROOT / 'bin' / 'pai.py')!r}, '--version']; "
                f"runpy.run_path({str(ROOT / 'bin' / 'pai.py')!r}, run_name='__main__')")
        rc, out, err = self.run_pai("-c", code, command=[sys.executable])
        self.assertEqual(rc, 2)
        self.assertIn("3.11 or newer is required; this is 3.10", err)
        self.assertIn("PAI_PYTHON", err)


class ExtractionTests(unittest.TestCase):
    """The test tooling's extractor refuses archives that could write outside its destination."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def zip_with(self, name, attr=None, data=b"x"):
        path = self.tmp / f"z{len(os.listdir(self.tmp))}.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("top/ok.txt", b"ok")
            info = zipfile.ZipInfo(name)
            # ZipInfo's constructor rewrites backslashes on Windows. Force the raw
            # member name into both ZIP headers, then verify the archive we created.
            info.filename = name
            if attr is not None:
                info.create_system, info.external_attr = 3, attr << 16
            z.writestr(info, data)
        with zipfile.ZipFile(path) as z:
            self.assertEqual(z.infolist()[1].orig_filename, name)
        return str(path)

    def tar_with(self, member):
        path = self.tmp / f"t{len(os.listdir(self.tmp))}.tar.gz"
        with tarfile.open(path, "w:gz") as t:
            t.addfile(member, io.BytesIO(b"x") if member.isfile() else None)
        return str(path)

    def test_hostile_members_refused(self):
        archives = [self.zip_with(n) for n in ("top/../../evil.txt", "/abs.txt", "C:/drive.txt", "top\\back.txt",
                                               "top//double.txt")]
        archives.append(self.zip_with("top/link", stat.S_IFLNK | 0o777, b"../../etc"))
        for name in ("../evil.txt", "/abs.txt"):
            ti = tarfile.TarInfo(name)
            ti.size = 1
            archives.append(self.tar_with(ti))
        link = tarfile.TarInfo("top/link")
        link.type, link.linkname = tarfile.SYMTYPE, "../../outside"
        archives.append(self.tar_with(link))
        for i, archive in enumerate(archives):
            with self.assertRaises(RuntimeError, msg=archive):
                bundle_smoke.safe_extract(archive, str(self.tmp / f"dest{i}"))
        self.assertFalse((self.tmp.parent / "evil.txt").exists())

    def test_corrupt_zip_member_refused(self):
        path = self.tmp / "crc.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as z:
            z.writestr("top/a.txt", b"A" * 64)
        data = bytearray(path.read_bytes())
        data[data.index(b"A" * 64) + 10] ^= 0x01
        path.write_bytes(bytes(data))
        with self.assertRaisesRegex(RuntimeError, "CRC"):
            bundle_smoke.safe_extract(str(path), str(self.tmp / "dest"))

    def test_raw_backslash_member_refused_after_windows_normalization(self):
        archive = self.zip_with("top\\back.txt")
        with mock.patch("zipfile.os.sep", "\\"):
            with zipfile.ZipFile(archive) as z:
                member = z.infolist()[1]
                self.assertEqual(member.filename, "top/back.txt")
                self.assertEqual(member.orig_filename, "top\\back.txt")
            with self.assertRaisesRegex(RuntimeError, "unsafe member path"):
                bundle_smoke.safe_extract(archive, str(self.tmp / "normalized dest"))


if __name__ == "__main__":
    unittest.main()
