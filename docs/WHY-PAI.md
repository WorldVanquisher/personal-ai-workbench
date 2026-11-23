# Why PAI

PAI is a **lubrication layer** for several coding agents working in parallel. It is not another
agent and not another orchestrator. This page says what that means, what PAI deliberately does not
do, and how it differs from the usual "a lead agent directs sub-agents" frameworks.

## What PAI is

**A person keeps every final decision.** The decisions are:

- the direction;
- approving a plan, a handoff, a PR;
- answering a decision sheet;
- the budget;
- every folder-trust prompt.

PAI's job is to make those decisions quick and well-founded. It does not make them.

**A mastermind splits; workers execute; PAI does the rest.** The roles:

| Who | Does | Does not |
| --- | --- | --- |
| You (the owner) | set the direction; approve plans, handoffs and PRs; tick the decision sheet | hunt for sessions in tmux, assemble commands, copy text around |
| The mastermind (a coordinating agent session) | split a request into lanes, items and decision-sheet questions; send prompts; read checkpoints; accept work | decide for you, answer trust prompts, push or merge |
| Workers (Claude Code or Codex sessions, one per lane) | work through their items in their own worktree; seal checkpoints; write the PR they propose into NEXT-PR.md | run git write commands, publish |
| PAI | prepare prompts and worktrees; start, watch and resume sessions; collect handoffs; keep the decision sheet, the queues and the budget; ship the manuals | rewrite Claude Code; be a chat or a terminal |
| JEV | cheap judgments: is this ready to start, does this report show evidence, is this owner-only | act as a gate: its answer is always compared with the real checks |

**All state lives in plain files.** The round board, the coordinator log, the handoffs, the decision
sheet and the PR plans are Markdown (plus a little JSON) in a folder. A cleared conversation loses
nothing. Obsidian is one way to read that folder; any editor works.

## What PAI is not

- **Not an agent runtime.** It starts the agents you already use (Claude Code with Remote Control,
  Codex) in tmux, and talks to them through their own interfaces. It does not reimplement them.
- **Not a chat or a terminal.** The browser views show screens, notes and buttons to *look, approve
  and choose*. To talk to a worker you use its own session.
- **Not autonomous.** Nothing is pushed, merged or published without a person's approval. Trust
  prompts are never answered by PAI.
- **Not a judge of truth.** JEV scores how a report is written. PAI's own checks (tests, the queue
  log, sha256 of sealed checkpoints) are what count.

## Three things PAI adds

1. **Your attention is the scarce resource.** Everything the agents produce becomes a ledger you can
   check:
   - every claim says whether it was really run, synthetic or self-reported;
   - decision sheets give options, trade-offs and a recommendation;
   - checkpoints are sealed with checksums;
   - the next PR is already written down.
   The aim is a trustworthy final decision in minutes.
2. **Prompts are assets that improve.** The prompts that drive the workers are versioned. They are
   checked against contracts and scored with JEV, and each improvement needs evidence and your
   approval (reported-by-prompts: `docs/PROMPTS.md`, in the prompts lane).
3. **No new engine, only lubrication.** PAI plugs into the sessions you already run (Remote Control,
   tmux). The interface is a folder of Markdown plus a small "look, approve, choose" workbench.

## How this differs from lead-agent / sub-agent frameworks

Everything in this section comes from the prompts lane's survey: `docs/research/LANDSCAPE.md`
(R6-PRM-036, 2026-11-22; re-read at its final version, sha256 c2397ae0…; §1 and §3, the parts used
here, unchanged; reported-by-prompts). That survey quotes only official documentation pages
it opened that day. Claims it took from a summary of a long page are marked *(summary)* below.
Where a page did not say something, that is not proof the product lacks it.

| | How work is routed | Where state lives | Where the person comes in |
| --- | --- | --- | --- |
| **LangGraph / LangChain** | the main agent "decides when and how to invoke each subagent" | a checkpointer; an interrupt saves the graph state "and wait[s] indefinitely until you resume" | `interrupt()` pauses for input; `Command(resume=…)` continues |
| **CrewAI** (hierarchical process) | a manager agent "coordinates the workflow, delegates tasks, and validates outcomes" | not stated on the pages opened | `human_input=True`: the agent asks the user before its final answer |
| **Microsoft AutoGen** (SelectorGroupChat) | a model "selects the next speaker based on the shared context" | the conversation, "kept within the team and all participants" | a user-proxy agent asks for feedback during a run *(summary)* |
| **OpenAI Agents SDK** | "Handoffs are represented as tools to the LLM" | conversation history; a durable `RunState` for approvals | a run can pause "until a person approves or rejects sensitive tool calls" |
| **Claude Code subagents** | Claude delegates by each subagent's description, or the user names one | each subagent has its own context window; transcripts under `~/.claude/projects/` *(summary)* | permission prompts reach the user *(summary)* |

What PAI does differently (the survey's §3, checked against the table above):

| | Those frameworks, as documented | PAI |
| --- | --- | --- |
| Who decides | the lead agent routes; a person approves a paused step where that is configured | the person decides, through decision sheets; the mastermind only splits the work and recommends |
| State | inside the runtime: a checkpointer, the message thread, a run state, each subagent's context | Markdown and JSON files in a folder (handoffs, queue logs, decision sheets). It survives `/clear` and reboots, and any editor reads it |
| What the person reviews | an interrupt, or an approval prompt for one tool call | a sealed checkpoint with labelled evidence, a PR plan and a decision sheet, readable in minutes |
| Prompts | written by the developer, in code | a versioned library with output contracts, evaluated and changed only with the person's approval |
| Engine | its own runtime | none: it drives the Claude Code and Codex sessions you already run |

The survey also notes that none of the pages it opened describes versioned prompts with output
contracts, or source labels on reported results. That is a statement about those pages, not about
every product.

Source for PAI's side: the owner's positioning note, *PAI 定位 v2：多 agent 润滑层*, §0–§2.
