"""pai round new: main clone by GitHub remote, base by ls-remote, vault inference, worktrees,
prompt rendering and splitting, idempotent re-runs. Disposable repositories only; the GitHub
URL is rewritten to a local bare repository (url.<bare>.insteadOf), so nothing goes online."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SWARM = os.path.dirname(HERE)
sys.path.insert(0, SWARM)
sys.path.insert(0, os.path.dirname(SWARM))
import round as rnd  # noqa: E402

URL = "https://github.com/Acme/widget.git"
IDENT = {"GIT_AUTHOR_NAME": "PAI Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
         "GIT_COMMITTER_NAME": "PAI Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
needs_git = unittest.skipUnless(shutil.which("git"), "git is not installed")

PROMPT = """# Round prompt

```text
共享规则。本轮：{{RUN_ID}}。基线：{{BASE_SHA}}。vault：{{VAULT}}。worktree：{{WORKTREE_ROOT}}/<线名>。
工作目录：{{WORK}}/<线名>。这里提到 {{ 但不是占位符，"{{lower}}" 也不是。
```

```text
线名：alpha。线缩写：ALP。
alpha 的条目。
```

```text
线名：beta。线缩写：BET。
beta 的条目。
```
"""


def git(cwd, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, **IDENT)
    p = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), p.stderr))
    return p.stdout.strip()


class RoundFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-round-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "HOME": self.tmp,
                    "PAI_ROUND_CONFIG": "", "VAULT": "", "PAI_ROUND": "", **IDENT}
        self._old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.addCleanup(self._restore)
        self.bare = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.bare], check=True)
        seed = os.path.join(self.tmp, "seed")
        subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True)
        with open(os.path.join(seed, "README.md"), "w") as f:
            f.write("hello\n")
        git(seed, "add", "README.md")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "push", "-q", self.bare, "main")
        self.base = git(seed, "rev-parse", "HEAD")
        self.search = os.path.join(self.tmp, "code")
        self.repo = self.clone("widget")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "Agent Inbox"))
        os.makedirs(os.path.join(self.vault, "99 Templates"))
        self.prompt = os.path.join(self.vault, "99 Templates", "Round.md")
        with open(self.prompt, "w", encoding="utf-8") as f:
            f.write(PROMPT)
        os.environ["VAULT"] = self.vault  # last in the order; the prompt's location wins when given

    def _restore(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def clone(self, name):
        path = os.path.join(self.search, name)
        subprocess.run(["git", "clone", "-q", self.bare, path], check=True, capture_output=True)
        git(path, "remote", "set-url", "origin", URL)
        git(path, "config", "url.%s.insteadOf" % self.bare, URL)
        return path

    def run_round(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rnd.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def new(self, *extra, write=True):
        args = ["new", "widget", "R9-TEST", "--github", "acme/widget", "--search", self.search,
                "--trusted-root", os.path.join(self.tmp, "workers", "{round}"),
                "--work-root", os.path.join(self.tmp, "work", "{round}"), "--tmux-session", "r9"]
        return self.run_round(*(args + list(extra) + (["--write"] if write else [])))


@needs_git
class New(RoundFixture):
    def test_finds_the_clone_reads_the_base_and_creates_everything(self):
        code, out, err = self.new("--prompt", self.prompt)
        self.assertEqual(code, 0, err + out)
        r = rnd.load_round(os.path.join(self.vault, "Agent Inbox", "R9-TEST", "ROUND.json"))
        self.assertEqual(r["base_sha"], self.base)
        self.assertEqual(r["repo"], os.path.realpath(self.repo))
        self.assertEqual(r["vault"], self.vault)
        self.assertEqual(r["vault_source"], "prompt location")
        self.assertEqual([l["name"] for l in r["lanes"]], ["alpha", "beta"])
        a = r["lanes"][0]
        self.assertEqual(a["branch"], "worker/R9-TEST/alpha")
        self.assertEqual(a["tmux_target"], "r9:alpha")
        self.assertEqual(a["session_name"], "WIDGET R9-TEST alpha")
        self.assertEqual(git(a["worktree"], "rev-parse", "HEAD"), self.base)
        self.assertTrue(os.path.isdir(a["handoff_dir"]) and os.path.isdir(a["work_dir"]))
        with open(a["prompt_file"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("本轮：R9-TEST。基线：%s" % self.base, text)
        self.assertIn("alpha 的条目", text)
        self.assertNotIn("beta 的条目", text)
        self.assertEqual(rnd.sha256_file(a["prompt_file"]), a["prompt_sha256"])
        self.assertEqual(os.stat(a["prompt_file"]).st_mode & 0o777, 0o600)
        self.assertTrue(os.path.isfile(os.path.join(r["state_dir"], "BOARD.md")))

    def test_a_missing_github_names_the_configuration_file(self):  # R6-FIN-005 (docs N7)
        args = ["new", "widget", "R9-TEST", "--search", self.search, "--tmux-session", "r9"]
        code, out, err = self.run_round(*args)
        self.assertNotEqual(code, 0)
        self.assertIn('add github = "OWNER/NAME" under [project.widget] in ~/.config/pai/round.toml', out + err)

    def test_preview_writes_nothing(self):
        code, out, _ = self.new("--prompt", self.prompt, write=False)
        self.assertEqual(code, 0, out)
        self.assertIn("preview", out)
        self.assertIn("create-branch", out)
        self.assertFalse(os.path.exists(os.path.join(self.vault, "Agent Inbox", "R9-TEST")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "workers")))

    def test_rerun_is_safe_and_keeps_existing_lanes(self):
        self.assertEqual(self.new("--prompt", self.prompt)[0], 0)
        path = os.path.join(self.vault, "Agent Inbox", "R9-TEST", "ROUND.json")
        first = rnd.load_round(path)
        code, out, err = self.new("--prompt", self.prompt)
        self.assertEqual(code, 0, err)
        self.assertNotIn("created", out)
        self.assertEqual(out.count("already a worktree"), 0)  # the plan line says `exists`, apply logs it
        self.assertEqual(sum(1 for l in out.splitlines() if l.startswith("  exists ")), 2)
        self.assertEqual(rnd.load_round(path)["created"], first["created"])
        # A third lane later: the two old lanes stay in ROUND.json.
        code, _, err = self.new("--lane", "gamma")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(l["name"] for l in rnd.load_round(path)["lanes"]), ["alpha", "beta", "gamma"])

    def test_an_existing_branch_is_reused_not_recreated(self):
        git(self.repo, "branch", "worker/R9-TEST/alpha", self.base)
        code, out, err = self.new("--lane", "alpha")
        self.assertEqual(code, 0, err)
        self.assertIn("created", out)
        code, out, _ = self.new("--lane", "alpha", write=False)
        self.assertIn("exists", out)

    def test_a_folder_in_the_way_is_a_conflict(self):
        os.makedirs(os.path.join(self.tmp, "workers", "R9-TEST", "alpha"))
        code, out, err = self.new("--lane", "alpha")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT", err)
        self.assertFalse(os.path.exists(os.path.join(self.vault, "Agent Inbox", "R9-TEST", "ROUND.json")))

    def test_another_base_for_the_same_round_is_refused(self):
        self.assertEqual(self.new("--lane", "alpha")[0], 0)
        code, _, err = self.new("--lane", "alpha", "--base", "1" * 40)
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", err)

    def test_a_base_not_fetched_yet_is_reported(self):
        code, _, err = self.new("--lane", "alpha", "--base", "2" * 40)
        self.assertEqual(code, 2)
        self.assertIn("fetch origin main", err)


@needs_git
class Clones(RoundFixture):
    def test_two_clones_need_an_explicit_choice(self):
        second = self.clone("widget-copy")
        code, _, err = self.new("--lane", "alpha")
        self.assertEqual(code, 2)
        self.assertIn("2 main clones", err)
        self.assertIn(os.path.realpath(second), err)
        code, _, err = self.new("--lane", "alpha", "--repo", second)
        self.assertEqual(code, 0, err)

    def test_a_linked_worktree_or_another_project_is_not_a_clone(self):
        git(self.repo, "worktree", "add", "-q", os.path.join(self.search, "wt"), "-b", "side")
        other = os.path.join(self.search, "other")
        subprocess.run(["git", "clone", "-q", self.bare, other], check=True, capture_output=True)
        git(other, "remote", "set-url", "origin", "https://github.com/acme/other.git")
        self.assertEqual(rnd.find_clones("acme/widget", [self.search]), [os.path.realpath(self.repo)])
        with self.assertRaises(rnd.RoundError):
            rnd.pick_clone("acme/widget", [self.search], explicit=other)
        with self.assertRaises(rnd.RoundError):
            rnd.pick_clone("acme/widget", [self.search], explicit=os.path.join(self.search, "wt"))

    def test_no_clone(self):
        with self.assertRaises(rnd.RoundError) as e:
            rnd.pick_clone("acme/none", [self.search])
        self.assertIn("no main clone", str(e.exception))


class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-vault-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v1 = os.path.join(self.tmp, "v1")
        self.v2 = os.path.join(self.tmp, "v2")
        for v in (self.v1, self.v2):
            os.makedirs(os.path.join(v, "Agent Inbox"))
            os.makedirs(os.path.join(v, "99 Templates"))
        self.prompt = os.path.join(self.v2, "99 Templates", "p.md")
        open(self.prompt, "w").close()

    def test_order_config_then_prompt_location_then_env(self):
        self.assertEqual(rnd.resolve_vault(None, {"vault": self.v1}, self.prompt, {"VAULT": self.v2}), (self.v1, "config"))
        self.assertEqual(rnd.resolve_vault(None, {}, self.prompt, {"VAULT": self.v1}), (self.v2, "prompt location"))
        self.assertEqual(rnd.resolve_vault(None, {}, None, {"VAULT": self.v1}), (self.v1, "VAULT"))
        self.assertEqual(rnd.resolve_vault(self.v2, {"vault": self.v1}, None, {}), (self.v2, "--vault"))

    def test_a_wrong_setting_is_refused_not_skipped(self):
        with self.assertRaises(rnd.RoundError) as e:
            rnd.resolve_vault(None, {"vault": "/vaults/MainVault"}, self.prompt, {})
        self.assertIn("not the vault", str(e.exception))

    def test_a_prompt_outside_any_vault_falls_through_to_env(self):
        outside = os.path.join(self.tmp, "p.md")
        open(outside, "w").close()
        self.assertEqual(rnd.resolve_vault(None, {}, outside, {"VAULT": self.v1}), (self.v1, "VAULT"))
        with self.assertRaises(rnd.RoundError):
            rnd.resolve_vault(None, {}, outside, {})


class Prompts(unittest.TestCase):
    def test_only_real_placeholders_count(self):
        text, missing = rnd.render("a {{RUN_ID}} b {{ c {{lower}} {{LEFT_OVER}}", {"RUN_ID": "R1"})
        self.assertEqual(missing, ["{{LEFT_OVER}}"])
        self.assertIn("a R1 b {{ c {{lower}}", text)

    def test_split_into_part0_plus_each_lane(self):
        lanes = rnd.lane_prompts(PROMPT, "x")
        self.assertEqual(list(lanes), ["alpha", "beta"])
        self.assertTrue(lanes["beta"].startswith("共享规则"))
        self.assertIn("线名：beta。", lanes["beta"])

    def test_single_block_is_one_lane_named_after_the_project(self):
        self.assertEqual(list(rnd.lane_prompts("```text\nhello\n```\n", "pai")), ["pai"])

    def test_a_part_without_a_lane_line_is_refused(self):
        with self.assertRaises(rnd.RoundError):
            rnd.lane_prompts("```text\np0\n```\n```text\nno lane here\n```\n", "x")

    def test_placeholders_left_refuse_the_write(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = {"round": {"vault": tmp, "round": "R"}, "missing": ["{{X}}"], "actions": [], "prompts": {}}
        with self.assertRaises(rnd.RoundError) as e:
            rnd.apply_new(p, lambda s: None)
        self.assertIn("{{X}}", str(e.exception))


if __name__ == "__main__":
    unittest.main()
