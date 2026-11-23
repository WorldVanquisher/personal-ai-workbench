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

## R4: the owner-to-worker loop (on top of the merged worktree publishing, PR #13)

New:
- `pai plan status PLAN --queues DIR [--lane L]`: per lane, current, blocked (with the reason),
  owner-only, remaining, done and ready items.
- `pai plan split-owner PLAN SLICE` and goals lines with `owner: yes`: a slice that needs live
  access becomes a local rehearsal for the worker and an owner decision queued as owner-only;
  `plan check` warns about goals that mention live work.
- `pai queue show FILE [ID]`: checkpoints with their handoff and whether it still matches, or one
  item's text and full history; `pai queue mark ID todo --reason` reopens an item (an owner's retry);
  item ids may have several prefix words (`R4-PAI-001`).
- `pai worktree init --reserve implementation|testing|design`; the managed section now lists the
  lanes' write scopes, the decisions the worker owns, what needs the owner, and what to do above
  the worker's authority; the preview prints the same.
- `pai worktree publish --expect-plan SHA` (the preview prints the plan's sha256) and an `outcome`
  line after every `--yes` run; `pai worktree list` shows commits not in the fetched base.
- `pai handoff new --decision` and `--needs-owner`.

Changed:
- `pai worktree publish` refuses a plan made before modes were recorded: preview again.
- The refresh commands quote every value for bash and PowerShell (a branch name could run code).
- AGENTS.md generation refuses broken, duplicate or malformed markers and non-UTF-8, UTF-16 and
  CR-only files, with the line numbers and the fix; nothing is written.
- The worktree page: the dialog keeps keyboard focus inside it; a refused save moves focus to its
  notice; split rows stack on a phone; a late answer can no longer show another worktree's file.
- `tools/build_bundle.py` accepts pre-release versions such as `0.5.0rc2`.

## R6b (2026-11-22): core lane (worker/R6-20261122/core; lane tree; rerun on merged main needed)

Synthetic tests, fake agents and mock endpoints, except where a line says real run or JEV. The other
lanes (docs, prompts, round) add their own sections; nothing here is reported-by another lane.

- **CI runs the workbench.** A `workbench` job (Linux, Python 3.11/3.13) runs `swarm/tests` strictly
  with `--jobs 4` and the headless fake-claude script; doc_check runs once the docs lane's tool is in
  the tree. `tools/strict_unittest.py --jobs N` runs one process per module (480 s → 154 s, real run,
  Arch Linux, 8 CPUs).
- **Tests no longer depend on the host:** `hermetic_env()` (git identity, git config, tmux, keys);
  the plain-bash onboarding check ignores a host `command_not_found_handle`.
- **verify.py:** a missing pip / ensurepip / setuptools is SKIPPED with its name; each step says
  whether CI runs it (`"ci"` in verify-report.json; "CI covers?" in EVIDENCE-MATRIX.md).
- **guard:** the Bash hook refuses writes outside the task's `paths:` (redirections, tee, sed -i, cp,
  mv, rm, dd of=, ...), found by the 2026-11-22 real headless smoke. Permission modes:
  docs/0.5.0/PERMISSION-MODES.md.
- **Dispatch fixes:** a handed-off note is not restarted by a plain `pai run`; `pai worktree init
  --remote/--base`; split plans see CLAUDE.md lanes; task cards keep run records private; advised
  `ssh-add` commands keep `~` outside the quotes.
- **Handoff v2** (`pai-handoff/2`, docs/HANDOFF-V2.md): `pai handoff new --queue --round --lane`,
  `pai handoff seal` (checked, sha256 in the queue log, copied to `<inbox>/<round>/<lane>/<cp>/`,
  idempotent, fsynced), `pai handoff approve|reject --expect-sha`; v1 and v2 discovery; one task ID
  form (`PAI-R6-COR-001`); continuations write v2 too; `pai worktree preview --handoff DIR
  --require-approved-handoff` and a PR body generated from the sealed handoff.
- **JEV:** an `evidence` question and a `checked` question that counts the task's own checks (real
  JEV, synthetic reports: a grep self-check the task asked for went from checked 0.06 to 0.95; a
  report that only claims "all tests pass" from 0.86 to 0.27, evidence 0.05); triage writes a model
  only for a note that becomes todo; usage log and `pai jev usage`; `pai handoff seal --jev-verify`
  (agrees / DISAGREES, a reference only).
- **Owner tool:** `tools/real_agent_smoke.sh` repeats the 2026-11-22 headless smoke (owner runs it).

Version: still 0.4.0 in the tree. The bump to 0.5.0 is prepared, not applied: the core lane's
`proposals/version-0.5.0.patch` in the round's Agent Inbox (owner decision).
