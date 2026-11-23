# Prompt inventory (R6-PRM-001)

Round R6-20261122, prompts lane, base `c8bc51e`. Lane tree; rerun on merged main needed.

What exists today, what each piece is for, the rules it states, and which of those rules were
broken in practice. Every "broken" line names its evidence. Counts over the Agent Inbox are
**real run** (grep over the 21 `project: flh` handoffs in `<vault>/Agent Inbox/*/HANDOFF.md`,
2026-11-22); everything quoted from a vault note is **self-report** of that note's author
(the owner or the mastermind) unless the note itself labels it.

## 1. The pieces

| # | Piece | Where | Purpose | Audience |
|---|---|---|---|---|
| P1 | `SESSION_START.md` | repo root (core lane) | paste-in opener for one agent session: `pai handoff list`, `pai context`, one draft `pai handoff new` at the end | interactive agent |
| P2 | `WORKER_PROMPT` + `FINISH_TEXT` / `FINISH_STRUCTURED` + `PAI_CONTEXT_BLOCK` + `PAI_RULES` | `swarm/pai_swarm.py:65-99` (core lane) | headless `claude -p` worker: task, scope, git ban, finish block | headless worker |
| P3 | dispatch message | `swarm/dispatch.py:111-123` | the one line pasted into a lane's tmux pane by `pai run` | interactive worker |
| P4 | AGENTS.md managed section `pai-agents/1` | `swarm/agentsmd.py:217-270` (core lane) | per-worktree rules: goal, branch/base, scope, checks, decisions, owner-only, inherited rules | interactive worker |
| P5 | `Worker Prompt 范式.md` | vault `99 Templates/` | short generic worker prompt: read Inbox first, write Inbox at the end, label evidence | any worker |
| P6 | `FLH Worker Prompt R6.md` Part 0 | vault `99 Templates/` | shared rules for 4 FLH lanes: start check, bans, IDs, checkpoints, stop rule, file ownership, evidence labels, HANDOFF layout, final reply | lane worker |
| P7 | `PAI Worker Prompt R6b 润滑层.md` Part 0 | vault `99 Templates/` | the same for the 4 PAI lanes (this round) | lane worker |
| P8 | `r6-prepare.sh` | vault `99 Templates/` | fills `{{RUN_ID}}`-style placeholders with sed, refuses leftovers, splits Part 0 + Part N per lane | owner / mastermind |
| P9 | `r6-worktrees.sh` | vault `99 Templates/` | removes clean idle worktrees, creates `worker/<RUN>/<lane>` worktrees | owner / mastermind |
| P10 | 定位 v2 §3 | vault `30 Projects/` | mastermind rules: routing, /clear, report places, decision sheets | mastermind |

P1–P4 are code-owned by other lanes this round (core: P1, P2, P4; P3 is `swarm/dispatch.py`, not
assigned to any lane). This lane only reads them; suggested changes go to `proposals/`.

## 2. Rules, and where they were broken

Legend: **B** = broken in practice (with evidence), **H** = holds as far as the evidence shows,
**?** = no evidence either way.

### Start-of-work rules

| Rule (source) | Status | Evidence |
|---|---|---|
| Stop if a placeholder is left (P6 §0.1, P7 §0.1, P8) | **B (tooling)** | P8's check is `grep -nE '\{\{[A-Z_]+\}\}'` on the whole filled file, so a prompt that *describes* a placeholder trips it. The R6 prompts work around it by spelling the rule out in words ("两个左花括号后面跟着大写字母的那种写法") instead of writing one. The FLH R6 note also records an older check that created an empty file when the prompt was missing and then reported "all placeholders filled" (FLH Worker Prompt R6 §A). Regression tests: `tests/test_prompt_lib.py` (R6-PRM-013). |
| Read the Inbox / prior handoffs before the first reply (P5 §开工前, P6 §0.4) | **?** | No first replies are archived anywhere; R6-PRM-023 adds them to the baseline from this round on. |
| First reply lists files read, IDs to do first, owner-only items (P6, P7) | **?** | Same; now a machine contract (`first-reply`). |

### Working rules

| Rule (source) | Status | Evidence |
|---|---|---|
| No git writes; the human commits (P1, P2, P4, FLH AGENTS.md:976-980) | **B** | The PR-REPAIR session made local commits (`Agent Inbox/PR-REPAIR-161-163-…/HANDOFF.md`: "local commits: #161 08ac621…"); recorded as a rule/practice conflict in `FLH 与 PAI 本地验证问题.md` (FLH, "规则和实际做法冲突"). |
| Edit only inside the scope (P2 "Edit only: {scope}") | **B (enforcement)** | Real `claude -p` smoke 2026-11-22: the worker ran `printf 'smoke ok\n' >> SMOKE.md`, `od -c`, `cat`, none of them allowed; `swarm/guard.py` checks `paths:` only for Edit/Write (融合方案 §0.1). The write happened to be in scope. |
| Unique item IDs per lane (P6 §2) | **B** (before R6) | R2/R3 numbered 001–100 in every lane, so R3-041 meant three things (validation note, FLH "各线的条目 ID 冲突"). |
| Paid calls only with a stated cap (P6 §1) | **H** | FLH-154 part 4: "Paid runs NO-GO" kept (`Agent Inbox/FLH-154-part4-…`). |
| Leave no bytecode (P6, P7) | **B** (before R6) | PR-REPAIR repaired "tracked Python bytecode removed" (`Agent Inbox/PR-REPAIR-161-163-…/HANDOFF.md` Summary). |

### Reporting rules

| Rule (source) | Status | Evidence (real run, grep over 21 FLH handoffs) |
|---|---|---|
| End with exactly one `pai handoff new` draft in the Inbox (P1, P5) | **B** | `SPEAKING-LOCAL-PROVIDER-20261118/HANDOFF.md` has no front matter (hand-written, 12 lines). PAI's own R2/R3/R4 workers never used `pai handoff` (融合方案 §2, "实际情况"). The 11-20/21 rounds wrote to `~/flh-handoff/` only (范式 "为什么需要"). |
| State rows kept separate, nothing inferred; unchecked = UNKNOWN (P1) | **H, but uninformative** | Merge row: 16 of 21 say some form of `UNKNOWN`; only 1 uses the R6 wording `not merged (owner-only)`. |
| Blockers listed, each with the owner's command (P5 §收尾.5, P6 §5) | **B** | 8 of 21 say "None supplied (not the same as none existing)". Of the rest, most blockers carry no command (e.g. PR-REPAIR "owner push of both repair heads (non-force commands in HANDOFF.md)" points elsewhere). |
| Evidence: path + full SHA-256, not just "sealed handoff" (P5 §收尾.4, P6 §5) | **B** | 17 of 21 point outside the vault (`~/flh-handoff/…`), and digests are truncated (`SHA256SUMS digest d4ffc8fae2f1d768`, `tar 92634054`). |
| Every number carries a source label (P5 §收尾.3, P6 §4) | **B** | Only 7 of 21 contain any of real run / synthetic / fake / self-report. |
| Items table ending in a `Totals:` line (P6 §2, §5) | **B** (format did not exist yet) | 0 of 21. `pai queue check` therefore cannot read any of them (融合方案 §4.4). |
| "rerun on merged main" items are followed up (P5 §收尾.3) | **B** | "needs rerun on merged tree" items were never rerun (范式 "为什么需要"); #173/#175 asked for post-merge reruns with no record after `9f09c6f` (validation note). |
| PR body numbers = handoff numbers; no "worker reports" as validation (P5 §不要) | **B** | At least 13 PR bodies of #140–176 rely on "worker reports" and none has a GitHub review (validation note). |
| Do not stop early; seal + RESTART.md first (P6 §2) | **B** (motivating) | "提前停工" listed among the problems the R6 prompt answers (FLH Worker Prompt R6, 怎么用 4). |

### Prompt-delivery rules (owner / mastermind side)

| Rule | Status | Evidence |
|---|---|---|
| Worker gets context: handoffs, queue, managed section (融合方案 §5.1.3) | **B** | P3 is one line without any of them (`swarm/dispatch.py:111-123`); only P2 receives `pai context`. |
| The managed section says how to hand off (P4) | **B** | Its only mention is "Write the options … into your report or handoff" (`swarm/agentsmd.py:255-257`): no command, place or format. |
| Paths in prompts are host paths, not container paths | **B** | Syncthing shows `/vaults/MainVault`, which does not exist on the host (`r6-prepare.sh` error text; FLH Worker Prompt R6 §A.1). |

## 3. What this means for the library (input to R6-PRM-002…011)

1. Placeholders need a declared list and an escape, so prose can talk about them (P8's bug).
2. The handoff format must be checkable by a program: Items table + `Totals:`, State rows with
   labels, Evidence with full sha256 (0/21 today; `pai queue check` needs it).
3. Every reporting rule above that was broken is a candidate contract check (R6-PRM-021).
4. Rules that only the harness can enforce (scope of Bash writes, git bans) stay in the prompt
   but must not be relied on as the only control (core lane, R6-COR-011).
5. The dispatch line (P3) and the managed section (P4) should point to the library's
   `worker-base.md` and `checkpoint.md` instead of restating rules: see
   `proposals/` in the prompts lane handoff.
