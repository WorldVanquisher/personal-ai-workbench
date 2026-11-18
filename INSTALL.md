# Install and use pai

pai is a small command-line tool. It reads project files you register and
prints the context a coding agent needs for a task. At the end of the task it
writes a draft handoff note into your notes folder.

**Requirements:** Windows, macOS or Linux with **Python 3.11 or newer**. pai
has no runtime dependencies and no background service, needs no administrator
or root rights, never downloads Python and never edits PATH or a shell profile
(`pai install` prints the line to add instead). The workbench (`pai run` and
friends, see README.md) also needs git and tmux: Linux, macOS or WSL.
Check with `python3 --version` (Linux, macOS) or `py -3 --version` (Windows).

**Not included (external prerequisites):** your coding agent (for example
Claude Code or Codex) and its login, your Markdown vault (for example
Obsidian), file synchronization (for example Syncthing) and your project
repositories. pai never installs, configures, launches or logs in to any of
these.

## With uv (one line)

If you have [uv](https://docs.astral.sh/uv/) and git (Linux, macOS or WSL):

```sh
uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.3.0
pai --version                                   # pai 0.3.0
```

uv builds the package from that tag and puts `pai` in its bin folder (usually
`~/.local/bin`); if that folder is not on PATH it prints the `export PATH=…` line to run (or
use `uv tool update-shell`), then open a new terminal. uv uses a Python 3.11+ it finds (by
default it downloads one when there is none). The package holds the same code as the archives, workbench included. Its
own configuration lives in `~/.config/pai/config.toml` (Windows: `%LOCALAPPDATA%\pai\config.toml`)
unless `--config` or `PAI_CONFIG` says otherwise, and the workbench's in
`~/.config/pai-workbench/`, so reinstalling never touches them. `pai doctor` checks the
installed files against the package's RECORD.

- Upgrade: the same command with the new version (`@v0.3.1`); `--force` reinstalls.
- Remove: `uv tool uninstall pai-workbench`; configuration and task notes stay.
- `pai install` is for the archives below; in a uv install it says to use uv instead.
- Native Windows: the package installs `pai.exe` with the core only (CI installs it this way
  on windows-latest); the workbench needs WSL, as below.

Without uv, use the archives:

In the examples, replace the placeholders: `<PROJECT>` is a short key such as
`flh`; paths in capitals (`/ABS/PATH/TO/REPO`, `D:\PATH\TO\VAULT`) are absolute
paths on this device. Paths after `--vault` (`--memory`, `--inbox`,
`--markdown-root`, `--project-note`) are relative to the vault, and forward
slashes work on every system. The inbox folder must already exist: pai creates
no vault folders.

## Linux and macOS

```sh
# 1. Put pai-workbench-0.3.0.tar.gz and its .sha256 into one folder; verify.
#    Download: https://github.com/WorldVanquisher/personal-ai-workbench/releases/tag/v0.3.0
#    (or build both from a checkout: `python3 -B tools/build_bundle.py --out dist`)
sha256sum -c pai-workbench-0.3.0.tar.gz.sha256            # macOS: shasum -a 256 -c …
# 2. Extract, then install: a verified copy in ~/.local/share/pai-workbench/versions/0.3.0
#    and the launcher ~/.local/bin/pai (--prefix DIR puts both under DIR instead).
tar -xzf pai-workbench-0.3.0.tar.gz
pai-workbench-0.3.0/bin/pai install
#    If it says ~/.local/bin is not on PATH, run the line it prints, e.g. for bash:
#    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc     and open a new terminal.
PAI=pai
# 3. Choose where this device's configuration lives (outside the bundle; see below).
mkdir -p "$HOME/.config/pai" && export PAI_CONFIG="$HOME/.config/pai/config.toml"
# 4. Check Python and the bundle ("no configuration" is expected until step 6).
"$PAI" doctor --agents
# 5. Preview the configuration: nothing is written.
"$PAI" init <PROJECT> --repo "/ABS/PATH/TO/REPO" --vault "/ABS/PATH/TO/VAULT" \
  --memory "Agent Inbox/PAI-001-EXAMPLE" --inbox "Agent Inbox" \
  --markdown-root "30 Projects" --project-note "30 Projects/Project.md"
# 6. Write it, exactly as previewed (never overwrites; mode 0600).
"$PAI" init <PROJECT> …same arguments… --write
# 7. Use it.
"$PAI" context <PROJECT> --task "what you are about to do"
"$PAI" handoff new <PROJECT> --task-id TASK-1 --summary "what happened" --evidence "what you checked"
"$PAI" handoff list <PROJECT>
```

Add the `export PAI_CONFIG=…` line to your shell profile (`~/.bashrc`,
`~/.zshrc`) as well. Without `pai install`, any extracted bundle still works
by its full path (`…/pai-workbench-0.3.0/bin/pai`), and a symlink to `bin/pai`
works too: the launcher follows the link to its bundle.

## Windows (PowerShell)

Native Windows runs the core (context and handoffs). The workbench needs tmux:
for `pai run` and friends, install pai inside WSL with the Linux steps above.
For 0.3.0 the CI checks that cover these steps pass on windows-latest (`tools/bundle_smoke.py`
with `pai install --prefix`, and the launchers from cmd.exe and PowerShell); a person has not
yet run them by hand on a Windows PC.

```powershell
# 1. Put pai-workbench-0.3.0.zip and its .sha256 into one folder; verify.
$want = (Get-Content .\pai-workbench-0.3.0.zip.sha256 -Raw).Split(" ")[0]
if ((Get-FileHash -Algorithm SHA256 .\pai-workbench-0.3.0.zip).Hash -ne $want) { throw "checksum mismatch: do not use this file" }
# 2. Extract, then install: a verified copy in %LOCALAPPDATA%\pai-workbench\versions\0.3.0
#    and the launcher %LOCALAPPDATA%\pai-workbench\bin\pai.cmd. If it says that folder is not
#    on PATH, run the PowerShell line it prints and open a new terminal window.
Expand-Archive .\pai-workbench-0.3.0.zip -DestinationPath .
& .\pai-workbench-0.3.0\bin\pai.cmd install
$pai = "$env:LOCALAPPDATA\pai-workbench\bin\pai.cmd"
# 3. Choose where this device's configuration lives, for this and future sessions.
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\pai" | Out-Null
$env:PAI_CONFIG = "$env:LOCALAPPDATA\pai\config.toml"
[Environment]::SetEnvironmentVariable("PAI_CONFIG", $env:PAI_CONFIG, "User")
# 4. Check Python and the bundle ("no configuration" is expected until step 6).
& $pai doctor --agents
# 5. Preview the configuration: nothing is written.
& $pai init <PROJECT> --repo "D:\PATH\TO\REPO" --vault "D:\PATH\TO\VAULT" `
  --memory "Agent Inbox/PAI-001-EXAMPLE" --inbox "Agent Inbox" `
  --markdown-root "30 Projects" --project-note "30 Projects/Project.md"
# 6. Write it, exactly as previewed (never overwrites).
& $pai init <PROJECT> …same arguments… --write
# 7. Use it.
& $pai context <PROJECT> --task "what you are about to do"
& $pai handoff new <PROJECT> --task-id TASK-1 --summary "what happened" --evidence "what you checked"
& $pai handoff list <PROJECT>
```

From **cmd.exe** the same launcher works:
`"%LOCALAPPDATA%\pai-workbench\bin\pai.cmd" doctor --agents`, with `^`
for line continuation and `set PAI_CONFIG=…` (or `setx PAI_CONFIG …` for future
sessions). For a short `pai` command in PowerShell, add
`function pai { & "$env:LOCALAPPDATA\pai-workbench\bin\pai.cmd" @args }`
to your `$PROFILE`.

**Quoting on Windows.** `pai.cmd` is a batch file, so cmd.exe reads its
command line. Quote any argument that contains spaces or `& | < > ^ ( )`, and
avoid `%` and `"` inside arguments (cmd.exe expands `%NAME%` and pairs quotes).
PowerShell adds quotes only around arguments that contain spaces, so
`--task "a&b"` reaches cmd.exe unquoted and breaks. When text needs those
characters, bypass cmd.exe with the script entry point, which receives
arguments exactly:
`py -3 "$env:LOCALAPPDATA\pai-workbench\versions\0.3.0\bin\pai.py" context <PROJECT> --task "a&b 100%"`.

**Output encoding.** pai writes UTF-8 when its output is piped or redirected
on Windows. In Windows PowerShell 5.1, capturing that output into a variable
may misdecode non-ASCII text unless `[Console]::OutputEncoding` is UTF-8;
reading the handoff files themselves is unaffected.

## Choosing the Python interpreter

| Launcher | Looks for, in order | Override |
| --- | --- | --- |
| `bin/pai` (Linux, macOS) | `python3`, then `python` on PATH | `PAI_PYTHON=/path/to/python3.12` or a command name |
| `bin/pai.cmd` (Windows) | `py.exe` (as `py -3`), then `python.exe`, then `python3.exe`, on PATH only, never the current folder | `PAI_PYTHON=C:\full\path\to\python.exe` |
| `bin/pai.py` (any) | the interpreter you run it with | — |

If no interpreter is found, or `PAI_PYTHON` points nowhere, the launcher says
so and exits non-zero. An interpreter older than 3.11 exits 2 with "Python
3.11 or newer is required". `py -0p` lists the interpreters the Windows py
launcher knows. pai never installs Python or edits PATH.

## Configuration, upgrades and several devices

pai looks for its configuration in this order: `--config FILE`, then the
`PAI_CONFIG` environment variable, then `pai.local.toml` inside the bundle
folder. Keep it **outside** the bundle (step 3 above) so that upgrades never
touch it; `doctor` warns when it lives inside a release bundle.

**Upgrade:** verify and extract the new archive, then run its `bin/pai install`
(Windows: `bin\pai.cmd install`). The new version is copied and checked before
the launcher switches to it in one step, so a failure at any point leaves the
previous version working; earlier versions stay in `versions/` until you delete
them. Then run `doctor`: it must report the new version and an intact bundle. With `PAI_CONFIG` set there is
nothing to copy; a 0.1.0 configuration works unchanged. If yours is still the
`pai.local.toml` inside the old bundle, move it to the `PAI_CONFIG` location
once. Delete the old folder when satisfied. Handoffs and notes live in your
vault, so upgrades never touch them.

**Several devices:** each device runs its own `pai init` with its own absolute
paths, because the repository and the synchronized vault usually sit in
different places (for example under your home folder on Linux and on `D:\` on
Windows). Do not synchronize the configuration file itself between devices.
The vault-relative entries (`--memory`, `--inbox`, …) are usually identical
everywhere. `doctor` reports any root that is missing on the current device.

**Permissions.** On Linux and macOS, `init --write` creates the file with mode
0600 (owner read/write only). Windows does not use POSIX modes: the file
inherits the ACLs of its folder. Under `%LOCALAPPDATA%` that normally means
you, SYSTEM and administrators. pai does not change ACLs, so choose the folder
accordingly.

## Synchronized folders and links

pai refuses symbolic links that leave a registered root and, during handoff
discovery, refuses every symlink in the Inbox. On Windows, junctions and other
reparse points are treated the same way. Inbox handoffs stored as cloud
placeholders (for example OneDrive "Files On-Demand") are reparse points too
and are refused by discovery; keep the vault fully local, as Syncthing does. Local NTFS and
the usual Linux and macOS filesystems are the supported targets; network
shares and FAT/exFAT drives have not been tested, and discovery refuses files
whose identity the filesystem cannot report.

## Agent sessions

`SESSION_START.md` contains a short prompt to paste at the start of an agent
session: it retrieves context first and ends with a draft handoff.
