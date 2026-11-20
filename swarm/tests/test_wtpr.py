"""Existing-worktree PR preview and publish (R3-011..R3-030), against a local bare remote
and the fake gh: nothing reaches GitHub or the network."""
import hashlib
import json
import os
import stat
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git, commit  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")


def lines(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


@helpers.needs_git
class WorktreePR(helpers.Fixture):
    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.tmp, "lane wt")
        git(self.repo, "worktree", "add", "-q", "-b", "feat/y", self.wt)
        self.write("docs/new note.md", "# New\n")           # approved, untracked, a space in the name
        self.write("README.md", "hello\nmore\n")            # approved, modified
        self.write("debug.log", "noise\n")                  # unexpected
        self.write("__pycache__/m.cpython-314.pyc", "x")    # unexpected cache
        self.write("staged-elsewhere.txt", "keep staged\n")
        git(self.wt, "add", "staged-elsewhere.txt")         # unrelated staged change
        self.body = os.path.join(self.tmp, "body.md")
        self.write_abs(self.body, "Adds a note.\n")
        self.plan = os.path.join(self.tmp, "plan.json")

    def write(self, rel, text):
        self.write_abs(os.path.join(self.wt, rel), text)

    @staticmethod
    def write_abs(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def pai(self, *args):
        return subprocess.run([sys.executable, "-B", PAI, "worktree"] + list(args), capture_output=True, text=True,
                              env=self.env, cwd=self.tmp, stdin=subprocess.DEVNULL)

    def preview(self, *extra, paths=("docs/new note.md", "README.md")):
        args = ["preview", self.wt, "--title", "docs: a note", "--body-file", self.body, "--github", "o/r"]
        for p in paths:
            args += ["--path", p]
        return self.pai(*(args + list(extra)))

    def snapshot(self):
        gd = git(self.wt, "rev-parse", "--absolute-git-dir")
        with open(os.path.join(gd, "index"), "rb") as f:
            index = hashlib.sha256(f.read()).hexdigest()
        files = {}
        for d, dirs, fs in os.walk(self.wt):
            dirs[:] = [x for x in dirs if x != ".git"]
            for fn in fs:
                p = os.path.join(d, fn)
                with open(p, "rb") as f:
                    files[os.path.relpath(p, self.wt)] = (f.read(), os.stat(p).st_mtime_ns)
        return index, git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)"), files, \
            git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)")

    def gh_calls(self):
        return lines(self.env["FAKE_GH_STATE"] + ".log")

    def test_preview_reports_everything_and_changes_nothing(self):
        before = self.snapshot()
        p = self.preview("--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.snapshot(), before, "no file, index or ref changed (here or on the remote)")
        pv = json.loads(p.stdout)
        self.assertEqual((pv["branch"], pv["base"]["last_fetched"], pv["base"]["ahead"]), ("feat/y", self.c0, 0))
        self.assertEqual(pv["pushed"]["state"], "not on the remote")
        self.assertEqual(pv["existing_prs"], [])
        pa = pv["paths"]
        self.assertEqual([r["path"] for r in pa["approved"]], ["docs/new note.md", "README.md"])
        self.assertEqual(pa["staged"], ["staged-elsewhere.txt"])
        self.assertEqual(pa["staged_unrelated"], ["staged-elsewhere.txt"])
        self.assertEqual(pa["unexpected"], ["__pycache__/m.cpython-314.pyc", "debug.log", "staged-elsewhere.txt"])
        self.assertIn("__pycache__/m.cpython-314.pyc", pv["caches"])
        self.assertTrue(any("generated cache" in w for w in pv["warnings"]))
        self.assertEqual(pv["draft"]["message"], "docs: a note\n\nAdds a note.")
        text = self.preview().stdout
        for needle in ("approved    docs/new note.md", "staged      staged-elsewhere.txt", "unexpected  __pycache__",
                       "PREVIEW ONLY: no file, index or ref was changed."):
            self.assertIn(needle, text)
        self.assertEqual(self.snapshot()[:2], before[:2])

    def test_detached_mismatched_caches_and_attribution_are_refused(self):
        p = self.preview("--branch", "feat/other")
        self.assertEqual(p.returncode, 1)
        self.assertIn("the worktree is on feat/y, not feat/other", p.stdout)
        p = self.preview(paths=("README.md", "__pycache__/m.cpython-314.pyc"))
        self.assertIn("is in the approved paths: remove it from the proposal", p.stdout)
        self.write_abs(self.body, "Co-Authored-By: Claude <noreply@anthropic.com>\n")
        p = self.preview()
        self.assertEqual(p.returncode, 1)
        self.assertIn("REFUSED     PR body:", p.stdout)
        det = os.path.join(self.tmp, "detached")
        git(self.repo, "worktree", "add", "-q", "--detach", det)
        p = self.pai("preview", det, "--title", "x", "--path", "README.md", "--offline")
        self.assertIn("HEAD is detached", p.stdout)
        p = self.preview("--out", self.plan, paths=("../outside.txt",))
        self.assertIn("is not a path inside the worktree", p.stdout)
        self.assertFalse(os.path.exists(self.plan), "no plan is written for a refused preview")

    def test_publish_needs_the_owner_then_commits_only_approved_paths_pushes_and_opens_once(self):
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        before = self.snapshot()
        p = self.pai("publish", self.plan)
        self.assertEqual(p.returncode, 1)
        self.assertIn("Nothing was done: add --yes", p.stdout)
        p = self.pai("publish", self.plan, "--yes")
        self.assertIn("choose --draft or --ready", p.stdout)
        self.assertEqual(self.snapshot(), before)
        hooks = git(self.repo, "rev-parse", "--git-path", "hooks")
        marker = os.path.join(self.tmp, "hook-ran")
        self.write_abs(os.path.join(self.repo, hooks, "pre-commit"), "#!/bin/sh\ntouch '%s'\n" % marker)
        os.chmod(os.path.join(self.repo, hooks, "pre-commit"), 0o755)
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.exists(marker), "the repository's hooks ran")
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(git(self.wt, "show", "--name-only", "--format=", head).splitlines(),
                         ["README.md", "docs/new note.md"])
        self.assertEqual(git(self.wt, "log", "-1", "--format=%B").strip(), "docs: a note\n\nAdds a note.")
        self.assertEqual(git(self.wt, "diff", "--cached", "--name-only"), "staged-elsewhere.txt", "still staged, not committed")
        self.assertTrue(os.path.exists(os.path.join(self.wt, "debug.log")))
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/feat/y"), head)
        creates = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("--draft", creates[0])
        self.assertIn("read back from the remote", p.stdout)
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("reused", p.stdout)
        self.assertEqual(len([c for c in self.gh_calls() if c[:2] == ["pr", "create"]]), 1, "never opened twice")
        self.assertEqual(git(self.wt, "rev-list", "--count", "%s..HEAD" % self.c0), "1", "never committed twice")

    def test_a_rejecting_hook_leaves_the_index_as_it_was(self):
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        hooks = os.path.join(self.repo, git(self.repo, "rev-parse", "--git-path", "hooks"))
        self.write_abs(os.path.join(hooks, "pre-commit"), "#!/bin/sh\necho 'lint failed' >&2\nexit 1\n")
        os.chmod(os.path.join(hooks, "pre-commit"), 0o755)
        before = git(self.wt, "status", "--porcelain")
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 1)
        self.assertIn("lint failed", p.stdout)
        self.assertIn("docs/new note.md untracked again", p.stdout)
        self.assertEqual(git(self.wt, "status", "--porcelain"), before, "index and files as before")
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.c0)
        self.assertEqual(git(self.origin, "branch", "--list", "feat/y"), "")

    def test_drift_after_the_preview_is_refused(self):
        self.preview("--out", self.plan)
        self.write("README.md", "hello\nchanged after the preview\n")
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 1)
        self.assertIn("DRIFT       README.md changed since the preview", p.stdout)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.c0)
        self.write("README.md", "hello\nmore\n")
        git(self.wt, "remote", "set-url", "--push", "origin", os.path.join(self.tmp, "elsewhere.git"))
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertIn("the remote's push URL changed", p.stdout)

    def test_an_interrupted_publish_resumes_without_doing_anything_twice(self):
        self.preview("--out", self.plan)
        objects = os.path.join(self.origin, "objects")
        mode = stat.S_IMODE(os.stat(objects).st_mode)
        os.chmod(objects, 0o555)  # the push fails after the commit
        try:
            p = self.pai("publish", self.plan, "--yes", "--ready")
        finally:
            os.chmod(objects, mode)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("committed", p.stdout)
        self.assertIn("FAILED      push", p.stdout)
        head = git(self.wt, "rev-parse", "HEAD")
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": [], "fail_create": True}, f)  # then the PR step fails
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertIn("pushed", p.stdout)
        self.assertIn("the branch is pushed but the PR was not opened", p.stdout)
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": []}, f)
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("opened", p.stdout)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), head, "the commit was made once")
        self.assertNotIn("--draft", [c for c in self.gh_calls() if c[:2] == ["pr", "create"]][-1])
        with open(self.plan + ".journal") as f:
            steps = json.load(f)["steps"]
        self.assertEqual(sorted(steps), ["commit", "pr", "push"])


    def test_a_kill_between_the_commit_and_the_journal_is_resumed_not_repeated(self):
        self.preview("--out", self.plan)
        with open(self.plan) as f:
            plan = json.load(f)
        msg = os.path.join(self.tmp, "msg")
        with open(msg, "w") as f:
            f.write(plan["draft"]["message"] + "\n")
        env = dict(os.environ, GIT_LITERAL_PATHSPECS="1")
        subprocess.run(["git", "-C", self.wt, "add", "--", "docs/new note.md"], check=True, env=env)
        subprocess.run(["git", "-C", self.wt, "commit", "-q", "--only", "-F", msg, "--", "docs/new note.md", "README.md"],
                       check=True, env=env)  # what publish does, then the process dies before the journal write
        head = git(self.wt, "rev-parse", "HEAD")
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("resumed     %s" % head[:12], p.stdout)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), head, "not committed again")
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/feat/y"), head)
        other = os.path.join(self.tmp, "plan2.json")
        self.write("README.md", "hello\nmore\nand more\n")
        self.assertEqual(self.preview("--out", other, paths=("README.md",)).returncode, 0)
        git(self.wt, "commit", "-qam", "someone else's commit")
        p = self.pai("publish", other, "--yes", "--ready")
        self.assertEqual(p.returncode, 1)
        self.assertIn("DRIFT       HEAD moved", p.stdout, "a commit this plan did not make is never adopted")

    # -- approval is a hard gate before anything leaves the machine

    def remote_refs(self):
        return git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)")

    def pr_creates(self):
        return [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]

    def hook(self, name, text):
        hooks = os.path.join(self.repo, git(self.repo, "rev-parse", "--git-path", "hooks"))
        self.write_abs(os.path.join(hooks, name), text)
        os.chmod(os.path.join(hooks, name), 0o755)
        return os.path.join(hooks, name)

    def assert_refused_and_nothing_left(self, p, remote_before, head, *needles):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        for needle in ("REFUSED     commit %s" % head[:12], "is not the commit the preview approved",
                       "Nothing was pushed and no PR was opened; the commit stays in your local branch") + needles:
            self.assertIn(needle, p.stdout)
        self.assertNotIn("pushed      ", p.stdout)
        self.assertEqual(self.remote_refs(), remote_before, "the remote is unchanged")
        self.assertEqual(self.pr_creates(), [], "gh pr create is never called")
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), head, "the local commit is kept as it is")

    def test_a_commit_with_unapproved_paths_is_refused_on_the_first_attempt_and_on_retries(self):
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        remote_before = self.remote_refs()
        hook = self.hook("pre-commit", "#!/bin/sh\ngit add debug.log\n")  # a hook slips an unapproved file in
        p = self.pai("publish", self.plan, "--yes", "--draft")
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertNotEqual(head, self.c0, "the commit was made")
        self.assertIn("debug.log", git(self.wt, "show", "--name-only", "--format=", head).splitlines())
        self.assert_refused_and_nothing_left(p, remote_before, head, "(made by this run)",
                                             "it changes path(s) outside the approved preview: debug.log")
        os.remove(hook)
        # a retry reads the journal: its entry for this commit authorizes nothing
        for args in (("--yes", "--draft"), ("--yes", "--ready"), ()):
            p = self.pai("publish", self.plan, *args)
            self.assert_refused_and_nothing_left(p, remote_before, head, "(recorded in the journal by an earlier run)",
                                                 "it changes path(s) outside the approved preview: debug.log")
        # a retry after an interruption that lost the journal adopts the commit, and refuses it the same way
        os.remove(self.plan + ".journal")
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assert_refused_and_nothing_left(p, remote_before, head, "(made before an interruption)",
                                             "it changes path(s) outside the approved preview: debug.log")
        self.assertFalse(os.path.exists(self.plan + ".journal"), "a refused commit is not recorded as adopted")
        self.assertEqual(git(self.wt, "rev-list", "--count", "%s..HEAD" % self.c0), "1", "never committed twice")
        listing = self.pai("list", "--repo", self.repo).stdout  # R4-PAI-047: still visible to the owner afterwards
        self.assertIn("branch feat/y, HEAD %s" % head[:12], listing)
        self.assertIn("1 commit(s) not in the fetched base", listing)

    def test_a_commit_with_other_content_than_the_preview_is_refused_when_adopted(self):
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        with open(self.plan) as f:
            plan = json.load(f)
        remote_before = self.remote_refs()

        def obj(*args, data=None):
            return subprocess.run(["git", "-C", self.wt] + list(args), input=data, capture_output=True, text=True,
                                  check=True, env=dict(self.env, GIT_INDEX_FILE=os.path.join(self.tmp, "other.index"))
                                  ).stdout.strip()
        # an interrupted run (or someone) committed the approved paths with the reviewed message, but README.md
        # holds another version than the previewed one; the working files still match the preview
        obj("read-tree", self.c0)
        other = obj("hash-object", "-w", "--stdin", data="hello\nsomething else\n")
        note = obj("hash-object", "-w", "--stdin", data="# New\n")
        obj("update-index", "--add", "--cacheinfo", "100644,%s,README.md" % other)
        obj("update-index", "--add", "--cacheinfo", "100644,%s,docs/new note.md" % note)
        tree = obj("write-tree")
        sha = obj("commit-tree", tree, "-p", self.c0, "-m", plan["draft"]["message"])
        git(self.wt, "update-ref", "refs/heads/feat/y", sha)
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assert_refused_and_nothing_left(p, remote_before, sha, "(made before an interruption)",
                                             "README.md in the commit is %s, not the previewed content" % other[:12])

    def test_a_hook_that_edits_the_reviewed_message_is_refused(self):
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        remote_before = self.remote_refs()
        self.hook("commit-msg", "#!/bin/sh\nprintf '\\nCo-authored-by: Someone <s@example.org>\\n' >> \"$1\"\n")
        p = self.pai("publish", self.plan, "--yes", "--draft")
        head = git(self.wt, "rev-parse", "HEAD")
        self.assert_refused_and_nothing_left(p, remote_before, head, "its message is not the reviewed one")

    # -- the tree entry Git records: type, mode and content (symbolic links are their target path)

    def link_blob(self, target):
        return subprocess.run(["git", "-C", self.wt, "hash-object", "--stdin"], input=target, capture_output=True,
                              text=True, check=True).stdout.strip()

    def test_an_approved_symbolic_link_is_previewed_and_published_as_a_link(self):
        os.symlink("new note.md", os.path.join(self.wt, "docs", "latest.md"))  # an ordinary relative link
        paths = ("docs/new note.md", "README.md", "docs/latest.md")
        p = self.preview("--out", self.plan, paths=paths)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(self.plan) as f:
            rows = {r["path"]: r for r in json.load(f)["paths"]["approved"]}
        self.assertEqual((rows["docs/latest.md"]["mode"], rows["docs/latest.md"]["blob"]),
                         ("120000", self.link_blob("new note.md")), "the link's target, not the bytes it points to")
        self.assertEqual(rows["README.md"]["mode"], "100644")
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(git(self.wt, "ls-tree", head, "docs/latest.md").split("\t")[0],
                         "120000 blob %s" % self.link_blob("new note.md"))
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/feat/y"), head)
        self.assertEqual(len(self.pr_creates()), 1)
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("reused", p.stdout, "a retry of a valid commit stays idempotent")
        self.assertEqual(len(self.pr_creates()), 1)

    def test_a_hook_cannot_change_an_approved_files_mode_or_type(self):
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        remote_before = self.remote_refs()
        hook = self.hook("pre-commit", "#!/bin/sh\ngit update-index --chmod=+x README.md\n")
        p = self.pai("publish", self.plan, "--yes", "--draft")
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(git(self.wt, "ls-tree", head, "README.md").split()[0], "100755", "the hook made it executable")
        self.assert_refused_and_nothing_left(p, remote_before, head, "(made by this run)",
                                             "README.md in the commit is 100755 (executable file), not the previewed "
                                             "100644 (file)")
        os.remove(hook)
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assert_refused_and_nothing_left(p, remote_before, head, "(recorded in the journal by an earlier run)",
                                             "100755 (executable file)")
        # the same plan, a fresh start: a hook turns the approved file into a symbolic link
        git(self.wt, "reset", "-q", "--soft", self.c0)
        git(self.wt, "rm", "-q", "--cached", "docs/new note.md")
        os.remove(self.plan + ".journal")
        self.hook("pre-commit", "#!/bin/sh\noid=$(printf 'elsewhere' | git hash-object -w --stdin)\n"
                                "git update-index --cacheinfo 120000,$oid,README.md\n")
        p = self.pai("publish", self.plan, "--yes", "--draft")
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(git(self.wt, "ls-tree", head, "README.md").split()[0], "120000")
        self.assert_refused_and_nothing_left(p, remote_before, head, "(made by this run)",
                                             "README.md in the commit is 120000 (symbolic link), not the previewed "
                                             "100644 (file)")

    def test_regular_executable_link_and_deletion_entries(self):
        """R4-PAI-038: one reviewed commit with a modified file, a new executable, a new link and a deletion."""
        commit(self.wt, "old.txt", "to be removed\n", "add old.txt")
        base = git(self.wt, "rev-parse", "HEAD")
        self.write("run.sh", "#!/bin/sh\necho hi\n")
        os.chmod(os.path.join(self.wt, "run.sh"), 0o755)
        os.symlink("run.sh", os.path.join(self.wt, "latest"))
        os.remove(os.path.join(self.wt, "old.txt"))
        paths = ("README.md", "run.sh", "latest", "old.txt")
        self.assertEqual(self.preview("--out", self.plan, paths=paths).returncode, 0)
        with open(self.plan) as f:
            rows = {r["path"]: (r["status"].strip(), r["mode"]) for r in json.load(f)["paths"]["approved"]}
        self.assertEqual(rows, {"README.md": ("M", "100644"), "run.sh": ("??", "100755"), "latest": ("??", "120000"),
                                "old.txt": ("D", None)})
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(git(self.wt, "rev-parse", "HEAD~1"), base)
        tree = {l.split("\t")[1]: l.split()[0] for l in git(self.wt, "ls-tree", head).splitlines()}
        self.assertEqual((tree["README.md"], tree["run.sh"], tree["latest"]), ("100644", "100755", "120000"))
        self.assertNotIn("old.txt", tree)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/feat/y"), head)

    def test_a_plan_without_approved_modes_needs_a_new_review(self):
        """R4-PAI-039: a plan from an earlier pai (no recorded modes) is refused before anything; a new preview publishes."""
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        with open(self.plan) as f:
            plan = json.load(f)
        for r in plan["paths"]["approved"]:
            del r["mode"]
        with open(self.plan, "w") as f:
            json.dump(plan, f)
        remote_before = self.remote_refs()
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 1)
        self.assertIn("was made by an earlier pai and does not record the approved type and mode of", p.stdout)
        self.assertIn("Preview again", p.stdout)
        self.assertEqual((git(self.wt, "rev-parse", "HEAD"), self.remote_refs(), self.pr_creates()),
                         (self.c0, remote_before, []))
        os.remove(self.plan)
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        self.assertEqual(self.pai("publish", self.plan, "--yes", "--draft").returncode, 0)

    def test_line_ending_attributes_are_matched_against_what_git_records(self):
        """R4-PAI-040: a representative repository: `* text=auto` and `*.txt text eol=lf`; a CRLF working file is
        stored with LF; preview, gate and publish agree with Git's own result."""
        self.write(".gitattributes", "* text=auto\n*.txt text eol=lf\n")
        git(self.wt, "add", ".gitattributes")
        git(self.wt, "commit", "-qm", "attributes")
        self.c0 = git(self.wt, "rev-parse", "HEAD")
        with open(os.path.join(self.wt, "notes.txt"), "wb") as f:
            f.write(b"line one\r\nline two\r\n")
        self.assertEqual(self.preview("--out", self.plan, paths=("notes.txt",)).returncode, 0)
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        head = git(self.wt, "rev-parse", "HEAD")
        stored = subprocess.run(["git", "-C", self.wt, "cat-file", "blob", "%s:notes.txt" % head], capture_output=True).stdout
        self.assertEqual(stored, b"line one\nline two\n", "Git normalised the line endings")
        expected = git(self.wt, "hash-object", "--path", "notes.txt", "notes.txt")
        self.assertEqual(git(self.wt, "rev-parse", "%s:notes.txt" % head), expected)
        self.assertEqual(self.pai("publish", self.plan, "--yes", "--draft").returncode, 0, "and its retry")

    def test_outcomes_report_only_what_happened_and_branch_drift_says_how_back(self):
        """R4-PAI-041..045: the outcome line after a push failure, a PR failure and success, from the journal alone;
        a branch switch since the preview is refused with both ways back."""
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        git(self.wt, "switch", "-q", "-c", "other")
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 1)
        self.assertIn("REFUSED     the worktree is on other, not feat/y: to publish feat/y, switch back "
                      "(git -C '%s' switch feat/y); to publish other, preview it instead" % self.wt, p.stdout)
        self.assertIn("outcome     commit: none made by this plan; remote: not pushed by this plan; PR: not opened", p.stdout)
        git(self.wt, "switch", "-q", "feat/y")
        objects = os.path.join(self.origin, "objects")
        mode = stat.S_IMODE(os.stat(objects).st_mode)
        os.chmod(objects, 0o555)
        try:
            p = self.pai("publish", self.plan, "--yes", "--ready")
        finally:
            os.chmod(objects, mode)
        head = git(self.wt, "rev-parse", "HEAD")
        if os.geteuid() != 0:  # root ignores the read-only object folder, so the push succeeds there
            self.assertIn("outcome     commit: %s (local branch at it); remote: not pushed by this plan; PR: not opened"
                          % head[:12], p.stdout)
            self.assertNotIn("pushed      ", p.stdout)
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": [], "fail_create": True}, f)
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertIn("outcome     commit: %s (local branch at it); remote: origin feat/y = %s (read back); PR: not opened"
                      % (head[:12], head[:12]), p.stdout)
        with open(self.env["FAKE_GH_STATE"], "w") as f:
            json.dump({"prs": []}, f)
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertRegex(p.stdout, r"outcome     commit: %s \(local branch at it\); remote: origin feat/y = %s \(read back\); "
                                   r"PR: https://\S+" % (head[:12], head[:12]))

    def test_reviewed_trailers_are_published_exactly_and_ai_attribution_is_refused(self):
        """R4-PAI-049: a commit-msg hook that drops a reviewed trailer is refused before the push; without it the
        reviewed message with its human trailers is published exactly; an AI attribution trailer is refused at preview."""
        msg = os.path.join(self.tmp, "msg.txt")
        with open(msg, "w") as f:
            f.write("docs: a note\n\nAdds a note.\n\nReviewed-by: Ada Owner <ada@example.org>\n"
                    "Co-authored-by: Grace Human <grace@example.org>\n")
        p = self.preview("--out", self.plan, "--message-file", msg)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        remote_before = self.remote_refs()
        hook = self.hook("commit-msg", "#!/bin/sh\ngrep -v '^Reviewed-by:' \"$1\" > \"$1.tmp\" && mv \"$1.tmp\" \"$1\"\n")
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("its message is not the reviewed one", p.stdout)
        self.assertEqual((self.remote_refs(), self.pr_creates()), (remote_before, []))
        os.remove(hook)  # the owner removes the hook, undoes the refused commit and publishes the reviewed plan
        git(self.wt, "reset", "-q", "--soft", self.c0)
        git(self.wt, "rm", "-q", "--cached", "docs/new note.md")
        os.remove(self.plan + ".journal")
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        trailers = git(self.wt, "log", "-1", "--format=%(trailers:only)").strip().splitlines()
        self.assertEqual(trailers, ["Reviewed-by: Ada Owner <ada@example.org>",
                                    "Co-authored-by: Grace Human <grace@example.org>"])
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/feat/y"), git(self.wt, "rev-parse", "HEAD"))
        with open(msg, "a") as f:
            f.write("Co-Authored-By: Claude <noreply@anthropic.com>\n")
        p = self.preview("--out", os.path.join(self.tmp, "ai.json"), "--message-file", msg)
        self.assertEqual(p.returncode, 1)
        self.assertIn("REFUSED     commit message", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ai.json")), "no plan is written for it")

    def test_a_clean_filter_still_publishes(self):
        """Git stores the filtered bytes, not the working file's: still the approved content."""
        git(self.repo, "config", "filter.upper.clean", "tr a-z A-Z")
        info = os.path.join(git(self.repo, "rev-parse", "--git-common-dir"), "info")
        self.write_abs(os.path.join(info if os.path.isabs(info) else os.path.join(self.repo, info), "attributes"),
                       "README.md filter=upper\n")
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        p = self.pai("publish", self.plan, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(git(self.wt, "show", "%s:README.md" % head), "HELLO\nMORE")
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/feat/y"), head)
        self.assertEqual(self.pai("publish", self.plan, "--yes", "--draft").returncode, 0, "and its retry")

    def test_a_mode_change_after_the_preview_is_drift(self):
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)
        os.chmod(os.path.join(self.wt, "README.md"), 0o755)
        p = self.pai("publish", self.plan, "--yes", "--ready")
        self.assertEqual(p.returncode, 1)
        self.assertIn("DRIFT       README.md changed type or mode since the preview: 100644 (file), now 100755 "
                      "(executable file)", p.stdout)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.c0, "nothing committed")

    def test_a_directory_is_refused_at_preview_before_any_commit(self):
        p = self.preview("--out", self.plan, paths=("docs", "README.md"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("REFUSED     approved path docs is a directory or a submodule: publish commits regular files "
                      "and symbolic links only", p.stdout)
        self.assertFalse(os.path.exists(self.plan), "a refused preview writes no plan, so nothing can be published")
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.c0)
        self.assertEqual(self.pr_creates(), [])

    def digest(self):
        with open(self.plan, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_from_planning_to_publishing_still_needs_the_owners_confirmation_of_the_reviewed_plan(self):
        """R4-PAI-018: a worker can prepare a preview and dry-run it; nothing is published without the owner's
        explicit --yes and PR state, and with --expect-plan only the exact plan the owner reviewed is published."""
        p = self.preview("--out", self.plan)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        reviewed = self.digest()
        self.assertIn("(sha256 %s; review it, then: pai worktree publish" % reviewed[:16], p.stdout)
        self.assertIn("--expect-plan %s --yes --draft|--ready" % reviewed[:16], p.stdout)
        remote_before = self.remote_refs()
        p = self.pai("publish", self.plan)
        self.assertIn("Nothing was done: add --yes", p.stdout)
        p = self.pai("publish", self.plan, "--yes")
        self.assertIn("choose --draft or --ready", p.stdout)
        with open(self.plan) as f:  # the plan is edited after the owner's review
            plan = json.load(f)
        plan["draft"]["title"] = "docs: a note (and more)"
        with open(self.plan, "w") as f:
            json.dump(plan, f, indent=1)
        p = self.pai("publish", self.plan, "--expect-plan", reviewed[:16], "--yes", "--draft")
        self.assertEqual(p.returncode, 1)
        self.assertIn("is not the plan that was reviewed", p.stdout)
        self.assertIn("Nothing was staged, committed, pushed or opened.", p.stdout)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.c0)
        self.assertEqual((self.remote_refs(), self.pr_creates()), (remote_before, []))
        p = self.pai("publish", self.plan, "--expect-plan", "not-a-digest", "--yes", "--draft")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--expect-plan takes the plan's sha256", p.stderr)
        os.remove(self.plan)
        self.assertEqual(self.preview("--out", self.plan).returncode, 0)  # the owner reviews again
        fresh = self.digest()
        p = self.pai("publish", self.plan, "--expect-plan", fresh[:12], "--yes", "--draft")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.pr_creates()), 1)
        p = self.pai("publish", self.plan, "--expect-plan", fresh, "--yes", "--draft")
        self.assertEqual(p.returncode, 0, "a retry with the same reviewed plan stays idempotent")
        self.assertEqual(len(self.pr_creates()), 1)


if __name__ == "__main__":
    unittest.main()
