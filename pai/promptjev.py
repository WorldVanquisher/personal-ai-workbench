"""JEV (TypeSafe) for the prompt library: score rules, artifacts and contracts.

The pai package itself opens no network connection (tests/test_pai.py, StaticTests). The request
is made by the workbench's JEV client, swarm/jev.py (`pai jev`), found the same way `pai run` is
(pai/launch.py workbench_dir): beside pai/ in a checkout or bundle, pai/_workbench when installed.
Without the workbench, JEV options report "skipped".

- The key comes only from TYPESAFE_API_KEY (or the workbench's credential store); never printed,
  logged or written.
- Every request appends one JSON line to $PAI_JEV_USAGE_LOG (when set): time, purpose, model,
  number of questions, seconds, ok/error, and the service's usage numbers when the workbench
  exposes them (`jev.last_usage`; proposal for R6-COR-034). Never the key or the text sent.
- JEV judges how a text is written, not whether it is true: callers report its scores beside their
  deterministic checks and never gate on them.
"""
import datetime
import json
import os
import sys
import time

STATE_LIMIT = 6000
_jev = []


class JevError(Exception):
    pass


def client():
    """The workbench's jev module, or None. Tests may put a fake in `_jev`."""
    if _jev:
        return _jev[0]
    from pai import launch
    d = launch.workbench_dir()
    if not d:
        return None
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        import jev
    except ImportError:
        return None
    _jev.append(jev)
    return jev


def available():
    j = client()
    return bool(j and j.available())


def _log(entry):
    path = os.environ.get("PAI_JEV_USAGE_LOG")
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def ask(state, questions, purpose, timeout=None):
    """One request through the workbench client. Returns {qid: answer}; raises JevError."""
    j = client()
    if not j or not j.available():
        raise JevError("JEV is not available (no workbench, or TYPESAFE_API_KEY is not set)")
    entry = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "purpose": purpose, "model": os.environ.get("JEV_MODEL", "jev-latest"), "questions": len(questions)}
    t0 = time.monotonic()
    if hasattr(j, "last_usage"):
        j.last_usage = None
    try:
        ans = j.ask(state[:STATE_LIMIT], questions, timeout=timeout)
    except Exception as e:  # noqa: BLE001  the workbench raises RuntimeError, URLError, timeouts
        entry.update(ok=False, error=type(e).__name__, seconds=round(time.monotonic() - t0, 3))
        _log(entry)
        raise JevError("JEV request failed: %s" % type(e).__name__)
    usage = getattr(j, "last_usage", None)
    entry.update(ok=True, seconds=round(time.monotonic() - t0, 3),
                 usage={k: v for k, v in usage.items() if isinstance(v, (int, float))} if isinstance(usage, dict)
                 else "not exposed by the workbench client")
    _log(entry)
    if not isinstance(ans, dict):
        raise JevError("JEV reply has no answers")
    return ans


def noul(ans, qid):
    a = ans.get(qid) or {}
    v = a.get("noul")
    if not isinstance(v, (int, float)) or not 0 <= float(v) <= 1:
        return None
    return float(v)


# Calibrated 2026-11-22 (JEV, 2 asks each; docs/research/JEV-QUESTIONS.md): the first wording ("knows exactly
# what to do ... and how to tell that it has complied, without asking any question") scored a plainly clear rule
# 0.67 (English) / 0.52 (Chinese) and a vague one 0.05, so every library rule fell below 0.6. This wording keeps
# vague rules low (0.21-0.33) and clear ones high (0.87-0.95).
CLEAR_Q = {"clear": {"type": "noul", "instructions":
                     "A careful developer given this RULE would know what action it requires or forbids. Answer yes "
                     "unless the rule is vague, contradictory, or uses undefined terms."}}


# Sample values for scoring unrendered rules. Measured 2026-11-22 (JEV, 3 asks each): the same rule with
# `{{VAULT}}`-style placeholders scored 0.06-0.38 lower than with values filled in, because JEV reads a
# placeholder as an undefined term. Workers only ever see rendered text, so rules are scored that way.
SAMPLES = {"RUN_ID": "R7-20261201", "LANE": "docs", "LANE_CODE": "DOC", "PROJECT": "pai", "VAULT": "/vault",
           "WORK_DIR": "/work/docs", "WORKTREE": "/worktrees/R7-20261201/docs", "BRANCH": "worker/R7-20261201/docs",
           "BASE_SHA": "0123456789abcdef0123456789abcdef01234567", "REPO_URL": "https://example.invalid/repo.git",
           "CHECKPOINT": "cp-B", "NEXT_ID": "R7-DOC-011", "TOPIC": "topic"}


def sample(text):
    import re
    return re.sub(r"(?<!\\)\{\{([A-Z][A-Z0-9_]*)\}\}", lambda m: SAMPLES.get(m.group(1), m.group(1).lower()), text)


def score_rules(rules, below=0.6):
    """{summary, scores: [{id, score, text}], low: [...]}: one request per rule, placeholders filled with
    SAMPLES first."""
    if not available():
        return {"summary": "skipped (TYPESAFE_API_KEY is not set)", "scores": [], "low": []}
    scores = []
    for rid, heading, text in rules:
        try:
            s = noul(ask(sample("SECTION: %s\nRULE: %s" % (heading, text)), CLEAR_Q, "lint-rule"), "clear")
        except JevError as e:
            s, text = None, text + " [%s]" % e
        scores.append({"id": rid, "score": s, "text": text})
    got = [x["score"] for x in scores if x["score"] is not None]
    low = sorted((x for x in scores if x["score"] is not None and x["score"] < below), key=lambda x: x["score"])
    summary = "%d rule(s) scored, %d failed, mean %.2f, %d below %.2f" % (
        len(got), len(scores) - len(got), sum(got) / len(got) if got else 0.0, len(low), below)
    return {"summary": summary, "scores": scores, "low": low}
