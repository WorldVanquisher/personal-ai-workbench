# The prompt library (`pai-prompts/1`)

PAI ships the prompts that drive a round (the mastermind's handbook, the shared worker rules, the
lane, checkpoint, restart, decision-sheet, research-report and next-PR templates) as a versioned
library. `pai init` copies it into the project; `pai prompt` renders, checks, compares, evaluates
and (later) improves it. Prompts are product: every change is versioned, checked by a program,
scored, and approved by a person before it replaces the library copy.

Contents: [1 Files](#1-files) · [2 Format](#2-format) · [3 Commands](#3-commands) ·
[4 Contracts and evaluation](#4-contracts-and-evaluation) · [5 pai prompt optimize (design)](#5-pai-prompt-optimize-design)

## 1. Files

Packaged in `pai/prompts/`; installed by default into `<vault>/PAI/prompts/`.

| File | kind | Who reads it | What it is |
|---|---|---|---|
| `COORDINATOR.md` | coordinator | mastermind | handbook: routing table (script → JEV → small sub-agent → worker → mastermind → separate session), /clear rule, where reports go, decision sheets, budget, what only the human does |
| `worker-base.md` | worker | every worker | shared rules (the generic Part 0): start check and first reply, rules, progress and stop rule, evidence labels, handoff and final reply |
| `lane.md` | lane | mastermind (writes), worker | one lane: files, item format with done criteria, evidence and dependencies |
| `checkpoint.md` | checkpoint | worker | what a checkpoint delivers: HANDOFF.md (`pai-handoff/2`), NEXT-PR.md, SHA256SUMS |
| `restart.md` | restart | worker | the resume message after /clear (≤ 800 characters) |
| `decision-sheet.md` | decision-sheet | mastermind, worker | one decision: question, options, trade-offs, recommendation, default, blocked items |
| `research-report.md` | research | sub-agent | a one-off research report: conclusions, evidence (file:line, link, arXiv id), inferences kept apart |
| `next-pr.md` | next-pr | worker | NEXT-PR.md: branch, `git add` per commit, messages, PR body, checks before merge |
| `intake.md` | intake | mastermind | split one request note (`type: pai-request`) into lanes of 10–50 items (PRM-D13 = B), each with done criteria, owner-only and dependencies, plus a decision sheet → INTAKE.md |
| `obsidian/*.md` | — | owner (Obsidian) | templates for the core Templates plugin: `request.md`, `decision.md`, `handoff-view.md` (§6) |
| `contracts/*.json` | — | `pai prompt eval` | machine-checkable output contracts (section 4) |

**Language policy (owner decision PRM-D1 = A, 2026-11-22).** The library stays in Chinese for this
round, because the owner's rounds run in Chinese. The docs lane decides the language for the release
and makes it consistent across the library. The lint section patterns accept Chinese and English
headings, so an English library can drop in without code changes. JEV scores Chinese rules
0.07–0.15 lower than the same rule in English (docs/research/JEV-QUESTIONS.md §1.1). That is why
the `lint --jev` threshold is 0.45 (PRM-D2 = B).

## 2. Format

A library file is Markdown with one managed block:

```text
<!-- pai-prompts/1 begin name=worker-base version=1 kind=worker vars=PROJECT,RUN_ID,LANE -->
...the managed text, with placeholders such as {{RUN_ID}}...
<!-- pai-prompts/1 end -->
...the project's own text (kept on every upgrade)...
```

**Format versions.** `pai-prompts/2` (this release) uses the same syntax as `/1`. It adds content
rules that lint checks per kind (`REQUIRED_TEXT` in pai/promptlib.py), taken from owner decisions
PRM-D3…D7:
- worker files state:
  - "not done = none / not merged (owner-only), never UNKNOWN";
  - "every blocker ends with a backticked command";
  - "item IDs in full".
- checkpoint files state:
  - "Items = `pai queue table` output";
  - "evidence copied into the cp folder, > 5 MB path + sha256 only";
  - "State | Value | Source".

The reader accepts `/1` and `/2`, and an installed copy keeps the format of the library it came from.
`pai prompt init` upgrades a `/1` project copy to `/2` (only the managed block changes) and says so.
Lint also warns on abbreviated ID lists such as `R6-PRM-001、002` in any prompt (D7).

**Version header.** The begin marker carries `name=` (the file name without `.md`), `version=`
(a positive integer, raised on every change to the managed text), `kind=` (one of `worker`,
`coordinator`, `lane`, `checkpoint`, `restart`, `decision-sheet`, `research`, `next-pr`,
`other`; it selects the lint profile) and `vars=` (the declared placeholders, comma-separated).
An installed copy also carries `sha256=` of its managed text. `pai-prompts/1` is the format
version: a reader refuses any other.

**Placeholders.** `{{NAME}}`, NAME = a capital letter followed by capitals, digits or `_`.

- Only declared names (`vars=`) are placeholders. Using an undeclared one is a lint error and a
  render refusal.
- Prose that has to *show* a placeholder escapes it: `\{{NAME}}` renders as `{{NAME}}` and is
  never a placeholder. This is the fix for the R6 bug where the check
  `grep -nE '\{\{[A-Z_]+\}\}'` over the filled prompt (`r6-prepare.sh`) counted the start-check
  rule itself ("stop if a placeholder is left, written like …") as an unfilled placeholder, so the
  R6 prompts had to describe the syntax in words.
- In escapes use a neutral name (`\{{NAME}}`), never a declared one: in a rendered file nothing
  tells `{{RUN_ID}}`-the-prose from `{{RUN_ID}}`-the-leftover (lint warns).
- `{{lower}}`, `{{ NAME }}` and the like are not placeholders; lint warns because they look like one.
- Values are substituted in one pass: a value containing braces is inserted as it is.
- Built-ins: `RUN_ID` (`--round`) and `LANE` (`--lane`); everything else comes from `--var` or
  `--vars-file`.

**Managed markers.** Text between the markers belongs to PAI and is replaced on upgrade; text
outside them belongs to the project and is never touched. If someone edits *inside* the block, the
recorded `sha256=` stops matching: `pai prompt init` then reports a CONFLICT for that file and
writes nothing to it (`pai prompt diff` shows the edit; move it below the end marker and run
again). A broken marker (two begins, no end, a line that mentions `pai-prompts/` but is not a
marker) is also a conflict, never guessed at. Same rules as AGENTS.md's `pai-agents/1` section
(`swarm/agentsmd.py`).

## 3. Commands

All `pai prompt` commands are offline except `--jev`. Exit codes: 0 done; 1 refused or a check
failed (the message says what to do); 2 usage error.

```text
pai prompt list [--library DIR]
pai prompt render NAME [NAME ...] --round R --lane L [--var K=V ...] [--vars-file F.json]
                  [--library DIR] [--out FILE] [--force]
pai prompt lint [NAME|PATH ...] [--library DIR] [--rendered [--vars A,B]] [--json]
                [--jev [--jev-below 0.6]]
pai prompt diff [NAME ...] --dest DIR [--library DIR]
pai prompt init --dest DIR [--library DIR] [--write]
pai init PROJECT --repo ... --vault ... [--prompts PAI/prompts | --prompts ""] [--write]
pai prompt eval ARTIFACT --contract NAME [--jev [--repeat N]] [--json] [--out DIR]
pai prompt optimize --dry-run [NAME ...] [--jev] [--budget-requests N]
```

### render

```bash
pai prompt render worker-base lane --round R7-20261201 --lane docs \
  --vars-file round-vars.json --var LANE_CODE=DOC --out prompts/docs.txt
# 4f3c...e1  prompts/docs.txt
# rendered worker-base + lane with 10 placeholder(s): PROJECT, REPO_URL, RUN_ID, ...
```

- Several names are joined in order with a blank line (the R6 pattern: shared rules + one lane).
- Every refusal lists *all* problems at once: format errors, undeclared placeholders, missing or
  empty values, each with the flag that supplies it. Nothing is written on refusal.
- The output is the project's own text plus the managed text, without markers. The first line of
  stdout is `sha256  path`, ready for ROUND.json (round lane, R6-RND-001/004).
- An existing output with other content is refused unless `--force`; the same content is fine.

### lint

Checks, per file (errors fail the run, warnings do not):

| Code | Level | What |
|---|---|---|
| `format` / `header` | error | markers, `pai-prompts/1`, `name=` matches the file, `version=`, `kind=` |
| `placeholder` | error | used but not declared; in `--rendered` files, a declared library name left unfilled |
| `placeholder` | warn | declared but unused; looks like a placeholder but is not; an escape that uses a declared name |
| `drift` | warn | the managed text was edited by hand |
| `section` | error | a required part is missing for the kind (worker: start check, rules, stop rule, evidence labels, handoff/final reply; coordinator: routing, /clear, reports, decision sheets, budget, human-only; lane: items, done criteria, evidence, dependencies; checkpoint: HANDOFF, NEXT-PR, Items/Totals) |
| `length` | warn/error | managed text over the kind's limit (worker 9 000 / 16 000 characters; restart 800 / 1 600; others 6 000 / 12 000); lines over 400 characters |
| `session-link`, `ai-attribution`, `private-path` | error | agent session links, AI attribution lines, private local paths such as `/home/<user>/` (a library must use placeholders; `--rendered` skips the path check, since rendered prompts are meant to contain them) |

A part counts as present when a heading-like line matches: a Markdown heading, a
`==== … ====` or `—— … ——` line, a bold line, or a template bullet `- ## Name`.

`--jev` splits the managed text into rules (list items and numbered lines, with their
continuation lines; a nested item is sent with its section heading and parent item), fills
placeholders with neutral sample values (a worker only sees rendered text), and asks JEV per rule:
"A careful developer given this RULE would know what action it requires or forbids. Answer yes unless
the rule is vague, contradictory, or uses undefined terms." Rules under `--jev-below` (default 0.6)
are listed lowest first. The wording and the sample values come from a calibration run
(docs/research/JEV-QUESTIONS.md): a stricter first wording scored even plainly clear rules 0.5–0.67.
The score is advice for a person rewriting the rule; lint never fails because of it. Template lines
and "follow the format in file X" rules score low by construction, because JEV sees one rule at a
time.

Example (real run, JEV, 2026-11-22; scores are per rule):

```text
$ pai prompt lint --jev      # excerpt: the next-pr.md part
ok    pai/prompts/next-pr.md
  jev   9 rule(s) scored, 0 failed, mean 0.69, 1 below 0.60
    0.55  next-pr#6  列出这个 commit 实现的条目 ID。
1 file(s), 0 error(s)
```

### diff and init

`pai prompt diff` prints, per library file: not installed / same / differs, the two versions,
`HAND-EDITED` when the managed text drifted, whether project text exists outside the block, and a
unified diff of the managed text (project → library). Files only in the project are listed and
left alone.

`pai prompt init --dest DIR` previews; `--write` applies: CREATE for missing files, UPDATE for
changed managed text (outside text byte-identical, CRLF files stay CRLF, written atomically),
SAME, CONFLICT (never written). Exit 1 if any conflict. `pai init ... --write` runs the same
install into `<vault>/PAI/prompts` after it has written the configuration (preview: it only
lists what it would do); `--prompts DIR` picks another folder relative to the vault, `--prompts ""`
skips it. A conflict never fails `pai init`.

### JEV usage

Every JEV request appends one line to `$PAI_JEV_USAGE_LOG` (when set): time, purpose
(`lint-rule`, `eval`, `optimize`), model, number of questions, the service's usage numbers,
seconds, ok or the error class. Never the key or the text sent. The key is read only from
`TYPESAFE_API_KEY`.

## 4. Contracts and evaluation

A **contract** (`pai/prompts/contracts/NAME.json`, format `pai-contract/1`) states what an output that
a prompt asks for must look like: deterministic checks, plus optional JEV questions shown beside them.

| Contract | For the output of | Main checks |
|---|---|---|
| `first-reply` | worker-base §0.3 | files-read part, ≥ 3 paths, decisions part, ≥ 5 full item IDs, owner-only/blocked part, dependencies part |
| `handoff-v2` | checkpoint.md | v2 front matter; Summary; State table with five rows, each with a source label; Items table agreeing with `Totals:`; Evidence with full sha256; both Decisions sections; Blockers each with a command or exactly "none"; Next names an ID; no session links, no AI attribution |
| `handoff-v1` | SESSION_START.md (pai 0.2–0.4 drafts) | the pre-R6 baseline: same idea, v1 fields |
| `next-pr` | next-pr.md | Branch, Commits with explicit paths (no `git add -A`), PR body, Checks before merge, Not included |
| `decision-sheet` | decision-sheet.md | every `- [ ] **D…**` block has question, options, trade-offs, recommendation, default, blocked items |
| `research-report` | research-report.md | conclusions, evidence, inferences; at least one link, arXiv id or file:line |
| `intake` | intake.md | JEV part; Lanes table; every item has done criteria, owner-only and dependencies; 10–50 items per lane (PRM-D13 = B); complete decisions; a not-read part |
| `request` | request template | front matter `type: pai-request`, `status: inbox`, body text; JEV intake questions `actionable`, `tier` (choice), `multi_lane` |
| `restart-reply` | restart.md | names RESTART.md and HANDOFF.md, the Totals, ≥ 3 IDs |

Check types: `section`, `regex`, `absent` (both optionally limited to a `section`), `frontmatter`,
`ids`, `table_rows`, `labels`, `totals` (the same parser as `pai queue check`), `full_sha256`,
`decisions`; see the docstring of `pai/prompteval.py`. A JEV question may name a `section` (it is
asked about that part only) and a check it `maps_to` (for calibration).

```bash
pai prompt eval cp-B/HANDOFF.md --contract handoff-v2
pai prompt eval "Agent Inbox"/*/HANDOFF.md --contract handoff-v1 --jev --repeat 3 \
  --out "<vault>/Agent Inbox/R6-20261122/_eval/baseline/handoff-v1-flh"
```

Output: per artifact PASS/FAIL with the failing checks; with several artifacts, failures by check
and the mean pass rate; with `--jev`, each question's mean and "calibration Q vs CHECK: agree/n".
Exit 0 only when every check of every artifact passes. JEV never changes PASS/FAIL.

**Result format and place (R6-PRM-026).** `--out DIR` writes one `pai-prompt-eval/1` JSON per
artifact (`<parent folder>.<file stem>.<contract>.json`) and `summary-<contract>.md`:

```json
{"format": "pai-prompt-eval/1", "artifact": "...", "artifact_sha256": "<64 hex>",
 "contract": "handoff-v2", "contract_version": 3, "at": "2026-11-22T20:15:03Z",
 "checks": [{"id": "items-totals", "desc": "...", "ok": false, "detail": "..."}],
 "passed": 10, "total": 13, "score": 0.769,
 "jev": {"state": "ok", "answers": {"numbers_labeled": [0.61, 0.6, 0.63]}, "mean": {"numbers_labeled": 0.613}}}
```

The place is `<vault>/Agent Inbox/<round>/_eval/<what>/`. It is outside every lane folder, so an
evaluation never changes a sealed checkpoint; the artifact's sha256 says which version was scored.
The baseline is in `_eval/baseline/`; findings are in docs/research/PROMPT-BASELINE.md.

## 5. pai prompt optimize (design)

Goal: prompts improve from round to round, and every change has evidence and a person's approval
(定位 v2 §0.2). This section is the design (R6-PRM-038). The offline prototype (R6-PRM-039) implements
steps 1–2 with deterministic operators and JEV only, as `pai prompt optimize --dry-run`.

**Loop**, one library file at a time:

1. **Propose.** Start from evaluation failures: `pai prompt eval` results in `_eval/` (which checks
   fail most, and which prompt asked for that output), plus `lint` errors and `lint --jev` scores below
   the threshold. A proposer produces candidate versions of the rules involved.
   - Prototype: deterministic operators, `split` for compound rules and `section` for missing parts.
   - Later: a model proposer that is a different model from the judge (2404.13076, self-preference),
     given the failing check, the rule, and two failing artifacts.
2. **Evaluate**, on three signals:
   - (a) lint and JEV clarity of the changed rules, with the same sample values on both sides;
   - (b) contract pass rates on a **tuning set** of past artifacts that the new rule should have
     prevented;
   - (c) the same on a **held-out set** chosen before the loop starts (APE, OPRO and ProTeGi report
     train/test gaps, see LITERATURE.md §5).

   A candidate qualifies only if (a) does not drop and (b) improves by at least `--min-gain`. A real
   run (EXPERIMENTS.md) is needed for anything that changes worker behaviour rather than wording.
   Step (c) has a precondition. Old artifacts were produced by the old prompt, so only *real* runs
   can show behaviour under the new one. Steps (b) and (c) on old artifacts only show that the new
   rule *names* the failure; the real-run comparison is what counts.
3. **Decision sheet.** Each qualifying candidate becomes one decision (decision-sheet.md format). It
   carries the diff, the before/after numbers with labels, the sets used, and the cost.
   - Default: "不做".
   - At most 3 rounds per rule (ProTeGi peaked at about 3 steps).
4. **Replace.** Only after the person ticks the decision: the library file gets the new text and
   `version=` + 1, and `pai prompt diff` shows the change to every project on its next `pai prompt init`.

**Budget and log, at every step:**
- `--budget-requests` caps JEV requests (default 60).
- A real-run step needs a dollar cap from the decision sheet and refuses without one.
- Every request goes to `$PAI_JEV_USAGE_LOG` with purpose `optimize`.
- Every proposal, score and decision is appended to `<vault>/Agent Inbox/<round>/_eval/optimize.jsonl`.
  This last part is a design item; the prototype prints instead.

**Route (owner decision PRM-D10 = C, then A).** First collect behaviour evidence: the behaviour
metrics in `pai prompt eval --behavior` (Block H) and the real E2 experiment (PRM-D8, run by the
mastermind after integration). Only then add a model proposer, which must be a different model from
the judge. It is judged on behaviour metrics and contracts over a held-out set, not on JEV text
scores. Until then the deterministic `--dry-run` prototype stays the only proposer.

**First prototype run** (2026-11-22, JEV real run, `--budget-requests 60`, 34 used):
- 10 split candidates, 2 cleared +0.05 (0.49 → 0.56, 0.51 → 0.57), 8 did not. Splitting alone is
  not a reliable improvement.
- Children have to be scored with their lead-in. Without it, splits looked worse (0.43 → 0.27) than
  they are.
- Raw output: `cp-D/evidence/optimize-dry-run-v2.txt` in the prompts-lane handoff.

## 6. Obsidian as the console (owner decision D9)

State is Markdown + front matter; PAI and agents read and write files directly. Dataview/Bases
queries only display; every page PAI generates also reads without plugins. PAI never edits a note
it did not generate.

**Templates** (`pai/prompts/obsidian/`, installed by `pai init --write` into
`<vault>/99 Templates/PAI/`, or `pai prompt templates --dest DIR [--write]`):

| Template | front matter | For |
|---|---|---|
| `request.md` | `type: pai-request`, `template: pai-request/1`, `status: inbox`, `project`, `priority`, `budget`, `created` | the owner's "one big request" note; input to intake.md |
| `decision.md` | `type: pai-decision`, `status: open`, `project`, `round`, `id`, `default`, `blocks` | one decision in decision-sheet format |
| `handoff-view.md` | `type: pai-view` | a Dataview table of a round's v2 handoffs, with a plain fallback |

- They use only the core Templates plugin variables `{{date}}`, `{{time}}`, `{{title}}` (with an
  optional `:FORMAT`), never Templater. Obsidian variables are lower case and PAI placeholders are
  capitals, so neither pattern matches the other (tests: `Templates.test_variables_and_placeholders_never_collide`).
- **Why no managed markers inside templates.** The Templates plugin copies the whole file into each
  new note, so begin/end markers would end up in the owner's own notes. A template instead carries
  `template: pai-<kind>/<n>`, which tells PAI the type of a note made from it. Its managed state
  is the manifest `99 Templates/PAI/.pai-templates.json` (sha256 per file). A file whose sha differs
  from the manifest, or that PAI never wrote, is a CONFLICT and is never overwritten.
- `pai prompt lint --templates [--schema FILE]` checks front matter (`template:` present; with a
  `pai-frontmatter-schema/1` file, every field known and fixed values allowed), the absence of PAI
  placeholders, core variables only, and no Templater syntax. The schema belongs to the core lane
  (R6-COR-039); a draft is in the prompts-lane proposals.

**Intake.** The JEV intake questions are in contract `request`: `actionable`, `tier` (choice)
and `multi_lane`. On three synthetic requests (JEV, 3 asks each; docs/research/JEV-QUESTIONS.md §3),
`multi_lane` alone gave a vague request 0.62, so routing combines the two questions:
- split with intake.md if `multi_lane` ≥ 0.8;
- treat as a single task if `actionable` ≥ 0.7;
- otherwise, needs detail.

## 7. Instruction layers L0–L3 (owner decision PRM-D9 = A; design, R6-PRM-063)

| Layer | What | Who writes | File (Claude Code lane) | File (Codex lane) | In git? |
|---|---|---|---|---|---|
| L0 constitution | the repository's rules | owner (workers propose `proposals/AGENTS-*.patch`) | `CLAUDE.md` containing `@AGENTS.md` | `AGENTS.md` (outside the managed section) | yes |
| L1 feature contracts | `docs/contracts/*` | owner | imported or linked from L0 | linked from L0 | yes |
| L2 this lane | the PAI-generated lane rules (today's `pai-agents/1` managed section) | PAI (`pai worktree init`) | **`CLAUDE.local.md` in the lane worktree** | the managed section of `AGENTS.md` (unchanged) | Claude: no; Codex: yes, uncommitted |
| L3 this session | the rendered worker prompt (worker-base + lane) and restart messages | PAI (`pai prompt render`) | pasted / dispatched | pasted / dispatched | no |

Why the Claude Code lane uses `CLAUDE.local.md` (LANDSCAPE §2, re-verified 2026-11-22):
- It is the documented personal, not-committed file. It is loaded at launch from the working directory and every
  directory above it, and it exists only in its own worktree, so each lane gets its own L2 without dirtying the tree
  (融合方案 §4.7: a dirty managed section blocks `recover --continue`).
- With any `CLAUDE.md`, Claude Code skips `AGENTS.md`, and the "read both" setting cannot be shipped in project
  settings. So L0 must reach Claude through `CLAUDE.md` + `@AGENTS.md`, never through AGENTS.md alone.

Codex has no documented un-committed instruction file, so L2 stays in the AGENTS.md managed section. Its combined
size counts against `project_doc_max_bytes` (32 KiB by default).

Before R7 adopts this (owner: "先核实再验证"):
1. The citations are verified (above).
2. Done 2026-11-22 (reported-by-mastermind): `docs/research/tools/verify_claude_local.py` with a real `claude -p`
   (Claude Code 2.1.330, haiku): 4/4, then a stricter rerun with file tools disabled 10/10. Four rules are real-run
   facts now (LANDSCAPE §2, "Real runs"), including one new one: with no `CLAUDE.md`, Claude Code reads `AGENTS.md`.
   So a repository needs either a one-line `CLAUDE.md` with `@AGENTS.md` or no `CLAUDE.md`; a symbolic link is not
   needed.
3. Core changes `pai worktree init` to write `CLAUDE.local.md` for Claude lanes (core lane, `--target claude-local`).

## 8. Behaviour metrics, eval notes and round prompts (Blocks G–H)

**`pai prompt eval --behavior`** adds deterministic behaviour metrics per checkpoint (docs/research/BEHAVIOR-METRICS.md):

| Metric | Meaning |
|---|---|
| `stop_ok` | finished, or Next gives the next ID and a restart pointer |
| `placeholders` | unfilled library placeholders |
| `handoff_complete` | v2 sections present, and Items agrees with Totals |
| `labelled_share` | share of number claims with a source label on the same line |
| `next_pr_exec` | share of `git add` paths in NEXT-PR.md that appear in changed-paths.txt |

Thresholds live in the contract (`behaviour_thresholds`). Falling below one prints `WARN` and never changes the exit
code. On the R6 checkpoints JEV's text score correlates only weakly with these metrics (r = 0.34, n = 22).

**`pai prompt eval … --note <vault>/PAI/eval/<round>.md --round R`** writes the results as a PAI-managed note:
- front matter that Dataview can query: `type: pai-eval`, `round`, `contract`, `mean_pass`, `all_pass`, `behaviour_warnings`;
- then a plain Markdown table, readable without plugins (D9).

It refuses (exit 1) to replace a file that has no PAI managed marker.

**`pai prompt render FILE.md --round R --var … --split-lanes DIR`** is what `r6-prepare.sh` did:
- fill the placeholders, and refuse if any real one is left (escaped prose is fine);
- split the ```text blocks;
- write `DIR/<lane>.txt` = Part 0 + the block whose first line is `线名：<lane>。` (a single block becomes `all.txt`);
- print each file's sha256.

**Mastermind tools** (`docs/research/tools/`; they run `claude`, so they live outside the `pai` package):
- `verify_claude_local.py [--dry-run] [--out DIR]`: four cases checking which instruction files Claude Code reads
  (CLAUDE.local.md alone; AGENTS.md shadowed by CLAUDE.md; `@AGENTS.md` import; CLAUDE.local.md next to CLAUDE.md).
  Model haiku, stops at $0.20, writes `RESULT.json`.
- `experiment_e2.py [--dry-run] --out DIR [--runs 8] [--budget 5]`: experiment E2 (first-reply checklist, 3 variants),
  scored by the `first-reply` contract. It resumes from `runs.jsonl` and starts no run after the budget is reached.

- `experiment_e3.py [--dry-run] --out DIR [--runs 16] [--budget 1]`: experiment E3 (a 14-item first reply, variants A
  and B, contract checks plus four task checks; EXPERIMENTS.md E3).
- `replay_e2.py RUNS.jsonl [--jev]`: rescores recorded E2 replies with the current first-reply contract (no model).

All pass with their fake agents (tests/test_prompt_tools.py, tests/test_prompt_eval.py). The real runs belong to the
mastermind. Done so far (reported-by-mastermind, 2026-11-22): verify_claude_local 4/4 and 10/10; E2 A 1.00, B 1.00,
C 0.48.
