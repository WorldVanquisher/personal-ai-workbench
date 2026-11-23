# 0.5.0 release notes (draft)

English | [简体中文](RELEASE-NOTES-DRAFT.zh-CN.md)

**Draft for the owner, not released.** The tree still says 0.4.0. Sources: the four R6 lanes'
NEXT-PR.md files and core's [CHANGELOG-CANDIDATE.md](CHANGELOG-CANDIDATE.md), which lists every
change with its test. This page is the user-facing summary. Before the release, check the two lines
marked **owner** below, then copy the page into RELEASE_NOTES.md.

## In one sentence

0.5.0 runs a whole round of parallel agent lanes from files: it opens the round, tells you which
lane waits for you, hands work and decisions back and forth through Markdown notes you can read in
any editor or in Obsidian, and collects the result for one PR that you merge.

## New

### Rounds: `pai round`

One command family for a round of parallel Claude Code lanes. The round lives in `ROUND.json`,
`BOARD.md` and a daily coordinator log, all Markdown or JSON in the vault's `Agent Inbox/`.

- `pai round new PROJECT ROUND --lane L --prompt FILE --write` clones from the merged main, makes
  one worktree per lane and fills each lane's prompt. Without `--write` it is a preview.
- `pai round status` shows one line per lane: session, window, state (running, idle, waiting on a
  dialog, a draft typed but not sent, queued, exited), worktree, last checkpoint, queue totals.
- `pai round launch LANE --paste-prompt --write` starts a lane and pastes its prompt only when that
  is safe; `pai round next` and `pai round inject` send a resume or a message the same way. Every
  send is recorded and never repeated blindly.
- `pai round collect` and `pai round integrate` gather the lanes' handoffs and rehearse the merge
  on a scratch copy.
- `pai decide answer` answers a decision sheet, approving exactly the text you read.

Full reference: [ROUND.md](../ROUND.md). A complete two-lane example with its transcript:
[examples/two-lane-round/](../../examples/two-lane-round/README.md).

### The prompt library: `pai prompt`

The prompts that start and steer workers are versioned files with declared placeholders
(`pai-prompts/1`). `pai prompt list` shows them, `pai prompt lint` checks them, and
`pai prompt init --dest DIR --write` installs them into your vault; `pai prompt diff --dest DIR`
shows what an upgrade would change. Only text inside the managed markers is ever rewritten. The
library is written in Chinese (owner decision PRM-D1). See [PROMPTS.md](../PROMPTS.md).

### Handoff v2

One handoff format, `pai-handoff/2`, with a State table, per-item rows and totals checked against
the queue. `pai handoff seal PROJECT FILE` checks a handoff, prints the sha256 of its body and files
it in the round's inbox. You approve exactly that version with
`pai handoff approve PROJECT DIR --expect-sha SHA`, or from Obsidian with `--from-vault`; an approval
becomes stale when the file changes. See [HANDOFF-V2.md](../HANDOFF-V2.md).

### Vault notes, with Obsidian as an optional console

PAI writes the round's state as plain Markdown notes with front matter: a home page, one note per
lane and one per decision. Generated notes carry managed markers; PAI never rewrites a note you
wrote or edited. `pai vault lint` checks the notes, `pai vault wait FILE --sha SHA --timeout 120`
waits until a synced copy has arrived. Obsidian is optional: every note reads the same in any
editor. See [OBSIDIAN.md](../OBSIDIAN.md) and [FRONTMATTER.md](../FRONTMATTER.md).

### Lane instructions in `CLAUDE.local.md` (layer L2)

`pai worktree init PATH --target claude-local --write` writes a lane's own instructions into a
git-ignored `CLAUDE.local.md` in its worktree, so the worktree stays clean. A real Claude Code
session reads it (mastermind 2026-11-22 real run, Claude Code 2.1.330). `pai context --worktree PATH`
shows what a lane will read. Why the layers matter: [AGENTS-LAYERS.md](../AGENTS-LAYERS.md).

### The JEV ledger

When JEV is enabled, every request is logged with its token counts. `pai jev usage` sums them by day
and project. JEV stays optional and advisory: it never overrides a check.

### Also new since 0.4.0

Continuing a lane in a fresh session (`pai recover --continue LANE`), transfer packets between
machines, numbered work queues (`pai queue`), a guard that refuses shell writes outside a task's
paths, and CI that runs the workbench suite and the documentation check. Details:
[CHANGELOG-CANDIDATE.md](CHANGELOG-CANDIDATE.md).

## Upgrading from 0.4.x

1. **Install** the same way as before with the new version: `uv tool install` of tag `v0.5.0`, or
   the new archive with `bin/pai install`. Configuration, task notes and run state stay.
   Rollback: install 0.4.x the same way ([REFRESH.md](../REFRESH.md#a-upgrade-pai)).
2. **Scripts that read `pai status --json`**: it is now an object (`pai_status_v1`); the lane rows
   are under `lanes`. Replace `.[0]` with `.lanes[0]`. See [MIGRATION.md](../1.0/MIGRATION.md).
3. **`pai doctor` exit code**: 1 now means only an essential gap (`gh` missing is no longer one).
4. **Headless permission mode (owner):** headless workers (`swarm/pai_swarm.py run`) default to
   `manual` in this tree. A real run showed that such a worker then cannot write the files its task
   asks for; `acceptEdits` with the guard works. Decision D-R6-1 sets the default before release.
   Per run: add `--permission-mode acceptEdits` to that command
   ([PERMISSION-MODES.md](PERMISSION-MODES.md)).
5. **If your repository has both `CLAUDE.md` and `AGENTS.md`**: Claude Code reads only `CLAUDE.md`.
   Put the line `@AGENTS.md` into `CLAUDE.md` so both agents read the same rules
   ([AGENTS-LAYERS.md](../AGENTS-LAYERS.md#l0-the-constitution)).
6. **Prompts in your vault**: run `pai prompt diff --dest DIR` first, then `pai prompt init --dest DIR --write`.
   Your own edits outside the managed markers stay.
7. **tmux below 3.8** (Ubuntu and WSL ship 3.6): `pai round` pastes only into a pane running
   Claude Code or Codex ([FIRST-USE.md § 1](../FIRST-USE.md#1-what-you-need)).

## Tested, and not yet

- **Tested:** the four lanes merged, on Arch Linux (lane trees) and on WSL Ubuntu with tmux 3.6
  (merged candidate): core and workbench suites, archives, bundle smoke, onboarding, doc_check.
  Real runs: a headless Claude Code smoke (7 of 7 with `acceptEdits`), the `CLAUDE.local.md` loading
  check (10 of 10), `pai jev check`. Platform detail:
  [FIRST-USE-PLATFORMS.md](../FIRST-USE-PLATFORMS.md).
- **Not yet (owner):** CI green on the release commit; the package step on WSL; the round tests
  rerun on tmux 3.6 after the round lane's fix; the workbench on macOS; a real Codex lane; a whole
  round driven by `pai round` against real agents.
