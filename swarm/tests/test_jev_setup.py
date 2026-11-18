"""Optional JEV setup in `pai init` and `pai jev check`, against a local fake endpoint and a
fake credential store only. No real TypeSafe request is made here: the one live request is
left to the operator (`pai jev check`), who types the key into their own terminal."""
import http.server
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
from test_workbench import WB, PAI, needs_311  # noqa: E402

KEY = "tsk-fake-0123456789abcdef"


class Endpoint(http.server.BaseHTTPRequestHandler):
    mode = "ok"
    requests = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                    "body": json.loads(body or b"{}")})
        mode = type(self).mode
        if mode == "slow":
            time.sleep(3)
        code, reply = {"ok": (200, {"answers": {"actionable": {"noul": 0.93}, "tier": {"choice": "haiku", "confidence": 0.8}},
                                    "usage": {"questions": 2, "input_tokens": 120}}),
                       "bad-key": (401, {"error": "invalid api key " + KEY}),
                       "limit": (429, {"error": "rate limited"}),
                       "malformed": (200, {"answers": {"something": "else"}}),
                       "slow": (200, {})}[mode]
        data = json.dumps(reply).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@needs_311
@helpers.needs_tmux
class JevSetup(WB):
    def setUp(self):
        super().setUp()
        Endpoint.requests, Endpoint.mode = [], "ok"
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.secret = os.path.join(self.tmp, "secret-store")
        tool = os.path.join(self.bin, "secret-tool")
        with open(tool, "w") as f:
            f.write("#!%s\nimport os, sys\np = %r\nif sys.argv[1] == 'store':\n    open(p, 'w').write(sys.stdin.read())\n"
                    "    open(p + '.argv', 'w').write(' '.join(sys.argv))\nelif sys.argv[1] == 'lookup':\n"
                    "    sys.exit(0 if os.path.exists(p) and print(open(p).read()) is None else 1)\n" % (sys.executable, self.secret))
        os.chmod(tool, 0o755)
        self.env.update(TYPESAFE_BASE_URL="http://127.0.0.1:%d" % self.server.server_port, PAI_SECRET_TOOL=tool,
                        JEV_TIMEOUT="1")
        self.env.pop("PAI_JEV_CREDENTIAL_STORE", None)

    def init_tty(self, key_bytes, send=b"y"):
        from test_tty import Terminal, ENTER
        t = Terminal(self, ["init", "--worktree-root", self.root], self.env, cli=PAI, cwd=self.repo)
        t.expect("Default agent [not set yet]: ")
        t.send(b"3" + ENTER)
        for prompt, answer in (("Remote [origin]: ", b""), ("Base branch [main]: ", b""),
                               ("'-' for none) [", b"-"), ("[-]: ", b""), (".toml? [", b"y")):  # [Y/n] new, [y/N] update
            t.expect(prompt)
            t.send(answer + ENTER)
        t.expect("Enter JEV API key (optional): ")
        t.send(key_bytes + ENTER)
        if key_bytes and send is not None:
            t.expect("Send it? [y/N] [n]: ")
            t.send(send + ENTER)
        code = t.wait()
        return code, t.text

    def everything_written(self):
        out = []
        for root in (self.env["PAI_WORKBENCH_HOME"], self.home, self.root, self.vault):
            for d, _, files in os.walk(root):
                for fn in files:
                    with open(os.path.join(d, fn), "rb") as f:
                        out.append(f.read())
        return b"\n".join(out)

    def test_a_key_is_typed_hidden_stored_outside_pai_and_checked_with_one_request(self):
        code, text = self.init_tty(KEY.encode())
        self.assertEqual(code, 0, text)
        self.assertNotIn(KEY, text, "the key was neither echoed nor printed")
        self.assertIn("ONE real request", text)
        self.assertIn("It may be billed", text)
        self.assertIn("JEV check: ok", text)
        self.assertIn('usage reported by the API: {"questions": 2, "input_tokens": 120}', text)
        self.assertEqual(open(self.secret).read(), KEY, "the credential store got it, on stdin")
        self.assertNotIn(KEY, open(self.secret + ".argv").read(), "never on a command line")
        self.assertNotIn(KEY.encode(), self.everything_written(), "not in pai config, state, worktrees or notes")
        self.assertEqual(len(Endpoint.requests), 1)
        r = Endpoint.requests[0]
        self.assertEqual((r["path"], r["auth"]), ("/v1/systemone", "Bearer " + KEY))
        self.assertIn("Rename the function slugify", r["body"]["state"])
        self.assertNotIn(self.repo, json.dumps(r["body"]), "no repository content or paths")
        # Later invocations find it in the store; `pai jev status` never prints it.
        p = self.pai("jev", "status")
        self.assertIn("key available from secret-tool", p.stdout)
        self.assertNotIn(KEY, p.stdout + p.stderr)

    def test_enter_skips_and_keeps_existing_credentials(self):
        code, text = self.init_tty(b"")
        self.assertEqual(code, 0, text)
        self.assertIn("JEV stays off; PAI works without it", text)
        self.assertFalse(os.path.exists(self.secret))
        with open(self.secret, "w") as f:
            f.write("existing-key")
        code, text = self.init_tty(b"")  # a second init (now updating) that skips
        self.assertIn("Your existing key (secret-tool) is kept as it is", text)
        self.assertEqual(open(self.secret).read(), "existing-key")
        self.assertEqual(Endpoint.requests, [])

    def test_failures_are_told_apart_never_retried_and_leave_pai_usable(self):
        for mode, label in (("bad-key", "INVALID KEY"), ("limit", "RATE LIMITED"), ("slow", "TIMEOUT"),
                            ("malformed", "UNEXPECTED REPLY")):
            Endpoint.requests, Endpoint.mode = [], mode
            p = self.pai("jev", "check", "--yes", env={"TYPESAFE_API_KEY": KEY})
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("JEV check: %s" % label, p.stdout)
            self.assertIn("Not retried", p.stdout)
            self.assertNotIn(KEY, p.stdout + p.stderr, "the key is redacted even when the server echoes it")
            self.assertEqual(len(Endpoint.requests), 1, mode)
        # JEV off or failing never blocks the workflow
        self.assert_ok(self.pai("init", "--no-input", "--default-agent", "codex", env={"TYPESAFE_API_KEY": KEY}))

    def test_non_interactive_runs_never_prompt_or_send(self):
        p = self.pai("init", "--no-input", "--default-agent", "codex")
        self.assertNotIn("JEV API key", p.stdout + p.stderr)
        p = self.pai("jev", "check", env={"TYPESAFE_API_KEY": KEY})
        self.assertEqual(p.returncode, 2)
        self.assertIn("add --yes to send it. Nothing was sent.", p.stdout)
        p = self.pai("jev", "check", "--yes")
        self.assertEqual(p.returncode, 1)
        self.assertIn("no key", p.stdout)
        self.assertEqual(Endpoint.requests, [])
        self.assertEqual(self.pai("jev", "status", env={"TYPESAFE_API_KEY": KEY}).stdout.strip(),
                         "JEV: key available from TYPESAFE_API_KEY")


if __name__ == "__main__":
    unittest.main()
