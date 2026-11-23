# Prompt experiments: design only (R6-PRM-037)

Round R6-20261122, prompts lane. Nothing here has been run. Running any of it needs the owner's
budget decision (定位 v2 §5 D7: up to $5 from the Anthropic credit; JEV is unmetered but logged).
Estimates are labelled *(estimate)*. Measured inputs are labelled.

## Common setup

- **Task.** A fixed synthetic mini-repo with 6 items in 2 Blocks: 3 small code changes with tests,
  1 docs change, 1 item that is impossible on purpose (needs a key that is not there), and 1 owner-only
  item. The items are designed so a correct worker must produce: a first reply, a blocked item, an
  owner-only item, two checkpoints and a final reply.
- **Runner.** `claude -p` headless through `pai_swarm.py run` on a throwaway clone, with model
  haiku, the guard hooks on, and no network except the API. This matches the 2026-11-22 real
  smoke: haiku, 8 s, $0.0038 for a one-line task *(real run, 融合方案 §0.1)*.
- **Scoring.** Deterministic first: contracts `first-reply`, `handoff-v2`, `next-pr`, and
  `restart-reply` where it applies, plus outcome checks (tests pass, the blocked item is marked
  blocked and not faked, the owner-only item is untouched, no git writes). JEV beside them with the
  section-scoped questions. JEV is never the verdict (PROMPT-BASELINE §4).
- **Unit.** One run = one variant × one seed (a fresh session). The order of variants is randomised.
- **Primary metric.** Mean contract pass rate per run (0–1). **Secondary**: the outcome checks
  (binary), tokens and cost per run, and time to final.
- **Sample size.** The R6 handoffs vary with SD ≈ 0.15 in pass rate *(real run: R6 v2 scores
  0.615–1.000, n = 5)*. To see a 0.15 difference between two variants at α = 0.05 with power 0.8,
  we need about 16 runs per variant *(estimate: two-sample, d = 1)*. Start with **8 per variant** as
  a pilot and decide whether to continue from its variance.
- **Cost per run.** A 6-item task is roughly 30–60× the smoke run, so about $0.10–0.25 on haiku
  *(estimate)*. 8 runs × 3 variants × $0.20 ≈ **$4.80**, inside the $5 cap. The full 16 per variant
  does not fit and needs a new decision.

## E1: how the stop rule is written

- A (current worker-base): "不要提前停 … 要停，必须按顺序做完三件事 (1) 封存 (2) RESTART.md (3) 第一句".
- B (procedural): the same as a checklist the worker must copy into its final reply, with each line
  ticked.
- C (minimal): one sentence "Stop only after sealing a checkpoint and writing RESTART.md."
- Forced stop: the context is capped (`--max-turns`) so every run hits the stop condition in Block B.
- **Metric**: the share of runs that leave a sealed checkpoint + RESTART.md + a correct first
  sentence. Literature: FollowBench (2310.20410), where constraint-following falls as constraints
  stack.

## E2: the first-reply checklist

- A: five headings (current). B: five headings plus a filled example. C: free form ("say what you
  read and what you will do first").
- **Metric**: the `first-reply` pass rate, especially `first-ids` (full IDs) and `paths`. The
  baseline from this round is 6/7 (real run, n = 1).

## E3: checkpoint cadence

- A: a checkpoint at each Block end (current). B: every 3 items. C: only at final.
- **Metric**: the `handoff-v2` pass rate of the last checkpoint, and the share of items whose state
  in the final handoff matches the queue log (`pai queue check`). Hypothesis: more checkpoints cost
  tokens but catch verification failures earlier (2503.13657, the "task verification" failures).

## Not in scope

- Prompt changes chosen by `pai prompt optimize`: they enter through the decision sheet, then are
  tested with the same harness.
- Opus or Sonnet runs: the budget does not cover them. Rerun the winner on Sonnet once, as a check.

## Owner decision

PRM-D8 in the cp-D handoff: which experiment first, the pilot size, and the budget.
