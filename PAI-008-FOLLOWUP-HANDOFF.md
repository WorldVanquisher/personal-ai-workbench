---
status: draft
approved: false
task: PAI-008-followup
project: personal-ai-workbench
created: 2026-11-17T05:40:00Z
---

# PAI-008 follow-up: dispatch after folder trust (draft, unapproved)

Worktree `overnight/20261117T042813Z/pai`, baseline `c1c27828`. Only `swarm/` changed (not in
the release bundle). Proposed PR title:

**fix(workbench): dispatch only to settled, trust-free panes; delivered needs transcript evidence**

| State | Report |
| --- | --- |
| Implementation | Implemented locally; human review pending |
| Validation | Fixture tests only (fake Claude/Codex, isolated tmux). Swarm suite 149 OK (132 existing + 17 new); core suite 91 OK, 6 Windows-only skipped; headless `run_tests.sh` 23/23. All 17 new tests fail on the baseline product code |
| Live behaviour | NOT verified: no real Claude/Codex was started; the original smoke run was not touched |
| Commit / merge / push | None. No Git write was run (read-only `status`/`diff`/`log` only) |

## Observed (owner's live smoke, as reported; not reproduced live here)

- O1. Claude lane: folder trust answered, `rc verified`, yet `pai resume` left `smoke-a`
  `pending`; no NOTES.md appeared.
- O2. Codex lane: reported `delivered` while its folder-trust prompt was still showing; it
  later did the task, but that `delivered` did not prove submission.

## Traced paths (baseline)

`pai resume` / `pai trust` → `rc.exclusive(cmd_up)` → `ensure_workers` (keeps running
agents) → `watch_startup`: each 0.25 s poll, `collect_status` → `lane_phase`; a lane with
`phase running`, `input_ready` and dispatch `pending` → `dispatch_lane` → `dispatch.deliver`.
Readiness was `Adapter.ready(screen)`: a ready marker on the visible screen and no trust
marker; Claude's only marker is `? for shortcuts`. `deliver` read the screen once, pasted,
pressed Enter immediately, then called the dispatch `delivered` when the tag was anywhere in
`capture-pane -S -` (scrollback included). `--mark-delivered` wrote outside the session mutex.

## Root cause

- O1 → readiness false negative (hypothesis, strongly supported). Claude Code 2.1.326 adds
  `? for shortcuts` to its footer only when no other footer item or notification is shown
  (static reading of the installed program). After trust, with Remote Control, its footer
  evidently showed something else, so pai never saw a ready lane and `watch_startup` waited
  out `--wait` with the task `pending`. The exact live footer was not captured. Ruled out:
  a trust prompt still on screen (status reports `rc not-connected` while one is visible,
  and `pai trust` only returns once it is gone). Not ruled out: a further dialog after trust;
  the new `input` line in `pai status` shows which.
- O2 → two faults. (a) Readiness from one frame: Codex can draw its idle footer before its
  trust prompt, and pai pasted and pressed Enter on that frame (mechanism reproduced with a
  fixture; the live timing is unknown). An Enter that lands on a trust prompt could answer
  it. (b) `delivered` meant "tag visible in the pane", which a terminal echo or an unsent
  input box also produces. How the live Codex later got the task is not known from here.
- Also found: a note name containing a newline reached the pasted line unescaped; the
  dispatch file could be overwritten by a concurrent `--mark-delivered`.

## Fix

Evidence is now separate in `pai status` and `--json`: `process`, `trust`
(`prompt-visible` / `not-shown` / `unknown`), `input` (`ready` + why), `dispatch`
(+ `evidence` or `reconcile`), `rc`. Old JSON keys stay.

- Input readiness (`agents.Adapter.input_state`): never with a trust prompt on screen. For
  Claude, its own session record for exactly this process (pid, start time, worktree)
  decides when it has a `status`: `idle` without `waitingFor` is ready even without the
  footer hint; `busy`/`waiting` is not. Otherwise the screen markers, as before.
- Settle: a lane must be ready, trust-free, for 3 s (`PAI_DISPATCH_SETTLE`) before anything
  is typed; lanes are tracked independently, so a ready lane never waits for one at a prompt.
- Keystrokes: `sending` recorded (fsync) → screen re-read → paste → `typed` recorded →
  0.5 s → re-read: a trust prompt (or a Claude record no longer `idle`) blocks Enter and the
  dispatch becomes `unknown` → Enter → `sent`.
- `delivered` only with evidence: the agent's own transcript records a user prompt with the
  tag at or after the attempt (Claude `projects/<dir>/<sessionId>.jsonl`; Codex
  `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`, matched on `session_meta.cwd`), or the
  operator's `--mark-delivered`. Evidence is persisted once seen, so it survives the process.
- `sent` without evidence for 20 s (`PAI_DISPATCH_CONFIRM_SECONDS`), a crash between a
  keystroke and its record, a changed process, or a blocked Enter → `unknown` with four
  numbered reconcile steps. Nothing is ever resent automatically.
- `--mark-delivered` / `--redispatch` decide and act under the session mutex; `--redispatch`
  waits for settled readiness and applies only to `unknown`.
- The whole pasted line is stripped of control characters.

Policy, stale RC with a ready local Claude (item 16): the task is sent and `rc` stays
`unverified`; `pai status` exits 1 until RC is verified. Dispatch uses local evidence only
(the line goes through the tmux pane, not RC). The record's age speaks only about the RC
bridge; its `status` is rewritten on every input-state change, so an old `updatedAt` does
not make `idle` stale. Neither kind of evidence upgrades the other.

Compatibility: manifests, run/ownership/lock records and `pai_dispatch_v1` are unchanged in
shape; entries gain fields. A legacy `delivered` written from the tag alone reads as
`unknown` unless the transcript shows the prompt; it is never resent. `--redispatch` is
refused while a dispatch is `sent` (inside the 20 s window).

## Diff (uncommitted)

`swarm/agents.py` +201/-1, `swarm/dispatch.py` +202/-69, `swarm/rc.py` +102/-23,
`swarm/workbench.py` +18/-15; tests and fixtures: `tests/test_dispatch.py` (new, 507 lines),
`tests/fake_tmux.py` (new: pass-through tmux via the existing `PAI_SWARM_TMUX`, logs
keystrokes, injects a dropped Enter, a trust prompt after the paste, a slow paste or a
SIGKILL of pai after a keystroke), `fake_claude_rc.py` (record in both modes with
`status`, transcript, `no_hint`, `stale_record`), `fake_codex.py` (rollout, late trust
prompts), `helpers.py` (per-test `CODEX_HOME`), `test_workbench.py` (one fixture step also
removes the transcript evidence, keeping that test's intent); docs `WORKBENCH.md` §4–6, 12,
`SMOKE.md` §3. Checkpoint patches (outside the repo, sha256 prefixes):
`~/.cache/pai-008-work/checkpoints/cp1-after-item8.patch` (4adb771a7fafbaf4) and
`cp2-after-item16.patch` (0bb615e70c2514b9).

## Tests

```sh
TMPDIR=/var/tmp/pai-008-tests python3 -B -W error::ResourceWarning -m unittest discover -s swarm/tests -v   # 149 OK, ~190 s
TMPDIR=/var/tmp/pai-008-tests python3 -B -W error::ResourceWarning -m unittest discover -s tests          # 91 OK, 6 skipped
TMPDIR=/var/tmp/pai-008-tests PAI_REPO=$PWD bash swarm/tests/run_tests.sh                                 # 23 passed
```

New regressions (items 3–5, 9–17): Claude trust → ready without hint → resume delivers once;
Codex trust prompt 2 s after its first frame gets no keystroke; a trust prompt after the
paste never gets Enter; a tag echoed without submission is `unknown`, reconciled by the
operator's Enter; SIGKILL after Enter → delivered from evidence, no resend; SIGKILL after
paste → `unknown`, no resend, reconcilable; concurrent `resume` and `--mark-delivered`
refused while one dispatches (one paste); stale RC + no hint → delivered, `rc unverified`;
three lanes, one ready and two at prompts; legacy records; plus unit tests for readiness
precedence, record identity, transcript matching, timestamps, message cleaning and stored
states. `TMPDIR` was moved off `/tmp` because its per-user quota was exhausted by other
sessions' files (not touched); with a `TMPDIR` under `$HOME` the existing SSH test fails on
both baseline and fix (pai prints such paths as `~/...`).

## Limitations

- Fixture evidence only. The record/transcript formats are undocumented; their structure
  was read from files Claude Code 2.1.326 and Codex 0.167.0 wrote on this host. If they
  change, Claude falls back to the footer hint and dispatches end `unknown`, never a false
  `delivered`.
- A trust prompt later than the settle window is still caught before Enter, but after the
  paste; the remaining window between the last screen read and a keystroke is milliseconds.
- Another prompt carrying the same tag (someone typing it, or sending it over RC) would count
  as evidence. Codex evidence looks at the 64 newest rollouts of the last 7 days.
- Each lane waits about 3 s of steady readiness before its task; Python 3.9 compatibility
  was checked by syntax only (no 3.9 interpreter here).

## Owner-run live smoke (fresh)

Follow `swarm/SMOKE.md` §0–3 with a new `S` (the old run stays as it is). Markers to record:
before trust each prompting lane shows `trust prompt-visible`, `dispatch pending` and no
`[pai ` line in `pai logs`; after `pai trust`, lane a shows `input ready: Claude's session
record for this process says idle` and `dispatch delivered` with `evidence: Claude's own
transcript ... records the prompt`; lane b `evidence: Codex's own rollout file ...`; two
`pai resume` runs leave the `[pai ` counts unchanged. Note whether `? for shortcuts` was on
lane a's idle screen. Any `unknown`: record its detail and follow its numbered steps.

Next: human review of this draft and the diff; the live smoke above; commit/PR stay with the
owner. No proposals approved.
