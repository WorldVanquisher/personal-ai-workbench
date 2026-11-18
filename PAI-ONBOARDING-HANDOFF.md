---
status: draft
approved: false
task: PAI-onboarding (from GitHub download to first successful task)
project: personal-ai-workbench
created: 2026-11-17T07:30:00Z
---

# From download to a first task (draft, unapproved)

Same worktree as the PAI-008 dispatch follow-up, reviewed separately: this work is the diff
between the final dispatch snapshot and the worktree. Proposed PR title:

**feat: ship the workbench in the release archive, with a user-local `pai install` and a first-run path**

| State | Report |
| --- | --- |
| Implementation | Implemented locally; human review pending. Version raised to 0.3.0 (unreleased) |
| Validation | Linux only, fakes only: acceptance harness 24/24 from built archives in a fresh HOME; core suite 97 OK (6 Windows-only skipped), incl. bundle smoke 52/52; swarm suite 153 OK; headless 23/23 |
| macOS / native Windows | NOT run. Their paths are covered by `tests/test_install.py` and `tools/bundle_smoke.py`, which the existing CI runs on macos-latest and windows-latest, but CI has not run |
| Real agents | NOT run: no real Claude Code, Codex, GitHub or SSH. The owner-run real-agent smoke is `swarm/SMOKE.md` |
| Commit / push / release | None. No GitHub Release, no tag; docs do not link to a 0.3.0 asset |

## What was broken (audit)

- The release archive held only the core: `bin/pai.py` refused `run`, `status`, `task`, ... with
  "not in this release bundle", so the README's workbench quick start worked only from a checkout.
- Nothing put `pai` on PATH. INSTALL.md left an alias or symlink to the user, so a new terminal
  says `pai: command not found` although `bin/pai` exists. Reproduced in the harness (a fresh
  `bash -i` with a new HOME) before applying the printed fix.
- Bare `pai` was an argparse usage error; `--help` started with the core's flags.
- Native Windows: workbench commands would import `fcntl` and fail with a traceback.

## Benchmark (patterns borrowed, nothing vendored)

Read on 2026-11-17 (gh 2.104.0, mise 2026.11.9, uv 0.13.8). The two marked * I re-fetched myself;
the rest come from a research pass and were not re-checked one by one.
- Sidecar checksum `<sha256>  <file>` for `sha256sum -c` / `shasum -a 256 -c`:
  https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-unknown-linux-gnu.tar.gz.sha256 ,
  https://github.com/cli/cli/releases/download/v2.104.0/gh_2.104.0_checksums.txt
- Fail closed on verification, print the PATH line instead of editing profiles, end with the
  doctor command ("mise: run `mise doctor` to verify this is set up correctly"): https://mise.run *
- Profile edits by default with an opt-out (`UV_NO_MODIFY_PATH`), "either restart your shell or
  run", and a warning for shadowed commands: https://releases.astral.sh/installers/uv/latest/uv-installer.sh *
  (pai prints instead of editing, and keeps the shadowed-command warning)
- `~/.local/bin` default; install receipt for later upgrades:
  https://docs.astral.sh/uv/reference/installer/ , https://docs.astral.sh/uv/reference/storage/
- Doctor lists problems with a fix each, exits 1, has `--json`: https://mise.jdx.dev/cli/doctor.html ;
  errors that end in the command to run ("To re-authenticate, run: gh auth login -h ..."):
  https://raw.githubusercontent.com/cli/cli/trunk/pkg/cmd/auth/status/status.go
- Avoided: network update checks (gh, mise), skipping verification when a tool is missing
  (uv's sh installer), `irm | iex` as the main path.

## Changes

1. Archive: `release/allowlist.txt` adds the workbench runtime (`swarm/*.py`, `swarm/WORKBENCH.md`;
   not its tests, examples or the Markdown server); `build_bundle.py` requires every
   `swarm/*.py` to be listed and records the install entry point. 39 files.
2. `pai install` (`pai/install.py`, standard library, no process or network imports): checks the
   extracted files against SHA256SUMS, copies to `PREFIX/versions/<v>` through a temporary folder,
   checks the copy, switches one launcher with an atomic rename (`~/.local/bin/pai` symlink;
   Windows `%LOCALAPPDATA%\pai-workbench\bin\pai.cmd`, CRLF, marked), writes `receipt.json`.
   `--prefix DIR` (launcher in `DIR/bin`), `--bin-dir`, `--dry-run`. Same version again: kept.
   Damaged source, foreign launcher, or a failed copy: refused, previous launcher untouched,
   no half copy left. Prints the exact PATH line for bash/zsh/fish/sh (macOS bash:
   `~/.bash_profile`) or PowerShell; warns when another `pai` comes first. Never writes config,
   task notes, credentials or profiles.
3. `bin/pai.py`: bare `pai` prints doctor → init → task new → run --dry-run → run; `--help` starts
   with the same and lists every command; `task` routed; native Windows runs only the core and
   says the workbench needs WSL (exit 2).
4. `pai init`: says Enter keeps the bracketed value; suggests the only installed agent; a new
   project's task folder defaults to `~/.local/share/pai-workbench/tasks/PROJECT` (`-` for none);
   saving a new file defaults to yes (an update stays `[y/N]`); ends with the next commands for
   the saved setup. Non-interactive flags and existing config files are unchanged.
5. `pai doctor` first reports pai itself: archive intact, `pai` on PATH (with the exact fix),
   and the checkout/project (`-> pai init`). `pai run` names lanes that would start without a
   todo note. Dispatch recovery steps now include `--run RUN`. `pai server` says the Markdown
   server is not part of an installed archive.
6. `pai task new TITLE [--body] [--path] [--priority] [--name] [--write]` and `--from FILE`
   (import one Markdown file, keeping its front matter); preview by default, exclusive create,
   refusal to overwrite; `pai task list` shows the queue in `pai run` order.
7. Docs: README.md and README.zh-CN.md quick starts (only commands the harness ran, plus
   `pai trust` in prose), INSTALL.md (0.3.0, install, upgrade, Windows/WSL), WORKBENCH.md.
8. Tests: `tools/onboarding_acceptance.py` + `swarm/tests/test_onboarding.py`,
   `tests/test_install.py`, installer and first-run checks in `tools/bundle_smoke.py`. Fixture
   fixes: the fakes answer `--version` (doctor was waiting out a 25 s timeout on them); two
   interactive init tests and `test_jev_setup` follow the new prompts; one dispatch test waits
   for the fake Claude to be idle again.

## Acceptance record (fakes; Linux; scripted typing)

`python3 -B tools/onboarding_acceptance.py --work DIR`, fresh HOME, minimal PATH, fake
claude/codex/gh/ssh, local bare origin, isolated tmux socket. Verified download → first task
delivered: 5.4 s (machine time; reading and typing excluded). `pai init` asked 9 questions;
one needed typing (the agent: with both fakes installed there is no single suggestion); the
other 8 were Enter. All 24 checks passed, including: corrupted archive fails `sha256sum -c`;
`command not found` before and `pai 0.3.0` after the printed PATH line; reinstall kept; upgrade
0.3.0 → 0.3.1 with configuration and task note byte for byte unchanged; damaged bundle and
unwritable install folder both refused with 0.3.1 still answering; doctor naming the missing
PATH entry. The run used `--workers 1 --tmux-socket` (isolation only). Not exercised by the
harness: `pai trust`, `pai attach`, a real agent's trust prompt or login.

## Compatibility

Core commands (`init PROJECT --vault ...`, `doctor PROJECT`, `doctor --agents`, `check`,
`context`, `handoff`) and config files are unchanged; a checkout behaves as before. In an
archive, bare `pai doctor`/`pai init` are now the workbench's (as in a checkout). The bundle
smoke's core checks therefore use `doctor --agents`. Old `PAI_CONFIG`/`pai.local.toml` and
workbench projects load as they are.

## Limitations

- The `.sha256` sidecar proves integrity, not origin (no signatures); the archive check is the
  user's first step; `pai install` re-checks only the extracted files.
- No `pai self-update` or uninstall: upgrade = the same steps with the new archive; remove
  `PREFIX/versions/<old>` by hand. PATH is never edited, only explained.
- Windows and macOS installer paths are unrun; the workbench still needs tmux (WSL on Windows).
- Test runs used `TMPDIR=/var/tmp/...`: the `/tmp` per-user quota was exhausted by other
  sessions' files (not touched).

Patch (outside the repo): `~/.cache/pai-008-work/checkpoints/onboarding.patch` (sha256 prefix
0d536066b706353e), against `~/.cache/pai-008-work/dispatch-final-tree`. Next: human review;
owner runs SMOKE.md with real agents; CI on all three systems before any release. No proposals approved.
