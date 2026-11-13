"""Tiny stand-in for POST /v1/systemone, used by run_tests.sh. Validates the request shape."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = sys.argv[2]


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/systemone", self.path
        assert self.headers["Authorization"] == "Bearer test"
        assert isinstance(body["state"], str) and body["questions"] and body["model"]
        with open(LOG, "a") as f:
            f.write(json.dumps(body) + "\n")
        q = body["questions"]
        vague = "inbox-vague" in body["state"]
        answers = {}
        for k, spec in q.items():
            if spec["type"] == "noul":
                assert "instructions" in spec
                answers[k] = {"type": "noul", "noul": 0.1 if vague else 0.91}
            elif spec["type"] == "choice":
                assert isinstance(spec["criteria"], dict)
                answers[k] = {"type": "choice", "choice": "opus", "confidence": 0.8,
                              "probabilities": {"haiku": 0.05, "sonnet": 0.15, "opus": 0.8}}
        out = json.dumps({"model": "jev-latest", "answers": answers,
                          "usage": {"input_tokens": 100, "output_tokens": 3}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
