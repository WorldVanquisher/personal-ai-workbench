"""guard.py's Bash hook checks where a command writes, not only git (R6-COR-011).

Reproduces the 2026-11-22 real `claude -p` smoke: under acceptEdits the worker ran
`printf 'smoke ok\\n' >> SMOKE.md`; the hook only looked for git and .git, so the same write to a
file outside the task's `paths:` would have gone through. Every case here runs the real hook
the way Claude Code does: JSON on stdin, exit 2 blocks."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SWARM = os.path.dirname(HERE)
GUARD = os.path.join(SWARM, "guard.py")
sys.path.insert(0, SWARM)
import guard  # noqa: E402
import pai_swarm  # noqa: E402


class BashWriteTargets(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="pai-guard-"))
        self.addCleanup(__import__("shutil").rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "docs", "sub"))
        os.makedirs(os.path.join(self.root, "src"))

    def hook(self, command, allow=("docs",), cwd=None):
        argv = [sys.executable, "-B", GUARD, "bash", "--root", self.root]
        for a in allow:
            argv += ["--allow", a]
        data = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd or self.root}
        return subprocess.run(argv, input=json.dumps(data), capture_output=True, text=True, timeout=30)

    def blocked(self, command, **kw):
        p = self.hook(command, **kw)
        self.assertEqual(p.returncode, 2, "not blocked: %r\n%s" % (command, p.stderr))
        return p.stderr

    def allowed(self, command, **kw):
        p = self.hook(command, **kw)
        self.assertEqual(p.returncode, 0, "blocked: %r\n%s" % (command, p.stderr))

    # -- the reproduction
    def test_the_smoke_append_outside_the_task_paths_is_blocked(self):
        err = self.blocked("printf 'smoke ok\\n' >> SMOKE.md")
        self.assertIn("SMOKE.md", err)
        self.assertIn("outside this task's paths", err)

    def test_the_same_append_inside_the_task_paths_is_allowed(self):
        self.allowed("printf 'smoke ok\\n' >> docs/SMOKE.md")
        self.allowed("printf 'smoke ok\\n' >> SMOKE.md", allow=("SMOKE.md",))

    # -- every writer named in the item
    def test_each_writer_outside_the_paths_is_blocked(self):
        for cmd in ("echo x > src/a.py", "echo x >| src/a.py", "echo x &> src/a.log", "echo x 2> src/err.log",
                    "echo x 2>>src/err.log", "echo x>src/a.py", "cat a | tee src/a.py", "tee -a docs/ok src/no < /dev/null",
                    "sed -i 's/a/b/' src/a.py", "sed -i.bak -e s/a/b/ src/a.py", "sed --in-place=.b s/a/b/ src/a.py",
                    "cp docs/a src/b", "cp -t src docs/a", "cp --target-directory=src docs/a", "mv docs/a src/a",
                    "mv src/a docs/a", "rm src/a.py", "rm -rf -- src", "dd if=/dev/zero of=src/blob bs=1 count=1",
                    "echo x > /etc/pai-guard-test", "echo x > ~/.bashrc", "touch src/new", "truncate -s 0 src/a.py"):
            with self.subTest(cmd=cmd):
                self.blocked(cmd)

    def test_reads_and_writes_inside_the_paths_are_allowed(self):
        for cmd in ("cat src/a.py", "grep -c 'smoke ok' SMOKE.md", "od -c SMOKE.md", "cp src/a docs/a",
                    "sed -n 1p src/a.py", "sed 's/a/b/' src/a.py > docs/out", "rm docs/sub/old.md", "echo x > /dev/null",
                    "echo x 2>/dev/null 1>&2", "ls 2>&1 | tee docs/log", "dd if=src/a of=docs/a", "npm test > /tmp/t.log",
                    "python3 -m pytest -q 2>&1 | tail -3", "mv docs/a docs/sub/a", "echo 'a > src/b'"):
            with self.subTest(cmd=cmd):
                self.allowed(cmd)

    def test_compound_and_nested_commands_are_followed(self):
        self.blocked("cd docs && echo x > ../src/a.py")
        self.allowed("cd docs && echo x > sub/a.md")
        self.blocked("true; rm src/a.py")
        self.blocked("bash -c 'echo x > src/a.py'")
        self.blocked("sh -c \"printf y >> SMOKE.md\"")
        self.blocked("echo $(echo x > src/a.py)")

    def test_a_target_that_cannot_be_resolved_is_blocked_when_paths_are_set(self):
        self.blocked("echo x > \"$OUT\"")
        self.blocked("echo x > `mktemp -p src`")
        self.allowed("echo x > \"$OUT\"", allow=())  # no paths: only repository and .git limits apply

    def test_globs_are_checked_by_their_fixed_prefix(self):
        self.allowed("rm docs/*.tmp")
        self.blocked("rm src/*.tmp")
        self.blocked("rm *")

    def test_without_paths_the_repository_and_git_limits_still_apply(self):
        self.allowed("echo x > src/a.py", allow=())
        self.blocked("echo x > /etc/pai-guard-test", allow=())  # the test root is under /tmp, where scratch is fine
        self.blocked("echo x > .git/config", allow=())
        self.allowed("echo x > /tmp/scratch.txt", allow=())

    def test_git_checks_are_unchanged(self):
        self.blocked("git commit -m x")
        self.allowed("git status")

    def test_cwd_from_the_hook_input_is_used(self):
        self.allowed("echo x > a.md", cwd=os.path.join(self.root, "docs"))
        self.blocked("echo x > a.md", cwd=os.path.join(self.root, "src"))


class MoreWriters(unittest.TestCase):
    """R6-COR-067: heredocs into files, install, truncate, >|, exec >file, and writers whose targets only
    exist at run time (xargs, find -delete/-exec), which are refused when the task has paths."""

    setUp, hook, blocked, allowed = (BashWriteTargets.setUp, BashWriteTargets.hook, BashWriteTargets.blocked,
                                     BashWriteTargets.allowed)

    def test_more_forms(self):
        for cmd in ("cat <<EOF > src/a.py\nx\nEOF", "cat > src/a.py <<'EOF'\nx\nEOF", "tee -a src/a <<EOF\nx\nEOF",
                    "install -m 644 docs/a src/b", "truncate -s0 src/a", "echo x >| src/a", "exec >src/log",
                    "exec 3>>src/log", "ls docs | xargs rm", "git ls-files | xargs -0 sed -i s/a/b/",
                    "find src -name '*.pyc' -delete", "find . -exec rm {} +"):
            with self.subTest(cmd=cmd):
                self.blocked(cmd)

    def test_harmless_forms_stay_allowed(self):
        for cmd in ("cat <<EOF > docs/a.md\nwrite > src/x is only text here\nEOF", "exec >docs/log",
                    "ls src | xargs wc -l", "find src -name '*.py'", "find docs -delete -name x"):
            with self.subTest(cmd=cmd):
                if "find docs -delete" in cmd:
                    self.blocked(cmd)  # the run-time targets cannot be checked, even inside the paths
                else:
                    self.allowed(cmd)
        self.allowed("ls docs | xargs rm", allow=())  # no task paths: only the repository limit applies


class WriteTargetParser(unittest.TestCase):
    def test_targets(self):
        t = guard.write_targets
        self.assertEqual(t("printf 'x' >> SMOKE.md"), ["SMOKE.md"])
        self.assertEqual(t("cp -r a b c dest"), ["dest"])
        self.assertEqual(t("mv a b"), ["a", "b"])
        self.assertEqual(t("dd if=a of=b"), ["b"])
        self.assertEqual(t("sed -e s/a/b/ -i f1 f2"), ["f1", "f2"])
        self.assertEqual(t("sed -i s/a/b/ f1"), ["f1"])
        self.assertEqual(t("echo hi 2>&1"), [])
        self.assertEqual(t("cat <<EOF > out\nx\nEOF"), ["out"])


class WorkerSettings(unittest.TestCase):
    def test_the_bash_hook_gets_the_task_paths(self):
        s = pai_swarm.worker_settings("/repo", ["docs", "src/api"])
        bash = [h for h in s["hooks"]["PreToolUse"] if h["matcher"] == "Bash"][0]["hooks"][0]["command"]
        self.assertIn("--allow docs", bash)
        self.assertIn("--allow src/api", bash)


if __name__ == "__main__":
    unittest.main()
