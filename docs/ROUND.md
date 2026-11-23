# pai round: one round of parallel lanes

`pai round` is the lubrication layer around a round of parallel Claude Code workers. It prepares
the round (main clone, base, worktrees, per-lane prompts), shows its state, hands lanes their next
message, collects their handoffs and closes the round. The decisions remain with the owner.
`pai round` does not commit, push, merge, delete a branch, answer a folder-trust prompt or talk
to a model (the only exception is `collect --jev`, an optional second opinion).

Everything a round knows is in files, so clearing a conversation loses nothing:

```
<vault>/Agent Inbox/<ROUND>/
  ROUND.json            the round (this page, format pai-round/1)
  BOARD.md              the board: one row per lane, open decisions (generated block + your notes)
  DECISIONS.md          the mastermind's decision sheet (pai decide)
  COORDINATOR-LOG.md    one line per mastermind decision (pai round log)
  COLLECT.json          the last pai round collect
  <lane>/cp-<X>/        each lane's checkpoints: HANDOFF.md, NEXT-PR.md, RESTART.md, SHA256SUMS
<trusted root>/<lane>   the lane's worktree, branch worker/<ROUND>/<lane>
<work root>/<lane>/     the lane's logs, queue (pai queue), jev-usage.jsonl, STATUS.md
<work root>/prompts/    <lane>.txt (paste-ready prompt, mode 0600), run.env
```

## ROUND.json (format `pai-round`, version 1)

```json
{
 "format": "pai-round", "version": 1,
 "round": "R6-20261122",
 "project": "pai",
 "github": "worldvanquisher/personal-ai-workbench",
 "repo": "<home>/workspace/personal-ai-workbench",
 "repo_found": "found under ~, /srv",
 "base_branch": "main",
 "base_sha": "c8bc51e4674f33e3afb9f8dc02d390085c840d64",
 "base_source": "git ls-remote origin refs/heads/main",
 "vault": "<vault>",
 "vault_source": "config",
 "trusted_root": "<home>/workspace/pai-workers/R6-20261122",
 "work_root": "<home>/.cache/pai-R6-20261122",
 "state_dir": "<vault>/Agent Inbox/R6-20261122",
 "tmux_session": "r6",
 "prompt": {"file": ".../99 Templates/PAI Worker Prompt R6b 润滑层.md", "sha256": "…"},
 "decision_sheets": ["30 Projects/PAI 定位 v2：多 agent 润滑层.md"],
 "created": "2026-11-22T13:30:00Z",
 "status": "open",
 "lanes": [
  {"name": "round",
   "worktree": "<home>/workspace/pai-workers/R6-20261122/round",
   "branch": "worker/R6-20261122/round",
   "session_name": "PAI R6-20261122 round",
   "tmux_target": "r6:pai-round",
   "prompt_file": "<home>/.cache/pai-R6-20261122/prompts/round.txt",
   "prompt_sha256": "…",
   "work_dir": "<home>/.cache/pai-R6-20261122/round",
   "handoff_dir": "<vault>/Agent Inbox/R6-20261122/round"}
 ]
}
```

| field | meaning |
|---|---|
| `round` | the round id: letters, digits, `.`, `_` and `-` (`R6-20261122`) |
| `project` | short project key (`pai`, `flh`); upper-cased in session names |
| `github`, `repo`, `repo_found` | the GitHub repository, its main clone and how that clone was chosen |
| `base_sha`, `base_source` | the base every lane starts from, and where it came from (`--base`, or `git ls-remote`) |
| `vault`, `vault_source` | the vault folder (it holds an `Agent Inbox` folder) and which rule found it |
| `trusted_root` | the folder holding every lane worktree; the owner trusts this folder once in Claude Code |
| `work_root` | logs, queues and prompts; never inside the repository |
| `state_dir` | `<vault>/Agent Inbox/<round>` |
| `tmux_session` | the tmux session of the round's windows |
| `prompt` | the round prompt note and the sha256 of its text before rendering |
| `decision_sheets` | optional: further Markdown decision sheets (absolute, or relative to the vault) |
| `status` | `open`, or `closed` once `pai round close --write` archived it |
| `lanes[].name` | lane name: letters, digits, `_`, `-` |
| `lanes[].worktree`, `branch` | `<trusted_root>/<lane>` on `worker/<round>/<lane>` |
| `lanes[].session_name` | the `--name` of the lane's Claude Code session: `<PROJECT> <round> <lane>` |
| `lanes[].tmux_target` | `session:window` (exact names) |
| `lanes[].prompt_file`, `prompt_sha256` | the rendered, paste-ready prompt and its sha256 |
| `lanes[].work_dir`, `handoff_dir` | per-lane work folder and handoff folder |

Unknown fields are kept and ignored, so a later version can add fields without breaking older
readers. ROUND.json is written atomically. `new` merges lanes into an existing file but refuses
a different base or repository (`MISMATCH`): a round keeps its base.

## Configuration: `round.toml` (optional)

`$PAI_ROUND_CONFIG`, else `~/.config/pai/round.toml`:

```toml
vault = "<vault>"   # host path; not a Syncthing container path
search = ["~", "/srv"]                    # where to look for main clones (depth 5)

[project.pai]
github = "WorldVanquisher/personal-ai-workbench"
prompt = "99 Templates/PAI Worker Prompt R6b 润滑层.md"   # relative to the vault, or absolute
trusted_root = "~/workspace/pai-workers/{round}"
work_root = "~/.cache/pai-{round}"
tmux_session = "r6"
window_prefix = "pai-"
session_name = "{PROJECT} {round} {lane}"

[routing]   # see "pai round route" below
```

## pai round new

```
pai round new PROJECT ROUND [--lane L ...] [--prompt FILE] [--repo PATH] [--base SHA] [--vault DIR] [--write]
```

Without `--write` it only previews, and the exit code is 1 when something is in the way.

1. **Main clone**: every directory under `--search` (default `~` and `/srv`, five levels down)
   with a `.git` *directory* whose `origin` is `github.com/OWNER/NAME`. A linked worktree is not
   a main clone. If it finds several, it refuses and lists them; choose one with `--repo`.
2. **Base**: `--base SHA` (the owner-confirmed full SHA), else `git ls-remote origin
   refs/heads/main`. If the base is not in the clone yet, the write is refused and the message
   shows the fetch command. `new` itself never fetches.
3. **Vault**, in this order: `--vault`, `vault` in round.toml, the prompt file's location (the
   nearest parent folder that has an `Agent Inbox` folder), then `$VAULT`. A configured
   folder without an `Agent Inbox` is refused, not skipped.
4. **Prompts** (r6-prepare.sh's rules):
   - fill `{{RUN_ID}} {{BASE_SHA}} {{VAULT}} {{WORKTREE_ROOT}} {{WORK}} {{HANDOFF_ROOT}}
     {{PROJECT}}`, plus any `--set NAME=VALUE`;
   - refuse when any `{{UPPER_CASE}}` is left; `{{` alone or `{{lower}}` is not a placeholder;
   - split the note's ```` ```text ```` blocks: block 1 is Part 0, and each further block must
     start with `线名：<lane>。`. Each lane file is Part 0 plus that lane's block. A note with a
     single block is one lane named after the project.

   When the prompts lane provides `pai/promptlib.py` with `render(text, values) -> (text,
   missing)`, that renderer is used instead.
5. **Worktrees**: `<trusted_root>/<lane>` on `worker/<round>/<lane>` from the base. Re-running
   is safe:
   - `exists`: the worktree is already there on its branch;
   - `add-existing-branch`: the branch exists, its commits are kept;
   - `create-branch`;
   - `CONFLICT`: a folder in the way, the branch checked out elsewhere, or a path outside the
     trusted root. Nothing is written while any lane is in conflict.
6. **Files**: per-lane work and handoff folders (an existing handoff folder is never touched),
   ROUND.json, and the board.

## pai round status

```
pai round status [ROUND.json | STATE_DIR] [--json] [--board]
```

One line per lane. It reads tmux, /proc and files only, and never asks a model:

| column | from |
|---|---|
| session name | the `--name` in the Claude process's own command line, checked against ROUND.json |
| window | `tmux list-panes`, matching the exact `session:window` names |
| state | `running` (footer says *esc to interrupt*), `idle`, `blocked-on-dialog`, `owner-draft`, `queued`, `exited` (pane dead, or no Claude process under the pane), `no-window`; see *States* below |
| worktree | `clean`, `N changed` (untracked files count) or `missing` |
| checkpoint | the newest `cp-*` in the lane's handoff folder; `+R` when it has RESTART.md |
| queue | Totals and the next item of `<work_dir>/queue` |

The round is found from, in order: the argument, `$PAI_ROUND`, or the newest open ROUND.json
under `$VAULT/Agent Inbox`. `$PAI_ROUND_TMUX_SOCKET` selects a tmux server other than the
default one. `--board` also refreshes BOARD.md. `--json` prints `pai-round-status/1`: the round
header plus one object per lane with all of the columns above, plus `agent_pid`, `agent_name`,
`name_ok` and `why`.

The Claude process is looked up in /proc among the pane's process and its descendants. This is
needed because `pane_current_command` can read `bash` while Claude runs: Claude starts its own
bash for tools.

### Flags under a lane

These come from the same sources: the screen, Claude Code's own session record
(`$CLAUDE_CONFIG_DIR/sessions/<pid>.json`, from 2.1.32x on, matched to the exact pid), tmux, and
the queue log.

| flag | meaning |
|---|---|
| `fresh: no prompt yet` | idle on the start screen (banner and input placeholder): the session was opened but nobody pasted a prompt |
| `RC on` | the session record has a Remote Control bridge (ids and links are not shown; see below) |
| `update waiting` | Claude says an update is installed and waits for a restart |
| `Claude's record: X` | the record's status disagrees with what the screen shows |
| `no screen change for N min` | tmux `window_activity` is 30 minutes or more old |
| `checkpoint overdue` | 10 or more items finished since the queue's last checkpoint event (or since the start) |

**Why the board never carries Remote Control links** (owner decision D-RND-2 = A):
- the board, the lane notes and Home.md sit in the vault, and the vault syncs to every device;
  a link there gets copied wherever the vault goes;
- on 2026-11-22 several sessions of this round shared the same bridge id prefix, so which link
  belongs to which lane is not verified;
- PAI's publication check refuses Claude session links in anything that becomes public, and a
  vault page is one copy away from a PR body.

Status therefore shows only `RC on`. Open the session from Claude Code itself (`/remote-control`
in the lane) or from claude.ai.

`--json` carries the same facts: `fresh`, `update`, `quiet_seconds`, `record` (status,
waiting_for, version, name, rc) and `queue.since_checkpoint`.

### States

The checks run in this order; the first one that matches wins.

| state | when | `needs_owner` |
|---|---|---|
| `exited` | the pane is dead, or no Claude process runs under it | yes, when items are left |
| `blocked-on-dialog` | a Claude Code dialog replaced the input box. `dialog` names it: `trust` (folder trust, see below), `teach-auto-mode` (*Teach auto mode about your environment?*), `permission` (*Do you want to proceed / make this edit / create …*), or `other` (*Enter to confirm · Esc to cancel* with no normal footer) | yes |
| `owner-draft` | the input box holds typed, unsent text. `draft_chars` gives its length; the text itself is never stored | yes (a reminder) |
| `queued` | the screen shows *Press up to edit queued messages* | no |
| `running` | the footer says *esc to interrupt* | no |
| `idle` | Claude runs, its input box is there, and it is not working | no |

**Draft or placeholder?** Status reads the screen with its colours (`capture-pane -e`). Text in
the input box that Claude draws **dim** (SGR 2) is never a draft. This covers its `Try "…"`
placeholder, and also its prompt suggestions: on 2026-11-22 a real lane showed a whole dim
sentence that nobody had typed. Only normal-weight text counts as an owner draft.

A dialog is recognised only when **no input box** is at the bottom of the screen. A transcript
that quotes a dialog's wording above an input box is therefore not a dialog. The `--json` rows
carry `dialog`, `draft_chars`, `busy` and `input` (whether an input box is on screen).

### Recognising the folder-trust prompt

The `trust` dialog requires all three of these:
- the dialog's own wording within the last 30 lines of the screen: *Quick safety check*, *Is
  this a project you created or one you trust*, *Yes, I trust this folder*, *Do you trust the
  files in this folder*, or *Yes, proceed*;
- its *No, exit* option;
- none of Claude's normal footer within the last 4 lines (*esc to interrupt*, *? for
  shortcuts*, *shift+tab to cycle*, *auto mode on*, …).

The third condition matters: a worker whose own prompt quotes "Yes, I trust this folder" is not
waiting at a trust dialog. The test screens are in `swarm/tests/fixtures/round_screens/`; their
README says which screens are real captures and which are synthetic. pai never answers a trust
prompt.

## pai round launch

```
pai round launch LANE [--paste-prompt] [--wait SECONDS] [--allow-unbracketed] [--write]
```

- Opens the lane's tmux window in its worktree. If the round's tmux session does not exist,
  it is created.
- The window runs a login shell, `$SHELL -lc 'claude --remote-control --name "<PROJECT>
  <ROUND> <LANE>"; exec $SHELL -l'`. The shell stays after Claude exits, so `status` shows
  `exited` rather than a vanished window.
- Without `--write` it only shows the command and the tmux action.
- A window whose Claude is still running is never touched. A window left with only its shell is
  restarted in place (`respawn-pane`).
- **Folder trust**: if the dialog appears, launch reports `TRUST` with the window to attach to,
  and exits 1. It never answers the dialog.

`--paste-prompt` is off by default. When given, launch pastes the lane's rendered prompt file:
- only if the file's sha256 still equals `prompt_sha256` in ROUND.json;
- only once Claude is `idle`;
- as one bracketed paste (`swarm/panes.py`), followed by one Enter after a second check.

Each step is recorded in `<work_dir>/round-dispatch.json`, using dispatch.py's words:
`sending` → `typed` → `sent`, or `unknown`.

- **Any** record for the same text is final. The prompt is never pasted twice, and an `unknown`
  record needs a person to look at the lane.
- Nothing is pasted while the trust dialog is on screen: the keys would answer it.
- If the trust dialog appears, or the Claude process changes, between the paste and Enter, Enter
  is not pressed and the record becomes `unknown`.

### tmux version and bracketed paste

A multi-line prompt must arrive as one bracketed paste; otherwise each line could be entered on
its own. Whether the program in the pane asked for bracketed paste is something only **tmux 3.8
and later** can report (`#{bracket_paste_flag}`). tmux 3.6, the version on Ubuntu and WSL, and
3.5a cannot.

| tmux | What launch, inject and `next --send` do |
|---|---|
| 3.8 or later | Use tmux's answer. If tmux says bracketed paste is off, they refuse (`--allow-unbracketed` overrides). |
| below 3.8 | Infer it from the program. If `#{pane_current_command}` is `claude`, `node` or `codex`, or the round status found the lane's Claude process in the pane's process tree, they send. A lane started by `pai round launch` runs claude under a login shell, so the pane command reads `bash` and the process tree decides. Any other program is refused unless `--allow-unbracketed`. |

How it was decided is recorded with each send, as `bracketed_by` in the ROUND.json event and in
`round-dispatch.json`. Values are `reported by tmux`, `inferred from the command name (…)` or
`--allow-unbracketed (…)`. `--dry-run` prints it too. tmux still sends the bracketed-paste
markers whenever the program asked for them, so with Claude Code the inference changes nothing
on the wire. It only lets pai stop refusing.

The tests cover both paths. `test_round_launch` and `test_round_inject` run once against the real
tmux, and once more (`LaunchTmux36`, `InjectTmux36`) through a wrapper whose `tmux -V` says 3.6
and which blanks `#{bracket_paste_flag}`, as 3.6 does.

## pai round next

```
pai round next LANE [--send [--queue-ok] [--no-enter]]
```

Builds a short resume message from the lane's newest `cp-*/RESTART.md`, falling back to
`<work_dir>/RESTART.md`. The message names the file and its sha256, the next ID (taken from
"下一个是 R6-XXX-NNN"), and the lane's queue. It is printed. `--send` sends it through
`pai round inject` (same checks, records and Enter; `--no-enter` pastes only).

## pai round brief

```
pai round brief [ROUND.json] [--out FILE] [--max-tokens 2000]
```

The handover package for a mastermind whose context was just cleared. It contains:
- the status table;
- unanswered decisions, with their sha (from `DECISIONS.md` and ROUND.json's
  `decision_sheets`);
- the last 10 lines of COORDINATOR-LOG.md;
- the next steps derived from the status (answer a trust dialog, `launch` an exited lane,
  `next` an idle lane that has a RESTART.md, `collect`).

Its size is estimated (about 4 ASCII characters per token, one token per CJK character) and
printed on stderr. When it is over `--max-tokens`, it shrinks: shorter decisions, then fewer
log lines.

## pai round log

```
pai round log "DECISION" --why "REASON" [--round-file ROUND.json]
```

Owner decision PRM-D11 = C sends the mastermind's log to the vault, one page per day:
- `PAI/log/<local date>.md` is a managed note (`pai_managed: log/1`). Its front matter
  `entries` (time, round, decision, reason) is the state; the table under it repeats the
  entries for reading without plugins.
- If the page was edited by hand, PAI stops writing to it, and the entry goes only to the index.
  The owner's own daily notes are never touched.
- `COORDINATOR-LOG.md` in the round's state folder stays as the **index**: one line per entry,
  `- <UTC time> | 决定：… | 理由：… | 日志：[[PAI/log/<date>]]`. It is append-only.
- Newlines are folded and `|` is replaced, so every entry stays one line.

`pai round brief` shows the last 10 entries of the **last two daily pages**. When there are no
daily pages, it falls back to the index.

## pai round close

```
pai round close [ROUND.json] [--only-re RE] [--all-worktrees] [--write]
```

1. It first prints the acceptance checklist for each lane: state, a clean worktree, commits on
   the branch, the newest checkpoint with HANDOFF.md, NEXT-PR.md and SHA256SUMS, and an empty
   queue. The checklist only informs; it decides nothing.
2. Then it reclaims worktrees using r6-worktrees.sh's rules. The candidates are this round's
   lane worktrees; with `--all-worktrees`, every linked worktree of the round's repository.
   - Never the main checkout, and never a worktree of another clone.
   - `--only-re`: paths that do not match are kept.
   - A worktree of another **open** round in the same Agent Inbox is protected.
   - A missing folder: its record is pruned.
   - Uncommitted or untracked changes: kept and listed (never `--force`).
   - A process whose working folder is inside the worktree (/proc): kept and named.
   - Otherwise `git worktree remove`. **Branches are never deleted.**
3. With `--write`, once no lane worktree is left, ROUND.json gets `status: closed`, and a copy
   goes to `archive/ROUND-<time>.json`.

Exit code: 0 when nothing would be kept, 1 otherwise.

## pai round budget

```
pai round budget [--remaining USD] [--per-item USD] [--add LANE USD --note TEXT] [--json]
```

Per lane, it adds up:
- `claude -p` costs: `cost_usd` or `total_cost_usd` lines in `<work_dir>/usage.jsonl` (append
  the JSON result of `claude -p --output-format json`);
- Remote Control usage recorded by hand with `--add` (source `rc-manual`);
- JEV requests and tokens from `<work_dir>/jev-usage.jsonl`. JEV is priced at `[budget]
  jev_usd_per_mtok`, default 0.042;
- items done or prototyped, from the queue.

With `--remaining`, it suggests the next batch size: remaining ÷ cost per item, at most the
items left. The cost per item is measured; otherwise `--per-item`, otherwise 0.5 USD. The
source of the number is printed.

## pai round route: `[routing]` in round.toml

```
pai round route KIND [--text "item" --jev]
```

| kind | default |
|---|---|
| deterministic | script or pai command |
| judgement | JEV |
| research | subagent, haiku, low |
| feature | worker, sonnet, medium |
| design | mastermind or worker, opus, high |
| standalone | its own session, sonnet, medium |

```toml
[routing.kinds]
feature = { model = "sonnet", effort = "medium" }
docs    = { who = "worker", model = "haiku", effort = "low" }
```

`--jev` asks JEV for its tier for `--text`. The answer is printed as a suggestion only; the
table stays the rule, matching how JEV is used elsewhere in PAI (a second opinion, never a gate).

## pai decide

```
pai decide list FILE.md [--open] [--json]
pai decide answer FILE.md ID --sha SHA12 --choice TEXT [--note TEXT] [--write]
```

A decision is a checkbox item with a bold ID, `- [ ] **D4 标题**：问题`. Its indented lines can
carry these fields: `选项`, `取舍`, `推荐`, `默认`, `阻塞`.

`list` prints each decision with the sha256 of its block, meaning the checkbox line and its
indented lines.

`answer`:
- needs that sha;
- if the block changed since it was listed, refuses with `MISMATCH` (exit 1) and writes
  nothing, so an approval always refers to the text the person read;
- an already answered decision is `REFUSED`;
- with `--write`, ticks the box and adds `- 回答：CHOICE（by pai decide, <UTC>）`; nothing
  else in the file changes;
- the write is atomic and is refused if the file changed while answering.

## pai round collect

```
pai round collect [ROUND.json] [--jev] [--json] [--write]
```

For each lane's newest `cp-*`:
- **SHA256SUMS** is checked line by line: `ok`, `MISMATCH` (names the file), or `missing`.
- **Items** are HANDOFF.md's `| ID | state |` rows and Totals line, compared with the lane's
  queue as it was when that handoff was checkpointed (its sha256 in the queue log). This uses
  the `pai queue check` rules.
- **Evidence**: the number of sha256 digests in the handoff, and whether it has a Totals line.
- `--jev` asks JEV two questions: does the handoff **claim** done and checked, and does it
  **show** evidence (commands with results, paths with sha256)? The result is **DISAGREES**
  when claims ≥ 0.7 and evidence < 0.5, or when the claims are high but the sums do not
  match. The deterministic checks stay the ground truth.
  - Each request's usage is appended to `$PAI_ROUND_JEV_USAGE`, default
    `<work_root>/jev-usage.jsonl`.
  - The key comes only from `TYPESAFE_API_KEY` or the credential store. It is never printed or
    written.

`--write` saves COLLECT.json and refreshes the board's collect table. The exit code is 1 when
any lane has a MISMATCH or a DISAGREES.

## pai round prplan

```
pai round prplan LANE [--commit N] [--next-pr FILE] [--offline] [--out PLAN.json]
```

Turns the lane's NEXT-PR.md (branch, numbered commits `` 1. `message` `` with a `git add …`
line, and a PR body draft) into a `pai worktree preview` plan for commit N:
- the paths are exactly that commit's; a folder stands for the files under it;
- the commit message, the first commit's subject as the PR title, and the body draft come from
  NEXT-PR.md.

The plan carries the round's fields (fusion design §5.4): `task` (`ROUND/LANE#N`), `round`,
`lane`, `commit`, `commits`, `next_pr` (path and sha256), `handoff_sha256`, and `queue_sha256`
(of the lane's queue log).

It reads only. `--out` writes a new file with mode 0600 and never overwrites. Publishing stays
the owner's job: `pai worktree publish PLAN.json --expect-plan SHA --yes --draft|--ready`.

## pai round web

The read-only board in the browser; see `docs/WORKSPACE.md`.

## Round state as vault notes (`--write-vault`)

Owner decision D9 (a): Obsidian is the console, and the data stays plain Markdown. Agents and
scripts read and write the notes' front matter. Dataview or Bases queries only display that
state; they are never the source of it. Every view also exists as a plain table, so the notes
read the same without plugins.

```
pai round status  [ROUND.json] --write-vault
pai round collect [ROUND.json] [--jev] [--write] --write-vault
pai decide answer --from-note "PAI/decisions/<ROUND>-<ID>.md" [--write]
```

| note | front matter | body |
|---|---|---|
| `PAI/Home.md` | rounds_open, lanes_need_owner, decisions_open, handoffs_unapproved | four blocks: active rounds, lanes that need the owner, open decisions, unapproved handoffs (`approved: false` under the rounds' handoff folders). Each block is a ```` ```dataview ```` query plus a plain table snapshot |
| `PAI/rounds/<ROUND>/ROUND.md` | round, project, status, base, lanes, needs_owner, round_json | one row per lane, linking its note |
| `PAI/rounds/<ROUND>/<lane>.md` | lane, status, current_id, last_cp, totals, blockers, needs_owner, needs_owner_why, round, project, branch, worktree_state | a facts table; blockers |
| `PAI/decisions/<ROUND>-<ID>.md` | round, id, status (open/answered), question, options, **choice**, **choice_note**, recommendation, default, deadline, blocks, answer, source, source_sha | the decision as a table, and how to answer it |

A lane called `round` (in any case) gets `round-lane.md`. `round.md` and `ROUND.md` would be one
file on Windows and macOS, and the vault syncs between them.

### `needs_owner`

`needs_owner: true` when any of these holds, with each reason listed in `needs_owner_why`:
- the folder-trust dialog is waiting;
- `collect` found **DISAGREES** (JEV: the claims and the evidence do not match);
- SHA256SUMS mismatches;
- queue items are blocked or owner-only;
- the session exited or vanished while items are left.

### Managed notes

- Every note PAI writes carries `pai_managed: <kind>/1` and `pai_sha256`, and its body sits
  between `<!-- pai:managed:begin pai-vault/1 -->` and `<!-- pai:managed:end -->`.
- `pai_sha256` covers everything PAI wrote, except `updated` and the owner's fields (a
  decision's `choice` and `choice_note`).
- A note is written only when all of these hold:
  - it is absent, or it carries the same `pai_managed` kind;
  - its markers are intact;
  - its content still matches `pai_sha256`;
  - no sync conflict copy sits next to it: Syncthing's `NAME.sync-conflict-<date>-<time>-<device>.md`,
    or `NAME (conflicted copy …).md`.
- Otherwise the note is `SKIPPED` and left exactly as it is. This covers the owner's own notes,
  hand-edited notes, broken markers and conflicts. Delete a note to let PAI write it again.
- Conflict copies are never read as state.
- Writing the same state twice changes nothing. This was checked on the real vault: a second
  run reported 0 changes.

### Answering a decision from Obsidian

1. The owner fills `choice:` (and optionally `choice_note:`) in the decision note's front matter.
2. `pai decide answer --from-note NOTE` checks the following, and refuses with nothing written
   if any fails:
   - the note's PAI part is unedited (`pai_sha256`);
   - the choice is one of `options`: the full text, or its first word such as `A`;
   - the source sheet's decision block still has `source_sha`, so the owner approved the text
     the note showed.
3. With `--write` it ticks the source sheet (as `pai decide answer FILE ID --sha`) and marks the
   note `status: answered`.

### Bases (`.base`)

Owner decision D-RND-3 = C: **Dataview only.** No `.base` file is generated. The unverified
draft is kept in the appendix *Unverified design: a `.base` view* at the end of this page.

### Owner check of the vault pages (companion to R6-RND-046)

Open each page in Obsidian with Dataview on, then once more with Dataview off (or in any
Markdown viewer), and check:

1. **`PAI/Home.md`**
   - four sections: 进行中的轮次, 需要 owner 处理的 lane, 还没回答的决策, 还没批准的交接;
   - each section shows a Dataview table (with Dataview on) **and** a plain table under it;
   - every plain table has exactly 4 columns on every row. Links show as `R6-20261122`, `D7`,
     `HANDOFF`, never with a stray `\` or with the path split into an extra column.
2. **`PAI/rounds/<ROUND>/ROUND.md`**: 6 columns (lane, 状态, 当前条目, checkpoint, done / todo, 需要 owner);
   the lane link opens its note; the `round` lane's note is `round-lane.md`.
3. **One lane note**, e.g. `PAI/rounds/<ROUND>/core.md`:
   - front matter shows lane, status, dialog, current_id, last_cp, totals, blockers and
     needs_owner (Obsidian's Properties panel);
   - the facts table has 2 columns; its `交接目录` link opens the lane's handoff folder.
4. **One decision note**, `PAI/decisions/<ROUND>-D7.md`: `options` is a list in Properties;
   `choice` is empty. Fill it in, then preview the answer in the terminal with
   `pai decide answer --from-note …` (without `--write`).
5. **`PAI/log/<date>.md`**: 4 columns (时间, 轮次, 决定, 理由).
6. **Sync**: no `*.sync-conflict-*` copies under `PAI/`. If one appears, PAI stops writing that
   note until you remove the copy.
7. **Links inside tables** (`[[path\|label]]`). PAI escapes the pipe in a table link so that
   Markdown does not read it as a column break. In Obsidian's reading view **and** live preview,
   check one such link in `PAI/Home.md` and one in `ROUND.md`:
   - only the label shows (`R6-20261122`, `core`, `D7`), with no `\`, no `|` and no path;
   - clicking it opens the right note;
   - the row keeps its column count, and the cell to its right holds what its header says.
   If Obsidian shows `\` or splits the cell, write down the Obsidian version in BOARD.md. A
   plain `[[path]]` link without a label is the fallback.

Anything wrong: note it in the round's BOARD.md, below the generated block. That part is yours
and is never overwritten.

## pai round inject

```
pai round inject LANE --file MESSAGE.md [--dry-run] [--queue-ok] [--again] [--no-enter] [--wait S] [--allow-unbracketed]
```

This puts one message into a lane's Claude Code session, the way the mastermind did it by hand
in R6, but only when that is safe. In order, it checks:

1. the lane's tmux window exists and Claude runs in it;
2. no folder-trust dialog is up;
3. no other Claude Code dialog is up (*Teach auto mode about your environment?*, permission
   prompts, …). The keys would answer it;
4. the input box is there and empty. **Typed text (an owner draft) refuses the injection**:
   pai never overwrites a draft and never appends to one. Dim placeholder or suggestion text
   does not count;
5. Claude is idle. With `--queue-ok`, a working Claude is accepted as well (Claude queues the
   message), but a draft still refuses.

Then it sends one bracketed paste. On tmux below 3.8, see *tmux version and bracketed paste*
under launch. Before pressing Enter it looks again: if a dialog appeared or
the Claude process changed, Enter is not pressed and the send is recorded as `unknown`.

Every send is recorded twice:
- in ROUND.json under the lane's `events`: kind, sha256, characters, state, pid, queued, time;
- in `<work_dir>/round-dispatch.json`.

The same text is not sent twice without `--again`, and an `unknown` send is never repeated on
its own. `--dry-run` shows what would be sent, to which window and Claude pid, and why it is
allowed, and sends nothing. `pai round next LANE --send` sends its resume message through
inject; `--no-enter` and `--queue-ok` work there as well.

## Appendix: unverified design, a `.base` view

**Unverified**: this has not been checked against Obsidian's documentation; the round lane
has no web access. Owner decision D-RND-3 = C: this file is **not** generated. The structure
follows a working `.base` in the owner's vault (filters / views / type: table / order / sort).
The property filters (`pai_managed == "lane/1"`, `needs_owner == true`) are the unverified part.

```yaml
filters:
  and:
    - file.inFolder("PAI")
    - file.ext == "md"
views:
  - type: table
    name: 需要 owner 的 lane
    filters:
      and:
        - pai_managed == "lane/1"
        - needs_owner == true
    order:
      - file.name
      - round
      - status
      - current_id
      - needs_owner_why
  - type: table
    name: 还没回答的决策
    filters:
      and:
        - pai_managed == "decision/1"
        - status == "open"
    order:
      - file.name
      - question
      - default
      - deadline
    sort:
      - property: file.name
        direction: ASC
```

## pai round integrate

```
pai round integrate [ROUND.json] --plan [--order L1,L2,...] [--proposals all|none] [--resolutions FILE] [--test] [--write]
```

This is a rehearsal of the merge, on a scratch copy and never on a branch. It reads only from the
repository and the vault: nothing is committed, pushed or merged.

**What goes in, and in which order** (`--order`; default ROUND.json's lane order. The R6 merge
rule PRM-D12 = A gives `core,docs,round,prompts`):
1. each lane's patch:
   - the `cumulative.patch` of its newest checkpoint (`cp-*` or `final`);
   - for a lane that seals without one, its worktree's `git diff BASE` plus untracked files.
     This is read-only, and the report labels it `worktree (… has no cumulative.patch)`;
2. then every lane's `proposals/*.patch`, lane by lane (skip them with `--proposals none`).

**How it is applied**:
- `git archive BASE` goes into `<work_root>/integrate/<time>-<random>/tree`, plus a pristine
  `base/` beside it.
- Each patch goes in with `git apply`. If it does not apply cleanly, it is applied with
  `--reject`, so the rest still goes in. Every rejected file is a **CONFLICT**, and its hunks are
  kept under `rejects/`.
- Two cases are not conflicts:
  - `ok (line union)`: `release/allowlist.txt` is a set of lines (build_bundle refuses
    duplicates and missing files). A rejected change to it adds each missing line whose file
    exists in the copy. Lines for absent files are listed as skipped; a change that *removes*
    lines stays a conflict;
  - `ok (already in the merged copy)`: applying the patch to the **base** version of each
    rejected file gives exactly the merged file. This happens with a proposal written as
    "base → both lanes' changes".

**What it reports**:
- the sha256 of every patch, and the order;
- conflicts;
- hot files: paths touched by two or more patches, with `pai/launch.py`, `pai/cli.py`,
  `swarm/workbench.py`, `release/allowlist.txt` and `pyproject.toml` always listed first;
- open dependencies: each lane's Blockers section, and the lines of its NEXT-PR.md that state a
  precondition;
- a **merge plan draft**: the lanes in order, each with its branch, NEXT-PR sha, commits and
  pre-merge checks.

`--test` runs `tools/verify.py` on the merged copy. **Read the result together with the
conflicts.** A conflicting patch is applied partially, so the copy can be broken in ways the real
merge would not be. This happened in the R6 rehearsal: prompts' `pai/cli.py` hunk adding
`from pai import promptlib` was rejected while its `promptlib.add_parser(sub)` went in, and every
CLI test failed with a NameError. INTEGRATION.md then carries a warning. Test a batch without the
conflicting patch (`--order core,docs,round`), or resolve the conflict first. It runs `tests/` and `swarm/tests` strictly,
plus whatever else this machine can run, with a test git identity (`example.invalid`). The report
keeps the exit code, each step's status and the tail of the output.

`--write` puts INTEGRATION.md (a managed note, kind `integration/1`) and INTEGRATION.json in the
round's state folder. The owner approves the merge plan there; each lane is then previewed with
`pai round prplan` and published by the owner with `pai worktree publish`.

### Stale data is refused

R6 showed the problem (review finding F6). The round lane ran integrate at 20:25Z. docs and core
sealed their finals at 20:34–20:36Z, and prompts was not in the plan. INTEGRATION.md looked
complete, but it was out of date. integrate now marks a run **STALE** when:

- a lane's queue (`<work_dir>/queue.log`) has a checkpoint event newer than the checkpoint folder
  the plan used. The event's `handoff` path names another folder, or, without a path, the event is
  more than 10 minutes newer than that folder;
- a lane of ROUND.json is not in `--order`.

A stale run still writes its report, but INTEGRATION.md starts with a `STALE` warning listing
each reason, front matter `stale: true`, and the exit code is **3**. `pai round brief` also marks
the last integration STALE when any lane checkpointed after it. Rerun after every lane's final,
covering all lanes.

### Approved resolutions (`--resolutions FILE`, off by default)

Some conflicts have a known answer that the owner or the mastermind already approved. For
example, in the R6 merge, `pai/cli.py` kept both core's registry and prompts' promptlib. Write it
down once, and integrate applies it instead of reporting the same conflict again:

```json
{"format": "pai-resolutions", "version": 1, "approved_by": "mastermind",
 "resolutions": [
   {"file": "pai/cli.py", "how": "both", "patch": "prompts-lane",
    "note": "core 的注册表和 prompts 的 promptlib 两边都保留", "by": "owner: Dispatch as recommended"}
 ]}
```

| `how` | What the file becomes |
|---|---|
| `both` | `git merge-file --union` of the copy before this patch, the base, and the patch's own result on the base. Both sides' lines are kept, with no conflict markers. |
| `ours` | the copy as it was before this patch. The patch's change to this file is dropped. |
| `theirs` | the patch's own result on the base. Earlier patches' changes to this file are dropped. |
| `file` | the content at `path` (relative to the list), checked against `sha256` when given |

- `patch` (optional) limits an entry to one patch name, such as `prompts-lane` or
  `docs-doctor-merged`.
- A settled file is reported as `ok (resolved: HOW)`.
- INTEGRATION.md lists every file settled this way under *按已审定的解决方式处理*, with the
  list's path and sha256 and who approved it.
- Files the list does not name stay conflicts. `both` is a textual union: for code, check the
  result with `--test`, or use `file` with the resolved content.

Exit code: 3 when the run is STALE; otherwise 1 when any conflict remains or the tests fail.
