# FLH's AGENTS.md, line by line: constitution, contracts, outdated, conflicts

R6-DOC-056 (docs lane, round R6-20261122). Read-only analysis of `AGENTS.md` in
french-learning-hub at `9f09c6f` (1,074 lines, 64,650 bytes; `CLAUDE.md` is a symbolic link to it).
Nothing in that repository was changed. It is the worked example for [AGENTS-LAYERS.md](../AGENTS-LAYERS.md).

**How this was made.**
- A subagent of the docs lane read the whole file and classified every line into one of four
  categories. A script confirmed the ranges cover lines 1–1,074 with no gap or overlap. The
  categories are:
  - **L0** (constitution: holds for every session);
  - **L1** (feature contract: matters only for one area);
  - **OUTDATED** (history, or contradicted later);
  - **CONFLICT** (two parts disagree, or a rule disagrees with the code).
- The docs lane then checked three of its code claims against the repository, and all three hold:
  - C8: `internal/extractor/openai.go:29` is `knowledge_extraction_v2`, while AGENTS.md:84–85 says v1;
  - C6: `internal/domain/entry.go:24–30` has no AI fields;
  - C2: `internal/transport/http/handler.go:218` serves `/knowledge-library/hybrid-search`, while
    AGENTS.md:430 says hybrid ranking is deferred.
- Other rows are as the subagent read them. Rows marked *(inferred)* were not read line by line.
- Labels: the classification is the subagent's own report, three claims were checked by docs, and
  the line counts were confirmed by script.

**Summary.**

| Category | Lines |
| --- | --- |
| L0 (constitution) | 252 (66 of them blank or headings) |
| L1 (17 feature contracts) | 767 |
| OUTDATED | 29 |
| CONFLICT | 26 (10 conflicts) |
| Total | 1,074 |

Three quarters of what every FLH session loads is feature detail that most tasks never touch. The
"Current Stage" section alone (lines 14–832) is 77 % of the file.

**Related decisions.** These conflicts overlap the owner's decision sheet
`Agent Inbox/R6-20261122/docs/FLH-RULE-CONFLICTS.md`:
- C4 ("speech processing") is F4;
- C3 and C5 (semantic retrieval, more AI providers) are the same kind of conflict as F4 and F5,
  scope rules written before the features shipped;
- F1–F3 (git, AWS, Terraform) are conflicts between rules and practice, not inside the file.

---


Source: `~/workspace/french-learning-app/AGENTS.md` (1074 lines; `CLAUDE.md` symlinks to it). Read-only analysis; code claims cite file:line in that repo. Blank lines are folded into the adjacent row and named in the topic column.

## 1. Line-by-line classification

| Lines | Heading or topic | Category | One-line reason | Proposed destination |
|---|---|---|---|---|
| 1-7 | ## Project Overview: what the repo is (2 blank, 7 last sentence of para) | L0 | Stable project identity; every session needs it | L0 constitution |
| 8-13 | Project Overview: current priority, deferrals, prefer simple (13 blank) | L0 | Repo-wide priority and simplicity rule | L0 constitution |
| 14-18 | ## Current Stage heading + 'single Go HTTP service backed by SQLite' (15,18 blank) | L0 | One-sentence stack fact useful to every session; 'built up in incremental milestones. Implemented so far' is history framing to drop | L0 constitution (one line) |
| 19-23 | Bullet: Entry persistence; legacy 001 columns ignored | L1 | Entry domain/schema detail | contracts/core-records.md |
| 24-34 | Bullet: Analyses, Analyzer impls, AI_PROVIDER, provenance strings, taxonomy | L1 | Analyzer subsystem; `rule-based:v2` checked at internal/analyzer/rulebased.go:38 | contracts/core-records.md |
| 35 | Bullet: immutable feedback | L1 | Feedback record fact | contracts/core-records.md |
| 36-40 | Bullet: read-only projections / learning inventory | L1 | Inventory/effective-analysis feature | contracts/core-records.md |
| 41-52 | Bullet: structured capture import POST /captures | L1 | Capture import contract | contracts/capture.md |
| 53-66 | Bullet: capture CLI cmd/capture, -batch | L1 | Capture CLI contract | contracts/capture.md |
| 67-84 | Bullet: knowledge extraction, eligibility, EXTRACTOR_PROVIDER, admission (first part) | L1 | Extraction pipeline rules. 79-80 'no staleness feature yet' not checked against code (inferred still true) | contracts/extraction.md |
| 85 | Extraction provenance `openai:<model>:knowledge_extraction_v1` | OUTDATED | Code uses v2: internal/extractor/openai.go:29 `schemaVersion = "knowledge_extraction_v2"`; also contradicts line 523. (Line also opens the admission sentence, which is L1.) | fix to v2, move to contracts/extraction.md |
| 86-92 | Extraction: admission ruleset, overrides, migration 005, error hygiene | L1 | Extraction/admission contract | contracts/extraction.md |
| 93-113 | Bullet: Knowledge concept resolution (routes, identity schema, conservative resolver) | L1 | Concept-resolution contract | contracts/concept-resolution.md |
| 114 | 'Milestone 10.5.1 (correctness patch, migration 007) sharpened the model:' | OUTDATED | Milestone history label | delete-as-history |
| 115-139 | Append-only resolution log, membership projection, derived support, lifecycle, migrations 006/007 | L1 | Concept-resolution data model (current, still authoritative) | contracts/concept-resolution.md |
| 140-143 | 'Inspect the repository before proposing changes; do not assume ... exist' (140,143 blank) | L0 | Universal evidence rule | L0 constitution |
| 144-146 | M10.6 label + 'web/ is a human annotation/data-collection UI, not a consumer product UI' | CONFLICT | Contradicted by 1031-1034 (workbench extended for personal daily use) and by 572-831 learner pages | owner decision |
| 147-154 | M10.6: CURRENT SAME authority, INVALID, RejectSame, DISTINCT, relations | L1 | Resolution authority tables | contracts/concept-resolution.md |
| 155-157 | 'At M10.6, no ML resolver or dataset exporter existed...' (157 blank) | OUTDATED | Milestone history; the surviving fact (no learned resolver) is already in 1059 | delete-as-history |
| 158-168 | M10.7: annotation UI client-side catalog search, candidate sources | L1 | Annotation workbench search contract | contracts/annotation-workbench.md |
| 169-171 | M10.7: 'No backend search endpoint ... was added' (171 blank) | OUTDATED | Milestone delta statement ('was added') | delete-as-history |
| 172-182 | M11-A..D: annotation authority, dataset v1, quality report | L1 | Annotation dataset/quality contracts | contracts/annotation-workbench.md |
| 183-185 | 'These milestones do not declare ... train models, or implement retrieval' (185 blank) | OUTDATED | Milestone delta; retrieval was later implemented (186-289) | delete-as-history |
| 186-200 | M12-A: retrieval evaluation v1, policy, exact retriever (200 blank) | L1 | Retrieval-evaluation contract | contracts/retrieval-evaluation.md |
| 201-221 | M12-B: weighted lexical retriever, registry, normalization (221 blank) | L1 | Retrieval-evaluation contract | contracts/retrieval-evaluation.md |
| 222-238 | M12-C: BM25 retriever | L1 | Retrieval-evaluation contract | contracts/retrieval-evaluation.md |
| 239-241 | 'M12-C adds no persistence, migration, index, ...' (241 blank) | OUTDATED | Milestone delta | delete-as-history |
| 242-263 | M12-D: embedding retriever, EmbeddingProvider, text representation | L1 | Retrieval-evaluation contract | contracts/retrieval-evaluation.md |
| 264-267 | 'M12-D adds no persistence, vector index/cache/database ...' (267 blank) | OUTDATED | Milestone delta; later contradicted by FLH-040 vector cache + Qdrant (416-428) | delete-as-history |
| 268-285 | M13-A0: retrieval comparison report | L1 | Retrieval comparison contract | contracts/retrieval-evaluation.md |
| 286-289 | 'M13-A0 adds no retriever, persistence, ... hybrid retrieval, fusion' (289 blank) | OUTDATED | Milestone delta; hybrid fusion later added (commit 33c1dc2, internal/application/hybrid_search.go) | delete-as-history |
| 290-303 | M13-A1: Experiment Dashboard (303 blank) | L1 | Workbench dashboard contract | contracts/annotation-workbench.md |
| 304-315 | v1.0 Entry ownership cleanup (315 blank) | L1 | Entry vs Analysis ownership; confirmed by internal/domain/entry.go:24-30 (no AI fields) | contracts/core-records.md |
| 316-320 | v1.0 release verification: QUICKSTART must stay truthful | L1 | Release docs rule | contracts/release.md |
| 321-324 | Root `make verify` non-mutating release check; needs `npm ci` (324 blank) | L0 | The single repo-wide validation command (Makefile:55) | L0 constitution |
| 325-359 | v1.0 personal-use release packaging: Docker, -web-dir, /healthz /readyz, Compose, smoke, upgrade rehearsal, disk preflight, restore check | L1 | Release/ops contract | contracts/release.md |
| 360-363 | Publishable metadata: no Claude/Anthropic co-author trailer or private claude.ai link (363 blank) | L0 | Applies to every suggested commit message (DoD item 7) | L0 constitution |
| 364-374 | FLH-023 extraction selection mode (374 blank) | L1 | Current-extraction selection API | contracts/extraction.md |
| 375-387 | FLH-029 annotation idempotency (387 blank) | L1 | Annotation write idempotency | contracts/concept-resolution.md |
| 388-404 | FLH-034 Knowledge Library (404 blank) | L1 | Knowledge Library read API | contracts/knowledge-library.md |
| 405-409 | FLH-036: LISTEN_HOST | L1 | Server listener config (internal/config/config.go:244,302) | contracts/release.md |
| 410-415 | FLH-036: FLH-034 demo process guard (415 blank) | L1 | Local demo tooling | contracts/knowledge-library.md |
| 416-428 | FLH-040 persistent semantic search (owner-authorized), Qdrant, migration 010, semantic-index CLI | L1 | Semantic search contract; 'owner-authorized for that milestone only' is history | contracts/semantic-search.md |
| 429-432 | 'Fake-vector tests prove mechanics... Further retrieval experiments, hybrid ranking, RAG ... remain deferred' (432 blank) | CONFLICT | Hybrid ranking exists: internal/transport/http/handler.go:218 `GET /knowledge-library/hybrid-search`, added 2026-10-14 (33c1dc2, #96); file itself cites hybrid at 474, 479 | owner decision |
| 433-445 | FLH-042 semantic index run consistency (445 blank) | L1 | Index lease/fencing; legacy file exists at internal/application/semantic_index_legacy.go and internal/storage/sqlite/semantic_index_legacy.go | contracts/semantic-search.md |
| 446-463 | FLH-054 query envelope + local E5 service (463 blank) | L1 | Embedding service contract | contracts/semantic-search.md |
| 464-484 | FLH-073 read-only public demo (484 blank) | L1 | Public demo boundary | contracts/public-demo.md |
| 485-501 | FLH-083 UI localization (501 blank) | L1 | Web i18n rules | contracts/web-ui.md |
| 502-511 | Workbench theme Endfield (511 blank) | L1 | Web theme rules | contracts/web-ui.md |
| 512-530 | FLH-095 bilingual explanations (530 blank) | L1 | Analysis/unit bilingual fields; v2 extractor and explanation_en_zh_v1 confirmed (internal/analyzer/openai.go:22) | contracts/core-records.md |
| 531-548 | FLH-097 AI-assisted Concept suggestions (548 blank) | L1 | Suggestion runs contract | contracts/concept-resolution.md |
| 549-561 | FLH-084 AWS demo deployment prep: Terraform, flh-release | L1 | Deployment contract | contracts/aws-deploy.md |
| 562-563 | 'Agents must not run terraform plan/apply/destroy or AWS commands; the owner runs docs/AWS_DEMO.md' | L0 | Safety rule independent of feature | L0 constitution |
| 564-565 | 'terraform fmt/init ... authorised for FLH-085 only. Decisions and cost: docs/plans/...' | OUTDATED | One-off past authorization; keep the doc pointer in aws-deploy | delete-as-history (pointer -> contracts/aws-deploy.md) |
| 566-571 | FLH-085 lock file, cloud-config render (571 blank) | L1 | Terraform lockfile rule | contracts/aws-deploy.md |
| 572-584 | Recall Practice v1 (584 blank) | L1 | Practice page contract (web/src/pages/RecallPractice.tsx exists) | contracts/review-and-notebook.md |
| 585-603 | Exam Listening Lesson v1 (603 blank) | L1 | Listening contract | contracts/listening.md |
| 604-621 | Exam Reading v1 (621 blank) | L1 | Reading contract | contracts/reading-writing.md |
| 622-640 | Exam Writing v1 (640 blank) | L1 | Writing contract | contracts/reading-writing.md |
| 641-686 | Speaking v1: migration 018, audio rules, providers, local Whisper, practice/export (686 blank) | L1 | Speaking contract (contains L0 dup 'Agents never run AWS commands' at 664) | contracts/speaking.md |
| 687-703 | Review Today, Make practice, learning-week | L1 | Review Today contract | contracts/review-and-notebook.md |
| 704-716 | Practice prototype + practice checks | L1 | Prototype/fixture contract | contracts/review-and-notebook.md |
| 717-718 | Web typecheck is `npm run typecheck`; `tsc --noEmit -p .` checks nothing | L1 | Web-only command fact; verified web/tsconfig.json has `"files": []` | contracts/web-ui.md |
| 719-723 | learning_history_export_v1 / cmd/history-export (723 blank) | L1 | Export contract (small; folded into notebook file) | contracts/review-and-notebook.md |
| 724-742 | Mistake Notebook v1: source_ref, migration 019, retry marks, grammar/vocab views | L1 | Notebook contract | contracts/review-and-notebook.md |
| 743-748 | Sections v1 Home tab (748 blank) | L1 | Home page contract; web/src/pages/LearningHome.tsx:4 still says seven sections. Commit 44a6dfd (#173, portal) may have changed the shell (inferred, not checked) | contracts/web-ui.md |
| 749-764 | Mock Exam v1 (764 blank) | L1 | Mock exam contract | contracts/mock-exam.md |
| 765-791 | Daily Listening pull/prepare/notify (791 blank) | L1 | Listening daily contract (contains L0 'agents must never install or enable' timer at 770-771) | contracts/listening.md |
| 792-793 | 'Authored listening scripts (workbook):' label (793 blank) | L1 | Section label | contracts/listening.md |
| 794 | 'Migration 021 (018-020 are reserved for the private stack) rebuilds' | OUTDATED | 018-020 now exist in repo: migrations/018_speaking_exercises.sql, 019_mistake_notebook.sql, 020_mock_exams.sql; also 022_vocabulary.sql exists and is undocumented here | fix wording, move to contracts/listening.md |
| 795-831 | Authored scripts: CHECK, pack, import, revisions, evidence audit (831 blank) | L1 | Listening scripts contract | contracts/listening.md |
| 832-840 | ## Design Priorities (840 blank) | L0 | Repo-wide data/design priorities | L0 constitution |
| 841-852 | ## Core Data Principles list (category, AI explanation, confidence per record) (842,844,852 blank) | CONFLICT | Says every learning record preserves category/explanation/confidence; 19-23 and 306-312 say Entry has none (internal/domain/entry.go:24-30). Intent survives only if 'record' = Entry+Analysis | owner decision (reword into L0) |
| 853-859 | 'Original user input is the source of truth'; AI metadata never rewrites; corrections immutable (854,859 blank) | L0 | Core data invariant. Wording 'editable metadata' (855) clashes with 837/857 append-only - see conflict C6 | L0 constitution |
| 860-872 | ## Architecture Principles (872 blank) | L0 | Layering, migrations, simplicity | L0 constitution |
| 873-887 | ## Development Workflow (887 blank) | L0 | Universal task procedure | L0 constitution |
| 888-907 | ## Agent Roles and Coordination (Claude Code large / Codex small / Kiro) (907 blank) | L0 | Session-role defaults; tool-specific but repo-wide | L0 constitution |
| 908-927 | ### One writer by default (927 blank) | L0 | Ownership rule | L0 constitution |
| 928-948 | ### Parallel work and shared contracts (948 blank) | L0 | Concurrency rules | L0 constitution |
| 949-965 | ### Handoff and review (965 blank) | L0 | Handoff/review rules | L0 constitution |
| 966-974 | ## Working Rules: evidence, no unrelated edits, secrets, small changes | L0 | Universal rules | L0 constitution |
| 975 | 'Update this document when real project commands or architecture change.' | CONFLICT | Drives 144-831 growth; conflicts with 999-1001 and with 997 'durable instructions' | owner decision (redirect to contract files) |
| 976-981 | Working Rules: agents never run Git/GitHub, incl. read-only (981 blank) | L0 | Git prohibition (duplicated at 1069-1073) | L0 constitution |
| 982-998 | ## Documentation: doc map | L0 | Where docs live | L0 constitution |
| 999-1001 | 'Documentation should describe the implemented system... Keep temporary, task-specific instructions out of this file.' (1001 blank) | CONFLICT | Routinely not followed: 144-831 are task-specific FLH/milestone notes | owner decision (keep rule in L0, move 144-831 out) |
| 1002-1007 | README.zh-CN.md companion rule (1007 blank) | L0 | Applies to every implementation task | L0 constitution |
| 1008-1028 | ## Definition of Done + commit-message template (1028 blank) | L0 | Universal DoD | L0 constitution |
| 1029-1035 | ## Scope Discipline: workbench may be extended; business rules in backend (1035 blank) | L0 | Scope rule | L0 constitution |
| 1036-1045 | AI-assisted batch resolution suggestions: roadmap + advisory-only (1040 blank, 1045 blank) | L0 | Advisory-AI rule; 'within the current roadmap' is status text (FLH-097 already shipped, 531-548) | L0 constitution (drop roadmap phrasing) |
| 1046-1048 | 'Existing retrieval and embedding ... may be maintained... New model experiments and retrieval capabilities are deferred.' (1048 blank) | L0 | Scope rule; see conflict C3 (hybrid/E5 added after it) | L0 constitution |
| 1049-1052 | 'Release packaging ... are within the current roadmap. Choose a simple deployment path' (1052 blank) | OUTDATED | Roadmap status; v1.0 packaging already shipped (325-359). Last sentence duplicates 12/870 | delete-as-history |
| 1053-1057 | Do-not-introduce list: agents, schema rewriting, recommenders | L0 | Scope prohibitions | L0 constitution |
| 1058 | 'New embedding, vector-search, or semantic-retrieval capabilities' | CONFLICT | Added 2026-10-07 (9ce0ca6), then E5 service 2026-10-12 (26a3221) and hybrid search 2026-10-14 (33c1dc2) were added; 416 only covers FLH-040 | owner decision |
| 1059-1060 | Learned resolvers; review scheduling/mastery | L0 | Scope prohibitions | L0 constitution |
| 1061 | 'Speech processing' | CONFLICT | Speaking v1 (641-686) does transcription incl. local Whisper (658-664; tools/speaking/CONTRACT.md:53) | owner decision |
| 1062 | Authentication / multi-user | L0 | Scope prohibition | L0 constitution |
| 1063 | 'Additional AI providers or automatic provider fallback' | CONFLICT | Local Whisper provider (658), local E5 service (452), plus SUGGESTION/LISTENING/READING/WRITING/SPEAKING providers added | owner decision |
| 1064-1068 | Automatic retry, streaming, batch; billing dashboards; unrelated infra (1068 blank) | L0 | Scope prohibitions | L0 constitution |
| 1069-1074 | ## Human-Owned Git Workflow (1074 blank) | L0 | Duplicate of 976-980; merge | L0 constitution (merge with 976-980) |
## 2. Proposed contract files (17)

Line counts are AGENTS.md lines moved before any condensing. OUTDATED lines that only need a wording fix are included where noted.

| File | Source line ranges | Approx lines |
|---|---|---|
| contracts/core-records.md | 19-23, 24-34, 35, 36-40, 304-315, 512-530 | 53 |
| contracts/capture.md | 41-52, 53-66 | 26 |
| contracts/extraction.md | 67-84, 85 (fix to `knowledge_extraction_v2`), 86-92, 364-374 | 37 |
| contracts/concept-resolution.md | 93-113, 115-139, 147-154, 375-387 (FLH-029), 531-548 (FLH-097) | 85 |
| contracts/annotation-workbench.md | 158-168, 172-182, 290-303 | 36 |
| contracts/retrieval-evaluation.md | 186-200, 201-221, 222-238, 242-263, 268-285 | 93 |
| contracts/knowledge-library.md | 388-404, 410-415 | 23 |
| contracts/semantic-search.md | 416-428, 433-445, 446-463 (+ owner's resolution of 429-431) | 44 |
| contracts/release.md | 316-320, 325-359, 405-409 | 45 |
| contracts/public-demo.md | 464-484 | 21 |
| contracts/aws-deploy.md | 549-561, 565 (doc pointer only), 566-571 | 20 |
| contracts/web-ui.md | 485-501, 502-511, 717-718, 743-748 | 35 |
| contracts/review-and-notebook.md | 572-584, 687-703, 704-716, 719-723, 724-742 | 67 |
| contracts/listening.md | 585-603, 765-791, 792-793, 794 (fix wording), 795-831 | 86 |
| contracts/reading-writing.md | 604-621, 622-640 | 37 |
| contracts/speaking.md | 641-686 | 46 |
| contracts/mock-exam.md | 749-764 | 16 |

Not covered by any text in AGENTS.md (gaps noticed while checking code, not classified lines): migration `022_vocabulary.sql` and the private vocabulary feature (commit 56fa8d3, #175), the private learning workspace and portal (44a6dfd, #173), cross-subject evidence-to-recall units (9f09c6f, #176), `cmd/extraction-render`, and the hybrid search endpoint `GET /knowledge-library/hybrid-search` (internal/transport/http/handler.go:218). The owner may want contract files for these (inferred need, not checked in depth).

## 3. Candidate L0 rules (quoted, with line numbers)

Project identity and priorities
- 3-6: "This repository contains a personal French-learning knowledge service and an engineering portfolio project. It preserves learner-authored records, produces versioned interpretations and extraction evidence, and supports auditable human organization of KnowledgeUnits into durable KnowledgeConcepts."
- 8-12: "The current priority is a usable, reproducibly released service ... Preserve existing data and research capabilities. Model training and new retrieval experiments are deferred until the operational baseline is reliable. Prefer simple, testable implementations over speculative platform architecture."
- 16: "The application is a single Go HTTP service backed by SQLite"
- 141-142: "Inspect the repository before proposing changes; do not assume planned directories, frameworks, services, or database schemas exist until confirmed."

Verification and publishing
- 321-323: "Root `make verify` is the non-mutating release check: Go formatting, tests, vet, server/capture builds, and frontend typecheck/tests/production build. It expects `web/node_modules` to have been installed separately with `npm ci`."
- 360-362: "Publishable metadata (commit messages, PR bodies, squash messages) carries no Claude/Anthropic co-author trailer or identity and no private `claude.ai` link; check it with `scripts/release/publish_metadata_check.py`."

Safety (cloud/host actions)
- 562-563: "Agents must not run `terraform plan/apply/destroy` or AWS commands; the owner runs `docs/AWS_DEMO.md`."
- 664: "Agents never run AWS commands for it." (duplicate; generalise with 562)
- 770-771: "`scripts/daily/systemd/` holds an example timer that agents must never install or enable." (generalise: agents never install/enable host services or timers)
- 943-944: "Never share a writable test database or mutate the user's daily database."

Design and data
- 834-839: "1. Preserve original learning questions without information loss. 2. Keep AI-generated metadata separate from original user data. 3. Keep every workflow complete from input to persistent storage. 4. Preserve history: prefer append-only, versioned records over in-place edits. 5. Add higher-level features (review generation, scheduling) only after the layers they depend on work reliably."
- 853: "The original user input is the source of truth."
- 855-858: "They must never replace or silently rewrite the original record. Human corrections and feedback are stored as additional immutable records, never by mutating the analysis they refer to." (drop "editable metadata", see C6)
- 313-314 (from an L1 block, generalisable): "Do not ... rewrite migration history."

Architecture
- 862: "Keep HTTP transport, business logic, persistence, and AI integration separate."
- 863: "HTTP handlers must not contain direct database logic."
- 864: "AI output must be validated before storage."
- 865: "Database schema changes must use explicit migrations."
- 866-867: "Prefer stable core tables plus extensible metadata over uncontrolled AI-generated schema changes."
- 868-869: "Do not introduce distributed services, Kubernetes, event buses, or complex agent orchestration unless a demonstrated requirement justifies them."
- 870-871: "Prefer standard libraries and mature dependencies." / "Keep components replaceable through small interfaces."

Workflow
- 875-886: "For non-trivial tasks: 1. Inspect relevant working-tree files and task context without Git/GitHub commands. 2. Explain the existing behavior and constraints. 3. Propose a concise implementation plan. 4. Identify exact files that will be created or modified. 5. Implement only the requested scope. 6. Format changed files. 7. Run relevant tests. 8. Report changed files, commands executed, test results, and unresolved risks. For small and obvious fixes, proceed after inspecting the affected files."

Roles and coordination
- 890-891: "These are task-assignment defaults, not claims about model capability. The human owner may override them for an explicitly scoped task."
- 893-897: "Claude Code owns large tasks ... Large scope does not authorize unrelated changes or a whole-repository rewrite."
- 898-900: "Codex owns small, bounded tasks ... Codex also performs bounded sanity checks and independent reviews of Claude's changes."
- 901-902: "Either tool may review the other's work. A review assignment is read-only unless the human owner explicitly transfers implementation ownership."
- 903-906: "Claude Code backed by Kiro credits and Claude Code backed by a personal subscription are separate sessions of the same role. Neither is automatically a coordinator or entitled to edit the other's files. Provider configuration and credentials stay outside this repository."
- 910-914: "One shared working tree has one implementation owner at a time. Other agents may inspect files and report findings, but must not edit, run mutating formatters, install dependencies, regenerate code, or update snapshots in that working tree."
- 916-922: "Before modifying files, every implementation task must identify: 1. Task ID ... and acceptance criteria. 2. Implementation owner and provided working directory. 3. Exact files or bounded directories it owns. 4. API, schema, and shared-file dependencies. 5. Validation commands and isolated test database/output locations."
- 924-926: "If ownership is missing or ambiguous, inspect and propose the scope first; do not start a second writer. Do not take over an active task merely because its owner is waiting, slow, or has hit a usage limit."
- 930-932: "Prefer separate working copies prepared by the human owner ... Different machines do not isolate a shared NAS-mounted folder. Agents must not create branches/worktrees themselves."
- 933-935: "The human owner must confirm disjoint file ownership and compatible contracts before parallel edits. Never have two agents edit the same file concurrently, including this document and task-status files."
- 936-938: "Schema/migration allocation, shared domain types, HTTP DTOs, API client types, dependency manifests/lockfiles, global configuration, Makefile and CI changes require one designated owner."
- 939-940: "If a task needs another owner's file or a contract change, report the dependency and continue independent in-scope work. Do not make a competing edit."
- 941-942: "Do not run repository-wide formatting, auto-fixes, dependency upgrades, or generated-file updates while another implementation task is active."
- 943-944: "Tests use separate temporary databases, output directories and ports."
- 945-947: "Temporary discussion belongs in per-task notes under docs/plans/ when useful; a note's file has one owner. Do not invent a shared editable lock file as a guarantee of mutual exclusion."
- 951-954: "The implementation owner reports the modified files, contract changes, validation evidence, pending work and known risks, then stops editing before ownership transfers. The receiving agent rereads the current files and must not overwrite newer work using a stale snapshot."
- 956-958: "Review findings must name the affected location, concrete failure scenario, impact and suggested verification. Report findings instead of silently patching."
- 959-961: "The human owner integrates separate copies and owns all Git/GitHub operations. After integration, validate the combined working tree; separate passing checks alone do not prove integration correctness."
- 963-964: "Do not spawn further coding agents, expand file ownership, or start an unattended multi-agent loop unless explicitly requested for that task."

Working rules
- 968: "Never claim a command or test passed unless it was actually executed."
- 969: "Do not silently modify unrelated files."
- 970: "Do not delete user work merely because another structure seems cleaner."
- 971: "Do not place credentials, tokens, account data, or local machine paths in Git."
- 972: "Do not read or expose `.env`, credential databases, SSH keys, or secret files."
- 973-974: "Keep working-tree changes small and focused so the human owner can commit them cleanly."
- 976-980 (and 1071-1073): "Coding agents must not run Git or GitHub commands at all, including read-only inspection. The human owner exclusively controls branches, staging, commits, pushes, pull requests, merges, and rebases."

Documentation
- 986-997: doc map ("`README.md` for setup and user-facing project information (concise; no milestone history or full API reference)"; "`docs/ARCHITECTURE.md` for the canonical current design ... HTTP API reference in its Appendix A"; "`docs/plans/` ... and `docs/validation/` ... are historical records, kept in place and not rewritten"; "`AGENTS.md` for durable instructions to coding agents").
- 999-1000: "Documentation should describe the implemented system, not an imaginary future platform. Keep temporary, task-specific instructions out of this file."
- 1002-1006: "Every completed implementation task must ensure that `README.zh-CN.md` exists ... update the corresponding Chinese documentation in the same task."

Definition of Done
- 1010-1022: "1. Relevant files are formatted. 2. Relevant automated tests pass. 3. Error paths are handled. 4. Documentation is updated when behavior changes. 5. No credentials or generated local files are included. 6. The final report names modified files and validation commands. 7. The final report ends exactly with a suggested commit message for the human owner. The agent must only suggest it, never execute it. 8. The final report includes an executable `git add -- ...` command listing only the files modified for the task. The agent must display the command ... and must never execute it."
- 1024-1027: template "Suggested commit message: <type>: <concise description>"

Scope
- 1031-1034: "The existing React/Vite/TypeScript workbench may be extended for personal daily use ... Keep business rules and current authority in the backend; UI convenience must not change annotation semantics."
- 1037-1039: "Suggestions must remain separate from CURRENT SAME and human labels. Human acceptance must validate current state and preserve auditable provenance. Automatic application of AI suggestions is not part of this phase."
- 1041-1044: "AI-assisted resolution suggestions may use semantic judgment, but must remain advisory. This does not authorize training a resolver or automatically applying its decisions. Roadmap permission is not an instruction to implement the whole roadmap in a single task."
- 1046-1047: "Existing retrieval and embedding capabilities may be maintained, tested, and fixed. New model experiments and retrieval capabilities are deferred."
- 1053-1067: "Unless explicitly requested, do not introduce:" advanced agents; automatic schema rewriting; recommendation systems; new embedding/vector/semantic retrieval (1058, see C3); learned or automatically applied resolvers; review scheduling or mastery systems; speech processing (1061, see C4); authentication/multi-user/commercial expansion; additional AI providers or automatic provider fallback (1063, see C5); automatic retry, streaming, or batch analysis beyond scoped batch suggestions; usage/billing dashboards; unrelated infrastructure changes.

Recurring cross-feature invariants that appear in many L1 blocks and could be stated once in L0 (inferred generalisation, owner to confirm): every provider is opt-in and defaults to `disabled` (e.g. 83, 533, 593, 614, 631, 655-657); no silent fallback and no automatic retry (84, 255, 597, 784); public errors never leak keys, provider bodies or learner content (90-92, 255); private-workbench features are never in the public demo (583, 601, 620, 639, 669, 695, 742, 763, 789-790, 809).

## 4. Conflicts

C1. Purpose of `web/`
- 146: "`web/` is a human annotation/data-collection UI, not a consumer product UI."
- 1031-1033: "The existing React/Vite/TypeScript workbench may be extended for personal daily use: capture import, learning-record browsing, ..." and 572-831 describe learner-facing practice, exam, speaking, notebook and Review Today pages in the workbench.

C2. Hybrid ranking deferred vs. present
- 429-431: "Further retrieval experiments, hybrid ranking, RAG, training and automatic SAME remain deferred."
- 474: "`/knowledge-library/demo{,/semantic-search,/hybrid-search}`" and 478-479: "The existing semantic and hybrid services run over a read-only in-process index". Code: internal/transport/http/handler.go:218 `GET /knowledge-library/hybrid-search`; internal/application/hybrid_search.go, added 2026-10-14 in 33c1dc2 (#96).

C3. No new embedding/semantic retrieval vs. later additions
- 1046-1047: "New model experiments and retrieval capabilities are deferred." and 1058: "New embedding, vector-search, or semantic-retrieval capabilities" (both from 9ce0ca6, 2026-10-07 by `git blame`).
- 446-462 (FLH-054 local E5 embedding service; tools/embedding/e5 added 2026-10-12 in 26a3221, #81) and the hybrid search above (2026-10-14). Only FLH-040 (416) is marked owner-authorized. Whether the later work was "explicitly requested" is not recorded in the file (owner decision).

C4. Speech processing
- 1061: "* Speech processing" (do not introduce unless explicitly requested).
- 641-686 Speaking v1: audio takes, transcription requests, `SPEAKING_TRANSCRIPTION_PROVIDER=local` sending audio to a local Whisper service (658-664; tools/speaking/CONTRACT.md:53 `whisper-base@...`). Added 2026-11-11 (45be549, #139).

C5. Additional AI providers
- 1063: "* Additional AI providers or automatic provider fallback"
- 452-457 (local E5 HTTP embedding service), 658-664 (local Whisper transcription provider), plus per-feature OpenAI providers at 533, 593, 614, 631, 655. The rule is routinely not followed (each may have been owner-requested; not recorded).

C6. Core Data Principles vs. Entry ownership
- 843-851: "Every learning record should preserve: ... Detected learning category; AI-generated explanation; Confidence or uncertainty metadata ..."
- 19-23: "Active `Entry` domain and wire models contain only learner-authored source data, IDs, and timestamps"; 306-307: "`Entry` ... has no active AI metadata fields." Code: internal/domain/entry.go:24-30 (ID, OriginalInput, OriginalContext, CreatedAt, UpdatedAt only).
- Sub-tension: 855 "AI classifications and explanations are editable metadata" vs 837 "prefer append-only, versioned records over in-place edits" and 857-858 "never by mutating the analysis".

C7. "Update this document" vs "keep task-specific text out"
- 975: "Update this document when real project commands or architecture change."
- 999-1000: "Keep temporary, task-specific instructions out of this file." and 997 "`AGENTS.md` for durable instructions". Routinely not followed: 144-831 are per-milestone/per-task (M10.6 ... FLH-097) notes, ~690 lines.

C8. Extractor schema version (internal)
- 84-85: "provenance is `openai:<model>:knowledge_extraction_v1`"
- 522-523: "extractor `knowledge_extraction_v2`". Code sides with 523: internal/extractor/openai.go:29 `const schemaVersion = "knowledge_extraction_v2"` (doc comment at internal/extractor/openai.go:109 and internal/domain/knowledge.go:237 still say v1 as an example).

C9. Migrations 018-020 "reserved" (internal)
- 794: "Migration 021 (018–020 are reserved for the private stack)"
- 643 "Migration 018: `speaking_exercises`", 730 "(migration 019)", 751 "Migration 020: `exam_sessions`". All three files exist in migrations/. (The header of migrations/021_listening_authored_scripts.sql:1-2 explains the reservation as history.)

C10. Report ending (minor, possible)
- 1017-1018: "The final report ends exactly with a suggested commit message"
- 1020-1022: "The final report includes an executable `git add -- ...` command". Not strictly contradictory, but the required position of the `git add` line relative to the "ends exactly with" commit message is unspecified (inferred ambiguity).

## 5. Line counts per category

| Category | Lines | Share |
|---|---|---|
| L0 | 252 | 23.5% |
| L1 | 767 | 71.4% |
| OUTDATED | 29 | 2.7% |
| CONFLICT | 26 | 2.4% |
| Total | 1074 | 100% |

Counts include blank lines folded into each row. Lines classified OUTDATED or CONFLICT whose conflict partner is classified L0/L1 are counted only once, under their own row. Of the 252 L0 lines, 66 are blank or headings and 976-980 / 1069-1073 duplicate each other, so the condensed constitution should fit well under 250 lines.

Caveat on method: most L1 rows still contain milestone/FLH labels and delta phrasing ("adds no ...") that should be dropped when moved; only lines that are wholly history were classified OUTDATED. Code was checked only where a row cites file:line.

---

## 6. Context budget (R6-DOC-060)

Measured on FLH `9f09c6f` and on the proposed split (`proposals/flh-agents-split.patch` in the docs lane's
handoff folder). Bytes, lines and words are real counts. "Est. tokens" is characters ÷ 4, a common rule of
thumb for English Markdown, **not** a tokenizer count; use it for the ratios, not as an exact number. The
L2 sample is a real `pai worktree init --json` preview (pai 0.4.0, base `c8bc51e`) for an extraction lane.

| Set | Lines | Bytes | Words | Est. tokens | vs. today | Under Codex's 32 KiB? |
| --- | --- | --- | --- | --- | --- | --- |
| Current AGENTS.md (every session today) | 1,074 | 64,650 | 8,191 | ~16,145 | 100 % | **no** (63 KiB) |
| Proposed L0 constitution | 249 | 12,157 | 1,650 | ~3,039 | 19 % | yes |
| L0 + typical L2 (extraction lane section) | 363 | 19,952 | 2,661 | ~4,988 | 31 % | yes |
| L0 + L2 + that lane's contract (extraction.md) | 407 | 22,730 | 2,997 | ~5,678 | 35 % | yes |
| Largest contract alone (retrieval-evaluation.md) | 101 | 5,980 | 722 | ~1,495 | 9 % | yes |

**Codex reads only the first 32 KiB.** Codex's documentation says it stops reading instruction files at
`project_doc_max_bytes`, 32 KiB by default (reported-by-prompts, `docs/research/LANDSCAPE.md`, re-verified
2026-11-22). In today's file the 32 KiB mark falls at line 519. Every L0 section starts after it:

- Design Priorities (line 832);
- Working Rules, including "agents must not run Git" (966–981);
- Definition of Done (1008);
- Scope Discipline (1029);
- Human-Owned Git Workflow (1069).

So a Codex session on FLH would not see any of them. This is *inferred* from the documented limit and the
measured byte offsets; no real Codex run was made. After the split, the constitution plus a lane section is
about 20 KB, well under the limit.

## 7. How clear each proposed rule is (R6-DOC-059)

JEV (TypeSafe, real requests) scored each of the 64 rules of the proposed constitution, with this question:
"an agent can tell exactly what to do or not do, and when, without asking". List items were sent together
with their lead-in sentence; the contract index was left out.
- Median 0.33; 27 rules below 0.3; 2 at 0.6 or above.
- Scores per rule: `~/.cache/pai-R6-20261122/docs/jev/DOC-059-rules-v2.jsonl`.
- An earlier run without the lead-in sentences scored isolated fragments ("Recommendation systems") at about
  0.12. That is an artifact of the extraction, and that run is not used.

| Line | Section | Score | Rule (start) |
| --- | --- | --- | --- |
| 164 | Working Rules | 0.73 | Coding agents must not run Git or GitHub commands at all, including read-only in |
| 157 | Working Rules | 0.64 | Never claim a command or test passed unless it was actually executed. |
| 160 | Working Rules | 0.59 | Do not place credentials, tokens, account data, or local machine paths in Git. |
| 217 | Scope Discipline | 0.53 | Unless explicitly requested, do not introduce: -> Usage or billing dashboards |
| 188 | Definition of Done | 0.51 | Before declaring a task complete: -> The final report includes an executable `gi |
| … | | | |
| 162 | Working Rules | 0.15 | Keep working-tree changes small and focused so the human owner can commit them c |
| 59 | Architecture Principles | 0.18 | Prefer standard libraries and mature dependencies. |
| 180 | Definition of Done | 0.18 | Before declaring a task complete: -> Relevant files are formatted. |
| 60 | Architecture Principles | 0.19 | Keep components replaceable through small interfaces. |
| 67 | Development Workflow | 0.20 | For non-trivial tasks: -> Explain the existing behavior and constraints. |
| 68 | Development Workflow | 0.20 | For non-trivial tasks: -> Propose a concise implementation plan. |
| 70 | Development Workflow | 0.20 | For non-trivial tasks: -> Implement only the requested scope. |
| 71 | Development Workflow | 0.20 | For non-trivial tasks: -> Format changed files. |

What the scores say: prohibitions with an exact object score high ("must not run Git or GitHub commands at
all", 0.73; "never claim a command or test passed unless it was actually executed", 0.64). Preferences
without a test score low ("keep working-tree changes small and focused", 0.15; "prefer standard libraries",
0.18; "relevant files are formatted", 0.18). Before adopting the constitution, give each low-scoring rule
a check an agent can run. For example, "formatted" can become "`gofmt -l` prints nothing; `npm run lint` exits 0".
