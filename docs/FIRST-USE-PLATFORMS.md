# Where PAI has really run

English. Companion to [FIRST-USE.md](FIRST-USE.md). Last checked: 2026-11-22, pai 0.4.0
(main `c8bc51e`).

Each cell says how it is known: **ran** (someone ran it on that platform, with the date and who),
**CI** (a job in `.github/workflows/ci.yml` covers it; that is only as good as the last green run
on the commit you use), or **inferred** (nobody ran it; it follows from the code). Nothing
here is stronger than its source.

## Summary

| | Install (archive / package) | Core: `pai context`, `pai handoff`, `pai queue`, `pai edit` | Workbench with fake agents: `pai guide`, `pai run`, `status`, `trust`, `resume`, `pr check`, `cleanup` | Workbench with a real Claude Code |
| --- | --- | --- | --- | --- |
| **Linux** (Arch, x86_64) | **ran** 2026-11-22 (docs lane): archive and pip-into-venv, fresh HOME | **ran** (lane tests); CI (ubuntu-latest) | **ran** 2026-11-22 (docs lane): first use end to end, a trust prompt answered, `pr check` passed | not run on Arch |
| **Linux** (Debian, Ubuntu, Fedora, Alpine) | CI (`userspace / …` containers: tests, build, bundle smoke) | CI | not in CI (CI runs `tests/` only, not `swarm/tests`) | not run |
| **WSL** (Ubuntu 24.04) | **ran** 2026-11-22 (owner): `tools/verify.py` | **ran** (owner): core 125 OK | **ran** (owner): workbench suite 316 tests, 1 failure and 1 error, both from the test setup, not pai (see below) | **ran** 2026-11-22 (owner): headless `claude -p` smoke, haiku, 8 s, $0.0038 |
| **macOS** | CI (macos-latest: archives, bundle smoke, `uv tool install`) | CI | **inferred only**: needs tmux from Homebrew; the `/proc`-based process checks are Linux-only | not run |
| **Native Windows** | CI (windows-latest: zip, `pai.cmd` from cmd.exe and PowerShell, `uv tool install`) | CI | **refused by design**: workbench commands exit 2 and point to WSL | n/a: use WSL |

## The evidence behind each "ran"

- **Linux, docs lane, 2026-11-22.** real run; Arch Linux (kernel 7.2.14), Python 3.14.9, git 2.56.0,
  tmux 3.8, not WSL; fake agents from `swarm/tests`; an isolated tmux server.
  - R6-DOC-001 (archive): `bin/pai install` → `pai doctor` → `pai init` → `pai guide` (5.4 s) → `pai task new`
    → `pai run --workers 1` → `pai status` → `pai pr plan` → `pai cleanup`.
  - R6-DOC-002 (pip into a venv; uv not installed): the same path plus a folder-trust prompt, `pai resume`
    and `pai pr check` (check PASSED).
  - Both were run on the lane tree, so they need a rerun on merged main.
- **WSL, owner, 2026-11-22.** real run; WSL Ubuntu 24.04 at `c8bc51e`.
  - `tools/verify.py`: 7 passed, 2 failed, 4 skipped. Both failures come from the test setup:
    - the onboarding "plain bash" test picks up Ubuntu's command-not-found hook;
    - `test_wtpr` does not isolate the git identity. With `GIT_AUTHOR_*` and `GIT_COMMITTER_*` set it is 21/21.
  - The skips are for missing uv, zsh, fish and pwsh.
  - A real `claude -p` headless smoke passed (Claude Code 2.1.330).
  - Source: the owner's notes for this round (not rerun by the docs lane).
- **CI.** The workflow covers Ubuntu, macOS and Windows with Python 3.11 and 3.13, and Debian, Ubuntu,
  Fedora and Alpine containers. The last run cited in RELEASE_NOTES.md with a link is for 0.3.0
  (run 44233348494, all 10 jobs passed). RELEASE_NOTES.md says CI had not run yet for 0.4.0
  when it was written. Check the Actions tab for the commit you install.

## What this means for a first-time user

- **Linux or WSL**: everything in FIRST-USE.md applies. On WSL, keep your checkout on the Linux file
  system (`~/code/...`), not under `/mnt/c`.
- **macOS**: install and the core are covered by CI. The workbench (`pai run` and friends) has never been
  run on a Mac. Expect to need `brew install tmux git`, and report what you see.
- **Native Windows**: install pai inside WSL for the workbench. On Windows itself, `pai edit`, `pai context`,
  `pai handoff`, `pai queue` and `pai doctor --agents` run.

## Still needed, and who can do it

| Gap | Who | Command |
| --- | --- | --- |
| Workbench on macOS | owner (needs a Mac) | `brew install tmux git && python3 -B tools/verify.py --work /tmp/pai-verify-mac` |
| First use from the archive on WSL | owner | in WSL: FIRST-USE.md steps 1–6 in a fresh `HOME=$(mktemp -d)` |
| CI green on the release candidate | owner (needs a push) | push the branch; all `CI` jobs green |
| Real Claude Code through `pai run` (interactive, trust prompt answered by a person) | owner | `docs/1.0/REAL-AGENT-SMOKE.md` |
