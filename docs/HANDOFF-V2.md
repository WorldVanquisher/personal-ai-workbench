# HANDOFF v2 (`pai-handoff/2`) and NEXT-PR.md

One handoff format for every place PAI used the word "handoff" before (R6-COR-019; 融合方案 §2,
§5.1): the vault draft (`pai handoff new`), the queue checkpoint handoff (`pai queue checkpoint
--handoff`), the branch `HANDOFF.md` a continuation reads, the root-level `PAI-*-HANDOFF.md` files
and the pai_swarm auto-archive. The worker writes it; `pai handoff seal` checks and freezes it;
the owner approves or rejects it by its sha256; `pai worktree preview` can require an approved
one, and the PR body is generated from it.

Aligned with the prompt library's `pai/prompts/checkpoint.md` (prompts lane, R6-PRM-006): the
same front-matter keys, section names and Source labels. Version 1 handoffs stay readable
(see "Discovery").

## Where it lives

```
<vault>/<inbox>/<round>/<lane>/<checkpoint>/HANDOFF.md      sealed copy (pai handoff seal)
<vault>/<inbox>/<round>/<lane>/<checkpoint>/NEXT-PR.md      the PR plan, when there is one
<vault>/<inbox>/<round>/<lane>/<checkpoint>/SEAL.json       what seal checked, with every sha256
<vault>/<inbox>/<TASK-ID>-<UTC>/HANDOFF.md                  version 1 drafts (pai handoff new)
```

`<round>` and `<lane>` are `[A-Za-z0-9._-]` names (for example `R6-20261122` and `core`).
`<checkpoint>` is `cp-<Block>` (`cp-A`) or `cp-final`.

## IDs

Written in full everywhere (PRM-D7): `R6-COR-031..R6-COR-035` or a list, never `R6-COR-031..035`;
`seal` refuses the short form in HANDOFF.md and NEXT-PR.md.

One task ID everywhere: `<PROJECT>-<ROUND>-<LANE>-<NNN>`, for example `PAI-R6-COR-001`,
`FLH-R6-REL-001`. The short `R6-COR-001` form (round, lane, number) is accepted too. `pai handoff`,
`pai queue` and `pai plan` all use `pai.handoff.ITEM_ID` (R6-COR-025). It is a superset of the
older `R2-001` and `PARENT-001` queue IDs, so existing queues still load.

## HANDOFF.md

Front matter: plain `key: value` lines, no YAML types. Every key is present; a value nobody knows
is `UNKNOWN`.

```
---
format: pai-handoff/2
status: draft | sealed | approved | rejected | superseded | continued | published
approved: false | true
project: <project key>
round: <round>
lane: <lane>
checkpoint: cp-<Block> | cp-final
task: <the checkpoint's last item ID, or the task ID>
base_sha: <40 hex>
head_sha: <40 hex; equal to base_sha when nothing was committed>
queue: <path of the queue file, or none>
queue_sha256: <sha256 of the queue log at sealing, or none>
created: <UTC, YYYY-MM-DDTHH:MM:SSZ>
generator: <who wrote it: "pai <version>" or "worker">
---
```

Written by tools, never by the worker by hand: `sealed_at`, `sealed_sha256`, `approved_by`,
`approved_at`, `rejected_reason`, `jev_done`, `jev_checked`, `jev_verify`. `pai handoff seal` adds
the JEV keys to `SEAL.json`, not to the handoff, so sealing never changes the sealed bytes.

Sections, in this order, with these exact names:

| Section | Content |
| --- | --- |
| `## Summary` | 3-5 sentences: what this block did and did not do |
| `## State` | table `\| State \| Value \| Source \|` (exactly three columns, PRM-D5), exactly five rows: Implementation, Validation, Commit, Merge, Deployment. Each row stands alone; none is inferred from another. A value says what happened (`none`, `not run`, `not merged (owner-only)`), never UNKNOWN (PRM-D6) |
| `## Items` | the output of `pai queue table QUEUE`, pasted as is (PRM-D3): one row per item, then `Totals: todo N, doing N, done N, prototype N, blocked N, owner-only N (N items)` |
| `## Evidence` | one line per file: `relative/path  sha256(64 hex, full)  command that produced it`. The file is copied into the checkpoint folder first and cited by its path relative to HANDOFF.md; `seal` copies it into the sealed folder and checks the sha256 (PRM-D4) |
| `## Decisions within authority` | what was chosen, why, how to reverse it |
| `## Decisions for the owner` | decision-sheet format: question, options, trade-offs, recommendation, default; none of it done |
| `## Blockers` | one sentence each, ending with the command the owner runs in backticks (PRM-D6); `none` when there are none |
| `## Next` | the next start ID and the RESTART.md path |

Source labels (State and every number): `real run`, `synthetic`, `fake agent`, `mock endpoint`,
`JEV`, `reported-by-<lane>`, `self-report`. Item states are the queue's: `todo`, `doing`, `done`,
`prototype`, `blocked`, `owner-only`. The `Totals:` line is what `pai queue table` prints, so
`pai queue check` and `pai handoff seal` can compare them.

## NEXT-PR.md

Written by the worker. The owner approves it, and `pai worktree publish` carries it out. The
worker runs none of its git write commands. Sections: `## Branch` (branch, base sha, target
branch), `## Commits` (per commit: an explicit `git add` list without wildcards, the message,
the result of the publish metadata check (`swarm/publish_check.py`), the item IDs), `## PR body`,
`## Checks before merge` (which ones rerun on merged main), `## Not included`. Every item ID it
names appears in the handoff's Items table (checked by seal).

## Lifecycle

```
draft ──seal──▶ sealed ──approve --expect-sha──▶ approved ──preview/publish──▶ published
                  │  └──reject --expect-sha──▶ rejected
                  ├──recover --continue──▶ continued   (once)
                  └──a later checkpoint──▶ superseded
```

- `pai handoff seal PROJECT FILE [--queue Q] [--item ID] [--next-pr FILE] [--jev-verify] [--dry-run]`: checks the format, checks
  NEXT-PR.md item IDs, compares Items/Totals with the queue when given, records the sha256 in the
  queue log (`pai queue checkpoint`), and copies HANDOFF.md, NEXT-PR.md and SEAL.json into
  `<inbox>/<round>/<lane>/<checkpoint>/`. The files and the directory are fsynced. It is idempotent
  by (task, sha256): sealing the same bytes again changes nothing. Different bytes for a
  checkpoint that is already sealed are refused; write the correction into the next checkpoint.
- `pai handoff approve PROJECT DIR --expect-sha SHA256 [--by NAME]` (or `--from-vault`, after `approved: true` in
  the sealed note) and `pai handoff reject PROJECT DIR --expect-sha SHA256 --reason TEXT`: refuse unless the sealed
  HANDOFF.md's body still has the sha256 the owner read (`sealed_sha256`). They record the decision in
  `DECISION.json` next to it and set `status` and `approval` in the front matter; the body is never
  rewritten, so its sha256 stays the one sealed. `--from-vault` marks a body edited after sealing
  `approval: stale` and records nothing.
- `pai worktree preview --handoff DIR --require-approved-handoff`: the plan records `task`,
  `queue_sha256` and `handoff_sha256`, and refuses a handoff that is not approved.

## Discovery (`pai handoff list`, `pai context --handoffs N`)

Version 1 drafts at `<inbox>/<ID>-<UTC>/HANDOFF.md`, and version 2 handoffs at
`<inbox>/<round>/<lane>/<cp>/HANDOFF.md` (three levels; deeper folders are not walked). Both are
listed with their status. A folder without HANDOFF.md (a memory package, notes) is skipped with
one line, not reported as a problem (R6-COR-016). An approved handoff is preferred over a sealed
one, and a sealed one over a draft, when `pai context` has to choose.

## Where a lane's L2 instructions live (R6-COR-063)

The lane's own rules (goal, scope, checks, owner rules: the `pai worktree init` managed section, "L2") are
written by `pai worktree init` into one of two places:

| Lane | File | In git? | Command |
| --- | --- | --- | --- |
| Codex, or the default | `AGENTS.md` at the worktree root | an uncommitted change (the worktree is dirty, so `pai recover --continue` and worktree reuse refuse until it is committed) | `pai worktree init PATH ...` |
| Claude (owner decision PRM-D9 = A; off by default) | `CLAUDE.local.md` at the worktree root, one per worktree | never: `/CLAUDE.local.md` is added to the repository's `info/exclude` before the file is written | `pai worktree init PATH ... --target claude-local` |

Claude Code reads CLAUDE.local.md next to CLAUDE.md; Codex does not, so Codex lanes keep AGENTS.md.
`pai context PROJECT --task ... --worktree PATH` reads L2 from that worktree (CLAUDE.local.md first, then the
worktree's own AGENTS.md), and `pai plan` sees the lane's scope in any of AGENTS.md, CLAUDE.md, CLAUDE.local.md.
A handoff does not copy L2; it is in the worktree, and the continuation reads it there.
