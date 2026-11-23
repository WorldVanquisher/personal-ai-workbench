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

## Limits

- These are artifact behaviours (did the checkpoint record what it should), not task outcomes (did the code work).
  Task outcomes need the real E2 run or the merged-main reruns.
- `labelled_share` is line-based. A label in the same table row counts; a label in a separate sentence does not.
- `next_pr_exec` only applies where a lane writes `changed-paths.txt` (prompts, round).
