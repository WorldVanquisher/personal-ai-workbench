"""Agent adapters for RC lanes: which program a lane runs, in which launch mode, and what can
be observed about it. rc.py owns worktrees, locks, tmux, records and dispatch; an adapter only

  - names its launch modes and says which of them run Remote Control,
  - builds the argv for a mode (an argv list: nothing here is ever a shell line),
  - validates a model name and, where it can do so reliably, the installed program,
  - recognises its own process, its first-use folder-trust prompt and its ready input box.

The screen texts below were read from the installed programs (Claude Code 2.1.323 and Codex
0.167.0 on the development host). They are matched as plain substrings of the visible pane,
never answered: confirming folder trust is always left to the operator.
"""
import json
import os
import re
import subprocess

MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\[\]-]{0,99}")
NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,23}")


def flat(text):
    """Lower case, every run of whitespace one space: box drawing and wrapping do not matter."""
    return " ".join((text or "").lower().split())


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

    def argv(self, program, mode, name, model, effort=None):
        return list(program) + (["--model", model] if model else []) + (
            ["-c", 'model_reasoning_effort="%s"' % effort] if effort else [])

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


def names():
    return sorted(ADAPTERS)


def process_names(program):
    """Basenames that identify the lane's process in `ps` (the program, or a script run by an
    interpreter)."""
    return {os.path.basename(p) for p in program[:2] if p}
