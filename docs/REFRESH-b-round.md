# Refresh (b) with `pai round`: the ready-to-swap version

English | [简体中文](REFRESH-b-round.zh-CN.md)

<!-- doc_check: pending pai round -->

**Not active yet.** [REFRESH.md](REFRESH.md#b-every-round) part (b) still documents the R6 scripts.
This page is its replacement for after the round lane is merged (owner D-DOC-4). To swap it in,
replace everything between `<!-- refresh-b:begin -->` and `<!-- refresh-b:end -->` in REFRESH.md
(and the same markers in REFRESH.zh-CN.md) with the block between the same markers below, then run
`python3 -B tools/doc_check.py`. Syntax: reported-by-round, `docs/ROUND.md`; every command and flag
below was confirmed on a four-lane scratch merge by doc_check (2026-11-22).

<!-- refresh-b:begin -->
A round starts from the **merged** main, in **new** worktrees under one trusted root, with freshly
filled prompts. `pai round` does the steps the R6 scripts did, and keeps the round in
`<vault>/Agent Inbox/<ROUND>/ROUND.json`.

**1. Preview the round, then create it.**

```sh
pai round new pai R7-YYYYMMDD --prompt "99 Templates/PAI Worker Prompt R7.md" --vault "$VAULT"           # preview; exit 1 if something is in the way
pai round new pai R7-YYYYMMDD --prompt "99 Templates/PAI Worker Prompt R7.md" --vault "$VAULT" --write   # does it
```

What it does:

- reads main's SHA with `git ls-remote` (or takes `--base SHA`);
- finds the one main clone (refuses and lists them when there are several: add `--repo PATH`);
- fills and splits the prompt per lane, and refuses if a placeholder is left;
- creates `worker/<ROUND>/<lane>` worktrees under the trusted root.

Re-running is safe. A lane shown as `CONFLICT` (a folder in the way, a branch checked out
elsewhere) stops the write until you fix it.

**2. Start each lane** in tmux, with Remote Control, and paste its prompt:

```sh
pai round launch docs --paste-prompt --write
pai round status                                  # one line per lane: running / idle / blocked-on-dialog / exited …
```

If a lane shows `blocked-on-dialog` with dialog `trust`, answer the folder-trust question in that window yourself. Then
check the first reply: it must list the files the worker read and its first item IDs.

**3. Every day:** `pai round status` (add `--write-vault` to refresh `PAI/Home.md`), and
`pai round next LANE --send` when a lane has stopped at a checkpoint.

**4. Close the round** when every PR is merged:

```sh
pai round close              # the acceptance checklist, and which worktrees would be reclaimed
pai round close --write      # reclaims only clean, idle worktrees; branches are kept
```
<!-- refresh-b:end -->
