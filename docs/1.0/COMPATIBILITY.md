# Compatibility commitments for PAI 1.x (proposal, owner decision)

What 1.0 would promise to keep working across every 1.x release, what may still change, and how
a change is made. Proposed by the worker; nothing here is binding until the owner accepts it.

## Rules for every committed surface

- Within 1.x a committed surface only **gains**: new commands, flags, fields, states and keys
  may appear; existing ones keep their name, meaning and exit codes.
- Every 1.x release **reads** everything an earlier 1.x (and 0.3/0.4) wrote: records are never
  migrated in place, and reading never rewrites them (tested: `test_runstate`, record digests).
- Anything removed or renamed is first deprecated for at least one minor release, with a warning
  that names the replacement; the release notes list it.
- Text output is for people and may be reworded at any time. Scripts use `--json`.

## Command line (committed)

`pai guide` (with `--recovery`, `--handoff`), `doctor`, `init`, `config`, `task new|list|export|import`,
`run` (with `--continue`), `status`, `inspect`, `attach`, `logs`, `trust`, `resume`, `recover`
(with `--continue`), `paste`, `pr draft|plan|check|open`,
`next`, `cleanup`, `edit`, and PAI's own `init PROJECT`, `doctor PROJECT`, `context`, `handoff`,
`install`. Their documented flags, the rule that previews are the default (`--write`, `--apply`,
`--out`, `--yes` are needed to change anything), and these exit codes: 0 done / nothing needed,
1 refused or needs a person, 2 usage or invalid input.

Not committed (experimental, may change): `pai web` and its page, `pai server`, `pai setup
--experimental`, `pai jev`.

## Task files (committed)

- Task notes: Markdown with front matter `status` (todo is the queue), `priority` (integer,
  higher first), `paths` (list); the `# ` heading is the title. Keys pai writes while a note runs
  (`worker`, `rc_run`, `branch`, `base_sha`, `remote`, `worktree`, `review*`) keep their names.
- Task cards: format `pai-task-cards` version 1 (cards.py). A new version number is used for any
  change a version-1 reader would misread; version 1 files stay importable in every 1.x. Before
  its first release, version 1 gained `fields` (every non-private front-matter key, such as the
  review keys `checks`, `acceptance`, `pr_title`): the earlier draft dropped them silently, which
  lost a note's goal metadata in transport. No card file was ever released without it.
- Continuations: the `## Continuation from RUN/LANE` section and its `pai-continuation v1` marker
  line in a task note (CONTINUATION.md). Their fields (`id`, `from`, `run`, `lane`, `note`,
  `branch`, `head`, `base`, `agent`, `at`, `completed`) only gain; a different meaning gets `v2`.

## Run data (committed for reading; written by pai only)

| File | Schema |
| --- | --- |
| run manifest `runs/PROJECT/RUN_ID/manifest.json` | `pai_rc_manifest_v1` |
| task record `tasks/<key>-<task_id>.json` | `pai_swarm_task_v1` |
| window ownership `rc/<key>-<run>.json` | `pai_rc_run_v1` |
| dispatch record `dispatch/<key>-<run>.json` | `pai_dispatch_v1` |
| recovery log `recovery/<key>-<run>.jsonl` | `pai_recovery_v1` |
| project configuration `projects/NAME.toml` | `pai_workbench_project_v1` keys |

The identifiers in RUN-STATE.md (run `PROJECT/RUN_ID`, task `RUN_ID/LANE/NOTE`) are derived from
these and stay stable.

## `pai doctor` exit codes (changed in 0.5.0)

| | 0.4.x | 0.5.0 candidate |
| --- | --- | --- |
| exit 1 when | any row is not `ok` or `info`: a missing `gh`, an unauthenticated `gh`, Remote Control not ready, a missing agent | only an **essential** row is not ok: git, tmux, ps, the chosen agents and their logins, Claude Remote Control (rc is Claude's default mode), this pai, remote access |
| `gh` missing or not logged in alone | exit 1 | exit 0, listed under OPTIONAL "(only for: pr open, pr plan, next)" |
| `pai setup` | the same rule as doctor | the same rule as doctor |
| `pai doctor --agent codex` | ran PAI's own `doctor PROJECT` with `codex` as a project name | runs the workbench doctor for that agent |
| JSON rows | `tool`, `used_by`, `state`, `detail`, `fix` | the same, plus `need` (`essential`, `optional`, `info`); backend rows also `capabilities`, `gaps` |
| a fix that cannot run here | sometimes printed (for example `pai setup --install-missing` where nothing could be installed) | `fix` is null and `detail` says why; REPAIR lists only commands that can run here |

A script that needs the old rule (fail on any gap) reads the rows instead of the exit status:

```sh
pai doctor --json | python3 -c 'import json,sys; sys.exit(any(r["state"] not in ("ok", "info") for r in json.load(sys.stdin)))'
```

Committed from 0.5.0 on: the meaning of exit 0/1/2 above, the row fields, the `need` values and
the rule "exit 1 only for an essential gap". The set of rows and the text of `detail` may change.

## Structured output and local APIs

- `pai status --json`, `pai inspect --json`: schema `pai_status_v1`, committed fields listed in
  RUN-STATE.md ("the supported contract") and checked against real output by a test; additive only.
- `pai recover --json`: `pai_recovery_preview_v1` (committed).
- `pai doctor --json` rows and backend `capabilities` (`pai_backend_capabilities_v1`): row
  fields committed; the set of rows may grow.
- `pai web` / `pai edit` HTTP endpoints: **not committed**; they serve their own page only, and
  stay loopback-only with one-time sign-in, session cookie and CSRF header (that security
  behaviour is committed even while the API is not).

## Platforms

Only what docs/1.0/PLATFORMS.md lists as meeting its criterion is part of the commitment.
