# Run state: names, identifiers and the evidence behind each status

`pai status` (and `pai status --json`, `pai inspect LANE`, the browser console) gives every lane
one lifecycle name, a sentence saying why, and what you can do next. This page lists the names,
the identifiers they hang on, and the **minimum evidence** pai needs before it shows each one.
Nothing here is guessed from a running process alone, and reading state never rewrites a record.

## Identifiers

| Identifier | Form | Comes from | Stable across |
| --- | --- | --- | --- |
| run | `PROJECT/RUN_ID` | the run's folder `runs/PROJECT/RUN_ID/manifest.json`; `pai run` never reuses a RUN_ID in a project | restarts of pai, tmux and the agents |
| task | `RUN_ID/LANE/NOTE` (no note: `RUN_ID/LANE/#GENERATION`) | the task record's lane entry (`note`) and generation | the same, and it is the text of the dispatch tag `[pai RUN_ID/LANE/NOTE]` the agent's transcript must contain |
| record | the task record's `task_id` | `$PAI_SWARM_HOME/tasks/<repo key>-<task_id>.json` | kept as written |

All three are computed from fields that records written by pai 0.3 and later already carry, so
older runs get them without any migration or rewrite.

## Where the evidence lives

| Evidence | File or command | Written by |
| --- | --- | --- |
| run manifest | `$PAI_SWARM_HOME/workbench/runs/PROJECT/RUN_ID/manifest.json` | `pai run` (once) |
| task record (branch, base, lane state) | `$PAI_SWARM_HOME/tasks/<repo key>-<task_id>.json` | `pai run`, `pai resume`, `pai next` |
| window ownership (tmux server, window, pane) | `$PAI_SWARM_HOME/rc/<repo key>-<run>.json` | when a lane's window starts |
| dispatch (attempts, pid, timestamps, evidence) | `$PAI_SWARM_HOME/dispatch/<repo key>-<run>.json` | dispatch, `pai resume --mark-delivered` |
| recovery decisions (and handoffs) | `$PAI_SWARM_HOME/recovery/<repo key>-<run>.jsonl` | `pai recover --apply`, `pai recover --continue --write` |
| continuation | the task note's last `## Continuation from RUN/LANE` section and its `pai-continuation v1` marker | `pai recover --continue --write` |
| process | `tmux list-windows` (pane alive or dead, exit status), `ps` | read live |
| trust | the lane's visible screen | read live, never answered |
| input readiness | Claude: its session record `$CLAUDE_CONFIG_DIR/sessions/<pid>.json`; Codex: the screen | the agent |
| delivery | Claude: `$CLAUDE_CONFIG_DIR/projects/<worktree>/<session>.jsonl`; Codex: `$CODEX_HOME/sessions/.../rollout-*.jsonl` | the agent |

`pai inspect LANE` prints each of these for one lane, with the exact path it read.

## Lifecycle names and their minimum evidence

Recorded evidence is read first (the dispatch record, then the task record); what tmux and the
agent show now refines it. "Terminal" means the name does not change until a person acts, or is
final for pai.

| Name | Minimum evidence | Terminal | What you do |
| --- | --- | --- | --- |
| `queued` | a task record assigns a note; no dispatch entry for it (or dispatch is off) | no | wait, or `pai resume` once the agent is ready |
| `waiting-for-trust` | a live agent process **and** its folder-trust prompt on the visible screen | yes | `pai trust` (answering is yours) |
| `sent` | a dispatch entry `sent` for this pid, Enter pressed less than `PAI_DISPATCH_CONFIRM_SECONDS` (20 s) ago | no | wait for the transcript |
| `running` | delivery evidence (below) **and** the agent's input is not ready (Claude's record says busy) | no | `pai logs LANE --follow` |
| `delivered` | the agent's own transcript records a user prompt containing the task's tag, at or after the attempt started, **or** your `pai resume --mark-delivered`; and its input is ready again | yes | read its handoff |
| `idle` | a task record with no note for the lane; a live agent | yes | give it text yourself (`pai paste`) |
| `not-accepting-input` | a live agent process **and** either its session record says a dialog is waiting, or no ready input box for 120 s after it started; task not delivered | yes | look at it (`pai attach`) |
| `exited` | tmux reports the pane's process dead, with its exit status (not 0: a crash) | yes | `pai logs`, then `pai recover` |
| `stopped` | no tmux window with the lane's name in the run's session (closed, or the server is gone) | yes | `pai recover` |
| `unknown` | any of: a dispatch entry `unknown`, `sending` or `typed`; `sent` older than the confirm window without transcript evidence; an old "delivered because the tag was visible" entry with no transcript evidence now; two windows with the lane's name; a window that is not the one this run started; a window without the agent | yes | the steps it prints; nothing resends on its own |
| `not-started` | the manifest lists the lane, but no task record has it | yes | `pai resume` / a new run |
| `handed-off` | the lane's task record carries `handed_off` (written by `pai recover --continue LANE --write` after its agent stopped); it wins over whatever tmux shows | yes | `pai run --continue NOTE` (a fresh session); see CONTINUATION.md |

A crash is `exited` (no process; tmux shows its exit status). An agent that is alive but will
not take input is `not-accepting-input`. Neither is ever sent a task until a person acts.

Delivery is tracked separately from the process: a lane whose agent exited after its task was
delivered shows `exited` with `delivered: true`, and the run summary counts it as delivered.

## Run summary and partial delivery

`summary.delivery` is `complete` (every lane with a task delivered, none missing), `partial`
(some delivered, some not), `none` or `no-tasks`. The text view says, for example:

```
summary   3 lane(s): 1 delivered, 1 waiting-for-trust; PARTIAL delivery: 1 of 3 task(s) delivered (a),
          not delivered: b (waiting-for-trust), c (not-started)
```

## `pai status --json` (schema `pai_status_v1`): the supported contract

`pai status --json` (and `pai inspect --json`, which adds `evidence`) is the supported way for a
program to read run state. Exit status: 0 when every lane is running and ready (Remote Control
verified where requested, task delivered where dispatch is on), 1 otherwise; 2 for usage errors.

Committed fields (present in every `pai_status_v1` document; within this schema they are only
added to, never renamed, removed or given another meaning):

| Field | Meaning |
| --- | --- |
| `schema` | `"pai_status_v1"` |
| `run`, `run_id` | `PROJECT/RUN_ID` and `RUN_ID` |
| `ok` | the exit-status condition above, as true/false |
| `summary.delivery` | `complete`, `partial`, `none` or `no-tasks` |
| `summary.lanes`, `.started`, `.with_task` | counts |
| `summary.delivered`, `.not_started`, `.attention` | lists of lane names |
| `summary.not_delivered` | lane name -> lifecycle state |
| `summary.states` | lifecycle state -> count |
| `lanes[].lane`, `.note`, `.branch`, `.worktree` | where the lane works |
| `lanes[].ids.run`, `.task`, `.record`, `.generation` | the identifiers above |
| `lanes[].lifecycle.state` | one of the names in the table above |
| `lanes[].lifecycle.terminal`, `.attention`, `.delivered` | true/false |
| `lanes[].lifecycle.next` | commands, as text |
| `lanes[].dispatch.state` | `none`, `pending`, `sent`, `delivered`, `unknown` |
| `lanes[].process.state` | `running`, `exited`, `no-window`, `no-claude`, `ambiguous`, `mismatched` |
| `lanes[].agent.name`, `.mode` | backend and launch mode |
| `lanes[].handed_off` | null, or `{at, head, branch, note, id, continued_by}` |
| `lanes[].continues` | null, or `{id, from, head, branch, agent}` for a continuation run |

Text fields (`lifecycle.why`, every `evidence`, `detail` and `cause`) are for people: their wording
may change in any release; do not parse them. Other fields present in the output (tmux ids, lock,
`head`, `ahead`, `uncommitted`, `rc`, `trust`, `input`, `phase`) are diagnostic: kept within
`pai_status_v1` but not part of the commitment. A field may be added in any release; a removal or
a change of meaning comes only with a new schema name (the proposed policy for keeping the previous
one available is in docs/1.0/COMPATIBILITY.md, an owner decision). `swarm/tests/test_continuation.py`
checks every committed field against real output.

### Reading it from a script (tolerant of new fields)

A client reads only the committed fields and ignores everything else, so a newer pai that adds
fields (at the top, in `summary`, in a lane, or in `lifecycle`) does not break it. This example is
tested as written (`swarm/tests/test_contracts.py` runs it on real output with extra fields added):

```python
import json, subprocess, sys

def lanes_needing_a_person(project_dir):
    p = subprocess.run(["pai", "status", "--json"], cwd=project_dir, capture_output=True, text=True)
    if p.returncode == 2:
        sys.exit("pai status: " + p.stderr.strip())
    doc = json.loads(p.stdout)
    if doc.get("schema") != "pai_status_v1":
        sys.exit("unexpected status schema %r" % doc.get("schema"))
    return [(l["lane"], l["ids"]["task"], l["lifecycle"]["state"])
            for l in doc["lanes"] if l["lifecycle"]["attention"]]
```

A 0.4.x `pai status --json` printed a JSON **array** of lane rows (no `schema`); 0.5.0 prints the
object above with the rows under `lanes` (`jq '.lanes'` gives the old array, with every 0.4 field).

### Fields recovery and automation use beyond status

| Output | Schema | Committed fields |
| --- | --- | --- |
| `pai inspect LANE --json` | `pai_status_v1` | `run`, `lane`, `ids`, `lifecycle` as in status; `evidence[]` with `kind`, `state`, `detail`, `source` (kinds include `run`, `task`, `note`, `process`, `trust`, `input`, `dispatch`, `delivery`, `paste`, `window`, `worktree`; `detail` is text for people) |
| `pai recover --json` | `pai_recovery_preview_v1` | `run`, `lanes[].lane`, `.task`, `.action` (`none`, `restart`, `deliver`, `record-delivered`, `trust`, `choose`, `refused`, ...), `.human`, `.choices`; `changed` (always false for a preview) |
| recovery log `$PAI_SWARM_HOME/recovery/*.jsonl` | `pai_recovery_v1` | one line per `begin` / `end`: `id` (a handoff attempt is `MARKER_ID.N`), `lane`, `task`, `action`, `before` / `after` snapshots, `result` (`done`, `not-done`, or `superseded by ID` when a later recovery of that lane completed); an id with a `begin` and no `end` is an interrupted recovery |
| hand pastes `$PAI_SWARM_HOME/pastes/*.jsonl` | one JSON object per line | `at`, `lane`, `via`, `sha256`, `chars`, `lines`, `enter`, `needle` (privacy-filtered first line); the text is never stored |
| transfer packet (`pai task export --transfer`) | `pai-task-cards` v1 + `transfer` v1 | see `swarm/transfer.py` and CONTINUATION.md |
| `pai queue status FILE --json` | `pai_queue_status_v1` | `name`, `items`, `counts`, `done`, `prototype`, `blocked[]`, `owner_only[]`, `doing`, `todo`, `next`, `last_checkpoint`, `next_checkpoint`, `sessions` |

### Earlier description (still accurate)

Top level: `schema`, `run` (the run identifier), `run_id`, `session`, `socket`, `ok`, `summary`
(`lanes`, `started`, `with_task`, `delivered`, `not_delivered`, `not_started`, `delivery`,
`states`, `attention`) and `lanes`. Each lane keeps every field of the unversioned output of pai
0.4 (`process`, `phase`, `trust`, `input`, `dispatch`, `rc`, branch and worktree facts) and adds
`ids` (`run`, `task`, `record`, `generation`) and `lifecycle` (`state`, `why`, `terminal`,
`attention`, `delivered`, `next`). Within `pai_status_v1` fields are only added; renaming or
removing one means a new schema name.

## Narrow terminals

Below 80 columns (or with `--brief`) `pai status` prints one short block per lane, wrapped to
the window: the name (upper case when it needs you), why, and the first next step. Long lane
names, paths and evidence wrap instead of running off the screen.
