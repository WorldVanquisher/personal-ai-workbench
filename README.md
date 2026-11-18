# pai: personal AI workbench

English | [简体中文](README.zh-CN.md)

PAI keeps an AI-assisted project's working memory in plain Markdown files, and runs coding
agents against it under your control.

- **Core** (`pai context`, `pai handoff`): gives an agent session the project context it must
  respect (rules, human decisions, status, limitations) and files a draft handoff afterwards.
  Windows, macOS, Linux; Python 3.11+; standard library only; no network or model calls;
  runs no commands, Git included.
- **Workbench** (`pai doctor`, `init`, `task`, `run`, `status`, `pr`, `next`, `cleanup`; in the
  same archive since 0.3.0, under `swarm/`): runs Claude Code and Codex in parallel lanes, one
  tmux window, Git worktree and branch each. Tasks come from Markdown notes; publishing goes
  through checks you approved and a PR you merge. Linux, macOS and WSL (it needs tmux).

## Quick start: from the archive to a first task

Linux, macOS or WSL, with Python 3.11+, git and tmux. Nothing needs sudo, and no script is
piped into a shell:

```sh
sha256sum -c pai-workbench-0.3.0.tar.gz.sha256      # macOS: shasum -a 256 -c pai-workbench-0.3.0.tar.gz.sha256
tar -xzf pai-workbench-0.3.0.tar.gz
pai-workbench-0.3.0/bin/pai install                  # a verified copy in ~/.local/share/pai-workbench, `pai` in ~/.local/bin
```

If `install` says `~/.local/bin is not on PATH`, run the one line it prints for your shell
(for bash: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc`) and open a new terminal.
Then, inside your Git checkout:

```sh
pai doctor               # tools, agent logins, remote access; changes nothing
pai init                 # asks a few questions; Enter keeps each suggested default
pai task new "Add a --json flag to export" --body "Done when: make test passes" --write
pai run --dry-run        # what would happen; changes nothing
pai run                  # starts the agents, one tmux window each
pai status               # per lane: process, trust prompt, input, task dispatch, Remote Control
```

`pai` alone prints this path again; `pai --help` lists every command. When a lane waits for
an agent's first-use folder-trust prompt, `pai status` says so and `pai trust` opens each
prompt for you to answer; the task follows. Upgrading is the same three lines with the new
archive: the previous version keeps working if anything fails, and your configuration and
task notes are not touched.

**Getting the archive.** 0.3.0 is not on the Releases page yet. Until it is, build the same
archives (`.tar.gz`, `.zip`, each with its `.sha256`) from a checkout:
`python3 -B tools/build_bundle.py --out dist`.

From a remote repository, pai clones into a checkout it manages and starts no agent until
you have reviewed it: `pai run --repo https://github.com/OWNER/REPO.git --workers 2 --agent
claude --agent codex`.

## Platforms

| | Core | Workbench workers |
| --- | --- | --- |
| Linux (Debian/Ubuntu, Arch, Fedora/RHEL family, Amazon Linux) | yes | yes (tmux) |
| macOS | yes | yes (tmux) |
| WSL | yes | yes (tmux, inside the distribution) |
| native Windows | yes (`pai context`, `pai handoff`, `pai doctor --agents`) | **no**: workers need tmux; install pai inside WSL |

On native Windows, workbench commands stop with a message pointing to WSL. INSTALL.md has the
Windows steps for the core.

## Tested, not yet verified, and optional

- **Tested:** the core on Windows, macOS and Linux in CI (see RELEASE_NOTES.md). The
  workbench with fake agents, fake GitHub, fake SSH and an isolated tmux server.
- **Not yet verified live by the developer:** the workbench against real Claude Code,
  Codex, GitHub and SSH; `pai install` on macOS and native Windows (tested on Linux; the
  cross-platform CI checks are configured but have not run). The owner-run checklists are
  `swarm/SMOKE.md` and `swarm/LIVE-VALIDATION.md`.
- **Optional:** JEV (TypeSafe) as an advisory signal for task triage and review; it never
  overrides a check. Tested against a mock endpoint; `pai jev check` sends the one real
  request when you choose to.
- **Experimental, optional:** a loopback-only Markdown server (a remote Nano with a preview
  and confirmed saves), installed only with `pai setup --experimental server`; see
  `swarm/mdserver/README.md`. Reach it through an SSH tunnel or your private tailnet.

## Documentation

- [INSTALL.md](INSTALL.md): installing the core bundle; [REFERENCE.md](REFERENCE.md): its commands.
- `swarm/WORKBENCH.md` (in the archive and in a checkout): the workbench guide, including platform setup,
  configuration, folder trust, SSH, cleanup and JEV costs.
- [SESSION_START.md](SESSION_START.md): the prompt for an agent session;
  [RELEASE_NOTES.md](RELEASE_NOTES.md): changes and test evidence.

License: MIT, see [LICENSE](LICENSE).
