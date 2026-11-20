# Agent backends: the capability contract

A backend is one agent program pai can run in a lane (today: Claude Code and Codex). Everything
pai knows about a backend lives in its adapter in `agents.py`, and `Adapter.capabilities()` is
the one representation of it that `pai doctor` (`--json`: `capabilities`, `gaps`) and run
planning (`pai run --dry-run`, the refusals before anything starts) both read.

## What each backend exposes (schema `pai_backend_capabilities_v1`)

| | Claude Code (`claude`) | Codex (`codex`) |
| --- | --- | --- |
| launch modes | `rc` (Remote Control), `local` | `local` |
| model / effort | `--model`; `--effort` low..max | `--model`; `-c model_reasoning_effort=` minimal..high |
| input readiness | its own session record for exactly this process (`$CLAUDE_CONFIG_DIR/sessions/<pid>.json`: idle, no dialog), then the screen ("? for shortcuts") | the screen only ("? for shortcuts", "% context left") |
| folder-trust prompt | detected on the visible screen; answered by a person, never by pai | the same |
| what blocks Enter after a paste | a trust prompt on screen; the session record no longer idle | a trust prompt on screen |
| delivery evidence (transcript) | `$CLAUDE_CONFIG_DIR/projects/<worktree>/<session>.jsonl`, a user message containing the task tag | `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl` whose `session_meta.cwd` is the worktree, a user `input_text` with the tag |
| login check | `claude auth status` (never logs in) | `codex login status` |
| gaps pai reports | none | no Remote Control mode; readiness from the screen only |

Readiness evidence is kept apart per backend: a Codex lane never reads Claude's session records,
and a Claude lane's record is tied to its pid, process start time and worktree.

## What planning refuses, before anything is fetched, locked or started

- a launch mode the backend does not have (`--mode rc` for Codex), with the modes it has;
- a program that is not installed, or (Claude `rc`) one without `--remote-control`;
- dispatch to a backend that records no transcript pai can read: its tasks could never be shown
  delivered. Repair: `pai run --no-dispatch` (give the task yourself, e.g. `pai paste`), or a
  backend with delivery evidence.

## Adding a backend: the smallest useful example

```python
# agents.py
class Example(Adapter):
    name, display, program = "example", "Example CLI", "example"
    modes = ("local",)
    trust_markers = ("do you trust this folder",)          # exact text of its own prompt, lower case
    trust_hint = "read it and answer it yourself; nothing answers it for you"
    ready_markers = ("> ready",)                           # text only its idle input box shows
    readiness = "screen"
    transcript = "example-jsonl"                           # None if it keeps none: dispatch is then refused
    transcript_root = "$EXAMPLE_HOME/history/*.jsonl"
    login_check = "example whoami"

    def argv(self, program, mode, name, model, effort=None):
        return list(program) + (["--model", model] if model else [])

    def submitted(self, probe, tag, since):
        ...  # read its own transcript; return one sentence of evidence, or None

ADAPTERS = {a.name: a for a in (Claude(), Codex(), Example())}
```

Then: a fake of the program in `swarm/tests/` (idle box, trust prompt, transcript), the dispatch
and recovery tests run against it, and `pai doctor --agent example` shows its capabilities.
`swarm/tests/fake_backend.py` is a minimal adapter registered only inside tests
(`agents.register`); the command line never selects it.

## When a backend counts as supported for 1.0

1. Its adapter states every capability above; `gaps()` lists what it lacks, and doctor shows it.
2. A fake of the program covers: idle input box, a first-use trust prompt (also one that appears
   after the first frame), a changed or missing footer, a missing transcript, and a crash on start.
3. The dispatch, recovery and status suites pass against that fake, including "never typed into a
   trust prompt", "nothing resent without a choice" and "delivered only on transcript evidence".
4. Its screen markers and transcript format were read from a named version of the real program,
   recorded in the adapter's docstring.
5. One owner-run real-agent smoke (docs/1.0/REAL-AGENT-SMOKE.md) passes with that version.
6. A backend that meets 1-3 only is **experimental**: it may ship, marked so in doctor and the
   release notes, and is not part of the 1.0 compatibility commitments.
