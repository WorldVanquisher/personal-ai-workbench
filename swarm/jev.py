"""Optional TypeSafe Jev calls. Standard library only.

Jev is a judgment model: it returns typed answers with probabilities, never prose, so it is a
cheap way to make yes/no and pick-one decisions that would otherwise burn Claude tokens.
pai-swarm uses it for two decisions:

  triage  - inbox note -> is it a ready-to-run coding task? which Claude model should run it?
  verify  - worker report -> did the worker actually finish, and did it run checks?

Needs TYPESAFE_API_KEY. Optional: TYPESAFE_BASE_URL (default https://api.typesafe.ai),
JEV_MODEL (default jev-latest).
"""
import json
import os
import re
import socket
import urllib.error
import urllib.request

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+")
SESSION_RE = re.compile(r"(?i)(?:https?://)?(?:www\.)?claude\.ai/\S+")
URL_AUTH_RE = re.compile(r"(://)[^/@\s]+@")


def available():
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def _redact(s, limit=4000):
    s = SESSION_RE.sub("<claude-link>", s)
    s = URL_AUTH_RE.sub(r"\1", s)
    s = EMAIL_RE.sub("<email>", s)
    s = SECRET_RE.sub(r"\1=<redacted>", s)
    return s[:limit]


def ask(state, questions, timeout=None):
    """POST /v1/systemone. Returns the `answers` dict keyed by question id."""
    timeout = timeout or float(os.environ.get("JEV_TIMEOUT") or 30)
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is not set")
    base = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
    body = {
        "model": os.environ.get("JEV_MODEL", "jev-latest"),
        "state": _redact(state),
        "questions": questions,
    }
    req = urllib.request.Request(
        base + "/v1/systemone",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError("Jev HTTP %s: %s" % (e.code, e.read()[:300].decode("utf-8", "replace")))
    return data.get("answers", {})


def _noul(ans, key):
    a = ans.get(key) or {}
    v = a.get("noul")
    return float(v) if v is not None else None


# Specific wording matters: Jev reads questions literally, and vague questions give unstable
# scores. Each instruction names what a "yes" looks like.
TRIAGE_QUESTIONS = {
    "actionable": {
        "type": "noul",
        "instructions": "The note asks for a specific change to code or files in a software "
                        "repository, and says what the result should be, so a developer could "
                        "start without asking any questions.",
    },
    "tier": {
        "type": "choice",
        "instructions": "How much reasoning the coding task needs.",
        "criteria": {
            "haiku": "Mechanical: rename, typo, formatting, add a docstring, bump a constant, "
                     "one obvious edit in one file.",
            "sonnet": "Normal feature or bug fix touching a few files, with tests to add or run.",
            "opus": "Design decisions, a bug with unknown cause, a refactor across many modules, "
                    "or anything security or concurrency related.",
        },
    },
}

VERIFY_QUESTIONS = {
    "done": {
        "type": "noul",
        "instructions": "The worker report says the requested change was made and lists at "
                        "least one changed file. A report that says BLOCKED or PARTIAL is a no.",
    },
    "checked": {
        "type": "noul",
        "instructions": "The worker report says tests, a build, or a linter were run and that "
                        "they passed.",
    },
}


def triage(title, body):
    ans = ask("TITLE: %s\n\n%s" % (title, body), TRIAGE_QUESTIONS)
    tier = ans.get("tier") or {}
    return {
        "actionable": _noul(ans, "actionable"),
        "tier": tier.get("choice"),
        "tier_confidence": tier.get("confidence"),
    }


def verify(task_title, task_body, report):
    state = "TASK: %s\n%s\n\nWORKER REPORT:\n%s" % (task_title, task_body[:1500], report[-2500:])
    ans = ask(state, VERIFY_QUESTIONS)
    return {"done": _noul(ans, "done"), "checked": _noul(ans, "checked")}


def signal(kind, *args):
    """Jev as an optional signal: {"state": ok|skipped|timeout|malformed|error, "detail", ...scores}.

    Never raises and never decides anything: callers keep their deterministic result and
    report this next to it. `kind` is "triage" (title, body) or "verify" (title, body, report).
    A missing key is `skipped`; a slow endpoint is `timeout`; a reply without usable
    probabilities is `malformed`; anything else is `error`."""
    if not available():
        return {"state": "skipped", "detail": "TYPESAFE_API_KEY is not set"}
    need = ("actionable",) if kind == "triage" else ("done", "checked")
    try:
        v = (triage if kind == "triage" else verify)(*args)
    except (socket.timeout, TimeoutError):
        return {"state": "timeout", "detail": "no answer in time"}
    except urllib.error.URLError as e:
        slow = isinstance(e.reason, (socket.timeout, TimeoutError)) or "timed out" in str(e.reason)
        return {"state": "timeout" if slow else "error", "detail": _redact(str(e.reason), 200)}
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        return {"state": "malformed", "detail": "unusable reply (%s)" % type(e).__name__}
    except Exception as e:  # noqa: BLE001  a signal must never break the workflow
        return {"state": "error", "detail": _redact(str(e), 200)}
    if not isinstance(v, dict) or any(not isinstance(v.get(k), float) or not 0.0 <= v[k] <= 1.0 for k in need):
        return {"state": "malformed", "detail": "reply has no probability for %s" % ", ".join(need)}
    return dict(v, state="ok", detail="")
