# pai release notes

## 0.5.0 (not released yet: the owner tags `v0.5.0`)

Rounds of parallel lanes run from files, with handoffs and decisions you approve in Markdown or Obsidian.
Configuration files, task notes and run state from 0.4.x stay; read "Upgrading from 0.4.x" below.
Chinese: [docs/0.5.0/RELEASE-NOTES-DRAFT.zh-CN.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/0.5.0/RELEASE-NOTES-DRAFT.zh-CN.md).

### In one sentence

0.5.0 runs a whole round of parallel agent lanes from files: it opens the round, tells you which
lane waits for you, hands work and decisions back and forth through Markdown notes you can read in
any editor or in Obsidian, and collects the result for one PR that you merge.

### New

#### Rounds: `pai round`

One command family for a round of parallel Claude Code lanes. The round lives in `ROUND.json`,
`BOARD.md` and a coordinator log, all Markdown or JSON in the vault's `Agent Inbox/`.

- `pai round new PROJECT ROUND --lane L --prompt FILE --write` finds your main clone of the
  project, reads the base from its remote, makes one worktree per lane and fills each lane's
  prompt. Without `--write` it is a preview.
- `pai round status` shows one line per lane: session, window, state (running, idle, waiting on a
  dialog, a draft typed but not sent, queued, exited), worktree, last checkpoint, queue totals.
- `pai round launch LANE --paste-prompt --write` starts a lane and pastes its prompt only when that
  is safe; `pai round next` and `pai round inject` send a resume or a message the same way. Every
  send is recorded.
- `pai round collect` and `pai round integrate` gather the lanes' handoffs and rehearse the merge
  on a scratch copy.
- `pai decide answer` answers a decision sheet, approving exactly the text you read.

Full reference: [ROUND.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/ROUND.md). A complete two-lane example with its transcript:
[examples/two-lane-round/](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/examples/two-lane-round/README.md).

#### The prompt library: `pai prompt`

The prompts that start and steer workers are versioned files with declared placeholders
(`pai-prompts/2`; files in the older `/1` format are still read). `pai prompt list` shows them,
`pai prompt lint` checks them, and `pai prompt init --dest DIR --write` installs them into your
vault; `pai prompt diff --dest DIR` shows what an upgrade would change. Only text inside the
managed markers is ever rewritten. The library is written in Chinese (owner decision PRM-D1). See
[PROMPTS.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/PROMPTS.md).

#### Handoff v2

One handoff format, `pai-handoff/2`, with a State table, per-item rows and totals checked against
the queue. `pai handoff seal PROJECT FILE` checks a handoff, prints the sha256 of its body and files
it in the round's inbox. You approve exactly that version with
`pai handoff approve PROJECT DIR --expect-sha SHA`, or from Obsidian with `--from-vault`; a body
edited after sealing is marked stale and cannot be approved. See [HANDOFF-V2.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/HANDOFF-V2.md).

#### Vault notes, with Obsidian as an optional console

PAI writes the round's state as plain Markdown notes with front matter: a home page, one note per
lane and one per decision. Generated notes carry managed markers; PAI never rewrites a note you
wrote or edited. `pai vault lint` checks the notes, `pai vault wait FILE --sha SHA --timeout 120`
waits until a synced copy has arrived. Obsidian is optional: every note reads the same in any
editor. See [OBSIDIAN.md](docs/OBSIDIAN.md) and [FRONTMATTER.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/FRONTMATTER.md).

#### Lane instructions in `CLAUDE.local.md` (layer L2)

`pai worktree init PATH --target claude-local --write` writes a lane's own instructions into a
git-ignored `CLAUDE.local.md` in its worktree, so the worktree stays clean. A real Claude Code
session reads it (mastermind real run, Claude Code 2.1.330: 10 of 10 checks). `pai context
--worktree PATH` shows what a lane will read. Why the layers matter:
[AGENTS-LAYERS.md](docs/AGENTS-LAYERS.md).

#### The JEV ledger

When JEV is enabled, every request is logged with its token counts. `pai jev usage` sums them for
a time range (`--since`, `--until`), split by kind of request. JEV stays optional and advisory: it
never overrides a check.

#### Also new since 0.4.0

Continuing a lane in a fresh session (`pai recover --continue LANE`), transfer packets between
machines, numbered work queues (`pai queue`), a guard that refuses shell writes outside a task's
paths, and CI that runs the workbench suite and the documentation check. Details:
[CHANGELOG-CANDIDATE.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/0.5.0/CHANGELOG-CANDIDATE.md).

### Upgrading from 0.4.x

1. **Install** the same way as before with the new version: `uv tool install` of tag `v0.5.0`, or
   the new archive with `bin/pai install`. Configuration, task notes and run state stay.
   Rollback: install 0.4.x the same way ([REFRESH.md](docs/REFRESH.md#a-upgrade-pai)).
2. **Scripts that read `pai status --json`**: it is now an object (`pai_status_v1`); the lane rows
   are under `lanes`. Replace `.[0]` with `.lanes[0]`. See [MIGRATION.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/1.0/MIGRATION.md).
3. **`pai doctor` exit code**: 1 now means only an essential gap (`gh` missing is no longer one).
   Before `pai init` has chosen an agent, a missing claude or codex is listed under
   `FOR YOUR FIRST RUN` and is not a gap either; once chosen, that agent is essential. In
   `pai doctor --json` those rows carry `need: later`.
4. **Headless workers now default to `acceptEdits`** (owner decision D-R6-1 = A): a headless worker
   (`swarm/pai_swarm.py run`) can write the files its task asks for, and the guard still refuses
   shell writes outside the task's `paths:`. `manual` stays available with
   `--permission-mode manual`, for a session with a person present; headless, it blocks even the
   task's own writes ([PERMISSION-MODES.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/0.5.0/PERMISSION-MODES.md)).
5. **If your repository has both `CLAUDE.md` and `AGENTS.md`**: Claude Code reads only `CLAUDE.md`.
   Put the line `@AGENTS.md` into `CLAUDE.md` so both agents read the same rules
   ([AGENTS-LAYERS.md](docs/AGENTS-LAYERS.md#l0-the-constitution)).
6. **Prompts in your vault**: run `pai prompt diff --dest DIR` first, then `pai prompt init --dest DIR --write`.
   Your own edits outside the managed markers stay.
7. **tmux below 3.8** (Ubuntu and WSL ship 3.6): `pai round` pastes only into a pane running
   Claude Code or Codex ([FIRST-USE.md § 1](docs/FIRST-USE.md#1-what-you-need)).

### Tested, and not yet

- **Tested on the merged candidate:** `tools/verify.py` 9 passed, 0 failed, 5 skipped (the skipped
  steps need uv, zsh, fish, PowerShell or an offline setuptools), on WSL Ubuntu with tmux 3.6
  (reported-by-mastermind) and on Arch Linux with tmux 3.8 (real run). The round tests that had
  failed on tmux 3.6 pass there now. The package step (wheel, upgrade from 0.4.0, interrupted
  install, uninstall) passed 36 of 36 on Arch Linux, offline; the core tests (`tests/`) passed on
  Python 3.11, the oldest version supported (core lane, before its last wording fixes).
- **Real runs** (reported-by-mastermind, WSL): a headless Claude Code smoke with the default
  `acceptEdits`, 7 of 7, with the write outside the task's paths refused; the `CLAUDE.local.md`
  loading check, 10 of 10; `pai jev check`. Prompt experiment E3 (haiku, n = 16 runs per opening;
  score = mean of the first-reply contract checks and four task checks): the opening with a filled
  example scored 0.994 (sd 0.023), the one without 0.955 (sd 0.081). That holds for these 16 runs
  each; it is not a measured improvement.
- **A new user's walk:** a session with no background installed the 0.5.0 candidate archive in an
  empty home folder and followed the Chinese FIRST-USE through section 5 (reported-by-mastermind);
  its 19 findings were fixed in the manuals, `pai doctor` and `pai --help`, and the same walk from
  the fixed archive, with no agent installed, matched the pages (real run).
- **Not yet (owner):** CI green on the release commit; the package step on WSL; the workbench on
  macOS; a real Codex lane; a whole round driven by `pai round` against real agents.

Platform detail: [FIRST-USE-PLATFORMS.md](docs/FIRST-USE-PLATFORMS.md).

## 0.4.0 (released 2026-11-18; tag v0.4.0 at b02acec)

pai in your browser, and long prompts that arrive whole. Configuration files, task notes and
every 0.3.0 command are unchanged.

### Install

- **uv:** `uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.4.0`
  (tag `v0.4.0`, pushed 2026-11-18). The package now also carries the
  browser pages (`pai/_workbench/mdserver`).
- **Archives:** `pai-workbench-0.4.0.tar.gz` / `.zip` with their `.sha256`; 55 files, now with
  `QUICKSTART.md`, `QUICKSTART.zh-CN.md`, the editor and the console.

### New (experimental)

- **`pai web`**: one run in your browser on 127.0.0.1, behind a sign-in link that works once.
  Each lane's screen live with its colours (select to copy); live typing into a lane (keys,
  Ctrl/Alt combinations, arrows, input methods); clipboard pastes and a message box sent as one
  piece; a shell in each lane's worktree (tmux session `<run session>-shell`); the task folder
  and lane worktrees in the Markdown editor. The browser names a lane, never a tmux pane.
- **`pai paste LANE [FILE|-]`**: a long or multi-line message into a lane's agent through a
  tmux buffer and a bracketed paste (no file on disk, no shell); Enter only with `--enter`.
  Shared with `pai web`: refused while the agent's folder-trust prompt shows; control
  characters removed (no early end of the paste); a final newline is never Enter; several
  lines only to a program that tmux says asked for bracketed paste, otherwise only after
  `--allow-unbracketed` or a confirmation (tmux 3.8 can tell, 3.5a cannot); 1 MB at most.
- **`pai edit FILE.md`** (from PAI-009): one Markdown file in a browser editor with a live
  preview and confirmed saves that never overwrite a newer file. Runs on native Windows too.
- **`QUICKSTART.md` / `QUICKSTART.zh-CN.md`**: install, doctor, first task, `pai web`,
  `pai edit`, upgrade and uninstall, in English and Chinese.

### Changed

- The editor's server takes its port exclusively on Windows (no `SO_REUSEADDR`), saves report
  a write error instead of dropping the connection, and accepts a file at a drive or
  file-system root.
- `pai server` / `pai setup --experimental server` (the long-running Markdown server) stay
  checkout-only: an archive or uv install says so and changes nothing, although the editor
  module now ships for `pai edit` and `pai web`.

### Tested environments

- **CI: not run yet for 0.4.0.** It runs when the branch is pushed: the unit tests, both
  archives with a `pai edit` session in the bundle smoke (Ctrl-Break on Windows), and the
  uv-installed `pai` with a full `pai edit` session (`pai.exe` on Windows), on Ubuntu, macOS
  and Windows with Python 3.11 and 3.13, plus Debian, Ubuntu, Fedora and Alpine containers.
  Until it has passed, macOS and native Windows are unverified for this release.
- **Linux host (Arch, Python 3.14, tmux 3.8), fake agents, isolated tmux:** core suite 108 OK
  (7 skipped: 6 Windows-only, 1 needs an installed `pai`); workbench suite 166 OK (13 new:
  paste byte for byte against a recording terminal program, `pai web` and `pai paste` end to
  end, `pai edit`); headless run tests 23/23; bundle smoke 57/57 on both archives;
  new-user acceptance from the archive 32/32 in a folder with spaces and non-ASCII characters
  (now including `pai paste`, `pai web` and `pai edit` from the installed copy); the
  QUICKSTART walkthrough 17 steps, none stuck; CI's uv step run by hand (wheel, `uv tool
  install`, RECORD check of 50 files, a `pai edit` session through the installed `pai`).
- **Clean Debian 13 container** (Python 3.13.5, tmux 3.5a, uv 0.13.9, unprivileged user):
  the paste, console and editor tests 21/21 on a tmux that cannot report bracketed paste;
  the core suite; `uv tool install` of a local stand-in for the `v0.4.0` tag through a first
  delivered task and the browser tools, 25/25; the QUICKSTART walkthrough, none stuck.
- **Real browser** (headless Chromium 156 in a Debian 12 container, Python 3.11, tmux 3.3a):
  `pai edit` 17/17 and `pai web` 26/26: one-time link; live typing, an input-method commit and
  Ctrl-C reach the pane; a clipboard paste is held until Enter; several lines ask first when
  tmux cannot tell; a trust prompt refuses Paste and is never answered; a lane shell with
  colours; the task note opens in the editor; 390 px phone width at zoom 1 without
  horizontal scrolling; no script errors.
- **Not tested:** real Claude Code or Codex with `pai paste` / `pai web`; a desktop browser on
  macOS or Windows; native Windows and macOS (CI pending); WSL by hand.

## 0.3.0 (prerelease)

The workbench (`pai run`, `status`, `task`, ...) now ships with the core, and pai installs in
one line with uv or from the archives. Configuration files and core commands are unchanged
from 0.2.0.

### Install

- **uv:** `uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.3.0`
  (new `pyproject.toml`; console script `pai`; the workbench inside the package as
  `pai/_workbench`). Upgrade with the same line and a newer tag; `uv tool uninstall
  pai-workbench` removes it and leaves configuration and task notes.
- **Archives:** `pai-workbench-0.3.0.tar.gz` / `.zip`, each with a `.sha256`. They now include
  the workbench runtime (`swarm/*.py`, `swarm/WORKBENCH.md`; not its tests, examples or the
  Markdown server). New `bin/pai install`: re-checks the extracted files against
  `SHA256SUMS`, copies them to `~/.local/share/pai-workbench/versions/0.3.0` and switches the
  `~/.local/bin/pai` launcher in one atomic step. It prints the PATH line instead of editing
  shell profiles. A failed upgrade leaves the previous version working.

### New

- **First run:** bare `pai` and `pai --help` show doctor → init → task new → run --dry-run → run.
  `pai init` explains that Enter keeps each default, suggests the only installed agent, offers
  a managed task folder and ends with the next commands. `pai task new` previews a task note
  (`--write` saves it, never overwriting; `--from FILE` imports one Markdown file).
  `pai task list` shows the queue.
- **`pai doctor`** also checks pai itself: the archive's `SHA256SUMS` or the package's RECORD,
  whether `pai` is on PATH (with the exact fix), and whether this is a configured checkout.
- **Native Windows** runs the core; workbench commands exit 2 and point to WSL.

### Fixed

- **Dispatch after folder trust** (PAI-008). Claude counts as ready when its own session record
  says idle, so it no longer stays `pending` after trust. Every lane must stay ready, with no
  trust prompt on screen, for 3 seconds before anything is typed, and Enter is never pressed
  when a trust prompt appeared after the paste. `delivered` now requires the task in the
  agent's own transcript (Claude session log, Codex rollout). Anything else is `unknown`, with
  recovery steps, and is never resent automatically. `pai status` reports process, trust,
  input, dispatch and Remote Control separately.
- POSIX PATH advice used the host's path separator when computed on Windows.

### Tested environments

- **CI** ([run 44233348494](https://github.com/WorldVanquisher/personal-ai-workbench/actions/runs/44233348494),
  commit `076bf57`; the release commit adds only documentation): all 10 jobs passed on Ubuntu,
  macOS and Windows (Python 3.11 and 3.13) and in Debian, Ubuntu, Fedora and Alpine containers.
  The jobs ran the unit tests, both archive builds (byte-identical rebuild), the bundle smoke
  with `pai install`, and a wheel installed with `uv tool install` (version, doctor's RECORD
  check, first screen, workbench loading; on Windows the WSL messages).
- **Linux (Arch host, Python 3.14), fake agents and an isolated tmux server:** the core suite,
  the workbench suite (153 tests, 17 of them new for dispatch) and the headless run tests
  (23/23). The new-user acceptance (`tools/onboarding_acceptance.py`) ran from the built
  archive and from `uv tool install` of the release tag in a clean Debian 13 container, up to a
  first task delivered to a fake agent.
- **Not tested:** real Claude Code, Codex, GitHub or SSH; the workbench's run and dispatch path
  on macOS; `pai install` and the uv install run by hand on a Windows PC (CI only).

## 0.2.0

Portable release for **Windows, macOS and Linux**, now under the **MIT
License**. Same commands and configuration format as 0.1.0: doctor → init →
context → handoff new → handoff list.

### New

- **Windows support.** `bin/pai.cmd` runs from PowerShell and cmd.exe. It
  finds Python through the py launcher (`py -3`), then `python.exe`, then
  `python3.exe`, searching PATH only, never the current folder. `PAI_PYTHON`
  selects an interpreter explicitly.
- **ZIP archive** for Windows beside the tar.gz for Linux and macOS. Both are
  built from one allowlist, hold byte-identical files (including `MANIFEST.json`
  and `SHA256SUMS`), and each has a `.sha256` sidecar.
- **`bin/pai.py`**, a script entry point for any platform
  (`python3 bin/pai.py …`, `py -3 bin\pai.py …`). It passes arguments exactly,
  without cmd.exe in between.
- **`LICENSE`** (MIT, copyright Sirui Liu), named in `MANIFEST.json`.

### Changed

- **Launchers never import code from the working directory.** 0.1.0's
  `bin/pai` ran `python3 -m pai`, which put the current folder first on the
  import path: running it from a folder containing another `pai/` package ran
  that package. Both launchers now run `bin/pai.py`, which pins the bundle.
- `bin/pai` follows symlinks to itself (for example from `~/.local/bin`), falls
  back from `python3` to `python`, and reports a missing interpreter or a wrong
  `PAI_PYTHON` with an actionable message.
- **Path containment** compares path components (case-insensitively on
  Windows) instead of string prefixes; paths on different drives are never
  inside each other.
- **Windows paths:** `--repo` and `--vault` need a drive or UNC share (a
  rooted `\folder` is refused), and registered relative paths reject drive
  forms (`C:notes`) and alternate data streams (`note.md:stream`).
- **Handoff discovery on Windows** uses path-based checks: symlinks, junctions
  and other reparse points are refused at each Inbox component, task folder and
  file, and a file or folder swapped between check and open (device and file id)
  is refused. Linux and macOS keep the descriptor-relative no-follow reads.
  Limits, ordering, labels and diagnostics are unchanged.
- **CRLF:** notes and handoffs saved with Windows line endings keep their front
  matter and headings. Configurations and handoffs are written with LF on every
  system, so synchronized files are identical across devices.
- **Permissions are described honestly:** POSIX mode 0600 on Linux and macOS;
  on Windows `init` says the file inherits its folder's ACLs.
- On Windows, piped or redirected output is UTF-8.
- `doctor` shows the interpreter path and platform.
- Release builds refuse to overwrite any existing artifact and check line
  endings (`.cmd` CRLF, all else LF).

### Upgrading from 0.1.0

Extract 0.2.0 into a new folder; keep your configuration where `PAI_CONFIG`
points (or move the old bundle's `pai.local.toml` there once); run `doctor`.
The 0.1.0 archive and its checksum are unchanged. See `INSTALL.md`.

### Tested environments

The evidence below distinguishes local Linux checks from the passing PR CI
run. It does not establish universal distribution, architecture or filesystem
compatibility.

- **Run locally (Linux):** the full suite, both builds and the bundle smoke on
  an Arch Linux host (Python 3.14), and in a Debian 13 container with Python
  3.11 (`/bin/sh` is dash), offline and unprivileged.
- **Verified PR CI:** PR #6 passed all **10 jobs** in
  [run 39325458683](https://github.com/WorldVanquisher/personal-ai-workbench/actions/runs/39325458683),
  testing PR head `b8a9b0f33ced4639ba5d64044bd8419dc1e2f923`.
  Passing environments were Windows, macOS and Ubuntu runners with Python
  **3.11 and 3.13**, plus Debian, Ubuntu 24.04, Fedora and Alpine userspaces.
  The workflow checks unit/synthetic tests, release builds, reproducibility
  and extracted-bundle smoke. Windows also exercises cmd.exe, PowerShell and
  explicit Python 3.10 refusal.
- **Post-merge status:** PR #6 merged as
  `49c2e71e884d82d81e5bd465dde6429b8400cc0b`. The
  [post-merge main run 39325609161](https://github.com/WorldVanquisher/personal-ai-workbench/actions/runs/39325609161)
  was still running when this evidence was supplied; a successful result is
  not established here. The passing PR run does not establish that 0.2.0 has
  been tagged or published.
- **Not verified:** installation on the user's actual Windows and Mac
  devices; ARM Linux, network shares, FAT/exFAT, and distributions and
  filesystems beyond those covered by the evidence above.

## 0.1.0

First portable release of the personal AI workflow tool. Download one archive,
extract it anywhere on Linux or macOS, point it at your project and notes, and
use it to prepare agent sessions and record their results.

### What it does

| Command | Purpose |
| --- | --- |
| `pai init` | Creates the local configuration from paths you give it. It previews by default, writes only with `--write`, and never overwrites |
| `pai doctor [PROJECT] [--agents]` | Read-only checks: Python version, bundle integrity (`SHA256SUMS`), configuration, project folders and sources; with `--agents`, which agent programs are on PATH (looked up, never started) |
| `pai check PROJECT` | Lists each registered source and its status |
| `pai context PROJECT --task "…"` | Task context in Markdown, within a size budget. Human decisions and limitations come first, proposals are kept separate and unapproved, and anything missing or omitted is named |
| `pai handoff new PROJECT --task-id ID …` | A new draft handoff in the vault inbox: never overwrites; implementation, validation, commit, merge and deployment are separate fields that stay UNKNOWN unless you state them |

### Installation and migration

See `INSTALL.md`:
- verify the checksum;
- extract into any directory, including one whose path has spaces;
- run `bin/pai init`, then `bin/pai doctor`.

**Updates** go into a new directory; keep your configuration with
`PAI_CONFIG`. **Another machine** needs its own `pai init` with its own paths.

### Contents

The tool, an example configuration, the handoff template, `SESSION_START.md`,
`INSTALL.md`, a `bin/pai` launcher, `MANIFEST.json` and `SHA256SUMS`.

### Safety and limits

- **No network, no model, no commands:** pai makes no network or model calls
  and runs no other programs. It reads only the files you register, refuses
  symlinks that leave a registered folder, and never reads dotfiles or files
  named like secrets.
- **Not authority:** context output is not authorization; it never approves a
  proposal or marks work complete.
- **External prerequisites:** agents and their logins, Syncthing, the vault
  and the repositories are not bundled. pai does not install, configure,
  launch or authenticate them.
- **Context selection is plain word matching** (no AI ranking). Important
  material may sit outside the budget; it is then listed as omitted.
- **Tests and platforms:**
  - Linux: tested locally.
  - macOS: covered only by the CI configuration. **No real-Mac run has been
    performed** for this release unless a CI run or a person on a Mac
    confirms it.
  - Windows: not supported.

### Verifying a download

```sh
sha256sum -c pai-workbench-0.1.0.tar.gz.sha256    # macOS: shasum -a 256 -c …
tar -xzf pai-workbench-0.1.0.tar.gz
pai-workbench-0.1.0/bin/pai doctor                 # bundle files are checked against SHA256SUMS
```
