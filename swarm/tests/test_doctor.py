"""doctor and setup against fake tools only. PATH holds nothing but fakes, so no real
package manager, installer, login or network can be reached from these tests."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(os.path.dirname(HERE), "pai_swarm.py")

TOOL = """#!{py}
import json, os, sys
a = sys.argv[1:]
name = os.path.basename(sys.argv[0])
state = json.load(open(os.environ["FAKE_STATE"]))
if a[:1] in (["--version"], ["-V"]):
    print(name + " 9.9 (fake)"); sys.exit(0)
if name == "gh" and a[:2] == ["auth", "status"]:
    sys.exit(0 if state.get("gh_auth", True) else 1)
if name == "claude" and a[:2] == ["auth", "status"]:
    print(json.dumps(state.get("claude_auth", {{"loggedIn": True, "authMethod": "claude.ai"}})))
    sys.exit(0 if state.get("claude_auth", {{"loggedIn": True}}).get("loggedIn") else 1)
if name == "claude" and a[:2] == ["remote-control", "--help"]:
    sys.exit(0 if state.get("claude_rc", True) else 1)
sys.exit(0)
"""

INSTALLER = """#!{py}
import json, os, shutil, sys
a = sys.argv[1:]
name = os.path.basename(sys.argv[0])
with open(os.environ["FAKE_STATE"] + ".installs", "a") as f:
    f.write(json.dumps([name] + a) + "\\n")
bindir = os.path.dirname(os.path.abspath(sys.argv[0]))
def provide(tool):
    shutil.copy(os.path.join(bindir, ".tool"), os.path.join(bindir, tool)); os.chmod(os.path.join(bindir, tool), 0o755)
if name == "sudo":
    os.execv(os.path.join(bindir, a[0]), a)
if name == "curl":
    open(a[a.index("-o") + 1], "w").write("# fake installer from " + a[-1] + "\\n"); sys.exit(0)
if name == "bash":
    provide("claude"); sys.exit(0)
if os.environ.get("FAKE_INSTALL_FAILS"):
    sys.exit(1)
for pkg in a:
    if not pkg.startswith("-") and pkg not in ("install", "add"):
        provide({{"github-cli": "gh"}}.get(pkg, pkg))
"""


@unittest.skipUnless(os.name == "posix", "POSIX only")
class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-doctor-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.state = os.path.join(self.tmp, "state.json")
        self.set_state()
        with open(os.path.join(self.bin, ".tool"), "w") as f:
            f.write(TOOL.format(py=sys.executable))
        self.os_release("arch")

    def set_state(self, **kw):
        with open(self.state, "w") as f:
            json.dump(kw, f)

    def os_release(self, ident, like=""):
        self.osr = os.path.join(self.tmp, "os-release")
        with open(self.osr, "w") as f:
            f.write('NAME="Fake"\nID=%s\n%s' % (ident, 'ID_LIKE="%s"\n' % like if like else ""))

    def have(self, *names, installer=False):
        for n in names:
            p = os.path.join(self.bin, n)
            with open(p, "w") as f:
                f.write((INSTALLER if installer else TOOL).format(py=sys.executable))
            os.chmod(p, 0o755)

    def run_cli(self, *args, **env):
        e = {"PATH": self.bin, "HOME": self.tmp, "FAKE_STATE": self.state, "PAI_SWARM_OS_RELEASE": self.osr,
             "TMPDIR": self.tmp}
        e.update(env)
        return subprocess.run([sys.executable, "-B", CLI] + list(args), capture_output=True, text=True, env=e,
                              stdin=subprocess.DEVNULL)

    def rows(self):
        p = self.run_cli("doctor", "--json")
        return {r["tool"]: r["state"] for r in json.loads(p.stdout)}, p.returncode

    def installs(self):
        p = self.state + ".installs"
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(l) for l in f]

    def test_everything_present_and_logged_in(self):
        self.have("git", "tmux", "claude", "gh")
        rows, code = self.rows()
        self.assertEqual(code, 0)
        self.assertEqual(set(rows.values()), {"ok"})
        self.assertEqual(sorted(rows), ["claude", "claude login", "claude remote control", "gh", "gh login", "git", "tmux"])
        self.assertIn("doctor changed nothing", self.run_cli("doctor").stdout)

    def test_missing_binaries_are_not_confused_with_missing_logins(self):
        self.have("git", "claude", "gh")
        self.set_state(gh_auth=False)
        rows, code = self.rows()
        self.assertEqual(code, 1)
        self.assertEqual((rows["tmux"], rows["gh"], rows["gh login"]), ("missing", "ok", "not-logged-in"))
        out = self.run_cli("doctor").stdout
        self.assertIn("MISSING        tmux", out)
        self.assertIn("NOT LOGGED IN  gh login", out)
        self.assertIn("-> gh auth login", out)

    def test_claude_login_and_remote_control_readiness_are_separate(self):
        self.have("git", "tmux", "claude", "gh")
        self.set_state(claude_auth={"loggedIn": False})
        rows, _ = self.rows()
        self.assertEqual((rows["claude"], rows["claude login"], rows["claude remote control"]), ("ok", "not-logged-in", "rc-not-ready"))
        self.set_state(claude_auth={"loggedIn": True, "authMethod": "apiKey"})
        rows, code = self.rows()
        self.assertEqual((rows["claude login"], rows["claude remote control"], code), ("ok", "rc-not-ready", 1))
        self.assertIn("needs a claude.ai subscription login", self.run_cli("doctor").stdout)
        self.set_state(claude_rc=False)
        rows, _ = self.rows()
        self.assertEqual((rows["claude login"], rows["claude remote control"]), ("ok", "rc-not-ready"))

    def test_setup_without_the_flag_installs_nothing(self):
        self.have("git", "claude")
        self.have("sudo", "pacman", installer=True)
        p = self.run_cli("setup")
        self.assertEqual(p.returncode, 1)
        self.assertIn("would install tmux, gh from arch (pacman):", p.stdout)
        self.assertIn("pacman -S --needed --noconfirm tmux github-cli", p.stdout)
        self.assertIn("Nothing was installed", p.stdout)
        self.assertEqual(self.installs(), [])
        self.assertFalse(os.path.exists(os.path.join(self.bin, "tmux")))

    def test_install_missing_installs_only_what_is_missing(self):
        self.have("git")
        self.have("sudo", "pacman", "curl", "bash", installer=True)
        self.set_state(gh_auth=False)
        p = self.run_cli("setup", "--install-missing")
        calls = self.installs()
        pac = [c for c in calls if c[0] == "pacman"]
        self.assertEqual(pac, [["pacman", "-S", "--needed", "--noconfirm", "tmux", "github-cli"]], "git was not reinstalled")
        curl = [c for c in calls if c[0] == "curl"][0]
        self.assertEqual(curl[-1], "https://claude.ai/install.sh")
        self.assertEqual(curl[1], "-fsSL")
        self.assertEqual(len([c for c in calls if c[0] == "bash"]), 1)
        rows, _ = self.rows()
        self.assertEqual((rows["tmux"], rows["gh"], rows["claude"]), ("ok", "ok", "ok"))
        self.assertEqual(p.returncode, 1, "installed, but gh still needs the owner's login")
        self.assertIn("Owner steps (never automated)", p.stdout)
        self.assertIn("gh login: gh auth login", p.stdout)
        self.assertEqual([c for c in calls if "login" in c], [], "setup never runs a login")
        self.run_cli("setup", "--install-missing")
        self.assertEqual(len(self.installs()), len(calls), "a second run finds nothing to install")

    def test_package_names_follow_the_platform(self):
        self.have("git", "claude")
        self.os_release("ubuntu", "debian")
        self.have("sudo", "apt-get", installer=True)
        self.assertIn("apt-get install -y tmux gh", self.run_cli("setup").stdout)
        self.os_release("rocky", "rhel centos fedora")
        self.have("dnf", installer=True)
        self.assertIn("dnf install -y tmux gh", self.run_cli("setup").stdout)
        self.os_release("alpine")
        self.have("apk", installer=True)
        self.assertIn("apk add tmux github-cli", self.run_cli("setup").stdout)

    def test_unsupported_platform_or_failed_install_is_reported_not_guessed(self):
        self.have("git", "claude", "gh")
        self.os_release("plan9")
        p = self.run_cli("setup", "--install-missing")
        self.assertEqual(p.returncode, 1)
        self.assertIn("cannot install tmux automatically: plan9 is not a supported distribution", p.stdout)
        self.assertEqual(self.installs(), [])
        self.os_release("arch")
        self.have("sudo", "pacman", installer=True)
        p = self.run_cli("setup", "--install-missing", FAKE_INSTALL_FAILS="1")
        self.assertEqual(p.returncode, 1)
        self.assertIn("failed", p.stdout)
        self.assertIn("MISSING        tmux", p.stdout.split("after installing:")[1])


if __name__ == "__main__":
    unittest.main()
