# pai: personal AI workbench

English | [简体中文](README.zh-CN.md)

**PAI is the lubrication layer for running several coding agents in parallel.** It prepares
each round, connects the agents' work, and collects it back for you. You keep every final
decision. It does not replace or wrap Claude Code or Codex; it works beside them.

How it differs from lead-agent frameworks such as LangGraph, CrewAI, AutoGen or the OpenAI Agents
SDK, as their own documentation describes them (reported-by-prompts, [docs/WHY-PAI.md](docs/WHY-PAI.md)):

1. **The person decides, not a lead agent.** Agents propose; open questions reach you as a decision
   sheet with options and a recommendation.
2. **State is files, not a runtime.** Handoffs, queues and decisions are Markdown in a folder. They
   survive a cleared conversation or a reboot, and any editor reads them.
3. **No new engine.** PAI drives the Claude Code and Codex sessions you already run. Their prompts
   are a versioned library that changes only with your approval.

| You are … | Read |
| --- | --- |
| **using PAI for the first time** | [docs/FIRST-USE.md](docs/FIRST-USE.md): from nothing to a finished first round, every step with its expected output |
| **starting another round, or upgrading** | [docs/REFRESH.md](docs/REFRESH.md): upgrade and rollback, a new round from the merged main, pitfalls, recovery |
| **looking something up** | [docs/README.md](docs/README.md): which document answers what; [docs/GLOSSARY.md](docs/GLOSSARY.md) for the words |

**In a hurry?** [QUICKSTART.md](QUICKSTART.md): install, first task, the browser console, upgrade, uninstall.

**Install in one line** (Linux, macOS or WSL; needs [uv](https://docs.astral.sh/uv/) and git):

```sh
uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.4.0
```

If uv warns that its bin folder is not on your PATH, run the `export PATH=…` line it prints
(or `uv tool update-shell`) and open a new terminal. Then, inside your Git checkout:
`pai doctor` → `pai init` → `pai task new "TITLE" --write` → `pai run --dry-run`.
Upgrade: the same line with the new version. Remove: `uv tool uninstall pai-workbench`
(your configuration and task notes stay). No uv? Use the archives on the
[v0.4.0 release page](https://github.com/WorldVanquisher/personal-ai-workbench/releases/tag/v0.4.0), as in the Quick start below.

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
- **In your browser** (new in 0.4.0, experimental): `pai web` shows a run's lanes on
  127.0.0.1: each agent's screen live, typing into it (Ctrl and arrow keys, input methods),
  pasting from your clipboard as one piece, a shell in each lane's worktree, and the task notes
  in a Markdown editor. `pai paste LANE FILE` sends a long or multi-line message from the
  terminal the same way. `pai edit FILE.md` opens one Markdown file with a live preview and
  confirmed saves; it also runs on native Windows. All three refuse to paste into an agent's
  folder-trust prompt; you answer that yourself.

## Quick start: from the archive to a first task

Linux, macOS or WSL, with Python 3.11+, git and tmux. Nothing needs sudo, and no script is
piped into a shell:

```sh
sha256sum -c pai-workbench-0.4.0.tar.gz.sha256      # macOS: shasum -a 256 -c pai-workbench-0.4.0.tar.gz.sha256
tar -xzf pai-workbench-0.4.0.tar.gz
pai-workbench-0.4.0/bin/pai install                  # a verified copy in ~/.local/share/pai-workbench, `pai` in ~/.local/bin
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
pai web                  # the same run in your browser (Ctrl-C stops it)
```

`pai guide` runs this whole path once, offline, in a disposable folder with a FAKE worker (no
account or network needed). `pai` alone prints this path again; `pai --help` lists every command. When a lane waits for
an agent's first-use folder-trust prompt, `pai status` says so and `pai trust` opens each
prompt for you to answer; the task follows. Upgrading is the same three lines with the new
archive: the previous version keeps working if anything fails, and your configuration and
task notes are not touched.

**Getting the archive.** Download `pai-workbench-0.4.0.tar.gz` and its `.sha256` from the
[v0.4.0 release page](https://github.com/WorldVanquisher/personal-ai-workbench/releases/tag/v0.4.0) (the `.zip` is the same files, for Windows). The same archives
can be built from a checkout: `python3 -B tools/build_bundle.py --out dist`.

From a remote repository, pai clones into a checkout it manages and starts no agent until
you have reviewed it: `pai run --repo https://github.com/OWNER/REPO.git --workers 2 --agent
claude --agent codex`.

## Platforms

| | Core | Workbench workers |
| --- | --- | --- |
| Linux (Debian/Ubuntu, Arch, Fedora/RHEL family, Amazon Linux) | yes | yes (tmux) |
| macOS | yes | yes (tmux) |
| WSL | yes | yes (tmux, inside the distribution) |
| native Windows | yes (`pai context`, `pai handoff`, `pai doctor --agents`, `pai edit`) | **no**: workers (and `pai web`) need tmux; install pai inside WSL |

On native Windows, workbench commands stop with a message pointing to WSL. INSTALL.md has the
Windows steps for the core.

## Tested, not yet verified, and optional

- **Tested:** the core, both archives, `pai install` and the uv-installed package on
  Windows, macOS and Linux in CI (see RELEASE_NOTES.md). The workbench with fake agents, fake
  GitHub, fake SSH and an isolated tmux server, on Linux: from the archive and from
  `uv tool install` of the release tag in a clean container, up to a first delivered task.
- **Not yet verified live by the developer:** the workbench against real Claude Code,
  Codex, GitHub and SSH, and the workbench's run/dispatch path on macOS (CI there checks the
  install, the core and that the workbench loads). The owner-run checklists are
  `swarm/SMOKE.md` and `swarm/LIVE-VALIDATION.md`.
- **Optional:** JEV (TypeSafe) as an advisory signal for task triage and review; it never
  overrides a check. Tested against a mock endpoint; `pai jev check` sends the one real
  request when you choose to.
- **Experimental:** `pai web` and `pai edit` listen on 127.0.0.1 only, behind a sign-in link
  that works once; from another computer, use an SSH tunnel. Tested with fake agents and a
  recording terminal program on Linux (exact bytes of every paste and keystroke, trust prompts
  refused, the security checks); CI runs a `pai edit` session from the installed package on
  Linux, macOS and native Windows (see RELEASE_NOTES.md for the runs). Not yet tried with a real
  Claude Code or Codex, nor by hand in a desktop browser on macOS or Windows.
- **Checkout only, experimental:** a longer-running Markdown server for several folders
  (`pai setup --experimental server`); see `swarm/mdserver/README.md`.

## Documentation

- [INSTALL.md](INSTALL.md): installing with uv or from the archives, upgrades, Windows; [REFERENCE.md](REFERENCE.md): its commands.
- `swarm/WORKBENCH.md` (in the archive and in a checkout): the workbench guide, including platform setup,
  configuration, folder trust, SSH, cleanup and JEV costs.
- Checking a change before review: `python3 -B tools/verify.py` runs the core and workbench
  suites (failing on leaked files too), builds both archives and smoke-tests them, then the
  headless run tests, the new-user acceptance from the built archive, the QUICKSTART
  walkthrough, the console walkthrough and the package check (install, upgrade, uninstall).
  Test folders are discovered (`--list`); steps whose tools are missing (tmux, uv, setuptools)
  are reported as skipped; the summary gives each step's time and every skipped test with its
  reason. `--quick` runs only the core suite and the archives.
- `swarm/RUN-STATE.md` (lifecycle names, identifiers and the evidence behind each status) and
  `swarm/BACKENDS.md` (what each agent backend exposes; adding one); both ship in the archive.
- [docs/1.0/](docs/1.0/): the 1.0 proposals for the owner: acceptance checklist, platforms,
  compatibility, experimental policy, capabilities matrix, migration from 0.4.x, real-agent smoke
  checklist, backlog.
- [SESSION_START.md](SESSION_START.md): the prompt for an agent session;
  [RELEASE_NOTES.md](RELEASE_NOTES.md): changes and test evidence.

License: MIT, see [LICENSE](LICENSE).
