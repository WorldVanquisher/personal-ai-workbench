# Coming from pai 0.4.x

Draft release-note material for the owner; the version number of the next release is the owner's
decision (the tree still says 0.4.0). Everything below was checked against a real 0.4.0 install in
this run (`tools/package_check.py`, item 083): the new code read a 0.4.0 project, task notes and a
0.4.0 run without writing anything, then upgraded in place.

## Nothing to migrate

- **Data stays as it is.** Configuration (`~/.config/pai`, `~/.config/pai-workbench`), task
  notes, run state (`~/.cache/pai-swarm`) and handoffs are read as written by 0.4.x; no file is
  converted, and reading state never rewrites a record. An old dispatch record that said
  "delivered" only because the tag was visible stays `unknown` unless the agent's transcript
  shows the prompt (as in 0.4.0).
- **Upgrade the program the usual way.** uv: `uv tool install --force git+...@vNEW`. Archives:
  verify, extract, `bin/pai install --dry-run` (shows the version it would replace, writes
  nothing), then `bin/pai install`. A failed or interrupted install leaves the previous version
  running (checked by killing installs at 7 points).

## What you will notice

| Area | 0.4.x | Now |
| --- | --- | --- |
| `pai status` text | per-lane evidence lines | adds a `summary` line and, per lane, `lifecycle` and `id` lines; below 80 columns (or `--brief`) a short wrapped view. Scripts that parsed the text should use `--json` |
| `pai status --json` | a JSON **array** of lane rows, no schema field | a JSON **object**: `schema: pai_status_v1`, `run`, `summary`, and the rows under `lanes` (per lane `ids`, `lifecycle`, every 0.4 field kept). Replace `json.load(f)[0]` / `jq '.[0]'` with `json.load(f)["lanes"][0]` / `jq '.lanes[0]'` |
| `pai doctor` / `pai setup` exit code | 1 when anything was missing, `gh` included | 1 only for an **essential** gap; `gh` and its login are OPTIONAL (only `pr open`, `pr plan`, `next` need them); output grouped ESSENTIAL / OPTIONAL / INFO with copyable REPAIR lines |
| `pai doctor --agent codex` | ran PAI's own `doctor PROJECT` with "codex" as a project name | runs the workbench doctor for that agent |
| PATH advice | `fish_add_path ~/My Tools/bin` broke on spaces | quoted for each shell |
| Narrow terminals | `ps` command lines were cut to `$COLUMNS`, so a lane at its trust prompt could look like "window without the agent" | full command lines (`ps -ww`) |
| `pai handoff new` | wrote session links and tokens as given | removes agent session links and credential-like text by default (`--keep-private` keeps them; `--preview` shows what would be removed) |
| `pai web` | lane phase badge, direct paste | lifecycle badge and explanation, Inspect, "Resume run…" (recover preview), a paste preview with destination and size, a STAGED / SUBMITTED cue |

## Continuing a task in a fresh session (new in the 0.5.0 candidate)

`pai recover --continue LANE [--write]` hands a stopped session's task off into its task note;
`pai run --continue NOTE [--agent ...]` continues it in a fresh session (any agent, any HOME with
the same repository). Nothing from 0.4.x needs converting to use it: a 0.4.0 run can be handed
off. A handed-off lane shows the new lifecycle name `handed-off` and is never restarted by
`pai resume` or `pai recover`. Task cards now carry other front-matter keys in `fields` (they were
dropped before); `swarm/CONTINUATION.md` describes the workflow.

## Carrying a task to another HOME or machine, and work queues (0.5.0 candidate, R2)

| Area | Before (0.4.x, or the unreleased earlier candidate) | Now |
| --- | --- | --- |
| Moving a stopped task | `pai task export NOTE --out FILE` (a card: the note only) | also `pai task export NOTE --transfer` (a card plus a versioned `transfer` object with evidence ids, capabilities and omissions); `pai task import` previews it with this machine's agents and repository; another project name needs `--other-project`, another repository is refused |
| Task cards | carried the `## Review` sections `pai pr check` appends (with the worktree path and the check command lines) | those sections are private (left out unless `--include-private`, which puts them in the `private` group) |
| Card files | unknown top-level keys were ignored | refused with their names (a card file from a newer pai says so instead of losing data) |
| `pai paste` and the console's paste | left no trace | each is recorded (time, size, sha256, filtered first line; never the text) and shown by `pai inspect` as staged / delivered / unknown; an unknown one needs `--choose` before a handoff |
| Agent windows | inherited `CLAUDE_CONFIG_DIR` / `CODEX_HOME` from the tmux server | pai passes the values it reads its evidence from (when set), so a tmux server started from another HOME no longer hides a delivery |
| Recovery log | a retried recovery left the interrupted one open forever | a completed recovery of a lane closes earlier interrupted ones as `superseded by ID`; handoff attempts have ids `MARKER_ID.N` |
| `pai status --run R` for a run this HOME has no record of | "no run R" | also lists the task notes that name R (worker, branch, base) and says to treat delivery as unknown |
| Work across context limits | (none) | `pai queue` (core: also native Windows): a queue file plus an append-only log; `status`, `next`, `mark`, `table`, `check`, `checkpoint`, `restart` |

Rolling back to 0.4.x after using 0.5.0 (checked by the package check, `rollback`): 0.4.0 reads
0.5.0 run records and task notes; it has no `pai recover` and does not know `handed-off`, so do not
`pai resume` a handed-off run with 0.4.x (in the check, its resume was refused because the
continuation held the worktree, and no prompt was sent). Task card and transfer packet files stay as
files; 0.5.0 imports them again unchanged.

## New commands and options

`pai inspect LANE`, `pai recover` (`--apply`, `--choose`, `--history`, `--json`, `--continue`,
`--write`), `pai run --continue`, `pai guide --recovery`, `pai guide --handoff [clean|reverse|interrupted]`,
`pai task export --transfer` (`--run`, `--lane`, `--include-logs`, `--without`), `pai task import
--other-project`, `pai queue` (`init`, `status`, `next`, `mark`, `checkpoint`, `table`, `check`, `restart`),
`pai task export` / `pai task import`, `pai pr draft`, `pai status --brief`, `pai handoff new
--preview / --keep-private`, project setting `review.strip_trailers`.

## For people who script pai

- Use `--json`; `pai_status_v1` only gains fields within 1.x (docs/1.0/COMPATIBILITY.md). Read only
  the committed fields and ignore the rest (the tested example in swarm/RUN-STATE.md).
- Affected scripts and their replacement: a script that indexed 0.4.x `pai status --json` as an
  array (`[0]`, `for row in data`) reads `data["lanes"]`; a script that scraped `pai status` text
  uses `--json`; a CI step that failed on any `pai doctor` gap uses the one-liner in
  COMPATIBILITY.md; a script that ran `pai task import` on a file with extra top-level keys now gets
  exit 2 naming them.
- Exit codes are unchanged in meaning and listed at the end of `pai --help`: 0 done (or a preview
  with nothing in the way), 1 refused or needs a person (or, for `pai status`, a lane still at
  work), 2 usage error or unreadable input (nothing changed). Every refusal prints a line that
  starts with `REFUSED`, `CONFLICT`, `MISMATCH` or `pai COMMAND:` and says what to do.
- `pai doctor --json` rows gained `need`, and backend rows `capabilities` and `gaps`.
- A CI job that relied on `pai doctor` failing when `gh` is absent should check the `gh` row's
  `state` instead.
