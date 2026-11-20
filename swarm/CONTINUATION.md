# Continue a task in a fresh session

An agent session ends: you stop it, it crashes, its context is full, or you want another agent
(Claude Code to Codex, or back). The task should go on in a fresh session without losing the goal,
the completed steps, the evidence or the decisions still open, and without sending anything twice.

Try it first, offline, with fake agents (each is one chapter of the walkthrough below):
`pai guide --handoff` (clean: a Claude session to a Codex session in a fresh HOME, through a transfer
packet), `pai guide --handoff reverse` (the same from Codex to Claude) and `pai guide --handoff
interrupted` (a session dies with its delivery uncertain).

## The workflow

```sh
pai status --run R1                      # session 1: what state is lane a in, and why
pai inspect a --run R1                   # the evidence and where each piece is read
pai cleanup --run R1 --lane a --apply --stop-running   # stop session 1 (a crashed one is already stopped)
pai recover --run R1 --continue a        # PREVIEW: what the next session would get; nothing written
pai recover --run R1 --continue a --write
#   only if session 1's delivery is unknown: add --choose a=mark-delivered (it had the task)
#   or --choose a=leave (carry the open question to the next session)
pai run --continue NOTE                  # fresh session, same agent; or --agent codex / --agent claude
```

To continue somewhere else (another HOME, a clean machine with the same repository), carry a
transfer packet:

```sh
git push origin pai/R1/a                          # a packet carries no commits: the branch must reach the other side
pai task export NOTE --transfer                   # PREVIEW: exactly what leaves, what stays and what that limits
pai task export NOTE --transfer --out packet.json # in the old place (never overwrites)
pai task import packet.json                       # in the new place (after `pai init` there): read-only preview
pai task import packet.json --write               # creates the note, nothing else
pai run --continue NOTE --agent codex
```

`pai task export --transfer --run R1 [--lane a ...]` takes the handed-off lanes of one run instead of
note names; lanes that were not handed off, or notes from different runs, are refused with the reason.

## Transfer packets

A packet is a task card file (`pai-task-cards` version 1) plus a `transfer` object (version 1):
the card is the note as it is, with its Continuation section, and the `transfer` object repeats what
the other side checks: the task id (`RUN/LANE/NOTE`) and continuation id, the source agent and its
capabilities, the branch, base and head, the completed commits, the open decisions, the evidence
(each with a sha256 or git blob id and a reference relative to the project: `note:NOTE#continuation`,
`git:HEAD:HANDOFF.md`, `run:R1/a/check/NAME`), what was left out and how much that matters (`none`,
`limits`, `blocks`), and the repository's root commits. The same state always gives the same bytes.

| Leaves by default | Never leaves |
| --- | --- |
| the note: title, description, acceptance criteria, non-private front matter | raw agent transcripts and session records |
| completed steps, open decisions, the continuation's evidence | session links and credentials (export is refused until they are removed from the note) |
| each `pai pr check` result at the handed-off head: name, exit, sha256 and size of its output | launcher configuration, agent permissions, approved check commands |
| with `--include-logs`: that output, privacy-filtered | private run fields and the `## Review` sections `pai pr check` writes (worktree path, commands) |

Each recorded check output is hashed again before it is used: one that changed after `pai pr check`
recorded it refuses the export (both hashes are shown; run the checks again, or leave it out with
`--without check:NAME`, which the packet then lists). Private paths and links in the note's own words
(home folders, temporary folders, `file://`, local-network URLs) are reported, not rewritten: you
decide whether they should leave.

`pai task import PACKET` shows, without writing: where the task comes from, the source agent's
evidence (readiness from a session record or the screen; delivery from a transcript or rollout), for
each agent on this machine whether it is installed and how its evidence differs, whether the base and
head are in this repository (if not: what to fetch), the completed steps, the open decisions, the
evidence and what was left out. A packet made for another project name is refused unless you add
`--other-project` (same repository under another name); a packet from another repository is always
refused. Import only creates task notes: it never changes the project or run configuration, the
repository, or agent settings, and a card can name checks but never supply their commands.

## What is carried, and where

`--write` appends a `## Continuation from RUN/LANE (time)` section to the task note itself, so the next
agent reads it as part of its task and a task card carries it unchanged:

| Kept | Where it comes from |
| --- | --- |
| goal | the note: title, description, acceptance criteria and its other front matter, untouched |
| completed steps | the commits on session 1's branch since its base; the new branch starts at that head, so nothing is redone or copied |
| evidence | lifecycle and dispatch evidence at handoff, checks recorded by `pai pr check` for that head, and session 1's HANDOFF.md at that head (session links and tokens removed) |
| open decisions | an unknown delivery carried with `--choose LANE=leave`, a hand paste that may have reached session 1, an interrupted recovery |
| marker | one HTML comment (`pai-continuation v1`) with the head, base, branch, lane and agent `pai run --continue` checks |

The decision is recorded in the run's recovery log (`pai recover --run R1 --history`: a `handoff`
begin and end with the before- and after-state), and lane a of R1 becomes `handed-off`: `pai resume`
and `pai recover` never restart it or resend its task.

## What is refused (nothing is written or started)

`pai recover --continue LANE --write`:
- session 1's agent is still there (stop it with `pai cleanup ... --stop-running`);
- its worktree has uncommitted work (commit it or move it yourself; nothing is reset or stashed);
- its delivery is unknown and you did not choose (`--choose LANE=mark-delivered` or `=leave`);
- text you pasted into it (`pai paste ... --enter`, or the console with Enter) may have reached it and
  its transcript cannot show it: the same choice decides (pai records every hand paste: time, size,
  sha256 and a privacy-filtered first line, never the text; `pai inspect` lists each as `staged`,
  `delivered` or `unknown`);
- the lane was already handed off;
- the lane changed between the preview and the write (it is checked again under the run's lock just
  before anything is written).

If `--write` is interrupted, nothing of it is kept (the note and the run record are written last); the
recovery log shows the interrupted attempt until a later handoff or recovery of that lane checks the
lane again, completes, and closes it as superseded.

`pai run --continue NOTE`:
- the note has no continuation, or that continuation was already used (`continued_marker`);
- the handed-off head or base is not in this repository;
- the lane's worktree is no longer on the handed-off branch and head (someone continued or changed it);
- a process still works inside that worktree (Linux: read from `/proc`; elsewhere this check cannot be made);
- this HOME's record of session 1 exists and is not handed off.

## No duplicate, and why

- Session 1's task is never resent: its lane is handed off, so resume and recover leave it alone;
  an unknown delivery is never retried for you.
- The fresh session gets exactly one prompt, with its own tag (`[pai R2/a/NOTE]`), and the prompt
  says it continues an earlier session: read the Continuation section, do not redo completed steps,
  settle open decisions with you.
- A continuation is used once: the note records `continued_marker`, and a second attempt is also
  refused because the worktree has moved on from the handed-off head.

Limits: two different checkouts or worktree roots could each continue the same card or packet once
(they do not share a worktree or a note); the worktree-process check is Linux-only; a branch must
exist in the repository where you continue (pai never pushes or fetches it for you). If this HOME's
run records are lost, pai cannot tell whether a task was delivered: `pai status --run R1` then lists
the notes that name R1 (worker, branch, base), and the delivery must be treated as unknown.

## Walkthrough (three chapters, each runnable offline)

1. **Clean handoff, Claude to Codex, fresh HOME** (`pai guide --handoff clean`): session 1 does step
   1 of 3 and is stopped; `pai inspect`; `pai recover --continue a` (preview, then `--write`); the
   branch goes to the demo's own origin; `pai task export --transfer` (preview, then `--out`); in a
   second HOME, `pai task import` (preview, then `--write`) and `pai run --continue --agent codex`;
   Codex does steps 2 and 3 and reports step 1 as already done. Then every way to send the task again
   is tried; each session received exactly one prompt.
2. **Reverse, Codex to Claude** (`pai guide --handoff reverse`): the same with the agents swapped;
   the import preview shows that Claude's readiness evidence differs from Codex's.
3. **Uncertain delivery** (`pai guide --handoff interrupted`): session 1 takes the prompt, keeps no
   transcript, commits step 1 and dies; status says `unknown`; the in-place recovery is only
   previewed; `--write` is refused until `--choose a=leave`; the open question is in the note and
   reaches session 2, which reports it. No prompt is sent twice.

## Switching agents with real programs

`docs/1.0/REAL-AGENT-SWITCH.md` (in a checkout) is the owner's checklist for a real Claude Code to
Codex switch and the reverse, and what each run proves.
