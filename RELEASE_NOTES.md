# pai 0.1.0 release notes

First portable release of the personal AI workflow tool. Download one archive,
extract it anywhere on Linux or macOS, point it at your project and notes, and
use it to prepare agent sessions and record their results.

## What it does

| Command | Purpose |
| --- | --- |
| `pai init` | Creates the local configuration from paths you give it. It previews by default, writes only with `--write`, and never overwrites |
| `pai doctor [PROJECT] [--agents]` | Read-only checks: Python version, bundle integrity (`SHA256SUMS`), configuration, project folders and sources; with `--agents`, which agent programs are on PATH (looked up, never started) |
| `pai check PROJECT` | Lists each registered source and its status |
| `pai context PROJECT --task "…"` | Task context in Markdown, within a size budget. Human decisions and limitations come first, proposals are kept separate and unapproved, and anything missing or omitted is named |
| `pai handoff new PROJECT --task-id ID …` | A new draft handoff in the vault inbox: never overwrites; implementation, validation, commit, merge and deployment are separate fields that stay UNKNOWN unless you state them |

## Installation and migration

See `INSTALL.md`:
- verify the checksum;
- extract into any directory, including one whose path has spaces;
- run `bin/pai init`, then `bin/pai doctor`.

**Updates** go into a new directory; keep your configuration with
`PAI_CONFIG`. **Another machine** needs its own `pai init` with its own paths.

## Contents

The tool, an example configuration, the handoff template, `SESSION_START.md`,
`INSTALL.md`, a `bin/pai` launcher, `MANIFEST.json` and `SHA256SUMS`.

## Safety and limits

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

## Verifying a download

```sh
sha256sum -c pai-workbench-0.1.0.tar.gz.sha256    # macOS: shasum -a 256 -c …
tar -xzf pai-workbench-0.1.0.tar.gz
pai-workbench-0.1.0/bin/pai doctor                 # bundle files are checked against SHA256SUMS
```
