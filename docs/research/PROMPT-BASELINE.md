# Prompt baseline: how existing worker outputs score (R6-PRM-023…029)

Round R6-20261122, prompts lane, base `c8bc51e`. Lane tree; rerun on merged main needed.

Labels: **real run** = `pai prompt eval` deterministic checks on Arch Linux, Python 3.14.9,
2026-11-22. **JEV** = TypeSafe `jev-latest`, each question asked 3 times per artifact. Raw results:
`<vault>/Agent Inbox/R6-20261122/_eval/baseline/` (one `pai-prompt-eval/1` JSON per artifact, a
summary per contract, and the combined runs under `runs/`).

## 1. What was scored

| Set | Artifacts | Contract(s) | Why |
|---|---|---|---|
| FLH | the 21 `project: flh` handoffs in `Agent Inbox/` (2026-11-12 … 11-20) | `handoff-v1` (what SESSION_START asked for then) and `handoff-v2` (checkpoint.md) | the "before" |
| R6 | this round's sealed handoffs on 2026-11-22: prompts cp-A, cp-B; round cp-A, cp-B; docs cp-A | `handoff-v2` | the first round with the v2 rules in its prompt |
| First replies | this lane's first reply (the only one archived; other lanes' first replies are not in the vault) | `first-reply` | the start check |

The `handoff-v2` contract went from version 1 to 3 during this work (§4); the numbers below are
version 3.

## 2. Results (real run)

| Set / contract | n | mean pass rate | all checks pass |
|---|---|---|---|
| FLH / handoff-v1 | 21 | 0.567 (0.40–0.70) | 0 |
| FLH / handoff-v2 | 21 | 0.403 (0.385–0.538) | 0 |
| R6 / handoff-v2 | 5 | 0.815 (0.615–1.000) | 1 (prompts cp-A) |
| first reply / first-reply | 1 | 0.857 | 0 |

Passes per check:

| Check | FLH v1 (n=21) | FLH v2 (n=21) | R6 v2 (n=5) |
|---|---|---|---|
| front matter (v1 keys / v2 keys) | 21 | 0 | 2 |
| State table with five rows | 21 | 21 | 5 |
| a source label anywhere (v1) / on every State row (v2) | 7 | 0 | 2 |
| Items table agrees with a `Totals:` line | 0 | 0 | 3 |
| Evidence with full sha256, none truncated | 2 | 2 | 4 |
| Evidence not only outside the vault (v1) | 5 | — | — |
| Blockers stated (v1: not "None supplied"; v2: each names a command, or exactly "none") | 15 | 0 | 2 |
| Merge state known, not `UNKNOWN` (v1) | 6 | — | — |
| both Decisions sections (v2) | — | 0 | 5 |
| Next names the next item ID (v2) | — | 3 | 5 |
| no session link / no AI attribution | 21 | 21 | 5 |

Per R6 artifact (v2, real run): prompts cp-A 13/13, prompts cp-B 12/13 (its Blockers name no
command), round cp-A 10/13, round cp-B 8/13, docs cp-A 10/13. The round and docs lanes wrote their
checkpoints before core's `docs/HANDOFF-V2.md` existed (front matter without `format`, `round`,
`base_sha`…, State rows without a Source column, Items table without the todo rows). That is a
cross-lane alignment gap, not a worker failing its prompt: their prompt only said "State 表（每行带来源标签）".

The first-reply check that failed: "先做的 5 个 ID：R6-PRM-001、002、004、003、013". Four of the five IDs are
abbreviated, so a program (and a reader in another lane) finds one ID, not five.

## 3. JEV beside the checks (JEV, 3 asks each)

| Set / question | n × asks | mean | spread within an artifact: median / max |
|---|---|---|---|
| FLH v1 · numbers_labeled | 21 × 3 | 0.26 | 0.03 / 0.08 |
| FLH v1 · evidence_verifiable | 21 × 3 | 0.30 | 0.02 / 0.10 |
| FLH v2 · numbers_labeled | 21 × 3 | 0.21 | 0.03 / 0.07 |
| FLH v2 · blockers_actionable | 21 × 3 | 0.35 | 0.03 / 0.07 |
| FLH v2 · evidence_verifiable | 21 × 3 | 0.34 | 0.02 / 0.07 |
| R6 v2 · numbers_labeled | 5 × 3 | 0.61 | 0.02 / 0.07 |
| R6 v2 · blockers_actionable | 5 × 3 | 0.49 | 0.04 / 0.05 |
| R6 v2 · evidence_verifiable | 5 × 3 | 0.66 | 0.02 / 0.09 |
| first reply · lists_files | 1 × 3 | 0.97 | 0.00 |
| first reply · names_ids | 1 × 3 | 0.93 | 0.01 |

**Stability (R6-PRM-024).** Asking the same question three times moves the score by a median of
0.02–0.04 and at most 0.10. Differences between artifacts and between sets (0.21 vs 0.61) are an
order of magnitude larger. One ask is enough for ranking; use `--repeat 3` when a score sits within
0.1 of a threshold.

## 4. Calibration: JEV against the deterministic checks (R6-PRM-025)

JEV says "yes" when its mean ≥ 0.5. Agreement = same verdict as the mapped check.

| Question → check | FLH (n=21) | R6 (n=5) |
|---|---|---|
| v1 numbers_labeled → labels-any | 14/21 = 0.667 | — |
| v1 evidence_verifiable → evidence-sha | 15/21 = 0.714 | — |
| v2 numbers_labeled → state-labels, whole-text question (contract v1) | 21/21 = 1.000 | 3/5 = 0.600 |
| v2 numbers_labeled → state-labels, State section only (v2) | 19/21 = 0.905 | 4/5 = 0.800 |
| v2 blockers_actionable → blockers-cmd, check v2 | 12/21 = 0.571 | 2/5 = 0.400 |
| v2 blockers_actionable → blockers-cmd, check v3 | 16/21 = 0.762 | 3/5 = 0.600 |
| v2 evidence_verifiable → evidence-sha | 15/21 = 0.714 | 4/5 = 0.800 |
| first reply names_ids → first-ids | — | 0/1 |

What the disagreements show:

1. **What JEV is shown matters more than how the question is phrased.** The first version sent the
   *last* 6 000 characters of an artifact. For this lane's long v2 handoffs that cut off the State
   table, so JEV scored "every row is labelled" at 0.23–0.28 on handoffs where every row is labelled.
   Asking about the State section only raised R6 agreement from 3/5 to 4/5. Contract questions now carry
   a `section`.
2. **JEV caught a bad check.** `blockers-cmd` v2 accepted "None supplied (not the same as none
   existing)" as "none". JEV scored those handoffs 0.27–0.28 ("not actionable"), which is right.
   Check v3 accepts only a backticked command or a bare "none", and agreement rose from 0.571 to 0.762.
   The 5 remaining FLH disagreements are blockers that name a concrete action in prose ("owner push of
   both repair heads", JEV 0.51–0.82). The check is stricter than JEV on purpose, because a command
   can be copied.
3. **JEV is lenient about truncated digests.** `evidence_verifiable` scored 0.50–0.63 on five FLH
   handoffs whose evidence is "~/flh-handoff/… (SHA256SUMS digest d4ffc8fae2f1d768)": a path plus a
   16-hex prefix reads as verifiable. Only the check sees that the digest cannot be recomputed.
4. **JEV accepts abbreviations.** "R6-PRM-001、002、004、003、013" scored 0.93 for "names five IDs".
   A reader in the same lane understands it; `pai queue`, `grep` and other lanes do not.

Conclusion for PAI: JEV disagreements are where a person should look. Two of the four kinds were the
check's fault (1 and 2) and two were JEV's (3 and 4). This matches 融合方案 §3.2: JEV is a second
opinion, never a gate.

## 5. The five most common problems, and a rule for each (R6-PRM-029)

Ranked by FLH failures (n=21, real run), confirmed against R6 where possible.

| # | Problem | Evidence | Proposed rule (target file) |
|---|---|---|---|
| P1 | No machine-readable Items table / Totals | FLH 21/21 fail; R6 2/5 fail (rows missing) | checkpoint.md: "Items 表直接粘贴 `pai queue table <queue>` 的输出（含 todo 行），不要手写或删行。" |
| P2 | Evidence not verifiable: truncated digests, files outside the vault | FLH 19/21 no full sha256, 16/21 only `~/…` paths | checkpoint.md: "Evidence 引用的文件先复制进 cp 目录；sha256 写全 64 位，由 `sha256sum` 输出粘贴，不手抄、不截短。" |
| P3 | Claims without a source label | FLH v1 14/21 no label anywhere; v2 21/21; R6 3/5 | checkpoint.md: State 表固定三列 `State \| Value \| Source`，Source 只能取标签表里的值（已在 v1 库里；本轮 round/docs 没用到库） |
| P4 | Non-answers in State and Blockers: `UNKNOWN`, "None supplied" | FLH Merge UNKNOWN 15/21; "None supplied" 6/21; v2 blockers-cmd 21/21 | worker-base.md: "没做的事写 none 或 not merged (owner-only)；不写 UNKNOWN 代替'没做'。Blockers 每条以 owner 要执行的命令结尾（反引号），没有就只写 none。" |
| P5 | Abbreviated or missing IDs | first reply 1/1 ("001、002…"); FLH Next without an ID 18/21 | worker-base.md: "条目 ID 一律写全称（R6-PRM-002，不写 002），第一条回复和 Next 都一样。" |

These rule changes are proposals for the owner (decision sheet in the prompts-lane handoff,
PRM-D3…D7); none is applied to the library yet.

## 6. Contract v5 (library pai-prompts/2): rerun and comparison (R6-PRM-060)

Contract `handoff-v2` v5 adds the owner's decisions PRM-D3…D7 to the v3 checks used above: `state-columns` (D5),
`not-done-not-unknown` and a per-blocker `blockers-cmd` (D6), `items-from-queue` (D3), `evidence-local` (D4),
`full-ids` (D7), plus core's per-line evidence rule (v4). It has 19 checks; v3 had 13. Real run plus JEV (1 ask each,
section-scoped questions), 2026-11-22. Raw results: `_eval/baseline-v2/`.

| Set | n | v3 mean pass rate | v5 mean pass rate | all pass (v5) |
|---|---|---|---|---|
| FLH, pre-R6 (the 21 of §1) | 21 | 0.403 | 0.351 | 0 |
| FLH, R6 (`R6-REL-cp-001`, written by the FLH R6 prompt) | 1 | — | 0.579 (11/19) | 0 |
| R6 PAI lanes, all checkpoints (core 6, docs 5, prompts 6, round 5) | 22 | — (5 of them: 0.815) | 0.777 | 1 (prompts cp-E) |

R6 per lane (v5): prompts 0.903, core 0.833, docs 0.695, round 0.642. Same five artifacts as §2, v3 → v5:

| Artifact | v3 | v5 |
|---|---|---|
| prompts cp-A | 13/13 | 16/19 |
| prompts cp-B | 12/13 | 16/19 |
| round cp-A | 10/13 | 11/19 |
| round cp-B | 8/13 | 11/19 |
| docs cp-A | 10/13 | 13/19 |

Which new checks R6 checkpoints fail (n = 22):

| Check | Passing |
|---|---|
| D6 `blockers-cmd` | 6 |
| D4 `evidence-local` | 6 |
| core's per-line `evidence-sha` | 8 |
| D5 `state-columns` | 12 |
| D3 `items-from-queue` | 13 |
| D7 `full-ids` | 20 |
| D6 `not-done-not-unknown` | 22 |

None of these rules existed when those checkpoints were written. The numbers are the starting point
for R7, not a grade.

JEV against the stricter checks (calibration, `maps_to`):

| Question → check | FLH (n=22) | R6 (n=22) |
|---|---|---|
| numbers_labeled → state-labels | 0.909 | 0.636 |
| blockers_actionable → blockers-cmd | 0.727 | 0.364 |
| evidence_verifiable → evidence-sha | 0.727 | 0.455 |

On the newer R6 handoffs JEV agrees with the checks far less often, mostly as "JEV yes, check no".
The handoffs are well written (labels in prose, actionable blockers described in words, evidence by
path), but they do not meet the mechanical rules. That is the gap Block H measures with behaviour
metrics instead of text scores.

## 7. Usage (JEV)

Baseline and calibration (Block C, purpose `eval:*` in `jev-usage.jsonl`): 378 requests, 329826 input + 13494 output tokens, largest request 2665 input tokens (JEV usage field, real run). By contract: first-reply 3, handoff-v1 63, handoff-v2 312.
