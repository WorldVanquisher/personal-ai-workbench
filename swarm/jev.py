"""Optional TypeSafe Jev calls. Standard library only.

Jev is a judgment model: it returns typed answers with probabilities, never prose, so it is a
cheap way to make yes/no and pick-one decisions that would otherwise burn Claude tokens.
It is always a second opinion: every caller keeps its own deterministic result, and a missing key,
a timeout or an odd reply changes nothing. Two question sets:

  triage  - inbox note -> is it a ready-to-run coding task? which Claude model should run it?
  verify  - worker report -> did it finish, did it run the checks the task asked for, and does it
            list the commands with their exit codes (evidence)?

Every call site (R6-COR-031):
  swarm/pai_swarm.py  cmd_triage       `pai_swarm.py triage`: actionable >= 0.7 -> todo (only then a model:
                                       tier confidence >= 0.6, R6-COR-032); < 0.3 -> needs-detail
  swarm/pai_swarm.py  run_task/report  `pai_swarm.py run --jev-verify`: done < 0.5 moves done -> review
                                       (the only place a score changes a state)
  swarm/rc.py         jev_intake       `pai run --jev-triage`: a note line only, never a gate
  swarm/review.py     check/open       `pai pr check|open --jev-verify`: second opinion next to the checks
  swarm/agentsmd.py   proposals        `pai worktree init --jev`: ranks the rule library for a goal
                                       (ask() directly; the AGENTS.md section records source: jev only)
  swarm/workbench.py  cmd_jev          `pai jev status|check|usage`; `pai init` setup (smoke(), one request)
  pai/handoff.py      jev_verify       `pai handoff seal --jev-verify`: agree / DISAGREES against the
                                       recorded evidence, written into SEAL.json, never a gate (R6-COR-035)
Outside this repository's core: the prompts lane's `pai prompt ... --jev` calls ask() through the workbench.

Usage: every reply's `usage` (tokens, no content) is appended to a JSON-lines log, PAI_JEV_USAGE_LOG or
$PAI_SWARM_HOME/jev-usage.jsonl (default ~/.cache/pai-swarm/jev-usage.jsonl); `pai jev usage` sums it
(R6-COR-034). The same ledger holds worker runs (kind `agent`, with claude -p's cost_usd; R6-COR-071), and
usage_lines / usage_day / usage_markdown are the query API for daily logs (R6-COR-070). `last_usage` holds the last reply's usage for callers that log their own.

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

import time

STORE_SERVICE, STORE_ACCOUNT = "pai-jev", "default"
last_usage = None  # the `usage` object of the last reply (ask() or smoke()), or None
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


def usage_log_path():
    """Where usage lines go: PAI_JEV_USAGE_LOG, else <PAI_SWARM_HOME or ~/.cache/pai-swarm>/jev-usage.jsonl."""
    if os.environ.get("PAI_JEV_USAGE_LOG"):
        return os.path.expanduser(os.environ["PAI_JEV_USAGE_LOG"])
    home = os.environ.get("PAI_SWARM_HOME") or os.path.join("~", ".cache", "pai-swarm")
    return os.path.join(os.path.expanduser(home), "jev-usage.jsonl")


def record_usage(kind, usage, seconds, http=None, state="ok", model=None):
    """Append one line {at, kind, model, state, http, seconds, input_tokens, output_tokens, ...}: numbers
    only, never the request, the answers or the key. A log that cannot be written is skipped."""
    global last_usage
    nums = {k: v for k, v in (usage or {}).items() if isinstance(v, (int, float))} if isinstance(usage, dict) else {}
    last_usage = nums or None
    line = dict({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kind": kind,
                 "model": model or os.environ.get("JEV_MODEL", "jev-latest"), "state": state, "http": http,
                 "seconds": round(seconds, 3)}, **nums)
    try:
        path = usage_log_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError:
        pass
    return line


def usage_lines(path=None, since=None, until=None):
    """(rows, unreadable): the ledger's lines with since <= at < until (UTC strings; a date such as
    2026-11-22 works as a prefix). The query API for other tools, such as the round lane's daily log
    (PAI/log/<date>.md): R6-COR-070. Reads only."""
    rows, bad = [], 0
    try:
        f = open(path or usage_log_path(), encoding="utf-8")
    except OSError:
        return rows, bad
    with f:
        for raw in f:
            try:
                e = json.loads(raw)
                at = str(e.get("at") or "")
            except (ValueError, AttributeError):
                bad += 1
                continue
            if (since and at < since) or (until and at >= until):
                continue
            rows.append(e)
    return rows, bad


def usage_day(day, path=None):
    """usage_summary for one UTC day (YYYY-MM-DD)."""
    if not re.fullmatch(r"\d{4}-\d\d-\d\d", day or ""):
        raise ValueError("a day is YYYY-MM-DD")
    nxt = time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime(day, "%Y-%m-%d")) + 36 * 3600))
    return usage_summary(path, since=day, until=nxt)


def usage_markdown(s):
    """A plain Markdown table of a usage_summary (no plugin needed to read it)."""
    out = ["| kind | requests | input tokens | output tokens | cost (USD) |", "| --- | --- | --- | --- | --- |"]
    for k, v in sorted(s["by_kind"].items()):
        out.append("| %s | %d | %d | %d | %s |" % (k, v["requests"], v["input_tokens"], v["output_tokens"],
                                                  "%.4f" % v["cost_usd"] if v["cost_usd"] else "-"))
    out.append("| total | %d | %d | %d | %s |" % (s["requests"], s["input_tokens"], s["output_tokens"],
                                                   "%.4f" % s["cost_usd"] if s["cost_usd"] else "-"))
    return "\n".join(out)


def usage_summary(path=None, since=None, until=None):
    """{log, requests, by_kind {kind: {requests, input_tokens, output_tokens, cost_usd}}, input_tokens,
    output_tokens, cost_usd, seconds, first, last, unreadable} from the usage ledger. Kind `agent` lines
    are worker runs (`claude -p` cost_usd, R6-COR-071); the others are JEV requests."""
    path = path or usage_log_path()
    out = {"log": path, "requests": 0, "by_kind": {}, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
           "seconds": 0.0, "first": None, "last": None, "unreadable": 0}
    rows, out["unreadable"] = usage_lines(path, since, until)
    for e in rows:
        at = str(e.get("at") or "")
        k = out["by_kind"].setdefault(str(e.get("kind") or "?"), {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                                                 "cost_usd": 0.0})
        k["requests"] += 1
        out["requests"] += 1
        for t in ("input_tokens", "output_tokens", "cost_usd"):
            n = e.get(t) if isinstance(e.get(t), (int, float)) else 0
            k[t] += n
            out[t] += n
        out["seconds"] += e.get("seconds") or 0
        out["first"] = min(out["first"] or at, at)
        out["last"] = max(out["last"] or at, at)
    out["cost_usd"] = round(out["cost_usd"], 6)
    out["seconds"] = round(out["seconds"], 3)
    return out


def ask(state, questions, timeout=None, kind="ask"):
    """POST /v1/systemone. Returns the `answers` dict keyed by question id; the reply's usage is
    appended to the usage log (see record_usage)."""
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
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        record_usage(kind, None, time.time() - t0, e.code, "error")
        raise RuntimeError("Jev HTTP %s: %s" % (e.code, e.read()[:300].decode("utf-8", "replace")))
    record_usage(kind, data.get("usage") if isinstance(data, dict) else None, time.time() - t0, 200)
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
        "instructions": "The worker report says the checks were run and passed: tests, a build or a "
                        "linter, or the specific check the task itself asks for (for example a grep, "
                        "a script or a command named in the task). A claim with no named check is a no.",
    },
    # R6-COR-033: 2026-11-22, a report that only said "done, all tests pass" scored checked 0.86, and a
    # worker that checked with grep (as asked) scored 0.06. This question asks for the proof itself.
    "evidence": {
        "type": "noul",
        "instructions": "The worker report lists the exact commands it ran and, for each, its exit code "
                        "or the decisive line of its output. A summary such as 'all tests pass' "
                        "without the command is a no.",
    },
}


def triage(title, body):
    ans = ask("TITLE: %s\n\n%s" % (title, body), TRIAGE_QUESTIONS, kind="triage")
    tier = ans.get("tier") or {}
    return {
        "actionable": _noul(ans, "actionable"),
        "tier": tier.get("choice"),
        "tier_confidence": tier.get("confidence"),
    }


def verify(task_title, task_body, report):
    state = "TASK: %s\n%s\n\nWORKER REPORT:\n%s" % (task_title, task_body[:1500], report[-2500:])
    ans = ask(state, VERIFY_QUESTIONS, kind="verify")
    return {"done": _noul(ans, "done"), "checked": _noul(ans, "checked"), "evidence": _noul(ans, "evidence")}


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
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res["http"] = r.status
            data = json.load(r)
            record_usage("check", data.get("usage") if isinstance(data, dict) else None, time.time() - t0, r.status)
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


# ---------------------------------------------------------------- `pai jev` verbs this module adds itself
# workbench.py keeps its own `pai jev status|check` parser and calls these two hooks (one line each), so
# a new verb is added here, not in workbench.py (R6-COR-050, the registry style of pai/commands.py).

EXTRA_VERBS = ("usage",)


def extend_parser(jv):
    """Add this module's verbs and options to workbench's `pai jev` parser."""
    for action in jv._actions:  # the positional `jev_cmd` defined in workbench.py
        if action.dest == "jev_cmd" and action.choices is not None:
            action.choices = tuple(action.choices) + tuple(v for v in EXTRA_VERBS if v not in action.choices)
    jv.add_argument("--log", help="usage: the usage log (default PAI_JEV_USAGE_LOG or ~/.cache/pai-swarm/jev-usage.jsonl)")
    jv.add_argument("--since", help="usage: only lines at or after this UTC time (2026-11-22 or 2026-11-22T10:00:00Z)")
    jv.add_argument("--until", help="usage: only lines before this UTC time")
    jv.add_argument("--json", action="store_true", help="usage: the summary as JSON")


def run_extra(a, log=print):
    """The exit code of one of EXTRA_VERBS, or None for workbench's own verbs."""
    if a.jev_cmd != "usage":  # R6-COR-034: reads the local log only; sends nothing
        return None
    s = usage_summary(getattr(a, "log", None), getattr(a, "since", None), getattr(a, "until", None))
    if getattr(a, "json", False):
        print(json.dumps(s, indent=1))
        return 0
    log("JEV usage  %s" % s["log"])
    log("requests   %d (%s .. %s), %.1f s in total" % (s["requests"], s["first"] or "-", s["last"] or "-", s["seconds"]))
    log("tokens     %d input, %d output" % (s["input_tokens"], s["output_tokens"]))
    if s["cost_usd"]:
        log("cost       $%.4f (agent runs: claude -p cost_usd)" % s["cost_usd"])
    for k, v in sorted(s["by_kind"].items()):
        log("  %-8s %4d request(s), %d input, %d output%s" % (k, v["requests"], v["input_tokens"], v["output_tokens"],
                                                            ", $%.4f" % v["cost_usd"] if v["cost_usd"] else ""))
    if s["unreadable"]:
        log("note       %d unreadable line(s) skipped" % s["unreadable"])
    return 0
