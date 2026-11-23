# Landscape: "a lead agent directs sub-agents" tools, and how PAI differs (R6-PRM-036)

Round R6-20261122, prompts lane, 2026-11-22. Only official documentation pages that were opened on
that day (through web-fetch agents) are used. Long pages were returned as ~40 000 verbatim characters
plus a model summary of the rest. Claims taken from that summary are marked *(summary)*. "Not
stated" means the opened pages did not say it. It does not mean the product lacks it. The docs
lane may cite this page as reported-by-prompts (R6-DOC-024).

## 1. Frameworks

| | Routing | Where state lives | Where the person is | Source (opened) |
|---|---|---|---|---|
| **LangGraph / LangChain** | Subagents pattern: "All routing passes through the main agent, which decides when and how to invoke each subagent." The supervisor library has been migrated to that pattern. | The graph state lives in a checkpointer. Interrupts "save the graph state using its persistence layer and wait indefinitely until you resume". Subagents are "stateless by design". | `interrupt()` pauses the graph for external input, and `Command(resume=…)` continues it on the same thread. | docs.langchain.com/oss/python/langgraph/interrupts; …/langchain/multi-agent/index.md (the old langgraph multi_agent page only redirected) |
| **CrewAI** | A manager agent "coordinates the workflow, delegates tasks, and validates outcomes" (hierarchical process). | Not stated on the pages opened. An example sets `memory=True`. | `human_input=True` on a Task: "the agent prompts the user for input before delivering its final answer". | docs.crewai.com/en/learn/hierarchical-process; …/human-input-on-execution |
| **Microsoft AutoGen (AgentChat)** | SelectorGroupChat: "A generative model (e.g., an LLM) selects the next speaker based on the shared context". A `selector_func` can override the choice. | The message thread: "the conversation context is kept within the team and all participants". | UserProxyAgent asks the user for feedback during a run; the example continues only on "APPROVE" *(summary)*. | microsoft.github.io/autogen/stable/…/selector-group-chat.html (Magentic-One not opened) |
| **OpenAI Agents SDK** | "Handoffs are represented as tools to the LLM"; control moves to the target agent within one run. | Conversation history ("still sees the conversation history unless you change it with an input_filter"). For approvals, `RunState` is "designed to be durable" and is serialised with `to_json()`. | The run can "pause agent execution until a person approves or rejects sensitive tool calls". | openai.github.io/openai-agents-python/handoffs/; …/human_in_the_loop/ (guardrails page not opened) |
| **Claude Code subagents** | "Claude uses each subagent's description to decide when to delegate tasks", or the user names one. Depth is limited to 3 levels and 20 run at once by default *(summary)*. | Each subagent "runs in its own context window with a custom system prompt". Transcripts are stored under `~/.claude/projects/…/subagents/` *(summary)*. | Foreground subagents pass permission prompts to the user; background ones surface them in the main session; approvals from other agents do not count *(summary)*. | code.claude.com/docs/en/sub-agents (Agent SDK overview not opened) |

## 2. Instruction files: local (not in git) and nested (mastermind's 2026-11-22 question)

### Claude Code: code.claude.com/docs/en/memory

| File | Scope | In git? |
|---|---|---|
| `/etc/claude-code/CLAUDE.md` (Linux/WSL); `/Library/Application Support/ClaudeCode/CLAUDE.md` (macOS) | managed policy, "Organization-wide instructions managed by IT/DevOps" | — |
| `~/.claude/CLAUDE.md`, `~/.claude/rules/` | user, "Personal preferences for all projects" | no |
| `./CLAUDE.md` or `./.claude/CLAUDE.md` | project, "Team-shared instructions" | yes |
| `./CLAUDE.local.md` | "Personal project-specific preferences; add to `.gitignore`". The page describes it as supported, not deprecated, and notes that a git-ignored copy "only exists in the worktree where you created it". | **no (local)** |
| `.claude/rules/*.md` | "discovered recursively"; rules without `paths` front matter load at launch; path-scoped rules load when a matching file is read or edited | yes |

**Nested directories.** Parent directories are read at launch: "loads `CLAUDE.md` and `CLAUDE.local.md`
from your current working directory and every directory above it", ordered from the root down.
Subdirectories are read on demand, when Claude "reads, writes, or edits another file in that
subdirectory". **Imports**: `@path`, relative to the importing file, at most four hops.
**AGENTS.md**: read "only when you have no `CLAUDE.md` in your working directory or above it", unless
the project-instructions setting is `claude-md-and-agents-md`. `AGENTS.local.md` and
`AGENTS.override.md` are "Not read".

### OpenAI Codex: learn.chatgpt.com/docs/agent-configuration/agents-md

The old URL developers.openai.com/codex/guides/agents-md redirects here, and the GitHub docs/agents_md.md is a stub that links here.

- **Global**: in `~/.codex` (or `$CODEX_HOME`), `AGENTS.override.md` if present, "Otherwise, Codex reads `AGENTS.md`".
- **Nested**: "Starting at the project root (typically the Git root), Codex walks down to your current
  working directory". In each directory it uses the first of `AGENTS.override.md`, `AGENTS.md`, then
  `project_doc_fallback_filenames`, and it "includes at most one file per directory". Files are
  concatenated root-first, so later (nearer) files override earlier ones.
- **Limit**: stops at `project_doc_max_bytes` (32 KiB by default).
- **When**: the chain is built once per run, at start.
- **Local, not-in-git file**: not stated. There is no CLAUDE.local.md counterpart. `AGENTS.override.md`
  is documented as an override, not as a personal file.

### Re-verification for R7 (R6-PRM-062, pages reopened 2026-11-22)

URLs: https://code.claude.com/docs/en/memory (HTTP 200; verbatim for the first 39 949 of 55 421 characters, which covers
everything quoted below) and https://developers.openai.com/codex/guides/agents-md (308 redirect to
https://learn.chatgpt.com/docs/agent-configuration/agents-md, read in full). Accessed 2026-11-22 through a fetch agent.

| Claim used above | Verbatim from the page | Verdict |
|---|---|---|
| With a CLAUDE.md, Claude Code does not read AGENTS.md | "By default, Claude reads `AGENTS.md` only when you have no `CLAUDE.md` in your working directory or above it." | confirmed |
| A setting makes it read both | Project instructions = `claude-md-and-agents-md` via /config; settings form `"pluginConfigs": {"cc-plugin-agents-md@builtin": {"options": {"instructionFiles": "claude-md-and-agents-md"}}}`. "Reading `AGENTS.md` directly requires Claude Code v2.1.277 or later." | confirmed; **new detail**: the page says this key is ignored in project and local settings files |
| CLAUDE.local.md is supported, personal, per worktree | "For private per-project preferences that shouldn't be checked into version control, create a `CLAUDE.local.md` at the project root." · "If you work across multiple git worktrees of the same repository, a gitignored `CLAUDE.local.md` only exists in the worktree where you created it." | confirmed |
| @ imports: at most four hops | "Imported files can recursively import other files, with a maximum depth of four hops." | confirmed |
| Codex AGENTS.override.md | Global: "Codex reads `AGENTS.override.md` if it exists. Otherwise, Codex reads `AGENTS.md`." Per directory: "it checks for `AGENTS.override.md`, then `AGENTS.md`, then any fallback names…" and "Codex includes at most one file per directory." | confirmed |
| Claude Code and AGENTS.override.md / AGENTS.local.md | "Not read: `AGENTS.local.md`, `AGENTS.override.md`, or anything under a `.agents/` directory." | confirmed; adds `.agents/` |

Differences from what was written earlier on this page: none contradicted. There are two additions that matter for R7:
- **The "read both" option cannot be shipped with the repository.** It is ignored in project and local settings files,
  so it is a per-user setting, and a lane must not rely on it.
- **`.agents/` is not read by Claude Code.**

The fetch agent's model summary of the unread part of the page wrongly said the AGENTS.* names do not appear. The
verbatim text above is what counts.

### What follows for PAI (L0/L1/L2, COORDINATOR §7)

- **L2 (PAI-generated, per lane).** Claude Code has a documented un-committed file for this:
  `CLAUDE.local.md` in the worktree, which is per worktree as the page notes. Codex has no documented
  equivalent, so for Codex lanes L2 must stay in AGENTS.md's managed section (today's `pai-agents/1`)
  or in the rendered prompt. That keeps a dirty-tree risk (融合方案 §4.7).
- **Do not rely on AGENTS.md for Claude Code lanes when CLAUDE.md exists.** Claude Code skips
  AGENTS.md in that case unless a setting is changed. A CLAUDE.md with `@AGENTS.md` makes both agents
  read L0.
- **Codex's 32 KiB cap** applies to the whole L0 + nested chain. Long managed sections count against it.
- Proposal for core (agentsmd.py): offer `--file CLAUDE.local.md` for Claude lanes. That is not
  verified on a real run; the owner decides (PRM-D9 in the prompts-lane handoff).

## 3. How PAI differs (定位 v2 §0, checked against §1)

| | Frameworks above (as documented) | PAI |
|---|---|---|
| Who decides | the lead agent routes, and a person approves a paused step where configured | the person decides through decision sheets; the mastermind only splits work and recommends |
| State | runtime: checkpointer, message thread, RunState, per-subagent context | Markdown + JSON files in a vault: handoffs, queue logs, decision sheets. It survives /clear and machine restarts, and any editor can read it |
| What the person sees | an interrupt or an approval prompt for one tool call | a sealed checkpoint with labelled evidence, a PR plan and a decision sheet, read in minutes |
| Prompts | written by the developer in code | a versioned library with contracts, evaluated (deterministic + JEV) and changed only with the person's approval |
| Engine | its own runtime | none: drives existing Claude Code / Codex sessions |

None of the opened pages describes versioned prompts with output contracts, or source labels on
reported results. That is a statement about the pages opened, not about every product.
