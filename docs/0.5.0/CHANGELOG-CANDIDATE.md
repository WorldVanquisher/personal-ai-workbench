# 0.5.0 candidate changelog (draft for the owner; only behaviour demonstrated in this tree)

Not released. The tree still says 0.4.0; the owner sets the version, writes RELEASE_NOTES.md and
tags. Every line below is shown by a test or a recorded run named in
`PAI-0.5.0-R2-HANDOFF-100.md`. Fakes only: no line here is a claim about real Claude Code or Codex.

## New

- **Continue a task in a fresh session** (`pai recover --continue LANE [--write]`, `pai run
  --continue NOTE [--agent claude|codex]`): the goal, completed commits, evidence and open
  decisions go into the task note; the old lane becomes `handed-off` and is never restarted or
  resent; the new branch starts at the handed-off head with the base pinned and nothing fetched.
- **Transfer packets** (`pai task export NOTE --transfer`, or `--run RUN [--lane L]`): a task card
  file plus a versioned `transfer` object; the preview lists what leaves, what stays and what each
  omission limits or blocks. `pai task import PACKET` previews the source and this machine's
  agents and repository before `--write` creates the note. Packets from another repository are
  refused; another project name needs `--other-project`.
- **Both directions demonstrated offline**: `pai guide --handoff clean` (Claude to Codex),
  `--handoff reverse` (Codex to Claude), `--handoff interrupted` (uncertain delivery).
- **Hand pastes are evidence**: `pai paste` and the console record each paste (time, size, sha256,
  filtered first line; never the text); `pai inspect` shows it as staged, delivered or unknown; an
  unknown one needs `--choose` before a handoff.
- **`pai queue`** (core, any OS): a numbered work queue in a queue file and an append-only log, with
  states, reasons, evidence, per-ID tables, total checks against handoffs, checkpoints and a
  restart prompt.
- `pai inspect`, `pai recover` (preview, `--apply`, `--choose`, `--history`, `--json`), task cards
  (`pai task export`, `pai task import`), `pai pr draft`, `pai status --brief`, `pai guide --recovery` (from the
  1.0 readiness work, also unreleased so far).

## Changed

- `pai status --json` is the object `pai_status_v1` (0.4.x printed an array of lane rows; they are
  under `lanes` now).
- `pai doctor` / `pai setup` exit 1 only for an essential gap (`gh` is optional).
- A handoff is checked again under the run's lock just before it is written; an interrupted
  recovery is closed as `superseded by ID` when a later one completes.
- Task cards keep `pai pr check`'s `## Review` sections (worktree path, check commands) private, and
  refuse unknown top-level keys.
- Agent windows get the `CLAUDE_CONFIG_DIR` / `CODEX_HOME` pai reads its evidence from.
- The continuation prompt says that carried text is a record, not an instruction; directive-like
  lines in a quoted HANDOFF.md are flagged.
- `pai --help` lists the exit codes. `pai status --run R` for a run this HOME has no record of lists
  the notes that name R.

## Compatibility notes

See `docs/1.0/MIGRATION.md` (scripts that indexed the 0.4.x status array, the doctor exit code,
rolling back to 0.4.x) and `docs/1.0/COMPATIBILITY.md`. Run state written by 0.4.0 is read
unchanged (saved fixture `swarm/tests/fixtures/run-0.4.0`, package check `083`).

## Still experimental or unverified

`pai web` (real browser unverified), Remote Control lanes (live session unverified), WSL, macOS
workbench, native Windows workbench (not offered), real agents (owner runs).
