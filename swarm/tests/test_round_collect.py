"""pai round collect / budget / route against a mock JEV endpoint (never the real one): SHA256SUMS,
Items vs the queue at checkpoint time, said-vs-shown DISAGREES, usage logging without the key."""
import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SWARM = os.path.dirname(HERE)
ROOT = os.path.dirname(SWARM)
sys.path.insert(0, SWARM)
sys.path.insert(0, ROOT)
import round as rnd  # noqa: E402
import round_collect as rc_  # noqa: E402

KEY = "test-key-not-real-0123456789"


class MockJev(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/systemone"
        assert self.headers["Authorization"] == "Bearer " + KEY
        MockJev.seen.append(body)
        state = body["state"]
        claims = 0.9 if "done" in state else 0.1
        evidence = 0.9 if "exit 0" in state else 0.1
        answers = {}
        for k, spec in body["questions"].items():
            if spec["type"] == "noul":
                answers[k] = {"noul": {"claims": claims, "evidence": evidence}.get(k, 0.5)}
            else:
                answers[k] = {"choice": "sonnet", "confidence": 0.7}
        out = json.dumps({"answers": answers, "usage": {"input_tokens": 480, "output_tokens": 12}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def ns(**kw):
    base = {"jev": False, "json": False, "write": False, "remaining": None, "per_item": None, "add": None, "note": None,
            "kind": None, "text": None}
    base.update(kw)
    return argparse.Namespace(**base)


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), MockJev)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.usage = os.path.join(self.tmp, "jev-usage.jsonl")
        env = {"TYPESAFE_API_KEY": KEY, "TYPESAFE_BASE_URL": "http://127.0.0.1:%d" % self.srv.server_address[1],
               "PAI_ROUND_JEV_USAGE": self.usage, "PAI_ROUND_TMUX_SOCKET": os.path.join(self.tmp, "none"),
               "PAI_ROUND_CONFIG": ""}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        MockJev.seen = []
        vault = os.path.join(self.tmp, "vault")
        sd = os.path.join(vault, "Agent Inbox", "R9")
        lanes = []
        for n in ("good", "claims", "tampered", "empty"):
            lanes.append({"name": n, "worktree": os.path.join(self.tmp, n), "tmux_target": "r9:" + n,
                          "session_name": n, "handoff_dir": os.path.join(sd, n), "work_dir": os.path.join(self.tmp, "work", n)})
            os.makedirs(lanes[-1]["work_dir"])
        self.r = {"format": "pai-round", "version": 1, "round": "R9", "project": "w", "base_sha": "a" * 40, "vault": vault,
                  "state_dir": sd, "work_root": os.path.join(self.tmp, "work"), "lanes": lanes, "_path": os.path.join(sd, "ROUND.json")}
        os.makedirs(sd, exist_ok=True)
        self.queue("good")
        self.cp("good", "## Items\n| X-001 | done | a |\n| X-002 | done | b |\nTotals: done 2, prototype 0, blocked 0, owner-only 0, todo 1\n"
                        "## Evidence\n`python3 -B -m unittest` -> exit 0, sha256 %s\n" % ("e" * 64), checkpoint=True)
        self.cp("claims", "All items done and all tests pass.\n")
        self.cp("tampered", "done; exit 0\nTotals: done 0\n")
        with open(os.path.join(sd, "tampered", "cp-A", "HANDOFF.md"), "a") as f:
            f.write("edited after sealing\n")

    def pai(self, *args):
        subprocess.run([sys.executable, "-B", os.path.join(ROOT, "bin", "pai.py")] + list(args), check=True, capture_output=True)

    def queue(self, lane):
        q = os.path.join(self.tmp, "work", lane, "queue")
        items = os.path.join(self.tmp, "items.txt")
        with open(items, "w") as f:
            f.write("X-001. one\nX-002. two\nX-003. three\n")
        self.pai("queue", "init", q, "--from", items, "--write")
        for i in ("X-001", "X-002"):
            self.pai("queue", "mark", q, i, "done", "--evidence", "e")

    def cp(self, lane, text, checkpoint=False):
        d = os.path.join(self.r["state_dir"], lane, "cp-A")
        os.makedirs(d)
        with open(os.path.join(d, "HANDOFF.md"), "w") as f:
            f.write(text)
        with open(os.path.join(d, "HANDOFF.md"), "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(os.path.join(d, "SHA256SUMS"), "w") as f:
            f.write("%s  HANDOFF.md\n" % digest)
        if checkpoint:
            q = os.path.join(self.tmp, "work", lane, "queue")
            self.pai("queue", "checkpoint", q, "X-002", "--handoff", os.path.join(d, "HANDOFF.md"))
            self.pai("queue", "mark", q, "X-003", "done", "--evidence", "later")  # the queue moves on

    def run_collect(self, **kw):
        out = io.StringIO()
        code = rc_.collect_main(ns(**kw), self.r, out)
        return code, out.getvalue()

    def test_deterministic_checks(self):
        res = {x["lane"]: x for x in (rc_.collect_lane(self.r, ln, False) for ln in self.r["lanes"])}
        self.assertEqual(res["good"]["sums"], "ok")
        self.assertEqual(res["good"]["items"], "ok")  # against the queue at checkpoint time, not now
        items = os.path.join(self.tmp, "more.txt")
        with open(items, "w") as f:
            f.write("X-004. added after the checkpoint\n")
        self.pai("queue", "add", os.path.join(self.tmp, "work", "good", "queue"), "--from", items, "--write")
        self.assertEqual(rc_.collect_lane(self.r, self.r["lanes"][0], False)["items"], "ok")
        self.assertEqual(res["good"]["evidence_sha_rows"], 1)
        self.assertEqual(res["tampered"]["sums"], "MISMATCH")
        self.assertEqual(res["tampered"]["sums_bad"], ["HANDOFF.md"])
        self.assertIn("no Totals line", res["claims"]["note"])
        self.assertEqual(res["empty"]["note"], "no checkpoint yet")
        self.assertEqual(MockJev.seen, [])  # no --jev, no request

    def test_jev_flags_claims_without_evidence(self):
        code, out = self.run_collect(jev=True, write=True)
        self.assertEqual(code, 1)
        with open(os.path.join(self.r["state_dir"], "COLLECT.json")) as f:
            data = json.load(f)
        v = {x["lane"]: x["jev_verdict"] for x in data["lanes"]}
        self.assertTrue(v["good"].startswith("agrees"))
        self.assertEqual(v["claims"], "DISAGREES")
        self.assertEqual(v["tampered"], "DISAGREES")  # says done with exit 0, but its sums do not match
        self.assertEqual(len(MockJev.seen), 3)
        with open(self.usage) as f:
            lines = [json.loads(l) for l in f]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0]["input_tokens"], 480)
        self.assertEqual(lines[0]["endpoint"], "mock")
        with open(self.usage) as f:
            self.assertNotIn(KEY, f.read())
        self.assertNotIn(KEY, out)
        with open(os.path.join(self.r["state_dir"], "BOARD.md"), encoding="utf-8") as f:
            board = f.read()
        self.assertIn("交接核对", board)
        self.assertIn("DISAGREES", board)

    def test_without_a_key_the_jev_part_is_an_error_not_a_verdict(self):
        os.environ["TYPESAFE_API_KEY"] = ""
        import jev
        old = jev.store_get
        jev.store_get = lambda: None
        self.addCleanup(setattr, jev, "store_get", old)
        res = rc_.collect_lane(self.r, self.r["lanes"][0], True)
        self.assertEqual(res["jev_verdict"], "error")
        self.assertIn("no JEV key", res["note"])
        self.assertFalse(os.path.exists(self.usage))


class Budget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["PAI_ROUND_JEV_USAGE"] = os.path.join(self.tmp, "round-jev.jsonl")
        self.addCleanup(os.environ.pop, "PAI_ROUND_JEV_USAGE", None)
        lanes = [{"name": n, "work_dir": os.path.join(self.tmp, n)} for n in ("a", "b")]
        for ln in lanes:
            os.makedirs(ln["work_dir"])
        with open(os.path.join(lanes[0]["work_dir"], "usage.jsonl"), "w") as f:
            f.write(json.dumps({"total_cost_usd": 0.5}) + "\n" + json.dumps({"cost_usd": 0.25}) + "\nnot json\n")
        with open(os.path.join(lanes[0]["work_dir"], "jev-usage.jsonl"), "w") as f:
            f.write(json.dumps({"input_tokens": 480, "output_tokens": 20}) + "\n")
        q = os.path.join(lanes[0]["work_dir"], "queue")
        with open(os.path.join(self.tmp, "items"), "w") as f:
            f.write("Y-001. a\nY-002. b\nY-003. c\nY-004. d\n")
        run = lambda *a: subprocess.run([sys.executable, "-B", os.path.join(ROOT, "bin", "pai.py")] + list(a),  # noqa: E731
                                        check=True, capture_output=True)
        run("queue", "init", q, "--from", os.path.join(self.tmp, "items"), "--write")
        run("queue", "mark", q, "Y-001", "done", "--evidence", "x")
        self.r = {"round": "R9", "work_root": self.tmp, "lanes": lanes}

    def test_measured_cost_per_item_and_next_batch(self):
        out = io.StringIO()
        rc_.budget_main(ns(add=["b", "1.25"], note="RC usage page", remaining=4.0, json=True), self.r, out, cfg={})
        data = json.loads(out.getvalue().split("\n", 1)[1])
        a, b = data["lanes"]
        self.assertAlmostEqual(a["claude_p_usd"], 0.75)
        self.assertEqual((a["jev_requests"], a["jev_tokens"]), (1, 500))
        self.assertAlmostEqual(b["rc_usd"], 1.25)
        self.assertEqual(data["items_done"], 1)
        self.assertEqual(data["per_item_source"], "measured")
        self.assertAlmostEqual(data["usd_per_item"], 2.0, places=3)
        self.assertEqual(data["suggested_items"], 2)  # 4.0 / 2.0, and not more than what is left (3)
        out = io.StringIO()
        rc_.budget_main(ns(remaining=100.0, json=True), self.r, out, cfg={})
        self.assertEqual(json.loads(out.getvalue())["suggested_items"], 3)  # capped by the items left

    def test_default_per_item_without_measurements(self):
        r = {"round": "R9", "work_root": self.tmp, "lanes": [{"name": "b", "work_dir": os.path.join(self.tmp, "b")}]}
        out = io.StringIO()
        rc_.budget_main(ns(remaining=2.0), r, out, cfg={})
        self.assertIn("default 0.5", out.getvalue())


class Route(unittest.TestCase):
    def test_defaults_and_overrides(self):
        out = io.StringIO()
        rc_.route_main(ns(kind="feature"), {}, out)
        self.assertEqual(out.getvalue().strip(), "feature: worker, model sonnet, effort medium")
        out = io.StringIO()
        rc_.route_main(ns(kind="feature"), {"routing": {"kinds": {"feature": {"model": "opus"}, "docs": {"model": "haiku"}}}}, out)
        self.assertIn("model opus, effort medium", out.getvalue())
        with self.assertRaises(rnd.RoundError):
            rc_.route_main(ns(kind="nope"), {}, io.StringIO())
        with self.assertRaises(rnd.RoundError):
            rc_.routing({"routing": {"kinds": {"x": "opus"}}})

    def test_jev_tier_is_only_a_suggestion(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), MockJev)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        env = {"TYPESAFE_API_KEY": KEY, "TYPESAFE_BASE_URL": "http://127.0.0.1:%d" % srv.server_address[1],
               "PAI_ROUND_JEV_USAGE": os.path.join(tmp, "u.jsonl")}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        out = io.StringIO()
        rc_.route_main(ns(kind="design", jev=True, text="Rename a helper in one file"), {}, out)
        self.assertIn("design: mastermind or worker, model opus", out.getvalue())
        self.assertIn("JEV suggests sonnet", out.getvalue())
        self.assertTrue(os.path.isfile(env["PAI_ROUND_JEV_USAGE"]))


if __name__ == "__main__":
    unittest.main()
