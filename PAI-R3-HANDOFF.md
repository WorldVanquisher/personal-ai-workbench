# PAI R3 handoff: post-merge refresh, worktree PRs, AGENTS.md, JEV, task splits

Batch R3-001..R3-100 ("Fast PR flow, worktree instructions, JEV generation and worker assignment").
Started after PR #12 was merged and the owner confirmed the refreshed worktree.

| | |
| --- | --- |
| Worktree | `~/workspace/pai-workers/20261120-postmerge/pai` |
| Branch | `worker/20261120-postmerge/pai` |
| Base | `5fe3627c0a1b3d943aa0bd2cc107618d8d131d29` (= `origin/main`, PR #12 merged; owner-confirmed) |
| State | all work **uncommitted** in this worktree; nothing committed, pushed or opened |
| Queue | `~/.cache/pai-r3-work/r3.queue` (+ `.log`); checkpoints 010, 025, 050, 075, 100 in `~/.cache/pai-r3-work/checkpoints/` |
| Prior handoffs | PAI-004, PAI-005, PAI-008-FOLLOWUP, PAI-ONBOARDING here, and PAI-006/007/009 in their worktrees: untouched |

## What is new

| Command | What it does | Writes |
| --- | --- | --- |
| `pai worktree list` | every worktree: branch, HEAD, uncommitted paths, operation in progress, whether it contains the last fetched main | nothing |
| `pai worktree refresh [--worktree P \| --new P]` | Bash and PowerShell commands for the owner: explicit fetch of main, then a fresh worktree at the fetched SHA (squash merges, dirty or detached worktrees) or a guarded `merge --ff-only` (clean, behind) | nothing |
| `pai worktree verify P --base SHA` | does the worktree itself contain the fetched base (a fetch only moves refs); exit 1 if not | nothing |
| `pai worktree preview P --path F ... --title T` | read-only PR preview: repository, branch, base SHA, remote SHA (ls-remote), existing PR (gh), approved vs staged vs unexpected paths, cache files, publication check on title/body/message | `--out PLAN.json` only |
| `pai worktree publish PLAN --yes --draft\|--ready` | drift check, commit only the approved literal paths (hooks run, other staged changes stay staged), push without force, read the remote SHA back, open or reuse the PR; journaled and resumable | the commit, the push, the PR |
| `pai worktree init P --goal G [--scope D] [--jev]` | a pai-managed section in the worktree's AGENTS.md; manual text byte for byte; inherited rules with `file:line`; conflicts marked; optional JEV scoring of the rule library | `--write` only |
| `pai plan propose\|check\|assign\|accept` | split a goal into worker slices (parent ID kept, owners from AGENTS.md scopes), refuse missing criteria, shared paths, owned paths, cycles; queue accepted slices idempotently | `--out`, `accept --write` |
| `pai queue add` | append items to an existing queue (older pai still reads it) | `--write` only |
| `pai worktree web` | all of the above in the browser (127.0.0.1, one-time link): chooser, instruction editor with sections, diff, conflicts and undo, refresh with expected/actual SHAs, split preview, PR preview and reviewed publish | confirmed saves and publish only |

Rules kept everywhere: no reset, stash, force-push or overwrite of a dirty worktree; nothing is
fetched for the owner; generated text and JEV output are proposals until the owner applies them;
no AI attribution in commits or PRs (the publication check refuses it).

## Items

Every item is recorded in the queue with its evidence; the table at the end is the exact
`pai queue table` output. Summary: **100 done** as implemented and tested here with disposable
repositories, a local bare remote, the fake gh, a local JEV fixture server and real browsers in
containers. What that does **not** cover is listed under owner-only below; no item is blocked.

AWS (live operations in the batch text): **not applicable**. Nothing in R3 touches AWS; no
account, region, data or spend was used, so no plan, cost envelope, teardown or rollback applies.

## Tests actually run (final code)

All with `TMPDIR=/var/tmp/pai-r3`, `-W error::ResourceWarning` where the suite takes it, fakes only.

| Run | Result |
| --- | --- |
| `tools/verify.py` (full) | core PASS (125 tests, 7 platform skips), workbench PASS (`swarm/tests` strict, 793 s), archives PASS, bundle smoke PASS, headless PASS, onboarding PASS, path-bash PASS; **console FAIL** (see below); SKIPPED for missing tools: quickstart (uv), path-zsh, path-fish, path-pwsh, package (setuptools) |
| console walkthrough race | pre-existing: the base 5fe3627 failed 6 of 12 alternating runs ("delivered vs running": the fake agent moves on between the `pai status` snapshot and the console read). Fixed in `tools/console_walkthrough.py` (compare with status read before and after): 10/10, then OK again on the final code |
| after the last code change (`pai worktree/plan --help` fix) | `tools/verify.py --quick` 3/3 (core, archives, bundle); test_refresh + test_workbench + test_inventory 41 OK; command examples 113 parsed, 0 failed |
| real browsers (node:22-bookworm-slim, Chromium 156), final code | `pai worktree web` 48/48; the existing `pai edit` smoke 17/17 |
| PowerShell 7.4.7 (mcr.microsoft.com/powershell, Linux) | generated refresh commands run as printed, path with a space and an apostrophe: OK |
| old release round trip (git archive of 5fe3627) | 14/14 |
| four-lane dry run | clean: refresh, verify, AGENTS.md, split with a dependency and a shared-file conflict fixed by the owner, queues, one draft PR to a local remote |
| package smoke | byte-identical rebuild of both archives, checksums OK, new modules in the bundle, `worktree refresh/verify`, `plan --help`, `queue add --help` from the extracted bundle |
| seal | `seal/r3.patch` applies to a clean export of the base and reproduces all 25 files byte for byte (its two whitespace warnings are the golden fixture's intentional trailing spaces) |

Found and fixed by these runs: `pai worktree --help` and `pai plan --help` failed (argparse
kept a leading `--help` from the pass-through), found by the package smoke.

## Evidence

All under `~/.cache/pai-r3-work/`:

- `RECON.md` (R3-001..009: baseline, JEV API facts: System One answers typed questions and writes no text)
- `checkpoints/CP-010.md`, `CP-025.md`, `CP-050.md`, `CP-075.md`; checkpoint 100 is this handoff (its sha256 is in the queue log)
- `evidence/browser-final/` (final code) and `evidence/browser/` (first run): real Chromium 156 runs of `pai worktree web`: `results.json` (48/48), 8 screenshots, server log; harness `browser/run.sh`, `browser/smoke.mjs`
- `evidence/pwsh-refresh.log` the generated PowerShell commands run in PowerShell 7.4.7 (path with a space and an apostrophe); harness `pwsh/run.sh`
- `evidence/roundtrip.log` (`roundtrip.sh`) the pre-feature release (git archive of 5fe3627) and this work reading and writing the same queue and worktrees, 14/14
- `evidence/dryrun-4-lanes.md` (`dryrun.sh`) four lanes from refresh to a published draft PR on a local remote, before and after
- `evidence/edit-smoke/` the existing `pai edit` smoke, 17/17; `evidence/package-smoke.log`
- `logs/` suite and tool logs; `seal/` the patch, file hashes and clean-apply check

## Owner-only, and platform gaps (R3-099)

Not run here, by design or for lack of the platform:

1. A real `pai worktree publish` (push to GitHub, `gh pr create`) and the web publish: tested only with a local bare remote and the fake gh.
2. A real JEV call from `pai worktree init --jev` (fixture server only: ok, malformed, timeout, no key, unreachable).
3. Native Windows: PowerShell 5.1/7 on Windows, `pai worktree web` in a Windows browser. The PowerShell commands were run in PowerShell 7.4.7 on Linux only.
4. macOS (CI covers the suites once pushed; not run locally).
5. Real agents working in the AGENTS.md-instructed worktrees (the dry run scripts the lane edits).

## Publishing this work (owner)

The new preview, run on this worktree offline, is clean (nothing unexpected or staged). Suggested split:
`tools/console_walkthrough.py` (the pre-existing race fix) as its own commit; the other 24 paths in `seal/files.txt` as the R3 change.

```sh
cd ~/workspace/pai-workers/20261120-postmerge/pai
python3 -B bin/pai.py worktree preview . PATHS --title "worktree: post-merge refresh, worktree PRs, AGENTS.md and task splits" --body-file BODY.md --out /var/tmp/r3-pr.json
python3 -B bin/pai.py worktree publish /var/tmp/r3-pr.json --yes --draft
```

`PATHS` is `--path F` for each file in `seal/files.txt`; this handoff stays out of it unless you
add it. Or commit the same path set by hand.

## Follow-up review plan

1. Read `swarm/wtpr.py` `publish()` and `adopt()` first: the only code that commits and pushes.
2. `swarm/wtweb.py` routes: worktree and file guards, save/undo, publish needing the stored preview digest.
3. `swarm/agentsmd.py` `merge()`: byte preservation (BOM, CRLF, no final newline; golden fixture).
4. `swarm/splitplan.py` `accept()`: idempotency after a kill, file names from plan IDs.
5. Run the PowerShell refresh once on Windows; run one real publish on a scratch repository.

## Queue table

| ID | State | Evidence or reason |
| --- | --- | --- |
| R3-001 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-002 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-003 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-004 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-005 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-006 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-007 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-008 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-009 | done | ~/.cache/pai-r3-work/RECON.md |
| R3-010 | done | swarm/tests/test_refresh.py (4 OK) |
| R3-011 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-012 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-013 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-014 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-015 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-016 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-017 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-018 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-019 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-020 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-021 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-022 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-023 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-024 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-025 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-026 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-027 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-028 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-029 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-030 | done | swarm/tests/test_wtpr.py (5 OK; local bare remote + fake gh) |
| R3-031 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-032 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-033 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-034 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-035 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-036 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-037 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-038 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-039 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-040 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-041 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-042 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-043 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-044 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-045 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-046 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-047 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-048 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-049 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-050 | done | swarm/tests/test_agentsmd.py (4 OK) |
| R3-051 | done | RECON.md (docs.typesafe.ai/primitives: choice/score/noul/box, no text) |
| R3-052 | done | swarm/WORKBENCH.md section 11 (Supported configurations table) + test_agentsmd JEV fixture states |
| R3-053 | done | swarm/tests/test_agentsmd.py::test_jev_scores... (fixture server) |
| R3-054 | done | swarm/tests/test_agentsmd.py::test_jev_scores... (fixture server) |
| R3-055 | done | swarm/tests/test_agentsmd.py::test_jev_scores... (fixture server) |
| R3-056 | done | swarm/tests/test_agentsmd.py::test_jev_scores... (fixture server) |
| R3-057 | done | swarm/tests/test_agentsmd.py::test_jev_scores... (fixture server) |
| R3-058 | done | swarm/tests/test_agentsmd.py::test_jev_scores... (fixture server) |
| R3-059 | done | swarm/tests/test_agentsmd.py::test_jev_scores... (fixture server) |
| R3-060 | done | swarm/tests/test_agentsmd.py::test_jev_scores... (fixture server) |
| R3-061 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-062 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-063 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-064 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-065 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-066 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-067 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-068 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-069 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-070 | done | swarm/tests/test_splitplan.py (3 OK) |
| R3-071 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-072 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-073 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-074 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-075 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-076 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-077 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-078 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-079 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-080 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-081 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-082 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-083 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-084 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-085 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-086 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-087 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-088 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-089 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-090 | done | swarm/tests/test_wtweb.py (5 OK); real Chromium 156 smoke 48/48 (~/.cache/pai-r3-work/evidence/browser/results.json) |
| R3-091 | done | ~/.cache/pai-r3-work/roundtrip.sh vs git archive of 5fe3627: 14/14 (~/.cache/pai-r3-work/evidence/roundtrip.log) |
| R3-092 | done | swarm/tests/fixtures/agents-golden/{manual,section}.md; test_agentsmd golden test OK |
| R3-093 | done | swarm/tests/test_splitplan.py::test_a_mixed_batch_where_one_lane_has_a_ci_blocker OK |
| R3-094 | done | test_splitplan kill-at-any-point (6 SIGKILLs) + test_wtpr kill between commit and journal: OK |
| R3-095 | done | verify.py: 7 PASS (core, workbench 793s, archives, bundle, headless, onboarding, path-bash), console FAIL = pre-existing race (base 6/12 fail) fixed in tools/console_walkthrough.py (10/10, then final OK); verify --quick 3/3 after the last change; browser smokes final 48/48 + pai edit 17/17; logs ~/.cache/pai-r3-work/logs |
| R3-096 | done | QUICKSTART.md and QUICKSTART.zh-CN.md section 6; PowerShell variant run in pwsh 7.4.7 (~/.cache/pai-r3-work/evidence/pwsh-refresh.log) |
| R3-097 | done | ~/.cache/pai-r3-work/dryrun.sh -> ~/.cache/pai-r3-work/evidence/dryrun-4-lanes.md (before/after, 4 lanes, fake gh) |
| R3-098 | done | swarm/WORKBENCH.md section 14 Troubleshooting (JEV states, push auth, gh auth, non-GitHub remote, drift) |
| R3-099 | done | ~/.cache/pai-r3-work/evidence/package-smoke.log (byte-identical rebuild, checksums, new modules in bundle, commands from extracted bundle); platform gaps listed in PAI-R3-HANDOFF.md |
| R3-100 | done | ~/.cache/pai-r3-work/seal/ (r3.patch sha256 01391309ca3e..., files.sha256, clean apply to base 5fe3627 reproduces 25 files); PAI-R3-HANDOFF.md |

Totals: todo 0, doing 0, done 100, prototype 0, blocked 0, owner-only 0 (100 items)
