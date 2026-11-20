#!/usr/bin/env python3
"""The fake worker of `pai guide`: an offline stand-in for Claude Code, for the guided demo only.

It is never chosen by `pai run` on its own: `pai guide` starts runs whose PATH begins with a
private folder where `claude` is this file, inside the guided demo's own directory. It needs no
account, makes no network call and runs no model. What it does, so that pai's normal evidence
rules apply unchanged:

- shows Claude's idle footer ("? for shortcuts") and keeps a Claude-style session record for its
  own process in $CLAUDE_CONFIG_DIR/sessions (readiness evidence);
- reads one prompt per line; records it in a Claude-style transcript under
  $CLAUDE_CONFIG_DIR/projects (delivery evidence);
- when the prompt names a task note whose front matter has `guided_file` and `guided_text`,
  writes that file in its worktree, writes HANDOFF.md, and commits both on the lane's branch.

It refuses to start outside a guided demo (PAI_GUIDED_DEMO must name the demo directory, and
its worktree must be inside it). With PAI_GUIDED_CRASH_ONCE (a file inside the demo; `pai guide
--recovery`) it exits with status 3 on its first start, before taking any task, and runs normally
once restarted.

For `pai guide --handoff` (continuation across sessions) it also:
- plays Codex when started through the demo's `codex` launcher (PAI_GUIDED_AS=codex): Codex's
  footer, no Claude session record, and a Codex-style rollout file under $CODEX_HOME/sessions;
- works through `guided_steps: [a, b, c]` (front matter): one file and one commit per step;
- reads the note's last continuation marker (written by `pai recover --continue`) and skips the
  steps it lists as completed; its HANDOFF.md names what it continued from, what it did, what is
  left and the open decisions it was given;
- follows `$PAI_GUIDED_DEMO/guided-control.json` per run id ($PAI_RC_RUN): {"steps": N} (at most N
  steps this session), "lose_transcript" (take the prompt but record no transcript: delivery stays
  uncertain), "crash_after" (exit with status 3 after its steps, without a handoff);
- appends every prompt it receives to $PAI_GUIDED_DEMO/guided-received.jsonl, so a demo can count
  prompts per session independently of pai.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
import uuid

DEMO = os.environ.get("PAI_GUIDED_DEMO", "")


def say(text=""):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def now_ms():
    return int(time.time() * 1000)


def proc_start(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            stat = f.read()
        return stat[stat.rindex(")") + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def front_matter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        k, _, v = line.partition(":")
        if k.strip():
            out[k.strip()] = v.strip().strip("\"'")
    return out


def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True)


def listed(value):
    v = (value or "").strip()
    if v.startswith("[") and v.endswith("]"):
        return [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
    return [v] if v else []


def marker(text):
    """The last `pai recover --continue` marker in the note (the same format continuation.py writes)."""
    found = re.findall(r"<!-- pai-continuation v1 (\{.*\}) -->", text or "")
    try:
        return json.loads(found[-1]) if found else None
    except ValueError:
        return None


def open_decisions(text):
    """The unchecked items under the last "Open decisions" heading."""
    part = text.rsplit("### Open decisions", 1)
    if len(part) < 2:
        return []
    out = []
    for line in part[1].splitlines()[1:]:
        if line.startswith("#") or line.startswith("<!--"):
            break
        if line.startswith("- [ ] "):
            out.append(line[6:].strip())
    return out


def control():
    try:
        with open(os.path.join(DEMO, "guided-control.json"), encoding="utf-8") as f:
            return (json.load(f) or {}).get(os.environ.get("PAI_RC_RUN", ""), {})
    except (OSError, ValueError):
        return {}


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def do_steps(here, note, text, meta, who, ctl):
    """The multi-step task: (done now, completed before, left, decisions seen)."""
    steps = listed(meta.get("guided_steps"))
    mk = marker(text)
    before = []
    if mk:
        subjects = [c.get("subject", "") for c in mk.get("completed") or []]
        before = [st for st in steps if "guided demo: %s" % st in subjects]
    todo = [st for st in steps if st not in before]
    limit = ctl.get("steps")
    now = todo[:limit] if isinstance(limit, int) else todo
    for st in now:
        with open(os.path.join(here, st + ".md"), "w", encoding="utf-8") as f:
            f.write("done: %s\nby: %s, run %s\n" % (st, who, os.environ.get("PAI_RC_RUN", "?")))
        git("add", st + ".md")
        git("commit", "-q", "-m", "guided demo: %s" % st)
        say("did %s (%s.md, committed)" % (st, st))
    return now, before, [st for st in todo if st not in now], open_decisions(text) if mk else [], mk


def main():
    here = os.path.realpath(os.getcwd())
    demo = os.path.realpath(DEMO) if DEMO else ""
    if not demo or not (here == demo or here.startswith(demo + os.sep)):
        say("pai guided worker: this fake agent only runs inside a `pai guide` demo directory. Nothing was done.")
        return 2
    crash = os.environ.get("PAI_GUIDED_CRASH_ONCE")  # `pai guide --recovery`: fail the first start on purpose
    if crash and os.path.realpath(crash).startswith(demo + os.sep) and not os.path.exists(crash):
        with open(crash, "w") as f:
            f.write("the guided worker crashed here once, on purpose\n")
        say("pai guided worker (FAKE): crashing on purpose before taking any task (pai guide --recovery)")
        return 3
    codex = os.environ.get("PAI_GUIDED_AS") == "codex"
    who = "codex (fake)" if codex else "claude (fake)"
    pid = os.getpid()
    session_id = str(uuid.uuid4())
    if codex:  # Codex keeps no session record; its prompts go to a rollout file whose first line names the cwd
        now = datetime.datetime.now(datetime.timezone.utc)
        rdir = os.path.join(os.environ["CODEX_HOME"], "sessions", now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"))
        os.makedirs(rdir, exist_ok=True)
        transcript = os.path.join(rdir, "rollout-%s-%s.jsonl" % (now.strftime("%Y-%m-%dT%H-%M-%S"), session_id))
        with open(transcript, "a") as f:
            f.write(json.dumps({"timestamp": stamp(), "type": "session_meta", "payload": {"id": session_id, "cwd": here,
                                                                                         "cli_version": "guided-fake"}}) + "\n")

        def save(**kw):
            pass
        ready = "? for shortcuts   100% context left"
    else:
        cfg = os.environ["CLAUDE_CONFIG_DIR"]
        sessions = os.path.join(cfg, "sessions")
        os.makedirs(sessions, exist_ok=True)
        slug = "".join(c if c.isascii() and c.isalnum() else "-" for c in here)
        transcript = os.path.join(cfg, "projects", slug, session_id + ".jsonl")
        record = {"pid": pid, "cwd": here, "kind": "interactive", "status": "idle", "sessionId": session_id,
                  "procStart": proc_start(pid), "updatedAt": now_ms(), "statusUpdatedAt": now_ms(), "waitingFor": None,
                  "bridgeSessionId": None}

        def save(**kw):
            record.update(kw, updatedAt=now_ms(), statusUpdatedAt=now_ms())
            tmp = os.path.join(sessions, "%d.json.tmp" % pid)
            with open(tmp, "w") as f:
                json.dump(record, f)
            os.replace(tmp, os.path.join(sessions, "%d.json" % pid))
        ready = "? for shortcuts"

    save()
    ctl = control()
    say("pai guided worker (FAKE %s, offline: no account, no network, no model)" % ("Codex" if codex else "Claude"))
    say("worktree %s" % here)
    say(ready)
    while True:
        line = sys.stdin.readline()
        if not line:
            time.sleep(0.2)
            continue
        line = line.rstrip("\n")
        if not line.strip():
            continue
        save(status="busy")
        with open(os.path.join(DEMO, "guided-received.jsonl"), "a") as f:
            f.write(json.dumps({"run": os.environ.get("PAI_RC_RUN"), "agent": "codex" if codex else "claude", "pid": pid,
                                "tag": line.split("]")[0] + "]" if line.startswith("[pai ") else None,
                                "time": time.time()}) + "\n")
        if not ctl.get("lose_transcript"):
            os.makedirs(os.path.dirname(transcript), exist_ok=True)
            with open(transcript, "a") as f:
                if codex:
                    f.write(json.dumps({"timestamp": stamp(), "type": "response_item", "payload": {
                        "type": "message", "role": "user", "content": [{"type": "input_text", "text": line}]}}) + "\n")
                else:
                    f.write(json.dumps({"type": "user", "sessionId": session_id, "cwd": here, "timestamp": stamp(),
                                        "message": {"role": "user", "content": line}}) + "\n")
        say("> %s" % line[:160])
        m = re.search(r"the note (.+?\.md) \(", line)
        note = m.group(1) if m else ""
        meta, ntext = {}, ""
        if note and os.path.isfile(note):
            with open(note, encoding="utf-8") as f:
                ntext = f.read()
            meta = front_matter(ntext)
        name, text = meta.get("guided_file"), meta.get("guided_text")
        if meta.get("guided_steps"):
            now, before, left, decisions, mk = do_steps(here, note, ntext, meta, who, ctl)
            if ctl.get("crash_after"):
                say("pai guided worker (FAKE): crashing on purpose after %s, without a handoff (pai guide --handoff "
                    "interrupted)" % ", ".join(now))
                return 3
            branch = git("symbolic-ref", "--short", "HEAD").stdout.strip()
            with open(os.path.join(here, "HANDOFF.md"), "w", encoding="utf-8") as f:
                f.write("# Handoff (guided demo, fake worker)\n\n- Session: %s, run %s, branch %s\n- Continued from: %s\n"
                        "- Completed before this session (from the note's continuation): %s\n- Done in this session: %s\n"
                        "- Left: %s\n- Open decisions I was given: %s\n- Not done: nothing was pushed; publishing is the "
                        "operator's step.\n" % (who, os.environ.get("PAI_RC_RUN"), branch, mk["id"] if mk else "nothing (first "
                        "session)", ", ".join(before) or "none", ", ".join(now) or "none", ", ".join(left) or "nothing",
                        "; ".join(decisions) or "none"))
            git("add", "HANDOFF.md")
            git("commit", "-q", "-m", "guided demo: handoff after %s" % (", ".join(now) or "no step"))
            say("wrote HANDOFF.md: done %s; left %s" % (", ".join(now) or "none", ", ".join(left) or "nothing"))
        elif name and text and "/" not in name and not name.startswith("."):
            with open(os.path.join(here, name), "w", encoding="utf-8") as f:
                f.write(text + "\n")
            branch = git("symbolic-ref", "--short", "HEAD").stdout.strip()
            with open(os.path.join(here, "HANDOFF.md"), "w", encoding="utf-8") as f:
                f.write("# Handoff (guided demo, fake worker)\n\n"
                        "- Task: %s\n- Branch: %s\n- Done: wrote `%s` with the line `%s`, as the note asks.\n"
                        "- Checked: the file exists and holds that line.\n"
                        "- Not done: nothing was pushed; publishing is the operator's step.\n" % (note, branch, name, text))
            git("add", name, "HANDOFF.md")
            c = git("commit", "-q", "-m", "guided demo: %s" % name)
            say("did the task: %s written and committed on %s (%s)" % (name, branch, "ok" if c.returncode == 0 else
                                                                     c.stderr.strip()[:120]))
            say("wrote HANDOFF.md")
        else:
            say("(no guided task in this prompt; nothing to do)")
        time.sleep(0.3)
        save(status="idle")
        say(ready)


if __name__ == "__main__":
    sys.exit(main())
