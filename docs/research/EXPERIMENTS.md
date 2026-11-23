# Prompt experiments (R6-PRM-037, R6-PRM-083, R6-PRM-085)

Round R6-20261122, prompts lane. E2 has been run by the mastermind (results below, reported-by-mastermind).
E1, E3 and E4 are designs. Running any of them needs the owner's budget decision (定位 v2 §5 D7: up to $5 from
the Anthropic credit; JEV is unmetered but logged). Estimates are labelled *(estimate)*. Measured inputs are
labelled.

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

## E2: the first-reply checklist (run 2026-11-22)

- A: five headings (current). B: five headings plus a filled example. C: free form ("say what you
  read and what you will do first").
- **Metric**: the `first-reply` pass rate, especially `first-ids` (full IDs) and `paths`. The
  baseline from this round is 6/7 (real run, n = 1).
- Tool: `docs/research/tools/experiment_e2.py` (6-item task, haiku, `--allowedTools Read`).

### Result (reported-by-mastermind: real run on the mastermind's WSL, 2026-11-22, haiku, $0.048 in total)

| Variant | n | Mean contract pass rate (first-reply v2) | SD | first_ids |
|---|---|---|---|---|
| A: five headings (worker-base §0.3) | 8 | 1.00 | 0 | 1.0 |
| B: A plus a filled example | 8 | 1.00 | 0 | 1.0 |
| C: free form | 8 | 0.48 | 0.07 | 1.0 |

- **Decision**: worker-base §0.3 keeps A. The checklist lifts the pass rate from 0.48 to 1.00; the example in B
  adds nothing on this task, and A is shorter. The basis is recorded in a maintainer comment in
  `pai/prompts/worker-base.md` that is not rendered (R6-PRM-083).
- **Limits** (from the mastermind's review):
  - The task was too easy for haiku to separate A and B. E3 below is the harder task.
  - first_ids was 1.0 even for C. The v2 check counted any five IDs anywhere in the reply, so a free-form reply
    that repeated the item list passed. first-reply v3 (R6-PRM-084) checks the "first 5 IDs" part only: 5
    distinct IDs, written in full, numbered items in order. `docs/research/tools/replay_e2.py` rescores
    recorded replies with the current contract (no model; JEV optional).
  - The real `runs.jsonl` (24 replies) is in the mastermind's scratchpad, not on the prompts lane's machine. The
    replay of the real replies is still to run: `python3 -B docs/research/tools/replay_e2.py out/real/e2/runs.jsonl
    --jev --out out/real/e2/REPLAY.json`. On the dry-run replies (synthetic, n = 9) v3 gives first_ids A 0.0
    (the fake A shortens IDs), B 1.0, C 0.0, and JEV's `names_ids` agrees with the check on 9 of 9 (JEV).

## E3: a harder first reply, to separate A from B (design and dry-run, R6-PRM-085)

- **Why**: E2's 6 items left A and B at 1.00 each. E3 makes the first reply depend on reading carefully.
- **Task** (`docs/research/tools/experiment_e3.py`, files written into a fresh scratch repo per run):
  - 14 items, R9-DOC-001 to R9-DOC-014, in 3 Blocks;
  - R9-DOC-002 is blocked: it needs DEMO_KEY, and STATUS.md says the key is not there;
  - R9-DOC-013 and R9-DOC-014 are owner-only (publish, tag);
  - R9-DOC-004 depends on R9-DOC-009 in the same lane;
  - 3 cross-lane dependencies: R9-DOC-006 on R9-COR-003 (already done, per NOTES-FOR-docs.md), R9-DOC-007 on
    R9-COR-005 (not done), R9-DOC-011 on R9-RND-002;
  - 3 decisions in two files: D1 answered A and D2 open (docs/PLAN.md), D3 answered B (DECISIONS.md).
- **Variants**: A and B as in E2. C is left out: E2 already measured it, and it saves a third of the budget.
- **Score per run**: mean of the first-reply v3 checks and four task checks:
  - `first5-doable`: the first five IDs are distinct, none of R9-DOC-002, -007, -011, -013, -014, and R9-DOC-009
    comes before R9-DOC-004 when R9-DOC-004 is among them;
  - `blocked-named`: R9-DOC-002, -013 and -014 are in the owner-only/blocked part;
  - `deps-named`: R9-COR-005 and R9-RND-002 are in the dependencies part;
  - `decisions-named`: D1, D2 and D3 are in the decisions part.
- **Runs**: 16 per variant, order shuffled with a fixed seed. Budget cap $2 (mastermind, R6-PRM-092). Cost *(estimate)*: E2 cost about
  $0.002 per run (reported-by-mastermind: $0.048 / 24); E3 reads 6 files instead of 3, so about $0.004–0.008
  per run, $0.13–0.26 for 32 runs.
- **Reading the result**: if A and B differ by less than 0.05 again, keep A (shorter). If B is better by 0.10 or
  more, propose B for worker-base through the decision sheet. Look at the per-check shares: they show which part
  of the reply the example helps with.
- **Dry-run** (fake agent, synthetic): 3 runs per variant, the fake A reply starts with the blocked R9-DOC-002 and
  misses R9-RND-002 and D3. Result A 0.727, B 1.000; a rerun resumes and starts no new run. Tests:
  `tests/test_prompt_tools.py` (`test_experiment_e3_dry_run_separates_the_fake_variants_and_resumes`,
  `test_e3_task_checks_respect_the_dependency_order`).
- **Full-size dry-run** (R6-PRM-092, fake agent, synthetic, 2026-11-23): `--runs 16 --budget 2` planned and finished 32 runs
  (A 0.727, B 1.000, SD 0); the same command again started no run; `complete: true`.
- **For the mastermind** (real run, from the repository root of the merged integ/0.5.0, with `claude` logged in or
  `ANTHROPIC_API_KEY` set in that shell):

      python3 -B docs/research/tools/experiment_e3.py --out out/real/e3 --runs 16 --budget 2

  - Model haiku (the default), tools limited to Read, `--max-turns 10`, a fresh scratch repo per run.
  - Budget: no new run starts once $2.00 is recorded. One run can still finish above the cap by its own cost
    (about $0.01 *(estimate)*). Expected total $0.13–0.26 *(estimate)*.
  - Resumable: rerun the same command after an interruption; finished runs in `runs.jsonl` are skipped.
  - Output: `out/real/e3/RESULT.json` (pai-experiment-e3/1: per variant n, mean, SD, per-check shares, spent) and
    `runs.jsonl` (reply text kept, so the contract can be replayed later without a model).
  - Then: `python3 -B docs/research/tools/replay_e2.py out/real/e2/runs.jsonl --jev --out out/real/e2/REPLAY.json`
    (R6-PRM-084, the E2 replay; JEV only).

## E4: checkpoint cadence (was E3 before R6-PRM-085)

- A: a checkpoint at each Block end (current). B: every 3 items. C: only at final.
- **Metric**: the `handoff-v2` pass rate of the last checkpoint, and the share of items whose state
  in the final handoff matches the queue log (`pai queue check`). Hypothesis: more checkpoints cost
  tokens but catch verification failures earlier (2503.13657, the "task verification" failures).

## Not in scope

- Prompt changes chosen by `pai prompt optimize`: they enter through the decision sheet, then are
  tested with the same harness.
- Opus or Sonnet runs: the budget does not cover them. Rerun the winner on Sonnet once, as a check.

## Owner decision

PRM-D8 in the cp-D handoff: which experiment first, the pilot size, and the budget. The owner chose E2 (D8 = A);
it ran on 2026-11-22 (above). E3 is ready to run within the same $5 cap.
