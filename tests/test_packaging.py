"""The installable package (pyproject.toml, `uv tool install ...`), without a network or a build
backend: the project metadata, and the installed layout simulated in a temporary folder (the pai
package with the workbench inside it as pai/_workbench, plus a dist-info RECORD), run through the
`pai` console-script entry point pai.launch:main with a fresh HOME.

The real build and install with uv run in CI (.github/workflows/ci.yml) and in
tools/onboarding_acceptance.py --uv."""
import base64
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pai import __version__  # noqa: E402

WINDOWS = os.name == "nt"


def allowlist():
    lines = (ROOT / "release" / "allowlist.txt").read_text(encoding="utf-8").splitlines()
    return {l.strip() for l in lines if l.strip() and not l.startswith("#")}


class Metadata(unittest.TestCase):
    def setUp(self):
        with open(ROOT / "pyproject.toml", "rb") as f:
            self.py = tomllib.load(f)

    def test_project_and_entry_point(self):
        p = self.py["project"]
        self.assertEqual(p["name"], "pai-workbench")
        self.assertEqual(p["scripts"], {"pai": "pai.launch:main"})
        self.assertEqual(p["requires-python"], ">=3.11")
        self.assertEqual(p.get("dependencies"), [], "pai has no runtime dependencies")
        self.assertEqual(p["dynamic"], ["version"])
        self.assertEqual(self.py["tool"]["setuptools"]["dynamic"]["version"], {"attr": "pai.__version__"})

    def test_the_package_holds_the_same_runtime_as_the_archive(self):
        st = self.py["tool"]["setuptools"]
        self.assertEqual(st["packages"], ["pai", "pai._workbench"])
        self.assertEqual(st["package-dir"], {"pai._workbench": "swarm"})
        self.assertFalse(st["include-package-data"])
        self.assertEqual(st["package-data"], {"pai": ["templates/handoff.md"], "pai._workbench": ["WORKBENCH.md"]})
        # setuptools takes every .py module of each package directory, and the archive must list them too
        shipped = {"pai/" + f.name for f in (ROOT / "pai").glob("*.py")} | {"swarm/" + f.name for f in (ROOT / "swarm").glob("*.py")}
        shipped |= {"pai/templates/handoff.md", "swarm/WORKBENCH.md"}
        self.assertEqual(shipped - allowlist(), set())
        self.assertIn("pai/launch.py", allowlist())


def record_line(site, path):
    data = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return "%s,sha256=%s,%d" % (path.relative_to(site).as_posix(), digest, len(data))


class InstalledLayout(unittest.TestCase):
    """What `uv tool install` puts in site-packages, built by hand."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        t = Path(self._t.name)
        self.site, self.home, self.data = t / "site", t / "home", t / "local"
        pkg = self.site / "pai"
        shutil.copytree(ROOT / "pai", pkg, ignore=shutil.ignore_patterns("__pycache__"))
        (pkg / "_workbench").mkdir()
        for f in list((ROOT / "swarm").glob("*.py")) + [ROOT / "swarm" / "WORKBENCH.md"]:
            shutil.copy(f, pkg / "_workbench" / f.name)
        info = self.site / ("pai_workbench-%s.dist-info" % __version__)
        info.mkdir()
        (info / "METADATA").write_text("Metadata-Version: 2.4\nName: pai-workbench\nVersion: %s\n" % __version__)
        lines = [record_line(self.site, f) for f in sorted(self.site.rglob("*")) if f.is_file()]
        (info / "RECORD").write_text("\n".join(lines + [info.name + "/RECORD,,"]) + "\n")
        self.home.mkdir()
        self.data.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def pai(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PAI_", "XDG_"))}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), LOCALAPPDATA=str(self.data))
        code = "import sys; sys.path.insert(0, %r); from pai.launch import main; sys.exit(main())" % str(self.site)
        return subprocess.run([sys.executable, "-I", "-B", "-c", code] + list(args), capture_output=True, text=True,
                              encoding="utf-8", env=env, cwd=cwd or self.home, timeout=60, stdin=subprocess.DEVNULL)

    def test_version_and_first_screen(self):
        p = self.pai("--version")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "pai %s" % __version__), p.stderr)
        p = self.pai()
        self.assertEqual(p.returncode, 0, p.stderr)
        if WINDOWS:
            self.assertIn("WSL", p.stdout)
        else:
            self.assertIn("pai run --dry-run", p.stdout)
            self.assertIn("Guide: %s" % (self.site / "pai" / "_workbench" / "WORKBENCH.md"), p.stdout)

    def test_workbench_commands_come_from_inside_the_package(self):
        p = self.pai("run", "--help")
        if WINDOWS:
            self.assertEqual(p.returncode, 2)
            self.assertIn("WSL", p.stderr)
        else:
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("--dry-run", p.stdout)
            p = self.pai("server", "status")
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("not part of this installation", p.stdout + p.stderr)

    def test_install_points_to_the_package_manager(self):
        p = self.pai("install")
        self.assertEqual(p.returncode, 2)
        self.assertIn("installed Python package", p.stderr)
        self.assertIn("uv tool install --force git+https://github.com/WorldVanquisher/personal-ai-workbench@v", p.stderr)

    def test_doctor_checks_the_record(self):
        p = self.pai("doctor", "--agents")
        self.assertIn("package pai-workbench %s intact" % __version__, p.stdout)
        cfg = (self.data if WINDOWS else self.home / ".config") / "pai" / "config.toml"
        self.assertIn("no configuration at %s" % cfg, p.stdout)
        self.assertNotIn("pai.example.toml", p.stdout)
        with open(self.site / "pai" / "context.py", "a") as f:
            f.write("\n# changed\n")
        p = self.pai("doctor", "--agents")
        self.assertEqual(p.returncode, 1)
        self.assertIn("installed files modified or missing: pai/context.py", p.stdout)

    def test_init_writes_to_the_users_config_folder_never_into_the_package(self):
        repo, vault = self.home / "repo", self.home / "vault"
        repo.mkdir()
        (vault / "Inbox").mkdir(parents=True)
        (vault / "Mem").mkdir()
        args = ["init", "demo", "--repo", str(repo), "--vault", str(vault), "--memory", "Mem", "--inbox", "Inbox"]
        p = self.pai(*args, "--write")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        cfg = (self.data if WINDOWS else self.home / ".config") / "pai" / "config.toml"
        self.assertTrue(cfg.is_file(), p.stdout)
        if not WINDOWS:
            self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
            self.assertEqual(cfg.parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse(any(self.site.rglob("*.toml")))
        p = self.pai(*args, "--write")
        self.assertNotEqual(p.returncode, 0, "never overwrites")


if __name__ == "__main__":
    unittest.main()
