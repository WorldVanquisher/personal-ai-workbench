# JEV questions for the prompt library: calibration notes

Round R6-20261122, prompts lane. Every number is **JEV** (TypeSafe `jev-latest`, `POST /v1/systemone`,
real requests from Arch Linux, 2026-11-22) unless labelled otherwise. Lane tree; rerun on merged main
needed. Raw results: `cp-B/evidence/*.json` in the prompts-lane handoff (sha256 in its SHA256SUMS).

JEV returns a probability (`noul`) for a yes/no question about a text. It judges how a text is written,
not whether it is true (融合方案 §3.2: a report that only *claims* "all tests pass" got checked 0.86). So
every JEV score here is advice for a person, never a gate.

## 1. Rule clarity (`pai prompt lint --jev`)

### 1.1 The first wording was too strict

| Wording | clear rule, en | vague rule, en | clear rule, zh | vague rule, zh | a library rule (zh) |
|---|---|---|---|---|---|
| Q1 "…knows exactly what to do or not do, **and how to tell that it has complied, without asking any question**" | 0.67, 0.67 | 0.05, 0.05 | 0.52, 0.51 | 0.05, 0.05 | 0.40, 0.39 |
| Q2 "A careful developer given this RULE would know what action it requires or forbids. Answer yes unless the rule is vague, contradictory, or uses undefined terms." | 0.95, 0.95 | 0.23, 0.21 | 0.88, 0.87 | 0.29, 0.33 | 0.83, 0.85 |

(2 asks per cell. Controls: "Do not run git commit or git push." / "Make the code better where
appropriate." and their Chinese translations; the library rule is worker-base's "条目被阻塞：标 blocked…".)

With Q1 every one of the 105 library rules scored below 0.6 (mean 0.234): the question could not tell a
clear rule from an unclear one in this range. Q2 keeps a 0.55–0.75 gap between the controls, so the
library now uses Q2 (`pai/promptjev.py` CLEAR_Q). Both wordings score Chinese ~0.07–0.15 lower than the
same rule in English: compare scores within one language only.

### 1.2 Unrendered placeholders read as undefined terms

| Rule | with `{{VAULT}}`-style placeholders (3 asks) | with values filled in (3 asks) |
|---|---|---|
| sub-agent report path | 0.44, 0.45, 0.41 | 0.50, 0.49, 0.48 |
| restart: read two files | 0.42, 0.42, 0.41 | 0.77, 0.80, 0.80 |
| worker checkpoint path | 0.39, 0.36, 0.37 | 0.48, 0.49, 0.43 |

Workers only see rendered text, so `lint --jev` now fills placeholders with neutral sample values
(`pai/promptjev.py` SAMPLES) before asking.

### 1.3 Stability

Repeats of the same request differ by at most 0.04 (all cells above). That is far smaller than the
effects measured here, so one ask per rule is enough for lint; `pai prompt eval --repeat N` exists for
the contract questions (R6-PRM-024).

### 1.4 The library, before and after (R6-PRM-018/019)

| Run | Rules | Mean | Below 0.6 |
|---|---|---|---|
| Q1, raw placeholders | 105 | 0.234 | 104 |
| Q2, raw placeholders | 105 | 0.618 | 45 |
| Q2 + parent context for nested bullets (the "before") | 105 | 0.614 | 47 |
| after rewriting 26 low rules | 108 | 0.630 | 47 |
| after rewriting + placeholders filled (final) | 108 | 0.642 | 42 |

Per rewritten rule (n = 26, same question, raw placeholders on both sides): mean change +0.085,
20 better, 4 worse. Biggest gains came from naming the action and its object explicitly, e.g. next-pr
"commit message：第一行不超过 72 个字符…" 0.35 → 0.82, decision-sheet "默认做法…" 0.36 → 0.58. Splitting the
compound L0/L1-proposal rule into three did not help (0.25 → 0.28 / 0.45 / 0.50).

What stays low (7 rules < 0.45): template lines (`- [ ] **D<编号> 一句话标题**`), rules whose content is
"follow the format in another file" (`HANDOFF.md：用 pai-handoff/2 格式…`), and rules with in-house terms
(/clear, 决策单, pai round close). JEV sees one rule at a time, without the referenced file, so these
are expected; the contract checks (R6-PRM-021) measure them better than a per-rule clarity score.

## 3. Intake questions (R6-PRM-046)

Three synthetic request notes, written for this test and not taken from the owner, each asked 3 times
(JEV), questions in `pai/prompts/contracts/request.json`:

| Request | actionable | tier (choice, confidence) | multi_lane |
|---|---|---|---|
| big: self-hosted photo library (ingestion, dedupe, gallery, backups, admin, CI, bilingual docs) | 0.15, 0.15, 0.14 | opus ×3 (1.00) | 0.91, 0.91, 0.92 |
| small: rename `timeout_s` in 3 files + tests + docs | 0.91, 0.90, 0.90 | sonnet ×3 (0.98) | 0.14, 0.14, 0.13 |
| vague: "make everything nicer and faster" | 0.07, 0.07, 0.06 | opus ×3 (0.80–0.82) | 0.63, 0.61, 0.63 |

- `multi_lane` separates big from small, but it gives a vague note 0.62: vagueness reads as size.
- `actionable` is low for both the big and the vague request (a big request cannot be started
  without questions either).
- Routing therefore combines the two: split with intake.md if `multi_lane` ≥ 0.8; treat as a single
  task if `actionable` ≥ 0.7; otherwise, needs detail. The vague note lands in needs-detail.
- The opus tier for the vague note matches 融合方案 §3.2, where "把应用做得更好" was written as
  `model: opus` although needs-detail. Core R6-COR-032 handles that.

## 2. Usage

`$PAI_JEV_USAGE_LOG` (`~/.cache/pai-R6-20261122/prompts/jev-usage.jsonl`) after Block B: 569 requests,
203 394 input + 11 380 output tokens, ~360 input tokens per rule request, 0.1–0.3 s each (JEV usage
field, real run). Usage was captured by a measurement harness wrapping the workbench client, standing
in for `proposals/swarm-jev-last-usage.patch` (core lane) until that lands.
