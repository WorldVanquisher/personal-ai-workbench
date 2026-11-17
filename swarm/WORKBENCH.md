# pai workbench: operator guide

`pai` runs a batch of coding agents in parallel: one tmux window, one Git worktree, one
branch and one agent process (Claude Code or Codex) per lane, with each lane's task
delivered from your Markdown task notes and published only through your own reviewed PR.

```text
pai init -> pai doctor -> pai run -> pai status -> pai pr check -> pai pr open -> pai next -> pai cleanup
```

The workbench lives in `swarm/` of a personal-ai-workbench checkout and is reached through
`bin/pai` (Python 3.11+). It is not in the release bundle. Everything below was exercised
with fake agents, fake `gh`, fake `ssh` and an isolated tmux server; what that does **not**
prove is listed in [section 12](#12-tested-with-fakes-and-not-verified-live).

## 1. Set up a project

```sh
cd ~/code/myapp                     # any directory inside the checkout, or one of its worktrees
pai init                            # asks: default agent, its model and effort, remote, base,
                                    # task-notes folder, review checks, then (optional) a JEV key
pai doctor                          # read-only: system, git, tmux, gh, ps, agents, logins, SSH access
pai setup --dry-run                 # OS, architecture, worker backend, missing tools, install commands
pai setup --install-missing         # runs only those commands; logins and folder trust stay yours
```

Non-interactive: `pai init --no-input --default-agent codex --remote origin --base main
--tasks ~/Vault/Tasks --check verify="make verify"`. An existing project is never
overwritten: `--update` changes only what you pass, or use `pai config edit`.

`pai init` lists each agent, whether it is installed, and whether pai can run it with
Remote Control (Claude: yes, `rc` mode; Codex: not supported), plus "not set yet".

**Platforms.** `pai doctor` / `pai setup` read `/etc/os-release` and the package managers
actually present:

| System | Installer pai proposes | Workers |
| --- | --- | --- |
| Debian, Ubuntu | `apt-get` | tmux |
| Arch | `pacman` | tmux |
| Fedora | `dnf` | tmux |
| RHEL, Rocky, AlmaLinux, CentOS | `dnf` (`yum` where dnf is absent); `gh` is not guessed (GitHub's own RPM repo) | tmux |
| Amazon Linux 2 / 2023 | `yum` / `dnf` | tmux |
| macOS | Homebrew, if installed | tmux |
| WSL | the Linux distribution it runs | tmux |
| native Windows | `winget` for git and gh, if installed | **none**: workers need tmux. Use WSL: `wsl --install -d Ubuntu`, then install and run pai inside it |
| anything else | nothing: the missing tools are listed and nothing is run | |

A package manager is never installed for you, and `pai run` never installs anything.
Claude Code comes from `https://claude.ai/install.sh`, Codex from npm (`@openai/codex`).

## 2. Defaults: `pai config`

```sh
pai config show --effective         # every value and where it comes from
pai config edit                     # this project's file, in $VISUAL, $EDITOR, nano, or Notepad on Windows
pai config edit --global            # defaults shared by all projects
pai config check                    # validate only; starts nothing, calls nothing, changes nothing
```

Precedence: command-line flag > project file > global file > built-in default. Files are
TOML with every setting present as a commented example, grouped by command (`[run]`,
`[review]`, `[logs]`, `[agents.claude]`, `[agents.codex]`). An invalid edit is not saved:
pai names the offending key, keeps the previous file, and keeps your draft for
`pai config edit` to continue. Key-like settings (`api_key`, `token`, ...) are refused.
One-time actions (`--yes`, `--apply`, `--stop-running`, `--install-missing`) are never
defaults.

Model and reasoning effort are stored per agent; switching the default agent never carries
one agent's model or effort to the other. Unset means the agent's own default (no flag is
passed). Claude takes `--effort low|medium|high|xhigh|max`; Codex takes
`model_reasoning_effort` `minimal|low|medium|high` (passed as `-c`). Anything else is
refused before a worktree is created.

## 3. Start a batch

```sh
pai run --dry-run --workers 2 --agent claude --agent codex   # read-only: no fetch, refs, state or sessions
pai run --workers 2 --agent claude                            # Claude-only (Remote Control)
pai run --workers 3 --agent codex --model gpt-5-codex         # Codex-only
pai run --workers 2 --agent claude --agent codex \
        --model 2=gpt-5-codex --effort 1=high --effort 2=medium   # mixed; lanes by position or name (a, b)
pai run --repo /path/to/local/repo --workers 2 --agent codex
pai run --repo https://github.com/OWNER/REPO.git --base main --workers 2 --agent claude --agent codex
pai run --repo https://github.com/OWNER/REPO.git --checkout-dir /path/to/checkout --workers 2 --dry-run
```

- One `--agent` applies to every lane; repeated values map to lanes `a`, `b`, ... in order
  and must match `--workers`. `--model` and `--effort` never choose an agent.
- Questions (branch names, the plan) are all asked before anything changes; `--yes`
  accepts pai's plan and nothing else (never an agent's folder-trust prompt). Without a
  terminal, `--yes` is required.
- A new run checks remote access (section 7), fetches the base once, pins one commit for
  every lane, creates `pai/<run>/<lane>` in each lane's worktree (`<repo>-pai-lanes/<lane>`,
  reused across runs), starts each agent in its own window, and gives each its task when its
  input box is ready. Your own checkout is never switched or pulled.
- `--repo URL` clones into a pai-managed checkout (or `--checkout-dir`). A new clone is its
  own trust decision: pai lists the files that can make an agent run code or take
  instructions (`AGENTS.md`, `CLAUDE.md`, `.claude/`, `.codex/`, `.mcp.json`, hooks,
  workflows, `package.json`, ...) and starts no agent until you pass `--reviewed` (or say yes
  in a terminal). A destination pai did not create, or one recorded for another URL, is
  refused.

## 4. Watch, attach, answer trust prompts

```sh
pai status                # per lane: process, task dispatch, Remote Control evidence
pai status --json
pai attach a              # opens lane a's window (pai remembers the socket); --print shows the tmux command
pai logs a --tail 100     # screen and scrollback; --follow keeps printing
pai trust                 # every lane waiting for folder trust, one after another, from this terminal
```

Three separate states per lane:

- **process**: `running`, `exited (status N)`, `no window`, `mismatched` (the window is not
  the one this run started), plus the phase `starting` / `waiting for folder trust`.
- **dispatch**: `pending`, `sent`, `delivered` (its tag is visible in the pane), or
  `unknown` (see section 6).
- **rc**: `verified` only from Claude Code's own current session record for that exact
  process and worktree; a stale record (older than 15 minutes) or a bridge session
  unchanged since a disconnect notice was seen is `unverified`. Codex lanes, and Claude in
  `local` mode, report `not supported`.

**Folder trust.** Both agents ask on first use of a directory, and pai never answers.

| Agent | What pai does | Still needs you |
| --- | --- | --- |
| Claude Code 2.1.323 | detects "Quick safety check: Is this a project you created or one you trust?"; the lane shows `waiting for folder trust` with `pai attach LANE`; its task waits | choose "Yes, I trust this folder" (the cursor starts on "No, exit") |
| Codex 0.167.0 | detects "Trust this folder? Codex can read, edit, and run files here" | answer it in the window |

pai found no documented, supported way to grant either agent's folder trust centrally, so
it does not edit their trust stores. `pai trust` is the guided flow: it lists the
repository and worktree paths, opens the first waiting prompt in your terminal, returns as
soon as the prompt is gone, moves to the next, then delivers the waiting tasks. Ready lanes
keep running meanwhile. Lane worktrees are reused for the same project and lane, so trust
is normally asked once per lane directory; a new or replaced directory is flagged in the
plan as a new trust decision.

## 5. Dispatch

When a lane's input box is ready (`? for shortcuts`), pai pastes one line through a tmux
buffer loaded from a 0600 file (no shell is involved) and presses Enter:
`[pai RUN/LANE/NOTE] Your task is the note PATH ("TITLE"). ...`. The note stays where it
is. Each run, lane and task is sent at most once.

## 6. Resume

```sh
pai resume                      # restart only missing workers; deliver tasks not yet delivered; no fetch
pai resume --lane b
pai resume --lane b --redispatch      # only for dispatch `unknown`: send it again, deliberately
pai resume --lane b --mark-delivered  # only for dispatch `unknown`: record that the agent has it
```

A resume keeps the recorded base, branch, task, agent, model and effort, and never
fetches or switches. If a process ended between sending a task and pai seeing it in the
pane, the dispatch is `unknown` and nothing resends it until you choose.

## 7. SSH remotes

Before a new run, a clone, `pai next` and `pai pr open`, pai runs one bounded
`git ls-remote` over the remote's actual fetch (or push) URL with BatchMode and no terminal,
so a passphrase prompt can never appear and a loaded but wrong key fails. On failure,
nothing is locked, fetched, started or pushed, and pai prints the exact steps for **this
shell**, for example:

```sh
eval "$(ssh-agent -s)"     # only when no agent is reachable
ssh-add ~/.ssh/id_ed25519  # ssh-add asks for the passphrase itself; pai never sees it
ssh-add -l
git -C ~/code/myapp ls-remote origin
```

Every worker gets the `SSH_AUTH_SOCK` of the shell that started it, so a tmux server
started earlier from another login cannot hand it a stale agent socket. Host keys are never
accepted for you. HTTPS remotes need no agent.

## 8. Review and publish

```sh
pai pr plan a             # read-only: commits, checks, publication check, remote, PR
pai pr check a            # run the approved checks at HEAD; record results in the note
pai pr open a --draft     # SSH preflight, checks again, publication check, push (never forced), PR
```

Only commands from `[review.checks]` run; a note can request one by name, never supply a
command. Results are tied to the exact commit and snapshot; `pr open` reruns them and
stops on any failure, on Claude co-author trailers, "Generated with Claude Code" footers
or session links (opt in per run: `--allow-ai-attribution`), and never prints such a
link. A rerun reuses the branch and PR. Merging stays yours. `--jev-verify` adds JEV's
opinion, which never overrides a check.

## 9. Next task

```sh
pai next a --dry-run
pai next a                    # only after gh reports the PR merged into the base (squash included)
pai next a --task NOTE --branch NAME
```

An open or closed-unmerged PR changes nothing. After a merge: fetch, pin, a new branch
(`<stem>-2`, or `--branch`), the next queued note, and the task is sent to the same agent.

## 10. Cleanup

```sh
pai cleanup                                   # preview: socket, session, window, agent state, lock
pai cleanup --apply                           # close exited workers' windows, release their locks
pai cleanup --lane b --apply --stop-running   # also stop a live worker (SIGTERM), then close it
pai resume --lane b                           # afterwards: the same branch, worktree and edits
```

Targets come only from the run's recorded tmux ids (server, session, window, pane, pid);
a window is never chosen by name, a mismatch is refused, the tmux server is never killed,
and everything is rechecked under the run's mutex right before closing. A worker waiting
for folder trust counts as live. Worktrees, branches, edits, notes and review records are
never touched. Running it twice is harmless.

## 11. JEV (optional)

`pai init` asks, in a terminal only, `Enter JEV API key (optional):` with hidden input.
Enter skips it (an existing key is kept). A key goes to your credential store
(libsecret `secret-tool` on Linux, the macOS Keychain) and nowhere else; without a store pai
explains how to export `TYPESAFE_API_KEY` yourself. `TYPESAFE_API_KEY` always wins.

```sh
pai jev status        # where the key comes from; never prints it
pai jev check         # ONE real request with a fixed synthetic task; may be billed; never retried
```

`pai jev check` reports `ok`, `INVALID KEY` (HTTP 401/403), `RATE LIMITED` (429),
`TIMEOUT` or `UNEXPECTED REPLY`, and the `usage` the API returned. Any failure leaves pai
working with JEV off.

**Cost estimate** (checked 2026-11-12, TypeSafe's own pages):

- Price: Jev 1.14 / 1.13 "$42 / $0.042" per billion / per million tokens; "You pay per input
  token. Output tokens are free." ([docs.typesafe.ai/models](https://docs.typesafe.ai/models)).
  The page shows a dollar sign but names no currency code; no per-request fee is listed.
- Usage: the reply's `usage.input_tokens` is "the number of billable input tokens"
  ([API reference](https://docs.typesafe.ai/api/)). A call ingests its `state` once for all
  its questions ([parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions));
  how the question text itself is counted is not documented.
- Formula: `cost = input_tokens x 0.042 / 1,000,000` (USD assumed from the `$` sign).

Token counts below assume about 4 characters per token (pai's own estimate, not TypeSafe's
tokenizer). The adapter sends one call per decision: triage = note (state capped at 4,000
characters) + 659 characters of questions; review = note (first 1,500 characters) + check
summary + 312 characters of questions.

| Scenario | Calls | Typical input tokens | Upper bound | Cost at $0.042/Mtok |
| --- | --- | --- | --- | --- |
| `pai jev check` | 1 | ~220 | ~400 | < $0.00002 |
| one task: intake triage + one completion review | 2 | ~820 | ~2,300 | $0.00003 to $0.0001 |
| 4 workers x 5 tasks, one night | 40 | ~16,000 | ~46,000 | $0.0007 to $0.002 |
| that batch, 20 nights a month | 800 | ~330,000 | ~910,000 | $0.014 to $0.04 |

Retries: the adapter makes none. Re-reviews are extra calls: each additional
`pr check`/`pr open` with `--jev-verify` adds one review call (about 400 typical, at most
about 1,100 tokens). These are estimates; the live `pai jev check` has **not** been run by
the developer, so no real usage figure exists yet.

## 12. Tested with fakes, and not verified live

Fixture-tested (disposable repositories, fake Claude/Codex/gh/ssh/installers/JEV endpoint,
isolated tmux socket): everything in sections 1 to 11.

Not verified against the real programs:

- the trust prompts' and ready markers' exact on-screen text in a live tmux pane (read from
  the installed binaries' strings, not from a running session);
- that pasting the dispatch line into the real Claude Code / Codex input box submits it;
- Codex accepting `-c model_reasoning_effort=...` for every model, and which models take
  which levels;
- Remote Control connectivity from `rc status` for a real session;
- real SSH, GitHub, merges, and installers on each listed OS (the planner is tested on
  simulated systems only);
- a real JEV request.

[SMOKE.md](SMOKE.md) is the owner-run checklist for those.

## 13. Experimental: the Markdown server

Optional, never installed or started by anything else: `pai setup --experimental server --root DIR --dry-run`, then `pai server start|status|stop|token|uninstall`. Loopback only; SSH tunnel or Tailscale Serve (never Funnel). See [mdserver/README.md](mdserver/README.md).
