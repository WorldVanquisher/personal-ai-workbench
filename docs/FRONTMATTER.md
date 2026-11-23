# Front matter of PAI notes (`pai-frontmatter-schema/1`)

Owner decision D9 (a), 2026-11-22: **Obsidian is the console, the data stays plain Markdown.**

- State is Markdown with front matter. Agents and scripts read and write it directly.
- Dataview or Bases queries only display state; nothing in PAI reads a query result.
- Everything can be read without plugins: every generated view also has a plain Markdown table.
- PAI never changes a note the owner wrote. A note PAI writes carries a marker: `pai_managed:
  <kind>/1` in the front matter and `<!-- pai:managed:begin pai-vault/1 -->` … `<!--
  pai:managed:end -->` around its generated body (the round lane's convention, reported-by-round).
  A sealed handoff is identified by `format: pai-handoff/2` instead.

The machine-readable schema is `docs/frontmatter.schema.json`, generated with `pai vault schema`
(a test keeps the two equal). `pai vault lint` applies it.

## Kinds

| Kind | Recognised by | Where | Written by | The owner may fill |
| --- | --- | --- | --- | --- |
| handoff | `format: pai-handoff/2` | `Agent Inbox/<round>/<lane>/<cp>/HANDOFF.md` | the worker; `pai handoff seal` adds `status: sealed`, `sealed_sha256`, `approval: pending` | `approved: true` |
| decision | `pai_managed: decision/1` | `PAI/decisions/<round>-<ID>.md` | `pai round` (round lane) | `choice`, `choice_note` |
| lane | `pai_managed: lane/1` | `PAI/rounds/...` | `pai round` (round lane) | nothing |
| request | `pai_managed: request/1` | `Agent Inbox/<round>/<lane>/requests/` | a lane or the mastermind asking someone for something | `status`, `answer` |

Required keys (all kinds list theirs in the schema): handoff — `format, status, approved, project,
round, lane, checkpoint, base_sha, head_sha, queue, queue_sha256`; decision — `pai_managed, status,
options, source, source_sha, pai_sha256`; lane — `pai_managed, round, status, pai_sha256`; request —
`pai_managed, id, status, from, to, created`.

## Approving in Obsidian (R6-COR-040)

1. `pai handoff seal` writes the sealed copy with `sealed_sha256:` = sha256 of the body (everything
   after the front matter). The queue log records it too (`body_sha256` of the checkpoint event).
2. The owner reads the note and changes `approved: false` to `approved: true`. Nothing is approved yet:
   the front matter alone never approves.
3. `pai handoff approve PROJECT DIR --from-vault` checks that the body still has `sealed_sha256` (and
   the sha256 in SEAL.json). Then it writes DECISION.json and sets `approval: approved`. If the body
   changed, it sets `approval: stale` and refuses. Re-seal the corrected text as the next checkpoint.

`pai handoff approve PROJECT DIR --expect-sha SHA` does the same from a terminal.

## Answering a decision in Obsidian (R6-COR-041)

The owner fills `choice:` with one of `options`. `pai vault lint` reports a choice that is not an
option. When the round lane's code is in the tree, it also checks two things: `pai_sha256` (the PAI
part was not edited) and `source_sha` (the question in the source decision sheet did not change).
`pai decide answer --from-note` (round lane) writes the answer back to the sheet.

## Lint and sync

- `pai vault lint [--project P | --vault PATH] [--json]` reads `Agent Inbox/` and `PAI/`. It exits
  1 on a PROBLEM or a CONFLICT, and never writes anything.
- A Syncthing conflict copy (`*.sync-conflict-*`) is reported as CONFLICT. PAI never deletes it:
  compare it with the original and delete the one you do not keep (R6-COR-042).
- `pai vault wait FILE --sha SHA [--timeout N]` returns when FILE exists with that sha256 (exit 0),
  or exits 1 after N seconds. The mastermind runs it before telling a worker to read a note it just
  wrote (R6-COR-044): on 2026-11-22 a sync delay made workers read an old copy twice.

## Which vault (R6-COR-043)

Every command uses one resolver (`pai.vault.resolve`). Its sources, in order: the configuration,
`--vault`, the folder above a script kept in the vault (`99 Templates/…`), then `$VAULT`. When the
configuration and `--vault` disagree, the command is refused. A path that does not exist on this
machine is refused with a hint: a container path such as `/vaults/MainVault` is not the host path
(on the NAS host the vault is `<host path>/obsidian/MainVault`).

## Not notes: the lane's L2 instructions (R6-COR-063)

L2 is not a vault note and has no front matter: it is the `pai worktree init` managed section, in the
worktree's `AGENTS.md` (Codex, default) or `CLAUDE.local.md` (Claude lanes, `--target claude-local`, never
committed). See docs/HANDOFF-V2.md, "Where a lane's L2 instructions live".
