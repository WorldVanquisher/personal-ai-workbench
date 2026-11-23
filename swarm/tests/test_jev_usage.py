"""JEV usage log and `pai jev usage` (R6-COR-034), the evidence question and the wider `checked`
wording (R6-COR-033). Mock endpoint on 127.0.0.1 only; the key is a fake."""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402

sys.path.insert(0, helpers.SWARM)
import jev  # noqa: E402

PAI = os.path.join(os.path.dirname(helpers.SWARM), "bin", "pai.py")


class Mock(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        type(self).seen.append(body)
        ans = {q: {"noul": 0.2 if "all tests pass" in body["state"] and q == "evidence" else 0.9}
               for q, spec in body["questions"].items() if spec["type"] == "noul"}
        data = json.dumps({"answers": ans, "usage": {"input_tokens": 480, "output_tokens": 12}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Usage(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.tmp = tempfile.mkdtemp(prefix="pai-jev-usage-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        self.log = os.path.join(self.tmp, "jev-usage.jsonl")
        Mock.seen = []
        env = {"TYPESAFE_API_KEY": "fake-key-for-the-mock", "PAI_JEV_CREDENTIAL_STORE": "none",
               "TYPESAFE_BASE_URL": "http://127.0.0.1:%d" % self.srv.server_address[1], "PAI_JEV_USAGE_LOG": self.log}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def test_verify_asks_for_evidence_and_the_task_s_own_checks(self):
        v = jev.verify("Rename", "Check with grep -c that make_slug is used.", "Done. all tests pass")
        self.assertEqual(set(v), {"done", "checked", "evidence"})
        self.assertEqual(v["evidence"], 0.2)
        q = Mock.seen[-1]["questions"]
        self.assertIn("exact commands it ran", q["evidence"]["instructions"])
        self.assertIn("the task itself asks for", q["checked"]["instructions"])
        sig = jev.signal("verify", "t", "b", "r")
        self.assertEqual(sig["state"], "ok")
        self.assertIn("evidence", sig)

    def test_every_reply_is_logged_with_numbers_only_and_summed(self):
        jev.triage("Rename slugify", "Rename it in utils/text.py.")
        jev.verify("t", "b", "r")
        with open(self.log, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f]
        self.assertEqual([r["kind"] for r in rows], ["triage", "verify"])
        self.assertEqual((rows[0]["input_tokens"], rows[0]["output_tokens"], rows[0]["http"]), (480, 12, 200))
        with open(self.log, encoding="utf-8") as f:
            raw = f.read()
        for secret in ("fake-key-for-the-mock", "slugify", "utils/text.py"):
            self.assertNotIn(secret, raw, "no key and no content in the usage log")
        self.assertEqual(jev.last_usage, {"input_tokens": 480, "output_tokens": 12})
        s = jev.usage_summary(self.log)
        self.assertEqual((s["requests"], s["input_tokens"], s["output_tokens"]), (2, 960, 24))
        self.assertEqual(s["by_kind"]["verify"]["requests"], 1)
        with open(self.log, "a") as f:
            f.write("{torn\n")
        self.assertEqual(jev.usage_summary(self.log)["unreadable"], 1)
        self.assertEqual(jev.usage_summary(self.log, since="2999-01-01")["requests"], 0)

    def test_pai_jev_usage_command(self):
        jev.verify("t", "b", "r")
        env = helpers.hermetic_env(PAI_JEV_USAGE_LOG=self.log, PAI_SWARM_HOME=self.tmp)
        p = subprocess.run([sys.executable, "-B", PAI, "jev", "usage"], capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("requests   1", p.stdout)
        self.assertIn("tokens     480 input, 12 output", p.stdout)
        p = subprocess.run([sys.executable, "-B", PAI, "jev", "usage", "--json", "--log", self.log], capture_output=True,
                           text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(json.loads(p.stdout)["by_kind"]["verify"]["input_tokens"], 480)

    def test_an_unwritable_log_never_breaks_a_call(self):
        blocker = os.path.join(self.tmp, "a-file")
        open(blocker, "w").close()
        with mock.patch.dict(os.environ, {"PAI_JEV_USAGE_LOG": os.path.join(blocker, "sub", "log.jsonl")}):
            self.assertEqual(jev.verify("t", "b", "r")["done"], 0.9)


class Ledger(unittest.TestCase):
    """The query API for daily logs (R6-COR-070) and agent cost lines (R6-COR-071)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pai-ledger-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        self.log = os.path.join(self.tmp, "ledger.jsonl")
        rows = [{"at": "2026-11-21T23:59:59Z", "kind": "verify", "input_tokens": 400, "output_tokens": 10},
                {"at": "2026-11-22T08:00:00Z", "kind": "verify", "input_tokens": 480, "output_tokens": 12},
                {"at": "2026-11-22T09:00:00Z", "kind": "agent", "cost_usd": 0.0038, "input_tokens": 9000},
                {"at": "2026-11-23T00:00:00Z", "kind": "check", "input_tokens": 477, "output_tokens": 59}]
        with open(self.log, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))

    def test_one_day(self):
        s = jev.usage_day("2026-11-22", self.log)
        self.assertEqual((s["requests"], s["input_tokens"], s["cost_usd"]), (2, 9480, 0.0038))
        self.assertEqual(sorted(s["by_kind"]), ["agent", "verify"])
        md = jev.usage_markdown(s)
        self.assertIn("| agent | 1 | 9000 | 0 | 0.0038 |", md)
        self.assertIn("| total | 2 | 9480 | 12 | 0.0038 |", md)
        with self.assertRaises(ValueError):
            jev.usage_day("22/11/2026", self.log)

    def test_range_and_lines(self):
        rows, bad = jev.usage_lines(self.log, since="2026-11-22", until="2026-11-23")
        self.assertEqual(([r["kind"] for r in rows], bad), (["verify", "agent"], 0))
        self.assertEqual(jev.usage_summary(self.log)["requests"], 4)

    def test_agent_runs_are_recorded(self):
        with mock.patch.dict(os.environ, {"PAI_JEV_USAGE_LOG": self.log}):
            jev.record_usage("agent", {"cost_usd": 0.12, "input_tokens": 10}, 3.5, state="done", model="haiku")
        last = jev.usage_lines(self.log)[0][-1]
        self.assertEqual((last["kind"], last["cost_usd"], last["model"], last["state"]), ("agent", 0.12, "haiku", "done"))


if __name__ == "__main__":
    unittest.main()
