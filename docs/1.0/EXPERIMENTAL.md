# Experimental features: how they are marked, and how they graduate (proposal)

## What "experimental" means

An experimental feature works and is tested, but its interface may change in any release and it
is outside the compatibility commitments (COMPATIBILITY.md). It must:

1. say so where people meet it: `EXPERIMENTAL` in its `--help` line, its page or first output,
   `pai doctor` (where it has a row) and the release notes;
2. be off until asked for: a command you run, or a flag such as `--experimental`; no default,
   setting or other command turns it on for you;
3. keep the same safety rules as the rest of PAI: previews before changes, loopback-only servers,
   no credentials in files, nothing published or answered for you;
4. have its own tests, and be removable without breaking a committed surface.

Experimental today: `pai web` (console), `pai server` and `pai setup --experimental server`,
`pai jev`, Codex Remote Control (not offered), any native-Windows workbench behaviour (none is
offered; see PLATFORMS.md).

### Feature boundaries for 0.5.0 (console and backends)

| Surface | In 0.5.0 | Outside it (and why) | Evidence | Still unproven |
| --- | --- | --- | --- | --- |
| `pai web` console (experimental) | the same lifecycle, task ids, recovery plan and inspect evidence as the CLI (paste records and handed-off lanes included); paste preview, staged versus submitted input, Enter only on request; `POST /api/recover` only with `confirm: true`, the CSRF header and a session, and only the automatic actions | writing a handoff or a transfer packet, `--choose` decisions, trust answers: terminal only, on purpose (each needs a person reading the preview) | test_console (Web, 5 tests), package check `console` (installed wheel), loopback listener read from `/proc/net/tcp` | real-browser focus, screen-reader announcements and a 390 px layout: the owner kit `sh ~/.cache/pai-1.0-work/browser/run.sh <worktree>` (no browser here; `docker run` denied) |
| Backends | built-in Claude Code and Codex adapters; capabilities in one schema (`pai_backend_capabilities_v1`), shown by `pai doctor` and in transfer previews | adding a backend from configuration; Codex Remote Control; a Codex readiness record (screen only) | test_backends, test_transfer (source/destination capability rows) | real Claude Code / Codex: REAL-AGENT-SMOKE.md, REAL-AGENT-SWITCH.md |
| Transfer packets and `pai queue` | proposed **stable** for 0.5.0 (formats versioned: `transfer` v1 inside `pai-task-cards` v1; `pai-queue` v1) | coordination between two machines that both continue the same packet (BACKLOG 10a) | test_transfer, test_r2_recovery, tests/test_workqueue | native Windows run of `pai queue` (CI runs `tests/` there on a push) |

## Graduation (experimental -> committed)

A feature graduates in a minor release when all of these hold, and the owner says so:

1. its interface (commands, flags, output schema, files) has not changed for one full minor
   release;
2. its documentation states what it does, what it never does, and its evidence (as RUN-STATE.md
   does for status);
3. its tests cover the refusals as well as the happy path, under the strict suite, on every
   platform it claims (PLATFORMS.md criterion met);
4. there is no open issue that would require breaking it;
5. the release notes announce it as committed.

## Removal

An experimental feature may be removed in any minor release; the release notes say so and name
any replacement. Its data files are left in place (never deleted for you).
