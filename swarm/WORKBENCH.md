# pai workbench: operator guide

`pai` runs a batch of coding agents in parallel: one tmux window, one Git worktree, one
branch and one agent process (Claude Code or Codex) per lane, with each lane's task
delivered from your Markdown task notes and published only through your own reviewed PR.

```text
pai init -> pai doctor -> pai run -> pai status -> pai pr check -> pai pr open -> pai next -> pai cleanup
```

The workbench lives in `swarm/` and is reached through `bin/pai` (Python 3.11+): in a
checkout of personal-ai-workbench, and since 0.3.0 in the release archive too (`bin/pai
install` puts `pai` on your PATH; README.md has the steps). Its tests, examples and the
experimental Markdown server stay in the checkout. Everything below was exercised
with fake agents, fake `gh`, fake `ssh` and an isolated tmux server; what that does **not**
prove is listed in [section 12](#12-tested-with-fakes-and-not-verified-live).

## 1. Set up a project

```sh
cd ~/code/myapp                     # any directory inside the checkout, or one of its worktrees
pai doctor                          # read-only: this pai (intact, on PATH), the checkout, system,
                                    # git, tmux, gh, ps, agents, logins, SSH access, each backend's capabilities;
                                    # grouped ESSENTIAL / OPTIONAL / INFO, then REPAIR commands to copy
pai init                            # asks: default agent, its model and effort, remote, base,
                                    # task-notes folder, review checks, then (optional) a JEV key;
                                    # Enter keeps each suggestion, and it ends with your next commands
pai task new "TITLE" --body "..."   # preview a task note; --write saves it (never overwrites)
pai task list                       # the queue, in the order `pai run` takes it
pai task export --out cards.json    # the queue as portable task cards; pai task import cards.json
pai setup --dry-run                 # OS, architecture, worker backend, missing tools, install commands
pai setup --install-missing         # runs only those commands; logins and folder trust stay yours
```

Non-interactive: `pai init --no-input --default-agent codex --remote origin --base main
--tasks ~/Vault/Tasks --check verify="make verify"`. An existing project is never
overwritten: `--update` changes only what you pass, or use `pai config edit`. A new project
without `--tasks` gets pai's own task folder, `~/.local/share/pai-workbench/tasks/PROJECT`
(answer `-` for none). With one agent installed, it is the suggested default.

**Task notes.** `pai task new` writes the queue's format: front matter with `status: todo`
and `priority` (and `paths` with `--path`), then `# TITLE` and the body. `--from FILE`
imports one Markdown file: its own front matter and text are kept, `status: todo` is added
when it has none, and a `# heading` is added from the file name when it has none. A note
whose status is not `todo` is saved but flagged, because `pai run` will not take it.

**Portable task cards.** `pai task export [NAME ...]` previews the notes as one versioned card
file (`pai-task-cards` version 1: title, description, acceptance criteria, status, priority,
repository-relative paths; deterministic JSON that diffs cleanly); `--out FILE` writes it,
never over an existing file. Run and personal fields (worker, branch, owner, email, ...) are
left out unless `--include-private`, which puts them in a separate `private` group. Text that
looks like a credential is reported by card, field and line, masked, and blocks `--out` until
removed (or `--allow-flagged`). `pai task import FILE` previews each note as create, unchanged
or CONFLICT (by path); `--write` creates only new notes and is refused while any conflicts
(`--skip-existing` leaves those alone). Absolute or escaping paths and unknown format versions
are refused.

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
pai status                # per lane: lifecycle (queued, waiting-for-trust, delivered, exited, ...), why, evidence
pai status --json         # the same, structured (schema pai_status_v1); --brief (automatic below 80 columns)
pai inspect a             # one lane: why it is in its state, and the file or command behind each piece of evidence
pai attach a              # opens lane a's window (pai remembers the socket); --print shows the tmux command
pai logs a --tail 100     # screen and scrollback; --follow keeps printing
pai trust                 # every lane waiting for folder trust, one after another, from this terminal
```

Every lifecycle name, its identifiers and the minimum evidence behind it: [RUN-STATE.md](RUN-STATE.md).
Five separate kinds of evidence per lane; none stands in for another:

- **process**: `running`, `exited (status N)`, `no window`, `mismatched` (the window is not
  the one this run started), plus the phase `starting` / `waiting for folder trust`.
- **trust**: `prompt-visible` (the agent's folder-trust prompt is on the visible screen) or
  `not-shown`. `not-shown` only means no prompt is visible now; it is not proof of trust.
- **input**: whether the agent's idle input box is ready, and why. For Claude, its own
  session record for exactly that process (pid, start time, worktree) saying `idle` with no
  dialog waiting; otherwise the screen's idle marker (`? for shortcuts`, Codex also
  `% context left`). Never ready while a trust prompt is visible.
- **dispatch**: `pending`, `sent` (Enter pressed, evidence awaited), `delivered` (with the
  evidence: the agent's own transcript records the prompt, or you marked it), or `unknown`
  (with the steps to reconcile it; see section 6).
- **rc**: `verified` only from Claude Code's own current session record for that exact
  process and worktree; a stale record (older than 15 minutes) or a bridge session
  unchanged since a disconnect notice was seen is `unverified`. Codex lanes, and Claude in
  `local` mode, report `not supported`.

Policy for a stale record with a ready local Claude: the task is still sent, and `rc` stays
`unverified`. Dispatch only needs local evidence (the process, no trust prompt, the input
box), because the line goes through the local tmux pane, not through Remote Control. The
record's age is evidence about the Remote Control bridge only; its `status` field is
rewritten by Claude whenever its input state changes (Claude Code 2.1.326, read from the
program and the records it wrote; not checked in a live dispatch), so an old `updatedAt` does
not make an `idle` stale. Neither is ever used to upgrade the other: a delivered task says nothing about
Remote Control, and `pai status` keeps exiting 1 until RC is verified.

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

### In your browser: `pai web`, `pai paste`, `pai edit` (experimental)

```sh
pai web                       # this run in your browser; Ctrl-C stops it (--run ID, --port, --no-browser)
pai paste a message.md        # a message into lane a in one piece; --enter submits it
pai edit notes/plan.md        # one Markdown file: live preview, confirmed save (also native Windows)
```

`pai web` serves one page on 127.0.0.1 with a sign-in link that works once, for as long as it
runs in your terminal; it stores nothing. Per lane: the agent's screen with its colours
(refreshed about once a second; select text to copy it), **live typing** (click the screen:
keys, Ctrl and Alt combinations, arrows and your input method go to the pane), **paste** from
your clipboard or the Message box as one piece, a **shell** in the lane's worktree (a window
named after the lane in the tmux session `<run session>-shell`, on the run's tmux server; it
stays until you close it), and the task folder plus every lane worktree in the Markdown
editor (`/notes`).

Why pasting needs help: a long or multi-line prompt typed into a terminal (over SSH, from a
phone, into tmux) can arrive in pieces, a newline can submit half of it, and tmux's own paste
buffer is not your clipboard. `pai paste` and `pai web` load the text into a tmux buffer
through stdin (no file, no shell) and paste it with `paste-buffer -p`: a **bracketed paste**,
so the agent takes it as one paste and nothing is submitted. Enter is pressed only when you
ask (`--enter`, "Paste and Enter"), and checked again just before. More rules:

- A paste is **refused while the agent's folder-trust prompt is on screen**; you answer that
  prompt yourself (typing in `pai web` is you at the keyboard, as with `pai attach`).
- Escape and other control characters are removed from pasted text (tabs and newlines stay),
  so a paste cannot end the bracketed paste early and turn into keystrokes. A file's final
  newline is dropped: it must not act as Enter.
- Several lines go only to a program that tmux says asked for bracketed paste
  (`#{bracket_paste_flag}`; tmux 3.8 reports it, 3.5a cannot tell). Otherwise each line could
  be entered on its own, so the paste is refused until you accept that: `--allow-unbracketed`,
  or the confirmation in the browser. Claude Code and Codex normally ask for bracketed paste,
  and tmux still sends the markers whenever the program did.
- At most 1 MB per paste. A pane in copy mode is refused (press `q` there first).
- The browser names a lane, never a tmux pane: pai looks the pane up itself, so only this
  run's lanes and their shells are reachable. Every write needs the session's CSRF header,
  the Host and Origin must be this server, and the page's Content-Security-Policy allows no
  other origin.

From another computer: `pai web --no-browser --port 8766` on the server and
`ssh -N -L 8766:127.0.0.1:8766 YOU@SERVER` on your laptop, then open the printed link.

## 5. Dispatch

When a lane's input box has been ready, with no trust prompt on screen, for 3 seconds
(`PAI_DISPATCH_SETTLE`; so a trust prompt that appears after the agent's first frame is seen
first), pai pastes one line through a tmux buffer loaded from a 0600 file (no shell is
involved; control characters become spaces), reads the screen again, and presses Enter only
if no trust prompt (and, for Claude, no dialog in its record) has appeared meanwhile:
`[pai RUN/LANE/NOTE] Your task is the note PATH ("TITLE"). ...`. The note stays where it
is. Each run, lane and task is sent at most once; lanes are handled independently, so a
ready lane never waits for one at a trust prompt.

`delivered` needs evidence that the agent took the line as a prompt: its own transcript
records a user message containing the tag, at or after the attempt (Claude:
`<config>/projects/<dir>/<session>.jsonl`; Codex: `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`).
The tag being visible in the pane proves nothing: it may still sit in the input box. Without
that evidence 20 seconds after Enter (`PAI_DISPATCH_CONFIRM_SECONDS`), the dispatch is
`unknown`. Remote Control never gates a dispatch (the line goes through the local pane), and
a dispatch never changes what the `rc` evidence says.

## 6. Resume

```sh
pai resume                      # restart only missing workers; deliver tasks not yet delivered; no fetch
pai resume --lane b
pai resume --lane b --redispatch      # only for dispatch `unknown`: send it again, deliberately
pai resume --lane b --mark-delivered  # only for dispatch `unknown`: record that the agent has it
```

A resume keeps the recorded base, branch, task, agent, model and effort, and never
fetches or switches. A dispatch is `unknown` when its outcome cannot be shown: pai stopped
between a keystroke and recording it, a trust prompt appeared after the paste (Enter is then
not sent), the process ended or changed, or Enter was pressed but no transcript shows the
prompt. Nothing resends it until you choose; `pai status` prints the steps:

1. look at the lane: `pai logs LANE --tail 40` (or `pai attach LANE`);
2. the agent is working on the task: `pai resume --lane LANE --mark-delivered`;
3. the line still sits in the input box: press Enter there yourself (the next `pai status`
   sees the prompt in the transcript), or clear it and `--redispatch`;
4. nothing arrived: `pai resume --lane LANE --redispatch`.

`--mark-delivered` and `--redispatch` run under the same lock as `pai resume`, `pai trust`
and `pai next`; while one of those runs, they are refused and change nothing. A record from
an earlier pai that says `delivered` only because the tag was visible is shown as `unknown`
unless the transcript shows the prompt; it is never resent.

### Recover an interrupted run: preview first

```sh
pai recover                         # read-only preview, lane by lane: task, lifecycle, evidence, action
pai recover --apply                 # restart exited or missing workers, deliver tasks never sent,
                                    # record a delivery the transcript already shows
pai recover --choose b=redispatch --apply      # an unknown dispatch: only on your explicit choice
pai recover --choose b=mark-delivered --apply  #   (or b=leave to decide later)
pai recover --history               # every recorded decision, with before- and after-state
pai guide --recovery                # try it offline: the fake worker crashes once, then is recovered
```

To continue a task in a **fresh session** instead (another agent, a clean context, another
HOME), hand it off: `pai recover --continue LANE` (preview), `--write`, then `pai run --continue
NOTE [--agent codex]`. Goal, completed steps, evidence and open decisions travel in the task note;
nothing is sent twice. See [CONTINUATION.md](CONTINUATION.md); try it offline with
`pai guide --handoff` and `pai guide --handoff interrupted`.

Actions marked AUTO happen with `--apply`; CHOOSE (an unknown dispatch) waits for `--choose`;
HUMAN (a folder-trust prompt, an agent that accepts no input, a doubtful window) is yours and
names the folder and repository. Each decision is written to
`$PAI_SWARM_HOME/recovery/<repo key>-<run>.jsonl` before anything happens and completed after;
an interrupted recovery is shown by the next preview. A second `pai recover --apply` (or
`pai resume`, `pai run`) on the same run is refused while one is working.

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
pai pr draft a            # local owner-review page: diff, recorded checks, PR text, filter removals,
                          # checklist and the publish commands; no network, index or refs touched
pai pr draft a --out review.md     # save it (never over an existing file)
pai pr plan a             # read-only: commits, checks, publication check, remote, PR (asks GitHub)
pai pr check a            # run the approved checks at HEAD; record results in the note
pai pr open a --draft     # SSH preflight, checks again, publication check, push (never forced), PR
```

**The publication boundary.** Workers are told not to push or open pull requests, and no pai
default or setting makes pai do it: `pai pr open` is the only command that pushes or opens a
PR, and only when you run it. `pai pr draft` is where the owner reviews: it renders the
branch, the proposed diff, the test evidence recorded for exactly this head, and the PR text
after the metadata filter (configured trailer keys in `review.strip_trailers`, Claude/Anthropic
co-author trailers, session trailers and footers, session links and credential-like text; each
removal is listed first, human co-authors stay). Publishing (push, PR) and merging remain the
owner's manual steps; the draft prints the commands without running them.

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

**Supported configurations.** JEV (TypeSafe System One, `POST /v1/systemone`) answers typed
questions (`choice`, `score`, `noul`, `box`) about a text you send; it does not write text.

| Setting | Remote (default) | Local endpoint |
| --- | --- | --- |
| Endpoint | `https://api.typesafe.ai` | `TYPESAFE_BASE_URL=http://127.0.0.1:PORT` (a proxy or test server you run) |
| Key | `TYPESAFE_API_KEY`, else the credential store (`pai init`) | the same; any value your endpoint accepts |
| Credential store | libsecret / macOS Keychain | `PAI_JEV_CREDENTIAL_STORE=none` skips it (CI, containers) |
| Timeout | `JEV_TIMEOUT` seconds (default 30) | the same |

Where pai uses it, all optional: intake triage and completion review (`--jev-verify`), and
`pai worktree init --jev`, which asks one `noul` question per rule of its rule library ("does
this rule help this goal in this scope?"). That call sends only the worktree's goal, its scope
folder names and the rule texts: no file contents, no paths, no task note. Without JEV, or
when it is not configured, times out, cannot be reached, answers with an error or answers
malformed, the deterministic template choice is used and the output says which (`JEV skipped`,
`timeout`, `unavailable`, `error`, `malformed`). Its scores only order proposals; nothing is
applied without `--accept`.

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
- the on-disk records used as evidence (Claude's per-process session record `status` and
  its transcript; Codex's rollout files): their structure was read from files the installed
  versions wrote on the development host, but no live dispatch has been checked against them;
- how long after its first frame Codex can show its trust prompt (pai waits 3 seconds of
  steady readiness; a prompt later than that is still caught before Enter, but after the paste);
- Codex accepting `-c model_reasoning_effort=...` for every model, and which models take
  which levels;
- Remote Control connectivity from `rc status` for a real session;
- real SSH, GitHub, merges, and installers on each listed OS (the planner is tested on
  simulated systems only);
- a real JEV request;
- `pai worktree publish` and `pai worktree web`'s publish against real GitHub (tested with a
  local bare remote and the fake gh); PowerShell on Windows itself (the generated PowerShell
  commands were run with PowerShell 7.4 on Linux);
- `pai paste` and `pai web` with the real Claude Code and Codex (bracketed paste, typing and
  the keys were checked byte for byte against a recording terminal program and the fake
  agents), and `pai web` / `pai edit` in a desktop browser on macOS or Windows.

[SMOKE.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/swarm/SMOKE.md) is the owner-run checklist for those.

## 13. Experimental: the Markdown server (checkout only)

`pai edit` and `pai web` above need no setup and come with every installation. The
longer-running server for several folders is for a checkout only (an installed archive or uv
package says so and changes nothing). Optional, never installed or started by anything else: `pai setup --experimental server --root DIR --dry-run`, then `pai server start|status|stop|token|uninstall`. Loopback only; SSH tunnel or Tailscale Serve (never Funnel). See [mdserver/README.md](mdserver/README.md).

## 14. Worktrees after a merge, their instructions, splits and PRs (experimental)

These commands work on any repository's worktrees, with or without a `pai run`.

**Refresh after a merge.** `pai worktree refresh [--worktree PATH | --new PATH]` prints the
commands (Bash and PowerShell) that fetch main explicitly and then either add a fresh worktree
at the fetched SHA or fast-forward a clean one; it runs none of them. `pai worktree verify PATH
--base SHA` checks the worktree itself (a fetch only moves refs) and exits 1 unless its HEAD
contains the base. A worktree with uncommitted changes, a detached HEAD or an operation in
progress always gets the new-worktree path; nothing resets, stashes or overwrites it. See the
quick start, section 6.

**Instructions per worktree.** `pai worktree init PATH --goal TEXT [--scope DIR] [--lane ID]
[--check NAME=CMD] [--accept RULE] [--jev]` previews a pai-managed section of the worktree's
AGENTS.md (`--file CLAUDE.md` for a Claude-only lane); `--write` writes it. Only the part between
`<!-- pai:managed:begin ... -->` and `<!-- pai:managed:end -->` is pai's: everything you wrote
stays byte for byte, BOM and line endings included, and the same inputs give the same bytes.
The section names the goal, the folders the worker may change, the owner, the checks and how to
report, the owner-only actions, and the repository's own rules with their `file:line` source;
a repository rule that asks for an owner-only action (push, deploy, ...) is marked as a conflict
there and in the preview. Never the main checkout, never through a symbolic link.
`--export-profile FILE` saves the accepted rules and checks (no goal, no paths) for `--profile`.

**Split a goal.** `pai plan propose --parent ID --goals FILE --out PLAN.json` reads one slice per
line (`- id: goal | paths: ... | verify: ... | prereqs: ... | needs: ...`), keeps the parent ID,
numbers the slices (`ID-001`, ...) and gives each a worker: the lane whose AGENTS.md scope owns
the slice's first path, else the least-loaded one. `pai plan check` refuses missing verification,
scope or prerequisites, two slices writing the same path, a path another lane owns, unknown or
circular dependencies. Edit the JSON or `pai plan assign PLAN SLICE WORKER`, then
`pai plan accept PLAN --queues DIR --write` puts each slice into its worker's `pai queue`
(a slice that waits for another is queued as blocked); a rerun never queues a slice twice.
`pai plan check PLAN --queues DIR` shows each slice's queue state and which waiting slice can start.

**A PR from any worktree.** `pai worktree preview PATH --path FILE ... --title T [--body-file F]
--out PLAN.json` changes nothing; it shows the base (its last fetched SHA), HEAD, the branch's
SHA on the remote (`git ls-remote`), any PR for that head and base, the approved paths apart from
the staged and the unexpected ones, cache files, and the title, body and commit message with the
publication check (no AI attribution, no session links, no credentials). `pai worktree publish
PLAN.json --yes --draft|--ready` re-checks for drift, commits only the approved literal paths with
the reviewed message and your hooks (other staged changes stay staged), pushes without force,
reads the remote SHA back and only then opens or reuses the PR. It records each step in
`PLAN.json.journal`: a rerun after an interruption continues and never commits, pushes or opens
twice. Nothing is merged.

**In the browser.** `pai worktree web [--queues DIR]` (127.0.0.1, one-time link, as `pai web`):
a worktree chooser, the instruction file with its manual, generated and inherited parts, side by
side with a preview, proposals shown as a diff before they reach the editor, confirmed saves
that refuse a file changed on disk, an undo of the last save made there; the refresh commands
with expected and actual SHAs and a "Check again" history; the split preview; and the PR
preview, whose publish needs the reviewed preview, draft or ready chosen, and a confirmation.

### Troubleshooting

- **JEV shows `skipped`:** no key. Set `TYPESAFE_API_KEY` or run `pai init`; `pai jev status`
  says where pai looks. `timeout` or `unavailable`: check `TYPESAFE_BASE_URL` and the network,
  or raise `JEV_TIMEOUT`. `error` (401/403): the key is wrong or revoked; `pai jev check` tells
  which. `malformed`: the endpoint is not System One. Every case falls back to the templates.
- **`FAILED push`:** Git could not authenticate or the remote refused. Check `git push` by hand
  (`ssh -T git@github.com`, or your credential helper). Nothing was forced; rerun
  `pai worktree publish PLAN.json --yes --draft|--ready` to continue after the commit.
- **`could not ask GitHub` / `the PR was not opened`:** `gh auth status`, then `gh auth login`.
  The branch is already pushed; the rerun opens the PR without pushing again.
- **`is not a GitHub remote`:** the remote URL is not GitHub's (a mirror, an SSH alias): preview
  again with `--github OWNER/REPO`, or publish with `--no-pr`.
- **`DRIFT`:** a file, HEAD, the staged set or the remote changed after the preview. Preview again.
- **`the fetch updated refs, not this worktree`:** you fetched but did not create or move the
  worktree; run the remaining refresh commands, then verify again.
