# Platforms: what PAI does where, the proof for each claim, and the 1.0 criterion

Draft for the 1.0 decision (owner). Each cell names its proof. "This run" means the worker's
session on 2026-11-19 (Arch Linux 7.2.14, Python 3.14.9, tmux 3.8, bash 5.3; not WSL). "CI" means
a step in `.github/workflows/ci.yml` that exists in this tree; CI was **not** run in this session
(running it is the owner's: it needs a push). "Simulation" means the platform was faked inside
Linux (environment overrides, fixtures). Nothing is claimed beyond its proof.

## Capability matrix

| Capability | Linux | macOS | WSL (Ubuntu) | Native Windows |
| --- | --- | --- | --- | --- |
| Core PAI: `pai init PROJECT --vault ...`, `doctor`, `context`, `handoff` | **verified** this run (`tests/`, strict) and CI (ubuntu) | CI (macos-latest) | not run; same code as Linux | CI (windows-latest: tests, `pai.cmd` from cmd.exe and PowerShell) |
| `pai edit FILE.md` (browser editor) | **verified** this run (`test_mdserver`, `test_packaging`) | CI (installed-command edit session) | not run | CI (`RealInstall.test_edit_through_the_installed_command`, `pai.exe`) |
| Release archive build, byte-identical rebuild, smoke | **verified** this run (57/57 at 010; see the 050 log for this run's count) | CI | not run | CI (zip first) |
| `uv tool install` of the wheel | **verified** this run at 010 (uv 0.13.9) | CI | not run | CI |
| Workbench: `pai run`, `status`, `inspect`, `recover`, `trust`, `resume`, `cleanup` | **verified** this run (fakes; isolated tmux) | **not verified**: needs tmux from Homebrew; `/proc`-based process age (120 s not-accepting-input rule) and `ps -o` start times are Linux-only | **simulation only**: platform detection (`PAI_SWARM_WSL`, a mocked `microsoft` kernel string) and the Ubuntu command-not-found fixture; never run on WSL | **refused by design**: the workbench needs tmux; the launcher prints the WSL route (CI checks `pai run`/`pai web` exit 2 with "WSL") |
| `pai guide` (offline first run, fake worker) | **verified** this run (fresh HOME, `unshare -rn`, installed wheel and extracted archive at 010) | not run | not run | refused (workbench) |
| `pai web` (console) | **verified** this run (HTTP API with fakes; console walkthrough under `unshare -rn` with loopback) | not run | not run | refused (workbench) |
| Real-browser behaviour of `pai web` (focus, announcements, 390 px) | **prototype**: static checks only this run; the browser kit is owner-run (`~/.cache/pai-1.0-work/browser/run.sh`) | not run | not run | n/a |
| Task cards (`pai task export`, `pai task import`) | **verified** this run | not run | not run | not run; the module has no POSIX-only import (see 057 in the handoff) |
| `pai pr draft` | **verified** this run (fake publish guard) | not run | not run | refused (workbench) |
| Real Claude Code / Codex sessions | **owner-only** (real-agent smoke, see `docs/1.0/REAL-AGENT-SMOKE.md`) | owner-only | owner-only | n/a |

## 1.0 release criteria per platform claim

A platform claim may appear in the 1.0 release notes only when its criterion is met **in a
recorded run of the release candidate commit**:

| Claim | Criterion |
| --- | --- |
| "Linux is supported" | Strict core and workbench suites pass on the candidate (`tools/verify.py --require-all` all PASS, including quickstart with uv); archive smoke passes; one owner real-agent smoke (Claude, one lane) passes on Linux |
| "macOS is supported for core PAI and `pai edit`" | The CI macOS job passes on the candidate |
| "macOS is supported for the workbench" | Not claimable until a macOS run of the workbench suite passes (tmux from Homebrew) and the Linux-only process-age rule is either ported or documented as absent |
| "WSL is supported" | A run on WSL Ubuntu of: the planner and missing-pai tests, `tools/onboarding_acceptance.py`, `pai guide`, and one owner real-agent smoke. Until then: "WSL: expected to work, not verified" |
| "Native Windows: core PAI and `pai edit`" | The CI windows-latest job passes on the candidate (tests, both launchers, installed edit session) |
| "Native Windows: workbench" | Not claimed for 1.0. The launcher's WSL message is the documented behaviour |
| Any browser claim beyond "works in a current Chromium" | The owner-run browser kit passes on the candidate |

The release notes must list, for every platform, what was verified, how, and what was not.

## R4: the owner-to-worker loop (worktree refresh, AGENTS.md, plans, queues, publish, worktree web)

What the R4 queue actually ran, per platform (no other platform was run):

| Platform | Verified in R4 | Not verified |
| --- | --- | --- |
| Linux (Arch, x86_64, Python 3.14.9, git 2.56.0, tmux 3.8, bash 5.3) | every R4 command and test (`swarm/tests`, `tests/`), `tools/worktree_rehearsal.py` with a local bare remote and the fake gh, bash execution of the refresh commands with hostile branch names | other distributions, Python 3.11-3.13 locally (CI covers them on a push) |
| PowerShell | the generated refresh commands parsed and run by PowerShell 7.4.7 (local image `mcr.microsoft.com/powershell:lts-debian-12`, on Linux, no network): every value arrives literally (6/6) | PowerShell 5.1; PowerShell on Windows |
| Browsers | `pai worktree web` in Chromium 131 (local image `mcr.microsoft.com/playwright/python:v1.49.1-jammy`, the server on the host's loopback): the R3 smoke 48/48 (5 runs) and the R4 smoke 30/30 | Firefox, Safari, a real screen reader |
| Native Windows | by code: `pai worktree` and `pai plan` are workbench commands, which the launcher refuses on native Windows with the WSL message (`pai/launch.py`); `pai queue` is core and is meant to run there (CI's Windows job runs `tests/test_workqueue.py`) | nothing was run on Windows |
| WSL | nothing (no WSL here) | the whole loop |
| macOS | nothing (no Mac here); `/proc`-based process checks are off there by design | the whole loop |

Owner-run commands for the gaps are in "Evidence still needed" below (WSL, macOS, Windows) and in
`docs/0.5.0/OWNER-WORKER-LOOP.md` (the loop's acceptance scenarios).

## Evidence still needed, with the exact owner-run command for each

The worker could not produce these (no browser, no `docker run` permission, no WSL, macOS or
Windows machine, no network). Each command is complete as written; run it on the release
candidate and keep its output with the release notes. `W` is the worktree or a checkout of the
candidate commit.

| Claim | Blocked by | Owner-run command | Passes when |
| --- | --- | --- | --- |
| Browser: focus, status announcements, staged/submitted cue, 390 px layout, draft restore (045, 046, 048, 049) | no browser on the worker's host; `docker run` denied by its permission system | `sh ~/.cache/pai-1.0-work/browser/run.sh "$W"` (uses the local image `mcr.microsoft.com/playwright/python:v1.49.1-jammy` with `--network host`) | it exits 0 and `~/.cache/pai-1.0-work/browser/out/results.json` has every check `"ok": true` |
| Browser, without Docker | the same | on a machine with Chromium and Python Playwright 1.49.1: `python3 -B "$W/tools/console_walkthrough.py" --serve 240 > /tmp/pai-walk.txt 2>&1 & until grep -q '^BROWSER: ' /tmp/pai-walk.txt; do sleep 1; done; mkdir -p /tmp/pai-browser-out; python3 ~/.cache/pai-1.0-work/browser/console_a11y.py "$(sed -n 's/^BROWSER: //p' /tmp/pai-walk.txt)" /tmp/pai-browser-out` | it exits 0; every check in `/tmp/pai-browser-out/results.json` is ok |
| WSL (Ubuntu) | no WSL here; detection only simulated | inside WSL Ubuntu, in a clone of the candidate on the Linux file system: `sudo apt-get install -y git tmux python3 && cd W && python3 -B tools/verify.py --work /tmp/pai-verify-wsl && for k in clean reverse interrupted; do python3 -B bin/pai.py guide --handoff $k || break; done` | verify reports 0 failed (quickstart may be SKIPPED without uv); the three guides end with "Done:" and exit 0; `pai doctor` shows `platform ... wsl` |
| macOS workbench | no macOS here; `/proc`-based process age is Linux-only | `brew install tmux git && cd W && python3 -B tools/verify.py --work /tmp/pai-verify-mac` | record every FAIL: the workbench claim needs 0 failed, and the 120 s not-accepting-input rule is documented as absent (backlog P2 6) unless ported |
| macOS core and `pai edit` | CI not run in this session | push the candidate branch; the CI job `macos-latest / Python 3.11` and `3.13` | both green |
| Native Windows core, both launchers, installed `pai edit` (056) | no Windows runner; CI not run | push the candidate branch; CI jobs `windows-latest / Python 3.11` and `3.13` (includes `tests.test_packaging.RealInstall.test_edit_through_the_installed_command` and `tests/test_portable_cards.py`) | both green |
| Native Windows, by hand | the same | in PowerShell, in a checkout: `py -3 -B tools\strict_unittest.py tests -v` then `py -3 -B bin\pai.py edit README.md --no-browser` (open the printed link, save once, Ctrl-Break) | the suite says OK (it includes `tests/test_workqueue.py`: `pai queue` on Windows); the edit saves |
| Native Windows `pai queue` by hand (0.5.0) | the same | in PowerShell: `py -3 -B bin\pai.py queue init q.json --from items.md --write; py -3 -B bin\pai.py queue mark q.json R2-001 done --evidence x; py -3 -B bin\pai.py queue restart q.json` (items.md holds lines like `R2-001. Title`) | each exits 0; `restart` lists R2-001 as done |
| zsh, fish, PowerShell PATH hints (059) | shells not installed here (bash passes: `tools/path_hint_check.py bash`) | `python3 -B "$W/tools/path_hint_check.py" zsh`, then `fish`, then `pwsh` (pwsh evaluates only the quoted folder string; your Path is not changed) | each prints `path_hint_check SHELL: OK` (exit 0); exit 2 means that shell is not installed |
| `uv tool install` quickstart step | uv not installed; no network | `cd W && python3 -B tools/verify.py --require-all --work /tmp/pai-verify-uv` on a machine with uv 0.13.9 | 0 skipped, 0 failed |
| Real agents | owner-only by rule | `docs/1.0/REAL-AGENT-SMOKE.md`, then `docs/1.0/REAL-AGENT-SWITCH.md` runs A and B | every box ticked |
