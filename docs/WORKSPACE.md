# The PAI workspace: look, approve, choose

Status: design (R6-RND-029). The only part built in R6 is the read-only round board, `pai round
web` (R6-RND-030). Owner decision D5 says the approve and publish buttons come next round.

The workspace is PAI's single browser page for the person who holds the final decision. It
does three things: show the round, collect approvals, and record choices. It is **not**:
- a chat: talking to a worker happens in its Remote Control session, and the page links there;
- a terminal: `pai web` (the console) already exists, and only the console types into lanes;
- a code editor;
- an approver: every approval is a click by the person, recorded with the sha of exactly what
  they saw.

All state stays in the vault as Markdown plus a little JSON (D2). The page reads and writes
those files, so Obsidian, an editor and the page always show the same thing.

## Pages

| page | shows | reads | buttons (writes) |
|---|---|---|---|
| **Board** | one row per lane: state (running / idle / trust-prompt / exited), session name, link to its RC session, worktree clean, checkpoint, queue totals; the last collect | ROUND.json, `pai round status` (tmux + /proc + files), COLLECT.json | refresh (read). Next round: "collect now" (runs `pai round collect --write`) |
| **Decisions** | every open decision: question, options, trade-offs, recommendation, default, blocked items; its sha | DECISIONS.md and ROUND.json `decision_sheets` (`pai decide list`) | choose an option → `pai decide answer SHEET ID --sha <shown sha> --choice …`; refused if the text changed since it was shown |
| **Handoffs** | per lane and checkpoint: HANDOFF.md rendered, SHA256SUMS result, Items vs queue, JEV said-vs-shown (DISAGREES highlighted), RESTART.md | `<lane>/cp-*/`, COLLECT.json, the queue log | approve / reject a checkpoint (front matter `approved`, with its sha, as in fusion design §5.3: `pai handoff approve`, core lane) |
| **PR plans** | per lane: NEXT-PR.md, the wtpr preview of each commit (`pai round prplan`), the publication check | NEXT-PR.md, wtpr preview | "publish as draft / ready" → `pai worktree publish PLAN --expect-plan <digest> --yes`: the existing wtweb flow, unchanged |
| **Rules** | see the next section | AGENTS.md, docs/contracts/, L2 files, proposals | approve / reject a proposal → a PR plan; roll back a rule version |

Every page links to the lane's RC session (`claude.ai/code/session_…`) when one is known. That
link is the only way the page offers to talk to a worker.

## The rules page

The owner treats AGENTS.md as the heart of PAI. The full design is in the vault note
*PAI 意义、局限与 R7 方案* §4; this section is the workspace's part of it.

### 1. Layers

| layer | what | who changes it | where |
|---|---|---|---|
| L0 constitution | short project rules | only a person, through a PR | AGENTS.md at the repository root (tracked) |
| L1 contracts | one contract per feature | the owning lane proposes, a person approves | `docs/contracts/*.md` (tracked), referenced from L0 |
| L2 lane instructions | this round and lane: goal, scope, checks, handoff rules | PAI generates (`pai round new`), a person may adjust | a local, **untracked** file in the worktree (excluded through `.git/info/exclude`), referenced from the entry file, so the worktree stays clean and continuation and reuse keep working |
| L3 proposals | a worker wants L0 or L1 changed | the worker proposes only | `Agent Inbox/<round>/<lane>/proposals/AGENTS-*.patch`, listed as a decision |

### 2. "What this worker actually sees"

For one lane, the page composes L0 + L1 + L2 in the order the agent reads them. Every rule
line is labelled with:
- its source `file:line`;
- its layer;
- how it got there, reusing wtweb's labels: `manual` (written by a person), `generated` (PAI's
  managed block, `pai:managed`) and `inherited` (from a parent instruction file, `agentsmd.inherited`).

It also shows the entry file each agent starts from: CLAUDE.md through `@AGENTS.md`, or
AGENTS.md directly. A symbolic link is flagged, because it breaks on Windows checkouts with
`core.symlinks=false`. Read-only.

### 3. Proposal inbox

One card per L3 proposal:
- the diff;
- JEV's ambiguity and conflict scores (the agentsmd `--jev` questions; a second opinion, shown
  as numbers, never a gate);
- which lanes it affects (from ROUND.json scopes);
- which existing rules it contradicts, each with its `file:line`.

Buttons:
- **approve**: generates a PR plan (wtpr preview) for the patch. Publishing stays a separate,
  explicit step.
- **reject**: requires a reason.
- **edit then approve**: opens the patch text, not the repository.

Every decision is recorded with the sha of the proposal as shown.

### 4. Rule vs practice, versions, rollback

- **Conflicts**: "the rule says A, the lanes do B", found in handoffs and the coordinator log.
  Example: "agents may run no Git command" versus a lane that committed locally. Each one
  becomes a decision with two options, change the rule or constrain the behaviour, and the
  person picks.
- **Versions**: each approved change of L0 or L1 is a version (commit sha plus approval record).
  The page diffs any two versions and offers a rollback, which is again a PR plan, never a direct
  write.

## Data sources and invariants

- **Read**: ROUND.json, BOARD.md (not needed by the page, which builds the same data), DECISIONS
  sheets, `<lane>/cp-*/`, COLLECT.json, queue files (`pai queue`), tmux and /proc (status), the
  repository (wtpr, agentsmd).
- **Write**: only through the same functions the terminal commands use (`decide.answer`,
  `wtpr.publish`, handoff approve). Each write names the sha of what the person saw and is
  refused if that changed (`MISMATCH`).
- **Never**: answer a folder-trust prompt, type into a lane, run git write commands outside
  wtpr's reviewed publish, push without the explicit publish click, or call a model except JEV
  as a labelled second opinion.

## Security

The workspace keeps the model of `pai edit`, `pai web` and `pai worktree web`
(`swarm/mdserver/server.py`):
- it listens on 127.0.0.1 only (remote access through an SSH tunnel or a tailnet);
- sign-in uses a one-time link, and the session cookie is HttpOnly and SameSite=Strict;
- every write needs a CSRF header, plus Host and Origin checks;
- the Content-Security-Policy is strict, and pages contain no inline script;
- values are rendered as text, never as HTML.

The R6 prototype adds a stricter wrapper (`swarm/round_web.py`):
- it serves only its own pages and its two GET routes;
- it answers every other path with 404 (including the editor's `/api/file` and `/api/save`);
- it refuses every POST except `/login` and `/logout`, even with a valid session and CSRF
  header, and every PUT, PATCH and DELETE, with 405.

`swarm/tests/test_round_web.py` checks each of these.

## Merging with what exists

| today | becomes |
|---|---|
| `pai web` (swarm/console.py): lane screens, typing, paste | stays the **console**. The workspace links each lane to it ("open in console") and never types into lanes itself |
| `pai worktree web` (swarm/wtweb.py): worktrees, AGENTS.md editor, refresh, split plan, PR preview and publish | its PR preview and publish become the **PR plans** page, using the same functions and digests. Its instruction-file editor becomes the **Rules** page, with L2 moving out of tracked files |
| `pai edit FILE.md`: one file in the browser | stays for single files. The workspace's "open in editor" for a handoff or decision sheet launches it |
| `pai round web` (R6 prototype): read-only board | becomes the **Board** and **Decisions** pages, the workspace's start page |

The plan is one server process (`pai workspace`), with each page registered as `install()`
routes on the same mdserver `App`, the way wtweb does it today. Each page keeps its own
permissions: read-only pages get the `round_web` wrapper, and write routes are added one at a
time, each with its sha check and its own test.

## Using the prototype

```
pai round web [ROUND.json] [--port N] [--no-browser]
```

It prints a one-time link. The page shows the round header, a lane table (its **刷新状态**
button reads tmux again), the open decisions with their sha, and the last collect.
`pai decide answer` and `pai worktree publish` stay in the terminal.
