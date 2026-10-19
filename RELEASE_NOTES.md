# pai release notes

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

What has actually been run for this version is listed here; everything else
is configured in CI and counts as tested only once a CI run passes.

- **Run locally (Linux):** the full suite, both builds and the bundle smoke on
  an Arch Linux host (Python 3.14), and in a Debian 13 container with Python
  3.11 (`/bin/sh` is dash), offline and unprivileged.
- **Configured in CI, not yet observed for this release:** Windows Server,
  macOS and Ubuntu runners with Python 3.11 and 3.13, including real Python 3.10
  refusal, cmd.exe, PowerShell and junction tests; Debian stable, Ubuntu
  24.04, Fedora and Alpine (musl) containers with their own `python3`.
- **Not tested:** Windows on a personal machine, ARM Linux, network shares,
  FAT/exFAT, other distributions and filesystems.

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
