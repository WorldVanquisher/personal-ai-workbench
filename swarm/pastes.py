"""What was pasted into a lane's agent by hand (`pai paste`, the console's paste), so that a stop,
a recovery or a handoff can say whether it may have reached the agent.

One JSON line per paste in $PAI_SWARM_HOME/pastes/<repo hash>-<run>.jsonl, appended and fsynced:
  {"at", "ts", "run", "lane", "via": "cli"|"console", "sha256", "chars", "lines", "enter",
   "needle": the first line (privacy-filtered, at most 60 characters), "session_id", "pid"}
The text itself is never stored. A paste's state:
  staged     pasted without Enter, and the transcript does not show it: it was not submitted through
             pai (an Enter typed later in the lane itself is not something pai records)
  delivered  the agent's own transcript shows a prompt with its first line at or after the paste
             (also for a paste without Enter that was submitted later)
  unknown    Enter was pressed, and the transcript does not show it (yet, or ever: the agent may have
             died, or its transcript is gone). Only the transcript or a person settles it.
"""
import hashlib
import json
import os
import time

import agents
import gitprep
import wtlock

try:
    from pai import privacy
except ImportError:  # the workbench started without the pai package on sys.path (its own tests)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pai import privacy


def path(m):
    cdir = gitprep.common_dir(m.repo) or m.repo
    return os.path.join(wtlock.home(), "pastes", "%s-%s.jsonl" % (hashlib.sha256(cdir.encode()).hexdigest()[:12], m.run_id))


def needle(text):
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        line = " ".join(line.split())
        if line:
            clean, _ = privacy.scrub(line)
            return clean[:60]
    return ""


def record(m, w, text, res, via, proc=None):
    """Append one paste (after panes.paste returned); never raises."""
    try:
        import rc
        pr = rc.probe(m, w, proc or {})
        e = {"at": gitprep.now(), "ts": time.time(), "run": m.run_id, "lane": w.lane, "via": via,
             "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "chars": len(text),
             "lines": res.get("lines"), "enter": bool(res.get("enter")), "needle": needle(text),
             "session_id": (pr.record or {}).get("sessionId") if pr else None, "pid": (proc or {}).get("pid")}
        p = path(m)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(e, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return e
    except Exception:  # noqa: BLE001  a record is evidence, never a reason for the paste to fail
        return None


def entries(m, lane=None):
    out = []
    try:
        with open(path(m), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and (lane is None or e.get("lane") == lane):
                    out.append(e)
    except OSError:
        pass
    return out


def state(m, w, e, proc=None):
    """(state, evidence) for one paste entry: staged | delivered | unknown."""
    if not e.get("needle"):
        return ("unknown", "Enter was pressed at %s; the text has no line pai can look for" % e.get("at")) if \
            e.get("enter") else ("staged", "pasted without Enter at %s (no line pai can look for)" % e.get("at"))
    import rc
    pr = rc.probe(m, w, proc or {})
    if w.agent == "claude" and (pr.record is None) and e.get("session_id"):
        pr = agents.Probe(w.worktree, None, {"sessionId": e["session_id"]}, m.claude_dir(), None)
    try:
        ev = w.adapter.submitted(pr, e["needle"], float(e.get("ts") or 0) - 2)
    except Exception:  # noqa: BLE001
        ev = None
    if ev:
        return "delivered", ev
    if not e.get("enter"):
        return "staged", ("pasted without Enter at %s, and %s's transcript does not show it: not submitted through pai"
                          % (e.get("at"), w.adapter.display))
    return "unknown", ("Enter was pressed at %s, and %s's transcript does not show the text: it is not known whether "
                       "it reached the agent" % (e.get("at"), w.adapter.display))


def describe(e):
    return "a paste at %s (%s line(s), %d characters, starting \"%s\", sha256 %s, via %s)" % (
        e.get("at"), e.get("lines"), e.get("chars") or 0, e.get("needle"), (e.get("sha256") or "")[:12], e.get("via"))
