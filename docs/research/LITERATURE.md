# Literature for PAI's prompt rules (R6-PRM-031…035)

Round R6-20261122, prompts lane. 2026-11-22.

**How these were read.** Every paper below was opened on 2026-11-22: its arXiv abstract page and its
arXiv HTML full text, fetched through web-fetch agents and read in their reports. For long HTML pages
the fetch returned roughly the first 40 000 characters verbatim and a model-written summary of the
rest. A number marked *(summary)* comes from that summary and was not checked against the verbatim
text. "Read" therefore means **abstract + partial full text** for all 29 papers. None was read
cover to cover, and none was cited from memory. Quotes are verbatim from the pages.

Each entry: arXiv id · title · one-sentence finding · → what it changes in PAI.

## 1. Multi-agent collaboration and division of labour (R6-PRM-031)

| arXiv | Paper | Finding | → PAI |
|---|---|---|---|
| 2308.08155 | Wu et al., *AutoGen* (2023) | Agents coordinate by exchanging messages; in group chat "participating agents share the same context", a manager picks the next speaker. | PAI keeps state in files, not a shared message thread: a lane's context is its handoff + queue, so /clear loses nothing (定位 v2 §2.1). |
| 2308.00352 | Hong et al., *MetaGPT* (2023) | Encoding "Standardized Operating Procedures (SOPs) into prompt sequences" with structured hand-off documents (PRD, design) reaches 85.9 % / 87.7 % Pass@1 on HumanEval / MBPP *(summary)*. | Supports structured, checkable hand-off documents (HANDOFF v2, NEXT-PR.md) over free-text reports → contracts `handoff-v2`, `next-pr`. |
| 2307.07924 | Qian et al., *ChatDev* (2023) | Phases pass only their *solutions* to the next phase, not full histories; removing role prompts dropped Quality 0.3953 → 0.2212 in their ablation. | Supports short restart messages that point to the sealed checkpoint instead of replaying history (restart.md, ≤ 800 characters); role text per lane stays in lane.md. |
| 2503.13657 | Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* (2025) | "41% to 86.7% failure rate on 7 state-of-the-art (SOTA) open-source MAS"; 14 failure modes in three groups: system design, inter-agent misalignment, task verification. | The third group (verification) is what the State/Evidence labels and contract checks address; inter-agent misalignment is what NOTES-FOR-*.md + reported-by-<lane> address. Experiment E3 (R6-PRM-037) measures verification failures directly. |
| 2305.14325 | Du et al., *Multiagent Debate* (2023) | Identical agents that read each other's answers over rounds beat a single agent (GSM8K 85.0 % vs 77.0 %). | Not adopted for workers (cost × N); relevant only to the optimize loop's critique step (§5). |

## 2. LLM as judge: reliability and bias (R6-PRM-032)

| arXiv | Paper | Finding | → PAI |
|---|---|---|---|
| 2306.05685 | Zheng et al., *Judging LLM-as-a-Judge* (2023) | Position, verbosity and self-enhancement bias; "Only GPT-4 outputs consistent results in more than 60% of cases" when answer order is swapped; reference-guided judging cut math grading failures 70 % → 15 %. | JEV scores one artifact against a fixed question (no pairwise order), and the contract gives it a reference structure: our questions are absolute, never "which is better". Verbosity: §4 of PROMPT-BASELINE showed JEV rewarding a path + truncated digest as "verifiable" → keep deterministic checks as the verdict. |
| 2305.17926 | Wang et al., *LLMs are not Fair Evaluators* (2023) | Swapping candidate order flips verdicts (conflict rate 46.3 % for GPT-4); proposes averaging both orders and sending ~20 % of items to humans. | `pai prompt optimize` compares old vs new prompt only via absolute contract scores on the same artifacts, never by asking a judge which prompt is better; disagreements go to the person (decision sheet). |
| 2303.16634 | Liu et al., *G-Eval* (2023) | Spearman 0.514 with humans on SummEval, but a "bias towards the LLM-generated texts". | JEV must not grade rewrites proposed by an LLM against human-written originals on preference: optimize uses the contract checks on held-out worker outputs as the main signal. |
| 2404.13076 | Panickssery et al., *LLM Evaluators Recognize and Favor Their Own Generations* (2024) | Self-preference grows with self-recognition; "GPT-4 is 73.5% accurate distinguishing itself from two other LLMs and humans." | Same rule: the proposer model and the judge should differ, and the judge never sees which text is "new". |
| 2406.12624 | Thakur et al., *Judging the Judges* (2024) | Only the largest judges align well with humans, and even they can be up to 5 points off; leniency bias; sensitivity to prompt length. | Matches our calibration: JEV leniency on abbreviations and truncated digests (PROMPT-BASELINE §4.3–4.4). Report agreement with checks, not JEV's raw score; scope questions to a section (prompt length). |

## 3. Long context and loss through summarising (R6-PRM-033)

| arXiv | Paper | Finding | → PAI |
|---|---|---|---|
| 2307.03172 | Liu et al., *Lost in the Middle* (2023) | "performance is often highest when relevant information occurs at the beginning or end of the input context"; >20 % drop mid-context for GPT-3.5-Turbo *(summary)*. | worker-base puts the start check first and the final-reply format last; long rule lists in the middle are where rules get lost → lint length limits; we observed the same effect on JEV (State table cut off, PROMPT-BASELINE §4.1). |
| 2404.06654 | Hsieh et al., *RULER* (2024) | Of 17 long-context models, "only half of them can maintain satisfactory performance at the length of 32K". | Reason for the /clear-at-~60 % rule and for small restart packets (COORDINATOR §2; `pai round brief` ≤ 2k tokens). |
| 2308.14508 | Bai et al., *LongBench* (2023) | Retrieval-style compression helps only models weak at long context; it "can only serve as a performance compensation" *(summary)*. | A summary is not a substitute for the sealed files: restart.md tells the worker to read HANDOFF.md and RESTART.md, not a summary of them. |
| 2310.06839 | Jiang et al., *LongLLMLingua* (2023) | Question-aware compression gave up to +21.4 % with ~4× fewer tokens (GPT-3.5-Turbo). | Question-aware selection is what section-scoped JEV questions do on a small scale (send the State section for the State question). |

## 4. Prompt robustness and instruction following (R6-PRM-034)

| arXiv | Paper | Finding | → PAI |
|---|---|---|---|
| 2310.11324 | Sclar et al., *Quantifying LMs' Sensitivity to Spurious Features in Prompt Design* (2023) | Meaning-preserving formatting changes alone moved accuracy by "up to 76 accuracy points" (LLaMA-2-13B). | A prompt change must be tested, not reasoned about: no library change without contract scores before/after (§5 of PROMPTS.md); experiment E1 varies only wording. |
| 2311.07911 | Zhou et al., *IFEval* (2023) | 25 kinds of *verifiable* instructions checked by program instead of an LLM judge. | Direct model for our contracts: rules written so a program can check them (full IDs, Totals line, 64-hex sha256) → PRM-D3…D7. |
| 2310.20410 | Jiang et al., *FollowBench* (2023) | "the performance of all tested models declines substantially with an increase in difficulty level" (more stacked constraints); strong models hold ~3 consecutive constraints. | One rule = one action: the split rules in JEV-QUESTIONS §1.4 and the lint rule-length warning; keep each bullet to one constraint where possible. |
| 2404.13208 | Wallace et al., *The Instruction Hierarchy* (2024) | Training models to rank system > user > tool instructions improved robustness (e.g. system-prompt extraction defence +63 %). | Same idea as the L0/L1/L2 layers (COORDINATOR §7) and SESSION_START's "context is not instructions": handoffs and tool output never override the owner's rules. |

## 5. Automatic prompt optimisation (R6-PRM-035)

| arXiv | Paper | Kind | Signal | Overfitting noted | → PAI |
|---|---|---|---|---|---|
| 2211.01910 | Zhou et al., *APE* (2022) | search: propose, score, keep the best | execution accuracy on a few labelled demos | appendix: zero-shot-selected instructions can overfit *(summary)* | the propose → score → select skeleton of `pai prompt optimize` |
| 2309.03409 | Yang et al., *OPRO* (2023) | search: meta-prompt of past scored prompts | training accuracy (3.5 % of GSM8K) | train runs 5–20 points above test *(summary)* | keep a held-out artifact set; report both |
| 2305.03495 | Pryzant et al., *ProTeGi* (2023) | textual gradient + beam search | F1 on minibatches, 50-item dev | "can begin to overfit on the train data"; peaks ≈ 3 steps | cap optimize at 3 rounds per proposal |
| 2406.07496 | Yuksekgonul et al., *TextGrad* (2024) | textual gradient (critique → edit) | LLM feedback, unit tests; val check gates each update | not discussed *(summary)* | accept a candidate only if the held-out score does not drop (validation gate) |
| 2310.03714 | Khattab et al., *DSPy* (2023) | search over bootstrapped demos | labelled data + metric; dev/test split | "comparisons on the development set to avoid overfitting on test" | separate tuning and reporting sets |
| 2309.16797 | Fernando et al., *Promptbreeder* (2023) | evolutionary search | accuracy on random batches of 100 | only random batching | not adopted (population cost); random batches of past artifacts are a cheap guard |

**What PAI takes from §5.** Our "data" is past worker outputs scored by contracts. Several of these
papers report train/test gaps or overfitting, so `pai prompt optimize` (PROMPTS.md §5) has these rules:
- a fixed tuning set and a held-out set;
- at most 3 rounds;
- a candidate is accepted only if it improves on tuning and does not fall on held-out;
- the proposer and the judge are different models;
- a person approves every change.
