"""`pai install` on a freshly built bundle, in temporary folders only: user-local layout, an
idempotent reinstall, refusals that leave the previous version working, the launcher on each
platform (the Windows .cmd launcher is checked on every system), and the PATH advice."""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_bundle  # noqa: E402
from pai import __version__, install  # noqa: E402

POSIX = os.name != "nt"


class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._t.name)
        archives, _, _ = build_bundle.build(str(ROOT), str(cls.tmp / "dist"), 0)
        import tarfile
        with tarfile.open(archives["tar.gz"][0]) as t:
            t.extractall(cls.tmp / "x", filter="data")
        cls.bundle = cls.tmp / "x" / f"pai-workbench-{__version__}"

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(dir=self.tmp))
        self.prefix, self.bin = self.t / "prefix", self.t / "bin"
        self.env = {"PATH": os.pathsep.join(["/nonexistent"]), "SHELL": "/bin/bash", "HOME": str(self.t)}

    def run_install(self, src=None, windows=False, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            code = install.install(src=str(src or self.bundle), prefix=str(self.prefix), bin_dir=str(self.bin),
                                   env=kw.pop("env", self.env), windows=windows, out=print, **kw)
        return code, out.getvalue()

    @unittest.skipUnless(POSIX, "symlink launcher")
    def test_install_reinstall_and_a_failed_upgrade_keep_a_working_launcher(self):
        code, out = self.run_install()
        dest = self.prefix / "versions" / __version__
        link = self.bin / "pai"
        self.assertEqual(code, 0, out)
        self.assertTrue(link.is_symlink() and Path(os.path.realpath(link)) == (dest / "bin" / "pai").resolve(), out)
        self.assertTrue(os.access(dest / "bin" / "pai", os.X_OK))
        self.assertEqual(install.damaged(str(dest), install.read_sums(str(dest))), [])
        self.assertIn("is not on PATH", out)
        self.assertIn("echo 'export PATH=", out)
        receipt = json.loads((self.prefix / "receipt.json").read_text())
        self.assertEqual((receipt["version"], receipt["bin_dir"]), (__version__, str(self.bin)))
        # again: kept as it is
        code, out = self.run_install()
        self.assertIn("already installed and intact; kept", out)
        # a damaged source is refused before anything changes
        bad = self.t / "bad"
        shutil.copytree(self.bundle, bad)
        with open(bad / "pai" / "cli.py", "a") as f:
            f.write("# changed\n")
        with self.assertRaisesRegex(install.InstallError, "does not match its SHA256SUMS.*pai/cli.py"):
            self.run_install(src=bad)
        self.assertEqual(os.readlink(link), str(dest / "bin" / "pai"))
        # a copy that fails half way (an unwritable versions folder) leaves the launcher alone
        shutil.rmtree(dest)
        (self.prefix / "versions").chmod(0o555)
        try:
            if os.access(self.prefix / "versions", os.W_OK):
                self.skipTest("running as a user who can write anywhere")
            with self.assertRaises(OSError):
                self.run_install()
        finally:
            (self.prefix / "versions").chmod(0o755)
        self.assertEqual(os.readlink(link), str(dest / "bin" / "pai"))
        self.assertEqual([p.name for p in (self.prefix / "versions").iterdir()], [], "no half copy left behind")

    @unittest.skipUnless(POSIX, "symlink launcher")
    def test_a_launcher_pai_did_not_make_is_never_replaced(self):
        self.bin.mkdir(parents=True)
        (self.bin / "pai").write_text("#!/bin/sh\necho mine\n")
        with self.assertRaisesRegex(install.InstallError, "not a launcher pai install made"):
            self.run_install()
        self.assertEqual((self.bin / "pai").read_text(), "#!/bin/sh\necho mine\n")
        self.assertFalse((self.prefix / "versions" / __version__).exists())

    def test_windows_launcher_is_a_marked_crlf_batch_file(self):
        code, out = self.run_install(windows=True, env={"PATH": "", "LOCALAPPDATA": str(self.t)})
        cmd = self.bin / "pai.cmd"
        data = cmd.read_bytes()
        self.assertEqual(data.count(b"\r\n"), data.count(b"\n"), "cmd.exe needs CRLF")
        self.assertIn(install.MARK.encode(), data)
        self.assertIn(str(self.prefix / "versions" / __version__ / "bin" / "pai.cmd").encode(), data)
        self.assertEqual(install.current(str(self.bin), str(self.prefix), windows=True)[0], __version__)
        self.assertIn("[Environment]::SetEnvironmentVariable", out)
        (self.bin / "pai.cmd").write_text("@echo off\necho mine\n")
        with self.assertRaisesRegex(install.InstallError, "not written by pai install"):
            self.run_install(windows=True)

    def test_a_checkout_or_a_malformed_sums_file_is_refused(self):
        with self.assertRaisesRegex(install.InstallError, "not a release bundle.*build_bundle.py"):
            install.read_sums(str(ROOT))
        d = self.t / "sums"
        d.mkdir()
        for bad in ("abc  x\n", "%s  ../escape\n" % ("0" * 64), "%s *bin/pai.py\n" % ("0" * 64)):
            (d / "SHA256SUMS").write_text(bad)
            with self.assertRaises(install.InstallError, msg=bad):
                install.read_sums(str(d))

    def test_path_advice_names_the_shell_file_and_never_edits_it(self):
        home = os.path.expanduser("~")
        b = os.path.join(home, ".local", "bin")
        self.assertEqual(install.path_fix(b, {"SHELL": "/bin/bash"}, windows=False, platform="linux")[0],
                         "echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bashrc")
        self.assertIn("~/.bash_profile", install.path_fix(b, {"SHELL": "/bin/bash"}, windows=False, platform="darwin")[0])
        self.assertIn("~/.zshrc", install.path_fix(b, {"SHELL": "/bin/zsh"}, windows=False)[0])
        self.assertEqual(install.path_fix(b, {"SHELL": "/usr/bin/fish"}, windows=False)[0], "fish_add_path ~/.local/bin")
        self.assertIn("~/.profile", install.path_fix("/opt/x/bin", {"SHELL": "/bin/dash"}, windows=False)[0])
        self.assertIn("/opt/x/bin", install.path_fix("/opt/x/bin", {}, windows=False)[0])
        self.assertTrue(install.on_path(b, {"PATH": os.pathsep.join(["/usr/bin", b])}))
        self.assertFalse(install.on_path(b, {"PATH": "/usr/bin"}))

    @unittest.skipUnless(shutil.which("bash") and os.name != "nt", "runs the advice in a real bash")
    def test_path_advice_works_when_pasted_into_bash(self):
        # 059: spaces, quotes, $, a backtick, non-ASCII, inside and outside HOME; the advice is
        # executed exactly as printed, then a new login-like shell reads the file it wrote.
        home = self.t / "Ada's home"
        home.mkdir()
        for rel in (".local/bin", "My Tools/bin", 'odd "quoted" $dir/`tick`/bin', "café/bin", "it's/bin"):
            for base in (str(home), "/opt/pai tools"):
                d = os.path.join(base, rel)
                env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "SHELL": "/bin/bash"}
                rc = home / ".bashrc"
                if rc.exists():
                    rc.unlink()
                with mock.patch.dict(os.environ, {"HOME": str(home)}):
                    first, second = install.path_fix(d, env, windows=False, platform="linux")
                p = subprocess.run(["bash", "--noprofile", "--norc", "-c", first], env=env, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, (first, p.stderr))
                p = subprocess.run(["bash", "--noprofile", "--norc", "-c", 'source ~/.bashrc; printf "%s" "$PATH"'],
                                   env=env, capture_output=True, text=True)
                self.assertEqual(p.stdout.split(":")[0] if ":" in p.stdout else p.stdout, d, (first, p.stdout))
                now = second.split("for this one only: ", 1)[1].rstrip(")")
                p = subprocess.run(["bash", "--noprofile", "--norc", "-c", now + '; printf "%s" "$PATH"'], env=env,
                                   capture_output=True, text=True)
                self.assertTrue(p.stdout.startswith(d + ":"), (now, p.stdout))

    def test_path_advice_is_quoted_for_fish_and_powershell(self):
        # no fish or PowerShell on the test host: the exact text is checked instead (unverified in those shells)
        home = os.path.expanduser("~")
        self.assertEqual(install.path_fix(os.path.join(home, "My Tools", "bin"), {"SHELL": "/usr/bin/fish"}, windows=False)[0],
                         'fish_add_path "$HOME/My Tools/bin"')
        self.assertEqual(install.path_fix("/opt/a$b/bin", {"SHELL": "/usr/bin/fish"}, windows=False)[0],
                         'fish_add_path "/opt/a\\$b/bin"')
        ps = install.path_fix(r"C:\Users\Ana Ng\$weird`bin", {}, windows=True)[0]
        self.assertIn(r'"C:\Users\Ana Ng\`$weird``bin;"', ps)

    def test_defaults_are_user_local(self):
        p, b = install.defaults({"XDG_DATA_HOME": "/xdg"}, windows=False)
        self.assertEqual((p, b), (os.path.join("/xdg", "pai-workbench"), os.path.join(os.path.expanduser("~"), ".local", "bin")))
        p, b = install.defaults({"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}, windows=True)
        self.assertTrue(p.endswith("pai-workbench") and b == os.path.join(p, "bin"))


if __name__ == "__main__":
    unittest.main()
