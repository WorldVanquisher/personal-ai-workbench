"""Per-worktree AGENTS.md (R3-031..R3-060): explicit init, the managed section, inherited
rules, profiles, and JEV scoring against a local fixture server only (no external calls)."""
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from helpers import git, commit  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")
sys.path.insert(0, helpers.SWARM)
import agentsmd  # noqa: E402


class Jev(http.server.BaseHTTPRequestHandler):
    mode, requests = "ok", []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        type(self).requests.append(body)
        if type(self).mode == "slow":
            time.sleep(3)
        if type(self).mode == "malformed":
            reply = {"answers": {"rule-tests-first": {"noul": "high"}}}
        else:
            reply = {"answers": {q: {"noul": 0.9 if "test" in spec["instructions"] else 0.2}
                                 for q, spec in body["questions"].items()}, "usage": {"input_tokens": 300}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client already gave up (the timeout case)


@helpers.needs_git
class AgentsMd(helpers.Fixture):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.upstream, "CLAUDE.md"), "w") as f:
            f.write("# Rules\n\n- Keep functions short.\n- Push your branch when done.\n- Docs live in /home/alice/docs.\n")
        os.makedirs(os.path.join(self.upstream, "src api"))
        with open(os.path.join(self.upstream, "src api", "AGENTS.md"), "w") as f:
            f.write("# API rules\n\n- Every endpoint has a schema test.\n")
        with open(os.path.join(self.upstream, "src api", "x.py"), "w") as f:
            f.write("x = 1\n")
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-qm", "rules")
        git(self.upstream, "push", "-q", "origin", "main")
        git(self.repo, "pull", "-q", "origin", "main")
        self.wt = os.path.join(self.tmp, "lane a wt")
        git(self.repo, "worktree", "add", "-q", "-b", "lane/a", self.wt)
        self.env.update(PAI_JEV_CREDENTIAL_STORE="none")
        self.env.pop("TYPESAFE_API_KEY", None)

    def pai(self, *args, env=None):
        e = dict(self.env)
        e.update(env or {})
        return subprocess.run([sys.executable, "-B", PAI, "worktree", "init"] + list(args), capture_output=True,
                              text=True, env=e, cwd=self.tmp, stdin=subprocess.DEVNULL)

    def agents(self, wt=None):
        with open(os.path.join(wt or self.wt, "AGENTS.md"), "rb") as f:
            return f.read()

    def test_explicit_init_previews_then_writes_one_managed_section(self):
        args = [self.wt, "--goal", "Fix the slugify bug", "--scope", "src api", "--owner", "Ada", "--lane", "a",
                "--check", "unit=python -m pytest -q"]
        p = self.pai(*args)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("PREVIEW ONLY", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "AGENTS.md")))
        self.assertIn("+- Goal: Fix the slugify bug", p.stdout)
        p = self.pai(*(args + ["--write"]))
        text = self.agents().decode()
        for needle in ("<!-- pai:managed:begin pai-agents/1", "- Goal: Fix the slugify bug", "`src api/`",
                       "Owner: Ada; this is lane `a`", "Run `python -m pytest -q` (unit)", "Do not push, open or merge",
                       "Keep functions short. (CLAUDE.md:3)", "Every endpoint has a schema test. (src api/AGENTS.md:3)",
                       "<!-- pai:managed:end -->"):
            self.assertIn(needle, text)
        self.assertNotIn("/home/alice", text, "local paths never copied")
        self.assertIn("<local path>", text)
        before = self.agents()
        p = self.pai(*(args + ["--write"]))
        self.assertIn("unchanged", p.stdout)
        self.assertEqual(self.agents(), before, "repeating it changes nothing")
        p = self.pai(self.repo, "--goal", "x")
        self.assertEqual(p.returncode, 2)
        self.assertIn("is the main checkout", p.stderr)
        p = self.pai(self.wt, "--goal", "x", "--scope", "new cli")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("scope new cli does not exist yet", p.stdout)
        for bad in ("../elsewhere", "/etc", ".git/hooks", "README.md"):
            p = self.pai(self.wt, "--goal", "x", "--scope", bad)
            self.assertEqual(p.returncode, 2, bad)
            self.assertIn("is not a folder inside the worktree", p.stderr)

    def test_manual_text_bom_and_crlf_survive_regeneration(self):
        manual = "﻿# Our notes\r\n\r\nKeep this   exactly  \r\n\r\nTrailing manual text\r\n"
        with open(os.path.join(self.wt, "AGENTS.md"), "wb") as f:
            f.write(manual.encode("utf-8"))
        self.pai(self.wt, "--goal", "first goal", "--write")
        data = self.agents()
        self.assertTrue(data.startswith(manual.encode("utf-8")), "BOM and the manual text first, byte for byte")
        self.assertNotIn(b"\n- Goal", data.replace(b"\r\n", b""), "the managed section uses CRLF too")
        self.pai(self.wt, "--goal", "second goal", "--write")
        after = self.agents()
        self.assertIn(b"- Goal: second goal", after)
        self.assertNotIn(b"first goal", after)
        self.assertTrue(after.startswith(data[:data.index(b"<!-- pai:managed:begin")]), "manual bytes unchanged")
        os.remove(os.path.join(self.wt, "AGENTS.md"))
        os.symlink(os.path.join(self.tmp, "elsewhere.md"), os.path.join(self.wt, "AGENTS.md"))
        p = self.pai(self.wt, "--goal", "x", "--write")
        self.assertEqual(p.returncode, 2)
        self.assertIn("symbolic link", p.stderr)

    def test_conflicts_entry_points_profiles_and_neighbours(self):
        p = self.pai(self.wt, "--goal", "add tests", "--json")
        info = json.loads(p.stdout)
        self.assertEqual([c["source"] for c in info["conflicts"]], ["CLAUDE.md:4"], "push asked by the repo: a conflict")
        self.assertTrue(any("@AGENTS.md" in n for n in info["notes"]), "Claude reads CLAUDE.md: the note says how")
        self.assertEqual(info["jev"]["state"], "off")
        self.assertIn("tests-first", [x["id"] for x in info["proposals"]])
        self.assertEqual(info["accepted"], [], "nothing is applied by itself")
        prof = os.path.join(self.tmp, "profile.json")
        p = self.pai(self.wt, "--goal", "add tests", "--accept", "tests-first", "--check", "unit=make test",
                     "--export-profile", prof)
        self.assertFalse(os.path.exists(prof), "a profile is previewed first")
        self.pai(self.wt, "--goal", "add tests", "--accept", "tests-first", "--check", "unit=make test",
                 "--export-profile", prof, "--write")
        with open(prof) as f:
            data = json.load(f)
        self.assertEqual((data["accepted"], data["checks"]), (["tests-first"], [["unit", "make test"]]))
        other = os.path.join(self.tmp, "lane b wt")
        git(self.repo, "worktree", "add", "-q", "-b", "lane/b", other)
        p = self.pai(other, "--goal", "Polish the docs", "--profile", prof, "--write")
        self.assertEqual(p.returncode, 0, p.stderr)
        a_text, b_text = self.agents(), self.agents(other)
        self.assertIn(b"Run `make test` (unit)", b_text)
        self.assertIn(b"(rule `tests-first`, owner)", b_text)
        self.assertNotIn(b"Polish the docs", a_text)
        self.assertNotIn(b"add tests", b_text)
        with open(os.path.join(other, "AGENTS.md"), "a") as f:
            f.write("\nmanual note in b\n")
        self.assertNotIn(b"manual note in b", self.agents(), "neighbouring worktrees stay independent")

    def start_jev(self, mode):
        Jev.mode, Jev.requests = mode, []
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Jev)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return {"TYPESAFE_API_KEY": "fixture-key", "TYPESAFE_BASE_URL": "http://127.0.0.1:%d" % srv.server_port,
                "JEV_TIMEOUT": "1"}

    def test_jev_scores_the_library_with_the_smallest_context_and_falls_back_cleanly(self):
        env = self.start_jev("ok")
        t0 = time.time()
        p = self.pai(self.wt, "--goal", "Fix the slugify bug", "--scope", "src api", "--jev", "--json", env=env)
        info = json.loads(p.stdout)
        self.assertLess(time.time() - t0, 10, "rapid from a new worktree")
        self.assertEqual(info["jev"]["state"], "ok")
        top = info["proposals"][0]
        self.assertEqual((top["id"], top["source"], top["template"]), ("tests-first", "jev", "pai-agents/1"))
        sent = json.dumps(Jev.requests[0])
        self.assertIn("Fix the slugify bug", sent)
        self.assertNotIn("Keep functions short", sent, "no repository content is sent")
        self.assertNotIn(self.wt, sent, "no local path is sent")
        self.assertFalse(os.path.exists(os.path.join(self.wt, "AGENTS.md")))
        for mode, state in (("malformed", "malformed"), ("slow", "timeout")):
            env = self.start_jev(mode)
            info = json.loads(self.pai(self.wt, "--goal", "Fix a bug in tests", "--jev", "--json", env=env).stdout)
            self.assertEqual(info["jev"]["state"], state, mode)
            self.assertTrue(info["proposals"] and all(x["source"] == "template" for x in info["proposals"]),
                            "the deterministic template answers instead")
        info = json.loads(self.pai(self.wt, "--goal", "x tests", "--jev", "--json").stdout)
        self.assertEqual(info["jev"]["state"], "skipped")
        info = json.loads(self.pai(self.wt, "--goal", "x tests", "--jev", "--json",
                                   env={"TYPESAFE_API_KEY": "k", "TYPESAFE_BASE_URL": "http://127.0.0.1:9", "JEV_TIMEOUT": "2"}).stdout)
        self.assertEqual(info["jev"]["state"], "unavailable")


    def test_golden_fixture_with_intentional_whitespace(self):
        gold = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "agents-golden")
        with open(os.path.join(gold, "manual.md"), "rb") as f:
            manual = f.read()
        target = os.path.join(self.wt, "AGENTS.md")
        with open(target, "wb") as f:
            f.write(manual)
        args = [self.wt, "--goal", "Keep the whitespace", "--scope", "src api", "--lane", "g", "--check", "unit=make test",
                "--accept", "tests-first", "--write"]
        self.assertEqual(self.pai(*args).returncode, 0)
        with open(target, "rb") as f:
            out = f.read()
        self.assertTrue(out.startswith(manual + b"\n\n<!-- pai:managed:begin"), "manual bytes first, exactly")
        base = git(self.repo, "rev-parse", "origin/main")[:12].encode()
        section = out[len(manual) + 2:].replace(base, b"<BASE>")
        golden = os.path.join(gold, "section.md")
        if os.environ.get("PAI_UPDATE_GOLDEN"):
            with open(golden, "wb") as f:
                f.write(section)
        with open(golden, "rb") as f:
            self.assertEqual(section, f.read(), "the generated section matches the golden file")
        self.assertEqual(self.pai(*args).returncode, 0)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), out, "regenerating changes no byte")
        p = self.pai(self.wt, "--goal", "Another goal", "--write")
        with open(target, "rb") as f:
            again = f.read()
        self.assertTrue(again.startswith(manual + b"\n\n<!-- pai:managed:begin"))
        self.assertIn(b"Another goal", again)


if __name__ == "__main__":
    unittest.main()
