# Intake trial: intake.md on two synthetic requests (R6-PRM-066)

Round R6-20261122, prompts lane, 2026-11-22. Lane tree. Following COORDINATOR's routing table, the splitting was done by
small-model sub-agents (haiku), one per request, each given only the rendered intake.md and the request note. The two
requests are synthetic (a family photo library; a recipe planner), not the owner's words. Scoring: contract `intake`
(real run) and JEV `split_ok` (3 asks each). Files: `cp-G/evidence/` in the prompts-lane handoff.

| Run | Lanes × items | Contract | JEV split_ok | Item ID prefix |
|---|---|---|---|---|
| request 1, intake v1 | 7 × 30 | 6/6 under v1; 6/7 under v2 (no id_prefix) | 0.80 | **R6** (should be R7) |
| request 2, intake v1 | 3 × 30 | 6/6 under v1; 6/7 under v2 | 0.59 | **R6** (should be R7) |
| request 2, intake **v2** | 3 × 30 | **7/7** | 0.70 | R7 |

What the trial found:
1. **Both agents ignored the rendered round.** The prompt said "R7-20261201 的轮次号…例如 R7-DOC-001", yet both took
   "R6" from the folder path they were working in. The v1 contract did not notice. intake.md v2 does three things:
   - it names the prefix explicitly through a new `{{ID_PREFIX}}`;
   - it says "the round is the one written here, not the directory name";
   - it makes the output start with front matter `round:`/`id_prefix:`, which contract check `id-prefix` verifies.

   The retrial used R7 throughout.
2. **A reference to another library file fails for a sub-agent that only has one file.** Both agents reported
   decision-sheet.md as "not read". v2 inlines the decision format. The JEV clarity scores showed the same pattern for
   "格式见 X" rules (JEV-QUESTIONS.md §1.4).
3. **The 30-item minimum produces padding.** Agents on a small request made every lane exactly 30 and said "several
   entries are small". The 30–50 rule came from the mastermind, so it stays; the decision sheet lists it as a question
   for R7.
4. Lane counts follow request size: 7 lanes for the big request, 3 for the medium one. Owner-only items were marked
   (buying storage, publishing; choosing websites, licensing). Spend, delete, publish and security decisions defaulted
   to "do not do", as the prompt says.
