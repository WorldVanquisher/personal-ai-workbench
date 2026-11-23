# Glossary

<!-- doc_check: fields -->
English | [简体中文](GLOSSARY.zh-CN.md)

The words PAI's manuals use, in the order you meet them. Commands in `code` exist in pai 0.5.0
unless marked *(planned)*.

## round

One batch of parallel work that starts from one base commit and ends when its PRs are merged
or dropped, for example `R6-20261122`. A round has several lanes, one prompt per lane, one
handoff folder per lane, and one decision sheet. `pai round` prepares, runs and closes one
([REFRESH.md](REFRESH.md#b-every-round), part (b)). A single `pai run` with a few task notes is a
small round of its own.

## lane

One line of work inside a round, with its own Git worktree, branch (`worker/<round>/<lane>`),
agent session and list of item IDs. Lanes own separate files so that they never edit the same
lines. In `pai run`, the lanes are named `a`, `b`, … and each one gets one task note.

## worker

The agent session that works in one lane: Claude Code or Codex, in a tmux window, often with
Remote Control so that you can follow it from claude.ai. A worker edits files and runs tests in
its own worktree. It does not commit, push, merge or answer trust prompts. In the first-use
walk-through the worker is a **fake** (a test program that pretends to be Claude Code), so that
nothing needs an account.

## mastermind

The coordinating agent session (or you, early on). It splits your request into lanes, items
and decision-sheet questions; it sends prompts and restart messages; it reads the checkpoints
and decides what is ready for you. It does **not** make your decisions: it writes them down
as a decision sheet. PAI is the set of commands it uses to do this without hand work.

## checkpoint

A sealed snapshot of one lane's progress, written every block of about 10 items, and also
before a worker stops early. It is a folder `cp-<name>/` in the lane's handoff folder with at
least `HANDOFF.md` and `NEXT-PR.md`. A sealed checkpoint is not edited; corrections go into
the next one. `pai queue checkpoint FILE ID --handoff PATH` records its sha256 in the queue log.

## handoff

The worker's report to whoever comes next: you, the mastermind, or a fresh session. Today PAI
has several formats under this name (the draft from `pai handoff new`, a branch's `HANDOFF.md`,
the queue checkpoint table). HANDOFF v2 (`pai-handoff/2`) is being defined by the core lane to
replace them. A v2 handoff has:

- a State table (implementation, validation, commit, merge, deployment), where each row says
  where it comes from;
- an Items table with a `Totals:` line;
- Evidence, with paths and sha256;
- two Decisions sections: within the worker's authority, and for the owner;
- Blockers, each with the command that unblocks it;
- Next.

## NEXT-PR

`NEXT-PR.md`, next to each checkpoint's `HANDOFF.md`, is the worker's proposal for the pull
request. It lists the branch, the `git add` lists split into commits with their messages, the
PR body, and the checks to run before merging. Nothing in it is executed by the worker. After
you approve it, you, or `pai worktree publish`, carry it out.

## decision sheet

The list of questions only you can answer, written by the mastermind or a worker. Each entry
has:

- an ID and the question;
- the options and the trade-off of each;
- a recommendation;
- the default if you do not answer;
- the items it blocks.

You answer by ticking a box in the Markdown file (in any editor, or Obsidian). Workers read it
before they start.

## JEV

TypeSafe's judgment model, used by PAI as a cheap second opinion. It answers fixed yes/no or
pick-one questions with a probability, for example "is this note a ready-to-run task?" or
"does this report say the checks passed?". It costs about 480 input tokens per question.

- It is optional: `pai init` asks for a key, and pressing Enter skips it.
- It never overrides a real check.
- It judges how a report is **written**, not whether it is true. A report that only claims
  "all tests pass" still scores high on "checked".

The key comes from `TYPESAFE_API_KEY` (or your system's credential store) and is never written
to a file by pai.

## trusted root

A folder whose subfolders your agent already treats as trusted. Claude Code and Codex ask
"do you trust this folder?" the first time they start in a new directory, and pai never
answers that for you. Each new worktree in a fresh place means another prompt per lane per
round.

On R6 day (2026-11-22, Claude Code 2.1.330) the owner observed that worktrees created under a
folder that had already been trusted did not ask again. So keep all rounds under one root,
for example `~/pai-lanes/<round>/<lane>`, trust it once, and keep using it. This
is an observation of one Claude Code version, not a documented guarantee: if a prompt
appears, answer it (`pai trust`).

## Instruction layers (L0–L3)

The layers of what agents read before they work; see [AGENTS-LAYERS.md](AGENTS-LAYERS.md).

- **L0, constitution**: the short `AGENTS.md` at the repository root with the rules that hold for
  every session: what agents may never do, the definition of done, evidence and scope. The owner
  writes it. The target is 250 lines or fewer.
- **L1, contract**: one file per subsystem (for example `docs/contracts/extraction.md`) with how that
  area works and what must keep working. It is read only when a task touches that area. No PAI
  command handles contracts yet.
- **L2, lane instructions**: what one lane needs for one round (goal, scope, base, checks, other
  lanes), written by `pai worktree init` as a managed section: into `CLAUDE.local.md` for Claude
  Code lanes, and into `AGENTS.md` for Codex lanes.
- **L3, proposal**: a suggested rule change (JEV-ranked rules, a worker's finding, a prompt
  improvement). It changes nothing until the owner accepts it.

## Vault notes (Obsidian as the console)

These come with the round lane of R6 (reported-by-round). The guide is [OBSIDIAN.md](OBSIDIAN.md).

- **Home**: `PAI/Home.md`, the console. It has four blocks: open rounds, lanes that need you, open
  decisions, unapproved handoffs. Each block is a Dataview query plus a plain table.
- **board**: `PAI/rounds/<round>/ROUND.md`, one row per lane of a round. ("Board" also names the
  plain-text `BOARD.md` that `pai round status --board` writes.)
- **lane note**: `PAI/rounds/<round>/<lane>.md`, one lane's status in its front matter.
- **decision note**: `PAI/decisions/<round>-<ID>.md`, one question of the decision sheet. Your
  answer goes in its `choice:` field.
- **managed note**: a note PAI wrote. It has `pai_managed:`, `pai_sha256:` and the markers
  `<!-- pai:managed:begin pai-vault/1 -->` … `<!-- pai:managed:end -->`. PAI rewrites it only while
  nobody has edited it. Notes without the marker are yours and are never touched.

**Front matter fields**, by note (the front matter is the state; tables and queries only show it):

| Field | In | Meaning |
| --- | --- | --- |
| `pai_managed` | every PAI note | the note's kind and format: `home/1`, `round/1`, `lane/1`, `decision/1` |
| `pai_sha256` | every PAI note | checksum of what PAI wrote (without `updated` and your fields): an edit shows up as a mismatch |
| `updated` | every PAI note | UTC time PAI last wrote it |
| `rounds_open`, `lanes_need_owner`, `decisions_open`, `handoffs_unapproved` | Home | the four counts |
| `round`, `project` | board, lane, decision | which round and project |
| `status` | board: `open` / `closed`; lane: `running`, `idle`, `blocked-on-dialog`, `owner-draft`, `queued`, `exited`, `no-window`; decision: `open` / `answered` | where it stands |
| `base`, `lanes`, `round_json` | board | base commit, lane count, path of ROUND.json |
| `dialog`, `queued` | lane | which Claude Code dialog blocks the lane (`trust`, `permission`, `teach-auto-mode`, `other`), and whether a message waits in its queue |
| `lane`, `current_id`, `last_cp`, `totals`, `blockers`, `branch`, `worktree_state` | lane | the lane, the item in progress, last checkpoint, counts per state, blockers, branch, clean or N changed |
| `needs_owner`, `needs_owner_why` | board, lane | `true` and the reasons when only you can unblock it |
| `id`, `question`, `options`, `recommendation`, `default`, `deadline`, `blocks` | decision | the decision as written in the sheet |
| `choice`, `choice_note` | decision | **yours**: your answer, which `pai decide answer --from-note` applies |
| `answer`, `source`, `source_sha` | decision | the recorded answer; the sheet it came from and the checksum of its text |

On a sealed handoff (`HANDOFF.md`, format `pai-handoff/2`, reported-by-core) the front matter is:

- `format`, `status`, `approved`, `project`, `round`, `lane`, `checkpoint`, `task`;
- `base_sha`, `head_sha`, `queue`, `queue_sha256`, `created`, `generator`.

`pai handoff seal` adds `sealed_sha256` (the body's sha256) and `approval: pending`. Approving sets
`approved: true` and `approval: approved` (or `stale` when the body changed after sealing) and writes
`DECISION.json` beside it. Setting `approved: true` by hand alone approves nothing.

## Other words you will see

- **base**: the commit a round or run starts from, pinned when it starts (`origin/main @ <sha>`).
- **task note**: a Markdown file with `status: todo` that tells one lane what to do (`pai task new`).
- **queue**: an item list kept by `pai queue`, which says where a worker is so that a new session can
  continue (`pai queue restart FILE`).
- **RC (Remote Control)**: Claude Code's mode that lets you follow and type into a session from
  claude.ai. `pai status` shows it per lane as `verified`, `not-connected` or `not-supported`.
- **evidence label**: the tag on every number in a report: `real run`, `synthetic`, `fake agent`,
  `mock endpoint`, `JEV`, `self-report`, or `reported-by-<lane>`.
