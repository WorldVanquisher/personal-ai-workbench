"""Agent adapters for RC lanes: which program a lane runs, in which launch mode, and what can
be observed about it. rc.py owns worktrees, locks, tmux, records and dispatch; an adapter only

  - names its launch modes and says which of them run Remote Control,
  - builds the argv for a mode (an argv list: nothing here is ever a shell line),
  - validates a model name and, where it can do so reliably, the installed program,
  - recognises its own process, its first-use folder-trust prompt and its ready input box,
  - finds evidence that a prompt was submitted, in the agent's own transcript on disk.

The screen texts below were read from the installed programs (Claude Code 2.1.323 and Codex
0.167.0 on the development host). They are matched as plain substrings of the visible pane,
never answered: confirming folder trust is always left to the operator.

Files the agents write themselves, read here and never written (structure observed on the
development host with Claude Code 2.1.326 and Codex 0.167.0; formats are undocumented):
  Claude  <config>/sessions/<pid>.json   per process: pid, procStart, cwd, sessionId, status
                                         (idle|busy|waiting|shell), waitingFor, statusUpdatedAt
          <config>/projects/<dir>/<sessionId>.jsonl
                                         transcript; a prompt is {"type": "user", "timestamp",
                                         "message": {"role": "user", "content": str | [{"type":
                                         "text", "text"}]}}; <dir> is the cwd, non-alphanumerics -> -
  Codex   $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl
                                         lines {"timestamp", "type", "payload"}: session_meta
                                         (payload.cwd), response_item message role user with
                                         content [{"type": "input_text", "text"}]
"""
import datetime
import glob
import json
import os
import re
import subprocess
import time

MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\[\]-]{0,99}")
CAPS_SCHEMA = "pai_backend_capabilities_v1"
NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,23}")


def flat(text):
    """Lower case, every run of whitespace one space: box drawing and wrapping do not matter."""
    return " ".join((text or "").lower().split())


class Probe:
    """What an adapter may look at for one lane: its worktree, its agent process, Claude's
    session record for exactly that process (already checked against pid, start time and
    cwd, else None) and the agents' own data directories."""

    def __init__(self, worktree, pid=None, record=None, claude_dir=None, codex_home=None):
        self.worktree, self.pid, self.record = worktree, pid, record
        self.claude_dir, self.codex_home = claude_dir, codex_home


TAIL_BYTES = 4 << 20   # newest part of a transcript that is searched for a prompt
MAX_FILES = 64         # transcripts looked at per check
SKEW = 2.0             # seconds: an agent's timestamp may round below pai's own clock reading


def epoch(stamp):
    """Seconds since the epoch for an ISO 8601 time ("...Z" or with an offset), or None."""
    if not isinstance(stamp, str) or not stamp:
        return None
    s = stamp.strip().replace("Z", "+00:00")
    m = re.fullmatch(r"(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)", s)
    if m and m.group(2):  # Python 3.9's fromisoformat takes 3 or 6 fraction digits only
        s = m.group(1) + (m.group(2) + "000000")[:7] + m.group(3)
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.timestamp()


def jsonl(path, tail=TAIL_BYTES):
    """The JSON objects of a JSONL file's newest `tail` bytes (a cut first line is dropped)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail))
            data = f.read()
    except OSError:
        return []
    if size > tail:
        data = data.split(b"\n", 1)[1] if b"\n" in data else b""
    res = []
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            res.append(d)
    return res


def recent(paths, since, limit=MAX_FILES):
    """Existing files modified at or after `since` (less a little), newest first."""
    seen, res = set(), []
    for p in paths:
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if p not in seen and mt >= since - 5:
            seen.add(p)
            res.append((mt, p))
    return [p for _, p in sorted(res, reverse=True)[:limit]]


def same_dir(a, b):
    return bool(a) and bool(b) and os.path.realpath(a) == os.path.realpath(b)


class Adapter:
    name = display = ""
    program = ""         # executable looked up on PATH unless the manifest names another
    modes = ()           # launch modes; the first is the default
    rc_modes = ()        # modes in which the agent runs Remote Control
    trust_markers = ()   # texts only the folder-trust prompt shows
    trust_hint = ""      # what the operator does in that prompt
    ready_markers = ()   # texts the idle input box shows
    no_rc_reason = ""
    efforts = ()         # reasoning-effort values this adapter can pass (documented controls only)
    example_model = ""
    readiness = "screen"     # where input readiness comes from: "session-record" (then the screen) or "screen"
    transcript = None        # the agent's own record of submitted prompts pai can read, or None (then a
    transcript_root = None   #   dispatched task could never be shown delivered); where it lives
    login_check = None       # the read-only command that tells whether the agent is logged in

    def capabilities(self):
        """What pai can launch and observe for this backend: the one representation `pai doctor`
        and run planning both read (schema pai_backend_capabilities_v1)."""
        return {"schema": CAPS_SCHEMA, "backend": self.name, "display": self.display,
                "launch": {"program": self.program, "modes": list(self.modes), "default_mode": self.default_mode(),
                           "remote_control_modes": list(self.rc_modes), "model": True, "efforts": list(self.efforts)},
                "readiness": {"source": self.readiness, "screen_markers": list(self.ready_markers)},
                "trust": {"detected_from": "the visible screen", "markers": list(self.trust_markers),
                          "answered_by": "a person, never pai"},
                "input": {"paste": "one bracketed paste when the program asks for it, then Enter only if allowed",
                          "enter_blocked_by": ["a folder-trust prompt on screen"] + (
                              ["its session record no longer idle"] if self.readiness == "session-record" else [])},
                "transcript": {"kind": self.transcript, "location": self.transcript_root,
                               "delivery_evidence": bool(self.transcript)},
                "login": {"check": self.login_check}}

    def gaps(self):
        """What this backend lacks compared with the full contract, in words (for doctor)."""
        out = []
        if not self.rc_modes:
            out.append("no Remote Control mode")
        if self.readiness != "session-record":
            out.append("input readiness from the screen only (no session record)")
        if not self.transcript:
            out.append("no transcript pai can read: a dispatched task could never be shown delivered")
        if not self.login_check:
            out.append("no login check")
        return out

    def effort_problem(self, effort):
        if effort is None or effort in self.efforts:
            return None
        if not self.efforts:
            return "%s has no reasoning-effort control in pai" % self.name
        return "%s does not take reasoning effort %r (it takes: %s); nothing is changed to another value" % (
            self.name, effort, ", ".join(self.efforts))

    def default_mode(self):
        return self.modes[0]

    def has_rc(self, mode):
        return mode in self.rc_modes

    def argv(self, program, mode, name, model, effort=None):
        raise NotImplementedError

    def model_problem(self, model):
        if model is None or MODEL_RE.fullmatch(model):
            return None
        return "model %r is not a model name (letters, digits and . _ : @ / + [ ] -; no leading -)" % model

    def probe(self, exe, mode, cache):
        """A problem with the installed program for this mode, or None. Only checks that are
        reliable offline; a model name is never checked against the vendor's catalogue."""
        return None

    def waiting_for_trust(self, pane_text):
        t = flat(pane_text)
        return any(m in t for m in self.trust_markers)

    def ready(self, pane_text):
        t = flat(pane_text)
        return bool(t) and not self.waiting_for_trust(pane_text) and any(m in t for m in self.ready_markers)

    def input_state(self, screen, probe=None):
        """(ready, evidence): may a line be typed into the agent's input box now? Never while
        its folder-trust prompt is on screen."""
        if self.waiting_for_trust(screen):
            return False, "its folder-trust prompt is on screen"
        t = flat(screen)
        if not t:
            return False, "the pane shows nothing yet"
        mark = next((m for m in self.ready_markers if m in t), None)
        if mark:
            return True, 'the screen shows its idle input box ("%s")' % mark
        return False, "the screen shows no idle input box (%s)" % " / ".join('"%s"' % m for m in self.ready_markers)

    def enter_blocked(self, screen, probe=None):
        """Why Enter must not be pressed now, after a paste, or None. The input box holds the
        pasted line, so the idle markers may be gone; only signs of a prompt that Enter could
        answer count."""
        return "a folder-trust prompt appeared" if self.waiting_for_trust(screen) else None

    def submitted(self, probe, tag, since):
        """Evidence (one sentence) that a prompt containing `tag` was submitted to the agent at
        or after `since` (seconds since the epoch), from the agent's own transcript; or None.
        A tag that is only visible on screen proves nothing: it may still be in the input box."""
        return None

    def rc_unsupported(self, mode):
        return self.no_rc_reason if not self.rc_modes else \
            "%s runs in %s mode; Remote Control was not requested for this lane" % (self.name, mode)

    def login(self, exe):
        """(state, detail): ok | not-logged-in | unknown. Never logs in."""
        return "unknown", "no login check for %s" % self.name


def _run(argv, timeout=20):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timed out after %ds" % timeout
    except OSError as e:
        return None, "", str(e)


class Claude(Adapter):
    name, display, program = "claude", "Claude", "claude"
    modes, rc_modes = ("rc", "local"), ("rc",)
    trust_markers = ("is this a project you created or one you trust", "yes, i trust this folder",
                     "do you trust the files in this folder")
    trust_hint = ('read it, move to "Yes, I trust this folder" (the cursor starts on "No, exit") and press '
                  'Enter; nothing answers it for you')
    ready_markers = ("? for shortcuts",)
    efforts = ("low", "medium", "high", "xhigh", "max")  # `claude --help`: --effort <level>, 2.1.323
    example_model = "sonnet"
    readiness = "session-record"
    transcript, transcript_root = "claude-projects-jsonl", "$CLAUDE_CONFIG_DIR/projects/<worktree>/<session>.jsonl"
    login_check = "claude auth status"

    def argv(self, program, mode, name, model, effort=None):
        argv = list(program) + (["--remote-control"] if mode == "rc" else []) + ["--name", name]
        return argv + (["--model", model] if model else []) + (["--effort", effort] if effort else [])

    def probe(self, exe, mode, cache):
        if mode != "rc":
            return None
        key = (exe, "rc")
        if key not in cache:
            code, out, err = _run([exe, "--help"])
            cache[key] = None if code == 0 and "--remote-control" in out else (
                "%s has no --remote-control option (%s); update Claude Code or choose --mode local"
                % (exe, "exit %s" % code if code else "not in its --help"))
        return cache[key]

    def input_state(self, screen, probe=None):
        """Claude's own session record for exactly this process, when it carries a status, wins
        over screen text: `idle` (no dialog waiting) is ready even when the footer shows something
        other than "? for shortcuts" (it shows that hint only while it has nothing else to show);
        busy, waiting or shell is not ready even if the hint is on screen."""
        if self.waiting_for_trust(screen):
            return False, "its folder-trust prompt is on screen"
        rec = probe.record if probe else None
        status = (rec or {}).get("status")
        if isinstance(status, str):
            if status == "idle" and not rec.get("waitingFor"):
                return True, "Claude's session record for this process says idle (no dialog waiting)"
            return False, "Claude's session record for this process says %s%s" % (
                status, " (waiting for %s)" % rec["waitingFor"] if rec.get("waitingFor") else "")
        return super().input_state(screen, probe)

    def enter_blocked(self, screen, probe=None):
        """Also: Claude's own record no longer says idle (a dialog opened, or it is busy)."""
        rec = probe.record if probe else None
        if isinstance((rec or {}).get("status"), str) and (rec["status"] != "idle" or rec.get("waitingFor")):
            return "Claude's session record now says %s%s" % (
                rec["status"], " (waiting for %s)" % rec["waitingFor"] if rec.get("waitingFor") else "")
        return super().enter_blocked(screen, probe)

    def submitted(self, probe, tag, since):
        if not probe or not probe.claude_dir:
            return None
        projects = os.path.join(probe.claude_dir, "projects")
        sid = (probe.record or {}).get("sessionId")
        paths = glob.glob(os.path.join(glob.escape(projects), "*", glob.escape(sid) + ".jsonl")) if isinstance(sid, str) and sid else []
        # after the process ended (no record) or before it recorded its session id
        slug = "".join(c if c.isascii() and c.isalnum() else "-" for c in os.path.realpath(probe.worktree))
        paths += glob.glob(os.path.join(glob.escape(projects), glob.escape(slug), "*.jsonl"))
        for path in recent(paths, since):
            for d in reversed(jsonl(path)):
                msg = d.get("message") if isinstance(d.get("message"), dict) else {}
                if d.get("type") != "user" or msg.get("role") != "user":
                    continue
                if d.get("cwd") and not same_dir(d["cwd"], probe.worktree):
                    continue
                content = msg.get("content")
                texts = [content] if isinstance(content, str) else [
                    x.get("text") for x in content if isinstance(x, dict) and x.get("type") == "text"
                    and isinstance(x.get("text"), str)] if isinstance(content, list) else []
                ts = epoch(d.get("timestamp"))
                if ts is not None and ts >= since - SKEW and any(tag in t for t in texts):
                    return "Claude's own transcript for this worktree records the prompt (user message at %s)" % d["timestamp"]
        return None

    def login(self, exe):
        code, out, err = _run([exe, "auth", "status"])
        try:
            auth = json.loads(out) if out.strip() else {}
        except ValueError:
            auth = None
        if code is None or not isinstance(auth, dict):
            return "unknown", "could not check: %s" % (err.strip() or "unexpected output")
        if code != 0 or not auth.get("loggedIn"):
            return "not-logged-in", "installed, not logged in (run `claude`, then /login)"
        return "ok", "logged in (%s)" % (auth.get("authMethod") or "method unknown")


class Codex(Adapter):
    name, display, program = "codex", "Codex", "codex"
    modes, rc_modes = ("local",), ()
    trust_markers = ("trust this folder? codex can read, edit, and run files here",
                     "continue only if you trust these files")
    trust_hint = "read it and choose whether to trust the folder; nothing answers it for you"
    ready_markers = ("? for shortcuts", "% context left")
    no_rc_reason = ("pai-swarm has no Remote Control mode for codex (its experimental `codex remote-control` "
                    "daemon is not launched or verified here)")

    efforts = ("minimal", "low", "medium", "high")  # config key model_reasoning_effort, passed with -c
    example_model = "gpt-5-codex"
    readiness = "screen"
    transcript, transcript_root = "codex-rollout-jsonl", "$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl"
    login_check = "codex login status"

    def argv(self, program, mode, name, model, effort=None):
        return list(program) + (["--model", model] if model else []) + (
            ["-c", 'model_reasoning_effort="%s"' % effort] if effort else [])

    def submitted(self, probe, tag, since):
        if not probe or not probe.codex_home:
            return None
        root = os.path.join(probe.codex_home, "sessions")
        days, t = [], max(since, time.time() - 7 * 86400)
        while t <= time.time() + 86400:  # UTC and local dates around the attempt, at most a week back
            for tm in (time.gmtime(t), time.localtime(t)):
                days.append(os.path.join(root, time.strftime("%Y", tm), time.strftime("%m", tm), time.strftime("%d", tm)))
            t += 43200
        paths = [p for d in dict.fromkeys(days) for p in glob.glob(os.path.join(glob.escape(d), "rollout-*.jsonl"))]
        for path in recent(paths, since):
            meta = self._head(path)
            if not meta or not same_dir((meta.get("payload") or {}).get("cwd"), probe.worktree):
                continue
            for d in reversed(jsonl(path)):
                p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                if d.get("type") != "response_item" or p.get("type") != "message" or p.get("role") != "user":
                    continue
                texts = [x.get("text") for x in p.get("content") or [] if isinstance(x, dict)
                         and x.get("type") == "input_text" and isinstance(x.get("text"), str)]
                ts = epoch(d.get("timestamp"))
                if ts is not None and ts >= since - SKEW and any(tag in t for t in texts):
                    return "Codex's own rollout file for this worktree records the prompt (user message at %s)" % d["timestamp"]
        return None

    @staticmethod
    def _head(path):
        """The rollout's first line, its session_meta (it carries the base instructions, so it
        may be long; a longer line than this bound is not read and the file is skipped)."""
        try:
            with open(path, "rb") as f:
                d = json.loads(f.readline(8 << 20).decode("utf-8", "replace"))
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) and d.get("type") == "session_meta" else None

    def login(self, exe):
        code, out, err = _run([exe, "login", "status"])
        if code is None:
            return "unknown", "could not check: %s" % err.strip()
        text = (out + err).strip().splitlines()
        first = text[0][:80] if text else ""
        return ("ok", first or "logged in") if code == 0 else ("not-logged-in", "installed, not logged in (`codex login`)")


ADAPTERS = {a.name: a for a in (Claude(), Codex())}


def get(name):
    return ADAPTERS.get(name)


def register(adapter):
    """Add an adapter in this process only (tests: swarm/tests/fake_backend.py). The command line
    never calls this, so `pai run --agent` and `pai doctor` only ever know the built-in ones."""
    if adapter.name in ADAPTERS:
        raise ValueError("backend %s exists" % adapter.name)
    ADAPTERS[adapter.name] = adapter


def unregister(name):
    if name in ("claude", "codex"):
        raise ValueError("the built-in backends stay")
    ADAPTERS.pop(name, None)


def names():
    return sorted(ADAPTERS)


def process_names(program):
    """Basenames that identify the lane's process in `ps` (the program, or a script run by an
    interpreter)."""
    return {os.path.basename(p) for p in program[:2] if p}
