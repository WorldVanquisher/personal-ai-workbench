"""Optional TypeSafe Jev calls. Standard library only.

Jev is a judgment model: it returns typed answers with probabilities, never prose, so it is a
cheap way to make yes/no and pick-one decisions that would otherwise burn Claude tokens.
pai-swarm uses it for two decisions:

  triage  - inbox note -> is it a ready-to-run coding task? which Claude model should run it?
  verify  - worker report -> did the worker actually finish, and did it run checks?

The key comes from TYPESAFE_API_KEY, else from the operator's credential store when `pai init`
put it there (libsecret's `secret-tool` on Linux, the macOS Keychain via `security`); never
from a file pai writes, a manifest, a note or a command line. Optional: TYPESAFE_BASE_URL
(default https://api.typesafe.ai), JEV_MODEL (default jev-latest).
"""
import json
import os
import re
import shutil
import socket
import subprocess
import urllib.error
import urllib.request

STORE_SERVICE, STORE_ACCOUNT = "pai-jev", "default"
_store_cache = {}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+")
SESSION_RE = re.compile(r"(?i)(?:https?://)?(?:www\.)?claude\.ai/\S+")
URL_AUTH_RE = re.compile(r"(://)[^/@\s]+@")


def store_kind():
    """secret-tool | keychain | None: a credential store pai can use here. PAI_JEV_CREDENTIAL_STORE=none
    turns it off (the tests do)."""
    if os.environ.get("PAI_JEV_CREDENTIAL_STORE") == "none":
        return None
    if shutil.which(os.environ.get("PAI_SECRET_TOOL") or "secret-tool"):
        return "secret-tool"
    if shutil.which("security") and os.uname().sysname == "Darwin":
        return "keychain"
    return None


def _store_run(argv, stdin=None):
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def store_get():
    kind = store_kind()
    if kind is None:
        return None
    if kind not in _store_cache:
        if kind == "secret-tool":
            code, out = _store_run([os.environ.get("PAI_SECRET_TOOL") or "secret-tool", "lookup",
                                    "service", STORE_SERVICE, "account", STORE_ACCOUNT])
        else:
            code, out = _store_run(["security", "find-generic-password", "-s", STORE_SERVICE, "-a", STORE_ACCOUNT, "-w"])
        _store_cache[kind] = out.strip() if code == 0 and out.strip() else None
    return _store_cache[kind]


def store_put(key):
    """Save the key in the credential store, passing it on stdin (never on a command line).
    Returns the store's name, or None when there is no usable store."""
    kind = store_kind()
    if kind == "secret-tool":
        code, _ = _store_run([os.environ.get("PAI_SECRET_TOOL") or "secret-tool", "store", "--label", "pai JEV API key",
                              "service", STORE_SERVICE, "account", STORE_ACCOUNT], stdin=key)
    elif kind == "keychain":
        line = 'add-generic-password -U -s %s -a %s -w "%s"\n' % (STORE_SERVICE, STORE_ACCOUNT, key.replace('"', '\\"'))
        code, _ = _store_run(["security", "-i"], stdin=line)
    else:
        return None
    _store_cache.clear()
    return kind if code == 0 else None


def api_key():
    """(key, where) with where env | secret-tool | keychain; (None, None) when JEV is not set up."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"], "env"
    k = store_get()
    return (k, store_kind()) if k else (None, None)


def available():
    return bool(api_key()[0])


def _redact(s, limit=4000):
    s = SESSION_RE.sub("<claude-link>", s)
    s = URL_AUTH_RE.sub(r"\1", s)
    s = EMAIL_RE.sub("<email>", s)
    s = SECRET_RE.sub(r"\1=<redacted>", s)
    return s[:limit]


def ask(state, questions, timeout=None):
    """POST /v1/systemone. Returns the `answers` dict keyed by question id."""
    timeout = timeout or float(os.environ.get("JEV_TIMEOUT") or 30)
    key = api_key()[0]
    if not key:
        raise RuntimeError("no JEV API key (TYPESAFE_API_KEY or the credential store)")
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


SMOKE_NOTE = ("Rename the function slugify to make_slug in utils/text.py",
              "Rename slugify() to make_slug() in utils/text.py and update its two callers in app/views.py. "
              "The tests in tests/test_text.py must still pass.")


def smoke(key, timeout=None):
    """Exactly one request: the fixed synthetic SMOKE_NOTE through the triage questions, with
    `key` in the normal Authorization header. Never retried. Returns
    {state: ok|invalid-key|rate-limited|timeout|unexpected|error, detail, http, usage, answers}."""
    timeout = timeout or float(os.environ.get("JEV_TIMEOUT") or 30)
    base = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
    body = {"model": os.environ.get("JEV_MODEL", "jev-latest"), "state": "TITLE: %s\n\n%s" % SMOKE_NOTE,
            "questions": TRIAGE_QUESTIONS}
    req = urllib.request.Request(base + "/v1/systemone", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    res = {"state": "error", "detail": "", "http": None, "usage": None, "answers": None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res["http"] = r.status
            data = json.load(r)
    except urllib.error.HTTPError as e:
        res["http"] = e.code
        text = _redact(e.read()[:300].decode("utf-8", "replace"), 300).replace(key, "<key>")
        res["state"] = {401: "invalid-key", 403: "invalid-key", 429: "rate-limited"}.get(e.code, "error")
        res["detail"] = "HTTP %s: %s" % (e.code, text)
        return res
    except (socket.timeout, TimeoutError):
        return dict(res, state="timeout", detail="no answer within %ss" % timeout)
    except urllib.error.URLError as e:
        slow = isinstance(e.reason, (socket.timeout, TimeoutError)) or "timed out" in str(e.reason)
        return dict(res, state="timeout" if slow else "error", detail=_redact(str(e.reason), 200))
    except ValueError:
        return dict(res, state="unexpected", detail="the reply is not JSON")
    usage = data.get("usage") if isinstance(data, dict) else None
    res["usage"] = {k: v for k, v in usage.items() if isinstance(v, (int, float))} if isinstance(usage, dict) else None
    ans = data.get("answers") if isinstance(data, dict) else None
    act = (ans or {}).get("actionable") or {}
    tier = (ans or {}).get("tier") or {}
    ok = isinstance(act.get("noul"), (int, float)) and 0 <= float(act["noul"]) <= 1 and tier.get("choice") in \
        TRIAGE_QUESTIONS["tier"]["criteria"]
    if not ok:
        return dict(res, state="unexpected", detail="authenticated, but the reply does not have the expected answers "
                    "(actionable probability, tier choice)")
    res["answers"] = {"actionable": float(act["noul"]), "tier": tier["choice"]}
    return dict(res, state="ok", detail="authenticated; reply has the expected schema")


def signal(kind, *args):
    """Jev as an optional signal: {"state": ok|skipped|timeout|malformed|error, "detail", ...scores}.

    Never raises and never decides anything: callers keep their deterministic result and
    report this next to it. `kind` is "triage" (title, body) or "verify" (title, body, report).
    A missing key is `skipped`; a slow endpoint is `timeout`; a reply without usable
    probabilities is `malformed`; anything else is `error`."""
    if not available():
        return {"state": "skipped", "detail": "TYPESAFE_API_KEY is not set" + (
            " and the credential store has no key" if store_kind() else "")}
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
