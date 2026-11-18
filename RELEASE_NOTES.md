# pai release notes

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
