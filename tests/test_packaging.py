"""The installable package (pyproject.toml, `uv tool install ...`), without a network or a build
backend: the project metadata, and the installed layout simulated in a temporary folder (the pai
package with the workbench inside it as pai/_workbench and the browser pages as
pai/_workbench/mdserver, plus a dist-info RECORD), run through the `pai` console-script entry
point pai.launch:main with a fresh HOME.

`pai edit` is checked here, through that entry point, on every CI system, native Windows
included: one-time link, one file only, live preview without writes, confirmed save keeping CRLF,
a newer file on disk never overwritten, and a clean stop (SIGINT; Ctrl-Break on Windows).

The real build and install with uv run in CI (.github/workflows/ci.yml) and in
tools/onboarding_acceptance.py --uv."""
import base64
import hashlib
import http.client
import json
import os
import shutil
import signal
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
        self.assertEqual(st["packages"], ["pai", "pai._workbench", "pai._workbench.mdserver"])
        self.assertEqual(st["package-dir"], {"pai._workbench": "swarm"})
        self.assertFalse(st["include-package-data"])
        self.assertEqual(st["package-data"], {"pai": ["templates/handoff.md"],
                                              "pai._workbench": ["WORKBENCH.md", "RUN-STATE.md", "BACKENDS.md",
                                                                 "CONTINUATION.md"],
                                              "pai._workbench.mdserver": ["README.md", "static/*.html", "static/*.css",
                                                                          "static/*.js"]})
        # setuptools takes every .py module of each package directory, and the archive must list them too
        md = ROOT / "swarm" / "mdserver"
        shipped = {"pai/" + f.name for f in (ROOT / "pai").glob("*.py")} | {"swarm/" + f.name for f in (ROOT / "swarm").glob("*.py")}
        shipped |= {"swarm/mdserver/" + f.name for f in md.glob("*.py")}
        shipped |= {"swarm/mdserver/static/" + f.name for f in (md / "static").iterdir()}
        shipped |= {"pai/templates/handoff.md", "swarm/WORKBENCH.md", "swarm/RUN-STATE.md", "swarm/BACKENDS.md",
                    "swarm/CONTINUATION.md", "swarm/mdserver/README.md"}
        self.assertEqual(shipped - allowlist(), set())
        self.assertIn("pai/launch.py", allowlist())
        self.assertEqual({f.suffix for f in (md / "static").iterdir()}, {".html", ".css", ".js"}, "package-data covers them")


def record_line(site, path):
    data = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return "%s,sha256=%s,%d" % (path.relative_to(site).as_posix(), digest, len(data))


def edit_session(t, pai_argv, env, home):
    """One full `pai edit` session through `pai_argv` (the simulated package, or a real installed
    `pai`): sign in once, edit, save, stale refusal, stop. `t` is the TestCase."""
    docs = Path(home) / "notes \u00e9"
    docs.mkdir()
    note = docs / "plan.md"
    note.write_bytes(b"# Plan\r\n\r\nfirst line\r\n")
    (docs / "other.md").write_text("# not this one\n")
    argv = list(pai_argv) + ["edit", str(note), "--no-browser"]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0  # so Ctrl-Break reaches only pai edit
    p = subprocess.Popen(argv, env=env, cwd=home, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", creationflags=flags)
    t.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait(), p.stdout.close(), p.stderr.close()))
    lines = []
    while not any("open:" in l for l in lines):
        line = p.stdout.readline()
        if not line:
            t.fail("pai edit printed no link: %s" % p.stderr.read())
        lines.append(line)
    t.assertIn(str(note), "".join(lines))
    url = [l for l in lines if "open:" in l][0].split("open:")[1].strip()
    port = int(url.split(":")[2].split("/")[0])
    link = url.split(str(port), 1)[1]

    def req(method, path, body=None, cookie=None, csrf=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % port}
        h.update({"Cookie": cookie} if cookie else {})
        h.update({"X-PAI-CSRF": csrf, "Content-Type": "application/json"} if csrf else {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    st, hdrs, _ = req("GET", link)
    t.assertEqual(st, 303)
    cookie = hdrs["Set-Cookie"].split(";")[0]
    t.assertEqual(req("GET", link)[0], 401, "the link signs in once")
    t.assertIn(b'id="text"', req("GET", "/", cookie=cookie)[2], "the page comes from the installed package")
    t.assertEqual(req("GET", "/static/app.js", cookie=cookie)[0], 200)
    session = json.loads(req("GET", "/api/session", cookie=cookie)[2])
    t.assertEqual(session["single"], {"root": 0, "path": "plan.md"})
    csrf = session["csrf"]
    t.assertEqual(req("GET", "/api/file?root=0&path=other.md", cookie=cookie)[0], 403, "only that one file")
    doc = json.loads(req("GET", "/api/file?root=0&path=plan.md", cookie=cookie)[2])
    before = note.read_bytes()
    st, _, prev = req("POST", "/api/preview", {"text": doc["text"] + "**typed**\n"}, cookie, csrf)
    t.assertIn(b"<strong>typed</strong>", prev)
    t.assertEqual(note.read_bytes(), before, "a preview writes nothing")
    new = doc["text"] + "second line\n"
    d = json.loads(req("POST", "/api/diff", {"root": 0, "path": "plan.md", "text": new, "base": doc["base"]}, cookie, csrf)[2])
    save = {"root": 0, "path": "plan.md", "text": new, "base": doc["base"], "crlf": doc["crlf"], "bom": doc["bom"]}
    t.assertEqual(req("POST", "/api/save", save, cookie, csrf)[0], 400, "no confirmation, no write")
    st, _, body = req("POST", "/api/save", dict(save, confirm=d["proposed"]), cookie, csrf)
    t.assertEqual(st, 200, body)
    t.assertEqual(note.read_bytes(), b"# Plan\r\n\r\nfirst line\r\nsecond line\r\n", "saved, line endings kept")
    base = json.loads(body)["base"]
    with open(note, "ab") as f:
        f.write(b"edited elsewhere\r\n")
    st, _, _ = req("POST", "/api/save", dict(save, text=new + "more\n", base=base,
                                              confirm=hashlib.sha256((new + "more\n").encode()).hexdigest()), cookie, csrf)
    t.assertEqual(st, 409)
    t.assertTrue(note.read_bytes().endswith(b"edited elsewhere\r\n"), "the newer file was kept")
    p.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGINT)
    t.assertEqual(p.wait(timeout=20), 0, p.stderr.read())
    t.assertIn("stopped", p.stdout.read())
    t.assertEqual([x.name for x in docs.iterdir() if x.name.endswith(".tmp")], [], "no temporary file left")


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
        md = ROOT / "swarm" / "mdserver"
        (pkg / "_workbench" / "mdserver" / "static").mkdir(parents=True)
        for f in list(md.glob("*.py")) + [md / "README.md"] + list((md / "static").iterdir()):
            shutil.copy(f, pkg / "_workbench" / "mdserver" / f.relative_to(md))
        info = self.site / ("pai_workbench-%s.dist-info" % __version__)
        info.mkdir()
        (info / "METADATA").write_text("Metadata-Version: 2.4\nName: pai-workbench\nVersion: %s\n" % __version__)
        lines = [record_line(self.site, f) for f in sorted(self.site.rglob("*")) if f.is_file()]
        (info / "RECORD").write_text("\n".join(lines + [info.name + "/RECORD,,"]) + "\n")
        self.home.mkdir()
        self.data.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def argv(self, *args):
        code = "import sys; sys.path.insert(0, %r); from pai.launch import main; sys.exit(main())" % str(self.site)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PAI_", "XDG_")) and k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), LOCALAPPDATA=str(self.data))
        return [sys.executable, "-I", "-B", "-c", code] + list(args), env

    def pai(self, *args, cwd=None):
        argv, env = self.argv(*args)
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=env, cwd=cwd or self.home,
                              timeout=60, stdin=subprocess.DEVNULL)

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

    def test_edit_runs_from_the_installed_package_on_every_system(self):
        argv, env = self.argv()
        edit_session(self, argv, env, self.home)

    def test_edit_refuses_what_it_cannot_edit(self):
        (self.home / "a.txt").write_text("x")
        for args, why in ((["edit", str(self.home / "missing.md")], "does not exist"),
                          (["edit", str(self.home / "a.txt")], ".md files"),
                          (["edit", str(self.home / "x.md"), "--port", "80"], "does not exist")):
            p = self.pai(*args)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn(why, p.stderr)
        p = self.pai("edit", "--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("one Markdown file", p.stdout)

    def test_paste_and_web_are_workbench_commands(self):
        for cmd in ("paste", "web"):
            p = self.pai(cmd, "--help")
            if WINDOWS:
                self.assertEqual(p.returncode, 2)
                self.assertIn("WSL", p.stderr)
            else:
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("--no-browser" if cmd == "web" else "--allow-unbracketed", p.stdout)

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


@unittest.skipUnless(os.environ.get("PAI_INSTALLED"), "set PAI_INSTALLED to an installed `pai` (CI's uv step)")
class RealInstall(unittest.TestCase):
    """The same `pai edit` session through a really installed `pai` (uv tool install), with a fresh
    HOME. CI runs it on Linux, macOS and native Windows."""

    def test_edit_through_the_installed_command(self):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if not k.startswith(("PAI_", "XDG_"))
                   and k not in ("DISPLAY", "WAYLAND_DISPLAY")}
            env.update(HOME=home, USERPROFILE=home)
            edit_session(self, [os.environ["PAI_INSTALLED"]], env, home)


if __name__ == "__main__":
    unittest.main()
