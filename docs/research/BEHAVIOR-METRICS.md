# Behaviour metrics: what the checkpoints did, not how they read (R6-PRM-070…072)

Round R6-20261122, prompts lane. This is R7 limitation L3: use behaviour evidence, not JEV's text scores. All
metrics are deterministic and computed from the artifacts (`pai prompt eval --behavior`, `pai/prompteval.py`
`behaviour()`). Real run over the real checkpoints of all four PAI lanes in `Agent Inbox/R6-20261122/` on
2026-11-22 (n = 23), lane tree. Raw: `cp-H/evidence/behaviour-r6.json` and `behaviour-vs-jev.json`.

## Metrics

| Metric | Computed from | Good |
|---|---|---|
| `stop_ok` | Work is finished (`Totals` todo 0), or the stop is resumable: Next names the next item ID and points to RESTART (any case) | 1 |
| `placeholders` | `{{NAME}}` placeholders left, counting only names that the library declares | 0 |
| `handoff_complete` | share of the 8 v2 sections present, halved if the Items table disagrees with its Totals line | 1 |
| `labelled_share` | share of number claims (x/y, "N tests", %, tokens) outside Items/Evidence that carry a source label on the same line | ≥ 0.8 |
| `next_pr_exec` | share of NEXT-PR.md `git add` paths that appear in the checkpoint's `changed-paths.txt` (none if either file is missing) | 1 |

Thresholds are in contract `handoff-v2` (`behaviour_thresholds`). Falling below one is a **warning, never a failure**
(R6-PRM-074).

## Per lane (real run, means over each lane's checkpoints)

| Lane | n | stop_ok | placeholders | handoff_complete | labelled_share | next_pr_exec |
|---|---|---|---|---|---|---|
| core | 6 | 1.00 | 0 | 1.00 | 1.00 | — (no changed-paths.txt) |
| docs | 5 | 0.80 | 0 | 1.00 | 0.69 | — (no changed-paths.txt) |
| prompts | 7 | 1.00 | 0 | 1.00 | 0.73 | 1.00 |
| round | 5 | 0.20 | 0 | 0.70 | 1.00 | 1.00 |

What these show:
- **round** writes "Next: R6-RND-039 final。" with no restart pointer in 4 of 5 checkpoints. Its cp-A to cp-C Items
  tables leave out the todo rows, so they disagree with Totals.
- **docs** and **prompts** put test counts in Summary/Validation prose without a label on the same line. For prompts,
  the label is in the State row's Source cell: the metric is line-based and does not credit that.
- No lane left a placeholder unfilled.

## JEV text scores against behaviour (R6-PRM-072)

Per checkpoint: the mean of the 0–1 behaviour metrics, against the mean of the three JEV questions from the v5
baseline (§6 of PROMPT-BASELINE). Pearson r = 0.34, n = 22: JEV's text score predicts behaviour only weakly.

**Written well, but not done** (JEV ≥ 0.70, behaviour < 0.75):

| Checkpoint | Behaviour | JEV | What the behaviour metrics caught |
|---|---|---|---|
| docs cp-E | 0.44 | 0.74 | Next names the final ID but no restart pointer; 1 of 3 number claims labelled |
| round cp-C | 0.63 | 0.78 | no restart pointer; the Items table disagrees with Totals (todo rows missing) |

**Done, but scored low by JEV** (JEV < 0.70, behaviour ≥ 0.90):

| Checkpoint | Behaviour | JEV | Why JEV scored it low |
|---|---|---|---|
| prompts cp-A | 1.00 | 0.66 | long handoff; JEV marked down the blockers written as prose with a command |

What follows:
- For the E2 experiment and `pai prompt optimize`, the primary metrics are the contract checks and these behaviour
  metrics (EXPERIMENTS.md, PROMPTS.md §5).
- JEV stays a second opinion. Its disagreements are where a person looks.

## Rerun after batch 3: cp-J and the finals (R6-PRM-091)

Real run plus JEV (1 ask per question, section-scoped), 2026-11-23 01:15Z, over all 43 handoffs then in
`Agent Inbox/R6-20261122/{core,docs,round,prompts}/` (the 23 above plus the later finals, cp-F to cp-J and round's
cp-I and final-J). Raw: `Agent Inbox/R6-20261122/_eval/behaviour-K/` (one JSON per handoff, now named
`<lane>.<cp>.HANDOFF.handoff-v2.json`), and the PAI-managed note `PAI/eval/R6-20261122.md` (43 artifacts, mean pass
0.801, 9 pass all 19 checks, 20 behaviour warnings). Behaviour = mean of the 0–1 metrics, as above. docs cp-J had no
SHA256SUMS at the time of the run, so it may still change.

| Lane | Checkpoint | Contract (handoff-v2 v6) | Behaviour | JEV | Warnings |
|---|---|---|---|---|---|
| core | cp-final | 14/19 | 1.00 | 0.87 | - |
| core | cp-final2 | 19/19 | 1.00 | 0.83 | - |
| core | cp-J | 18/19 | 1.00 | 0.84 | - |
| docs | cp-final | 13/19 | 0.83 | 0.81 | labelled_share 0.50 |
| docs | cp-J | 13/19 | 1.00 | 0.72 | - |
| prompts | cp-final | 18/19 | 0.94 | 0.77 | labelled_share 0.75 |
| prompts | cp-final2 | 19/19 | 1.00 | 0.78 | - |
| prompts | cp-J | 19/19 | 1.00 | 0.80 | - |
| round | final | 14/19 | 1.00 | 0.83 | - |
| round | cp-J | 12/19 | 0.67 | 0.71 | stop_ok 0; labelled_share 0.67 |
| round | final-J | 12/19 | 0.94 | 0.71 | labelled_share 0.75 |

Per lane over all their handoffs (n): core 11, contract mean 0.900, stop_ok 1.00; prompts 11, 0.947, 1.00; docs 9,
0.702, 0.67; round 12, 0.649, 0.33. No lane left a placeholder.

What changed against the first run:
- **JEV against behaviour**: Pearson r = 0.41 (n = 43); r = 0.49 on the 11 cp-J and final handoffs. The first run
  had r = 0.34 (n = 22). JEV still predicts behaviour only weakly; it stays a second opinion.
- **Later handoffs behave better**: mean behaviour 0.955 on the cp-J and finals, 0.914 over all 43.
- **round** still writes "Next: R6-RND-089 final。" with no restart pointer (round cp-J: stop_ok 0). Its final-J does
  have one. This is the same pattern as round cp-A to cp-D.
- **Written well, but not done** (JEV ≥ 0.70, behaviour < 0.75): docs cp-E 0.44/0.73 and round cp-C 0.62/0.80 as before;
  new: docs cp-F 0.67/0.72, docs cp-G 0.67/0.76, round cp-J 0.67/0.71. All five are a missing restart pointer or
  number claims without a label.
- **Done, but scored low by JEV** (JEV < 0.70, behaviour ≥ 0.90): docs cp-A 1.00/0.68, prompts cp-A 1.00/0.69, round
  cp-I 1.00/0.65.
- Bug found and fixed on the way: `pai prompt eval --out` named each result file by its checkpoint folder only, so
  four lanes' `cp-J/HANDOFF.md` overwrote each other. Names now get as many parent folders as needed to be unique
  (`prompteval.out_names`; test `OutNames.test_regression_same_checkpoint_name_in_two_lanes_gets_two_files`).

## Limits

- These are artifact behaviours (did the checkpoint record what it should), not task outcomes (did the code work).
  Task outcomes need the real E2 run or the merged-main reruns.
- `labelled_share` is line-based. A label in the same table row counts; a label in a separate sentence does not.
- `next_pr_exec` only applies where a lane writes `changed-paths.txt` (prompts, round).
