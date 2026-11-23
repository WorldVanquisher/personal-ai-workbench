# AGENTS.md in layers: constitution, contracts, lane, proposals

English | [简体中文](AGENTS-LAYERS.zh-CN.md)

<!-- doc_check: pending pai worktree init --target, pai context --worktree, pai round, pai prompt -->

Every agent session starts by reading the repository's instruction file: `AGENTS.md` (Codex, and
Claude Code when there is no `CLAUDE.md`) or `CLAUDE.md`. Everything in it is loaded into every
session's context, for every task. That makes it the **heart** of a multi-agent project:

- a rule there reaches every worker;
- a stale line there misleads every worker;
- a long file there costs every worker context before it reads a single source file.

PAI splits what usually piles up in that one file into four layers. Each layer has a different
owner, a different lifetime, and a different place on disk.

| Layer | What it holds | Who writes it | Where it lives | Lifetime |
| --- | --- | --- | --- | --- |
| **L0 constitution** | the few rules that hold for every session in this repository: what agents may never do, the definition of done, evidence and scope rules | the owner | `AGENTS.md` at the repository root (≤ 250 lines is the target) | until the owner changes it |
| **L1 contracts** | how one subsystem works and must stay working: its data model, invariants, commands, tests | the owner, or a worker with the owner's approval | one file per area, for example `docs/contracts/extraction.md` | as long as the subsystem |
| **L2 lane instructions** | this lane's goal, scope, branch and base, checks, the other lanes' areas, what the worker may decide | PAI (`pai worktree init`), from the round plan | Claude Code lane: `CLAUDE.local.md` in the lane's worktree, not in git; Codex lane: a managed section of `AGENTS.md` | one round |
| **L3 proposals** | rule changes someone suggests: a JEV-ranked rule, a prompt improvement, a "this rule is wrong" finding | workers, the mastermind, JEV | the proposal output of `pai worktree init --jev`, decision sheets, NEXT-PR notes | until the owner accepts or rejects them |

Nothing moves up a layer by itself. An L3 proposal becomes an L1 or L0 rule only when the owner
accepts it.

## L0: the constitution

Short, stable and true for every task. Typical sections:

- the safety rules (for example "agents run no git write command");
- the definition of done;
- the evidence labels;
- the scope discipline;
- where the contracts are.

Two limits from the agents' own documentation (reported-by-prompts, `docs/research/LANDSCAPE.md`,
re-verified 2026-11-22):
- Codex stops reading instruction files at 32 KiB, counting L0 and every nested file together. A
  real example: FLH's 63 KiB AGENTS.md puts every rule past line 519 out of Codex's reach
  ([analysis §6](research/FLH-AGENTS-ANALYSIS.md#6-context-budget-r6-doc-060)).
- When a `CLAUDE.md` exists, Claude Code does not read `AGENTS.md`, unless each user changes a
  setting, and that setting is ignored in a repository's settings files. A `CLAUDE.md` holding
  `@AGENTS.md` makes both agents read the same L0. Claude Code does not read `.agents/`,
  `AGENTS.local.md` or `AGENTS.override.md`.

What does **not** belong here: status reports ("round 4 delivered …"), one subsystem's details, and
anything dated. For a worked example of splitting a real 1,074-line file, see
[research/FLH-AGENTS-ANALYSIS.md](research/FLH-AGENTS-ANALYSIS.md).

**Windows note.** A `CLAUDE.md` that is a symbolic link to `AGENTS.md` becomes a 9-byte text file on
a Windows checkout without symlink support, and Claude Code then reads no rules. Use a plain
`CLAUDE.md` holding the one line `@AGENTS.md` instead. The steps are in
[AGENTS-LAYERS.md § Migrating a CLAUDE.md symlink](#migrating-a-claudemd-symlink-to-an-import).

## L1: contracts

One file per area, loaded only when a session works in that area.

**Not implemented in PAI.** No `pai` command creates, lists or loads contract files yet. Today,
you link them from L0 ("before touching tools/extraction, read docs/contracts/extraction.md"). Both
agents also have a native way to load files by location (reported-by-prompts:
`docs/research/LANDSCAPE.md`):

- Claude Code: `.claude/rules/*.md` with a `paths:` front matter;
- Codex: nested `AGENTS.md` files per directory.

Neither has been tried with PAI on a real run (to verify).

## L2: lane instructions

What only this lane, in this round, needs to know. PAI writes it as a managed section between
markers, so it never overwrites your text:

```
<!-- pai:managed:begin pai-agents/1 … -->
… goal, scope, branch/base, checks, other lanes, decisions the worker may take …
<!-- pai:managed:end -->
```

| Agent | Where L2 goes | Command (core lane; not merged; reported-by-core) | Status |
| --- | --- | --- | --- |
| Claude Code | `CLAUDE.local.md` in the lane's worktree. Claude Code reads it next to `CLAUDE.md`; PAI adds it to the repository's `info/exclude`, so git never sees it and the worktree stays clean | `pai worktree init PATH --target claude-local --write` | the file handling is tested with fakes; that a real Claude Code session follows it is **to be verified** |
| Codex | a managed section in the worktree's `AGENTS.md` (Codex does not read `CLAUDE.local.md`) | `pai worktree init PATH --write` (the default target) | **to be verified** on a real Codex run; the section makes the worktree dirty, which the continuation flow has to tolerate |

`pai context --worktree PATH` (core lane) reads a lane's instructions as the agent would:
`CLAUDE.local.md` first, then `AGENTS.md`.

## L3: proposals

Proposals never change a file by themselves.

- `pai worktree init --jev` ranks a small rule library with JEV and lists the proposals. Only
  `--accept RULE` writes one, into the lane's L2 section.
- Workers write rule problems into their handoff's "Decisions for the owner" and into NEXT-PR.md.
  The mastermind turns them into a decision sheet. An example is the five FLH rule-vs-practice
  conflicts in `Agent Inbox/R6-20261122/docs/FLH-RULE-CONFLICTS.md`.
- `pai prompt optimize` (prompts lane, offline prototype) proposes prompt changes, scored against
  contracts and JEV. Each one needs your approval.

## Where PAI handles each layer

| | L0 | L1 | L2 | L3 |
| --- | --- | --- | --- | --- |
| `pai init` | reads it (`--instructions`, the files `pai context` passes on) | — | — | — |
| `pai worktree init` | quotes its rules with `file:line` in the lane section | — | **writes it** (AGENTS.md section, or CLAUDE.local.md with `--target claude-local`) | `--jev` proposes, `--accept` adopts |
| `pai round new` (round lane) | the prompt's shared part repeats the round's rules | — | renders each lane's prompt; does **not** write CLAUDE.local.md yet (not implemented) | decisions go to `DECISIONS.md` and the decision notes |
| `pai context` | passes it on | — | `--worktree PATH` reads the lane's L2 first | — |
| browser workbench (`pai worktree web`, `pai round web`) | shows the worktree's AGENTS.md | — | shows the managed section | shows open decisions |
| doc_check / lint | — | — | — | — (no layer checks yet) |

Every "—" is a gap today. The R7 round is planned around closing them, starting with L1.

## Migrating a CLAUDE.md symlink to an import

These are documentation-only steps, shown on FLH. Run them in your own checkout and commit them
yourself.

1. Check that it is a link: `git -C ~/workspace/french-learning-app ls-files -s CLAUDE.md`. Mode
   `120000` means a symbolic link.
2. Replace it with a one-line file:

   ```sh
   cd ~/workspace/french-learning-app
   rm CLAUDE.md && printf '@AGENTS.md\n' > CLAUDE.md
   git ls-files -s CLAUDE.md          # still 120000 until you stage it; after `git add CLAUDE.md`: 100644
   ```

3. Check it: start Claude Code in the folder and ask it to list its instruction files (`/memory`).
   `AGENTS.md` must appear as imported from `CLAUDE.md`. Claude Code follows `@path` imports relative
   to the importing file, up to four levels deep (reported-by-prompts, from Claude Code's memory
   documentation).
4. On Windows, after the next pull, open `CLAUDE.md`. It must hold `@AGENTS.md`, not the text
   `AGENTS.md` left by a broken link.
5. Codex keeps reading `AGENTS.md` directly; nothing changes for Codex lanes.
