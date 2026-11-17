# pai: personal AI workbench

English | [简体中文](README.zh-CN.md)

PAI keeps an AI-assisted project's working memory in plain Markdown files, and runs coding
agents against it under your control.

- **Core** (`pai context`, `pai handoff`; the release bundle): gives an agent session the
  project context it must respect (rules, human decisions, status, limitations) and files
  a draft handoff afterwards. Windows, macOS, Linux; Python 3.11+; standard library only;
  no network or model calls; runs no commands, Git included.
- **Workbench** (`pai init`, `run`, `status`, `pr`, `next`, `cleanup`; in a checkout's
  `swarm/`, not in the bundle): runs Claude Code and Codex in parallel lanes, one tmux
  window, Git worktree and branch each. Tasks come from Markdown notes; publishing goes
  through checks you approved and a PR you merge.

## Quick start: a mixed Claude + Codex batch from your checkout

```sh
ln -s /path/to/personal-ai-workbench/bin/pai ~/.local/bin/pai   # once
cd ~/code/myapp                       # your Git checkout
pai init                              # default agent, model, remote/base, task notes, checks
pai doctor                            # tools, agent logins, SSH access; changes nothing
pai run --dry-run --workers 2 --agent claude --agent codex
pai run --workers 2 --agent claude --agent codex
pai status                            # per lane: process, task dispatch, Remote Control
pai trust                             # answer first-use folder-trust prompts; tasks follow
pai pr open a --draft                 # checks, publication guard, push, draft PR
pai cleanup --apply                   # close finished workers' windows; work is kept
```

From a remote repository, pai clones into a checkout it manages and starts no agent until
you have reviewed it: `pai run --repo https://github.com/OWNER/REPO.git --workers 2 --agent
claude --agent codex`.

## Platforms

| | Core | Workbench workers |
| --- | --- | --- |
| Linux (Debian/Ubuntu, Arch, Fedora/RHEL family, Amazon Linux) | yes | yes (tmux) |
| macOS | yes | yes (tmux) |
| WSL | yes | yes (tmux, inside the distribution) |
| native Windows | yes | **no**: workers need tmux; use WSL |

## Tested, not yet verified, and optional

- **Tested:** the core on Windows, macOS and Linux in CI (see RELEASE_NOTES.md). The
  workbench with fake agents, fake GitHub, fake SSH and an isolated tmux server.
- **Not yet verified live by the developer:** the workbench against real Claude Code,
  Codex, GitHub and SSH, and the installer on each listed OS. The owner-run checklists are
  `swarm/SMOKE.md` and `swarm/LIVE-VALIDATION.md`.
- **Optional:** JEV (TypeSafe) as an advisory signal for task triage and review; it never
  overrides a check. Tested against a mock endpoint; `pai jev check` sends the one real
  request when you choose to.
- **Experimental, optional:** a loopback-only Markdown server (a remote Nano with a preview
  and confirmed saves), installed only with `pai setup --experimental server`; see
  `swarm/mdserver/README.md`. Reach it through an SSH tunnel or your private tailnet.

## Documentation

- [INSTALL.md](INSTALL.md): installing the core bundle; [REFERENCE.md](REFERENCE.md): its commands.
- `swarm/WORKBENCH.md` (in a checkout): the workbench guide, including platform setup,
  configuration, folder trust, SSH, cleanup and JEV costs.
- [SESSION_START.md](SESSION_START.md): the prompt for an agent session;
  [RELEASE_NOTES.md](RELEASE_NOTES.md): changes and test evidence.

License: MIT, see [LICENSE](LICENSE).
