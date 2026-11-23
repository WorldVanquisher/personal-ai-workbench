# Obsidian as PAI's console

English | [简体中文](OBSIDIAN.zh-CN.md)

<!-- doc_check: fields -->
PAI keeps a round's state as Markdown notes in your vault: the board, one note per lane, one note
per decision. Obsidian is where you **look, approve and choose**. The rules (owner decision D9,
2026-11-22):

- **State is Markdown with front matter.** Agents and scripts read and write it directly.
- **Queries only display.** A Dataview or Bases query shows the state; it is never the source of it.
- **Plugins are optional.** Every view also exists as a plain table in the note.
- **Your own notes are never changed.** Every note PAI writes carries a managed marker, and PAI
  rewrites only notes it wrote and nobody edited.

The vault is just a folder. Any editor, `cat` or `grep` sees the same thing Obsidian does.

The commands on this page are specified in [`docs/ROUND.md`](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/ROUND.md) and [`docs/HANDOFF-V2.md`](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/HANDOFF-V2.md). The layout below was
written into the owner's real vault by `pai round status --write-vault` on 2026-11-22.

## 1. The folders

| Path in the vault | What it is | Written by |
| --- | --- | --- |
| `PAI/Home.md` | the console: open rounds, lanes that need you, open decisions, unapproved handoffs | `pai round status --write-vault` |
| `PAI/rounds/<round>/ROUND.md` | the board of one round: one row per lane | the same |
| `PAI/rounds/<round>/<lane>.md` | one lane: status, current item, last checkpoint, totals, blockers | the same |
| `PAI/decisions/<round>-<ID>.md` | one decision: question, options, recommendation, default, your `choice` | `pai round collect --write-vault` |
| `Agent Inbox/<round>/<lane>/cp-*/` | the sealed checkpoints: `HANDOFF.md`, `NEXT-PR.md`, `SEAL.json` | `pai handoff seal` |
| `Agent Inbox/<round>/<lane>/NOTES-FOR-<lane>.md` | messages between lanes | the workers |

A lane named `round` gets `round-lane.md`, because `round.md` and `ROUND.md` would be one file on
Windows and macOS.

To check it worked, run `ls "$VAULT/PAI" "$VAULT/PAI/rounds"`. Success: `Home.md`, `decisions`,
`rounds`, and one folder per round. If `PAI/` is missing, run `pai round status ROUND.json
--write-vault` once on the machine that runs the round. Then wait for the sync (see
[REFRESH.md](REFRESH.md#c-common-pitfalls)) before you look on another device.

## 2. Open the console: `PAI/Home.md`

1. In Obsidian, open the vault, then open `PAI/Home.md` (Ctrl/Cmd-O, type `Home`). Pin it, or make
   it the start page with the Homepage plugin if you use it.
2. Check the front matter at the top (Obsidian shows it as **Properties**). `updated:` is when PAI
   last wrote the note. Four counts follow: `rounds_open`, `lanes_need_owner`, `decisions_open`,
   `handoffs_unapproved`.
3. Work through the four blocks, top to bottom. PAI writes these notes in Chinese; the headings are
   进行中的轮次, 需要 owner 处理的 lane, 还没回答的决策 and 还没批准的交接:
   1. **Active rounds**: click a round to open its board (`ROUND.md`).
   2. **Lanes that need you**: each row gives the reason (`needs_owner_why`). Act on it:
      - a waiting folder-trust prompt: answer it in that lane's window;
      - JEV disagrees with the evidence, or a checksum does not match: read the lane's last handoff
        before anything else;
      - an item is blocked or owner-only: the handoff's Blockers section says which command to run;
      - the session exited with items left: on the round machine run `pai round launch LANE --write`,
        then `pai round next LANE --send` (more cases: [REFRESH.md](REFRESH.md#e-recovery)).
   3. **Open decisions**: open each one and answer it (section 5).
   4. **Unapproved handoffs**: read each one and approve or reject it (section 5).

Success: after you act and the round machine runs `pai round status --write-vault` again, the
row disappears and the count drops.

If something looks wrong:

- `updated:` is older than you expect: the round machine has not refreshed it. Run that command
  there, then wait for the sync.
- The note does not exist: see section 1.
- You see `Home (conflicted copy …).md` or `Home.sync-conflict-….md` beside it: two devices wrote at
  once. Delete the copy; PAI rewrites Home.md on its next run.

## 3. Without plugins, with Dataview, with Bases

Each block of Home.md has two forms: a `dataview` query, and a **plain table** that is a snapshot
from when PAI wrote the note. Which one you see depends on your plugins:

| You have | You see |
| --- | --- |
| no plugins (or any other editor) | the plain tables. The `dataview` blocks show as code; ignore them |
| **Dataview** (a community plugin) | the plain tables, and above each one a **live** table that updates as soon as a lane or decision note changes |
| **Bases** (a core plugin: Settings → Core plugins → Bases; enabled in the owner's vault, `.obsidian/core-plugins.json`) | nothing extra yet. PAI writes no `.base` file, because the format was not checked against Obsidian's documentation |

To turn on the live tables:

1. Settings → Community plugins → Browse, search "Dataview", Install, Enable.
2. Reopen `PAI/Home.md`.
3. Success: each `dataview` code block is replaced by a table with the same columns as the plain
   table below it. If it still shows code, Dataview is installed but not enabled; enable it in
   Settings → Community plugins.

Plugins only change how the state is shown. Turning one off loses nothing: the front matter is the
state.

## 4. A lane note, a decision note

A **lane note** (`PAI/rounds/<round>/<lane>.md`) has these front-matter fields:

- `lane`, `status` (`running`, `idle`, `blocked-on-dialog`, `owner-draft`, `queued`, `exited`, `no-window`),
  `dialog` (`trust`, `permission`, …), `queued`, `current_id`, `last_cp`;
- `totals`: done / prototype / blocked / owner-only / doing / todo;
- `blockers`, `needs_owner`, `needs_owner_why`;
- `round`, `project`, `branch`, `worktree_state`.

Below it is a facts table with the same values. Read it; do not edit it. Your edit would only stop
PAI from updating the note.

A **decision note** (`PAI/decisions/<round>-<ID>.md`) has `question`, `options`, `recommendation`,
`default`, `deadline`, `blocks`, `status` (`open` or `answered`), and two fields that are **yours**:
`choice` and `choice_note`.

Every note PAI writes also has `pai_managed: <kind>/1`, `pai_sha256` and `updated`, and its body
sits between `<!-- pai:managed:begin pai-vault/1 -->` and `<!-- pai:managed:end -->`.

## 5. Answer a decision, approve a handoff

**A decision.** In the decision note's front matter, fill `choice:` with one of the `options`
(the full text, or its first word such as `A`), and optionally `choice_note:`. Then, on the machine
that runs the round:

```sh
pai decide answer --from-note "PAI/decisions/R6-20261122-D9.md"           # checks; changes nothing
pai decide answer --from-note "PAI/decisions/R6-20261122-D9.md" --write   # ticks the decision sheet
```

Success: the source decision sheet gets its box ticked and a `回答：…` line, and the note says
`status: answered`. It refuses, and writes nothing, in three cases:

- the note's PAI part was edited (`pai_sha256` no longer matches);
- your `choice` is not one of the options;
- the decision's text in the source sheet changed since the note was written (`source_sha`).

In each case, read the message, then fix the `choice` or ask for a fresh note.

**A handoff.** Read `Agent Inbox/<round>/<lane>/cp-X/HANDOFF.md` and its `NEXT-PR.md`. There are two
ways to approve. Both check that the handoff's body is byte for byte what was sealed: the sha256 in
`sealed_sha256` and `SEAL.json`.

*From Obsidian:*

1. In the handoff's front matter, change `approved: false` to `approved: true`. That alone approves
   nothing.
2. On the round machine, run:

   ```sh
   pai handoff approve PROJECT "Agent Inbox/R6-20261122/docs/cp-C" --from-vault
   ```

*From a terminal:* use the command that `pai handoff seal` printed on its `approve` line. Its sha is
the body's sha256, not `sha256sum` of the whole file.

```sh
pai handoff approve PROJECT "Agent Inbox/R6-20261122/docs/cp-C" --expect-sha <the 16+ hex seal printed>
pai handoff reject  PROJECT "Agent Inbox/R6-20261122/docs/cp-C" --expect-sha <…> --reason "what is wrong"
```

Success: `DECISION.json` appears beside the handoff, and its front matter says `approved: true`,
`approval: approved`. Only the front matter changes; the body is never touched. If the body was
edited after sealing (even a typo fixed in Obsidian), the command refuses (exit 2), nothing is
approved, and the note gets `approval: stale`. Put the correction into the next checkpoint and
approve that one instead.

## 6. What PAI never does to your vault

- It never writes a note it did not create. A note without `pai_managed`, or with broken markers,
  or whose content no longer matches `pai_sha256`, is reported as `SKIPPED` and left exactly as it
  is. To let PAI write it again, delete it.
- It never writes while a sync conflict copy sits next to the note
  (`NAME.sync-conflict-<date>-<time>-<device>.md`, or `NAME (conflicted copy …).md`), and it never
  reads a conflict copy as state.
- It never answers a decision or approves a handoff by itself. `--write` and `approve` are your
  commands.
- Writing the same state twice changes nothing. A second `--write-vault` run reports 0 changes.
