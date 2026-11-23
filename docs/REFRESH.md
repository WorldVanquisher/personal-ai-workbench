# Refresh: upgrading pai and starting every new round

English | [简体中文](REFRESH.zh-CN.md)

[FIRST-USE.md](FIRST-USE.md) gets you to one finished round. This page is for every round after
that:

- (a) upgrade pai itself;
- (b) start a round from the merged main;
- (c) the traps that were really hit on R6 day (2026-11-22);
- (d) a cheat sheet for the daily commands;
- (e) what to do when a worker, a session or the machine stops.

Words: [GLOSSARY.md](GLOSSARY.md).

## (a) Upgrade pai

Upgrade the same way you installed. `pai doctor`'s first line says which way that was:
`bundle pai X intact: … match SHA256SUMS` means the archive, and
`package pai-workbench X intact: … match its RECORD` means uv or pip.

Find the new version number on the
[release page](https://github.com/WorldVanquisher/personal-ai-workbench/releases). The examples
use `0.4.1`; put yours in `V`.

**From the archive** (keeps every earlier version for a rollback):

```sh
V=0.4.1
mkdir -p ~/Downloads && cd ~/Downloads
U=https://github.com/WorldVanquisher/personal-ai-workbench/releases/download/v$V
curl -fLO "$U/pai-workbench-$V.tar.gz" && curl -fLO "$U/pai-workbench-$V.tar.gz.sha256"
sha256sum -c "pai-workbench-$V.tar.gz.sha256" && tar -xzf "pai-workbench-$V.tar.gz"
"pai-workbench-$V/bin/pai" install
```

If `sha256sum -c` says `FAILED`, delete both files and download again.

Expected:

- `launcher … -> …/versions/0.4.1/bin/pai (was 0.4.0)`, then
- `kept  earlier version(s) 0.4.0 in …/versions (delete them when satisfied)`.

A failure at any step leaves the previous version working. Configuration, task notes and
credentials are never touched.

**Roll back** to a version you kept: run `install` from its folder.
`ls ~/.local/share/pai-workbench/versions/` lists the kept versions.

```sh
~/.local/share/pai-workbench/versions/0.4.0/bin/pai install
```

Expected: `version … 0.4.0 (already installed and intact; kept)` and
`launcher … -> …/versions/0.4.0/bin/pai (was 0.4.1)`.

**With uv:**

```sh
V=0.4.1
uv tool install --force "git+https://github.com/WorldVanquisher/personal-ai-workbench@v$V"
```

Success: `Installed 1 executable: pai`, and `pai --version` prints `pai 0.4.1`. If uv says the tag
does not exist, check the number on the release page; the tag is `v` plus the version. To roll
back, set `V=` to the old version and run the same two lines.

**With pip in a venv** (FIRST-USE path B2):

```sh
V=0.4.1
~/.venvs/pai/bin/pip install --upgrade "git+https://github.com/WorldVanquisher/personal-ai-workbench@v$V"
```

Success: pip ends with `Successfully installed pai-workbench-0.4.1`, and `pai --version` prints
`pai 0.4.1`. If pip says `Cannot import 'setuptools.build_meta'`, run
`~/.venvs/pai/bin/pip install setuptools` and repeat. To roll back, set `V=` to the old version and
run the same two lines. uv and pip keep no copy of the old version, so a rollback downloads it
again.

**Confirm the upgrade:**

```sh
pai --version        # the new version
pai doctor           # first line: "… intact"; then every ESSENTIAL line ok
```

If `pai --version` still shows the old version, another `pai` comes first on your PATH. Run
`type -a pai` and remove or reorder the extra one. If you run `pai install` from a uv or pip
install, it stops with "an installed Python package …": use the uv or pip line above instead.

**After the upgrade,** if the new version ships a prompt library (reported-by-prompts; in the
prompts lane, not released yet), first compare your project's copy, then write the update:

```sh
pai prompt diff --dest <vault>/PAI/prompts
pai prompt init --dest <vault>/PAI/prompts --write
```

Parts you edited by hand are outside the managed markers and are kept.

## (b) Every round

<!-- refresh-b:begin -->
A round starts from the **merged** main, in **new** worktrees under one trusted root, with freshly
filled prompts. `pai round` does the steps the R6 scripts did, and keeps the round in
`<vault>/Agent Inbox/<ROUND>/ROUND.json`.

**0. Once per machine: name the project's GitHub repository.** `pai round new` finds the main
clone by its GitHub remote. Without this file it stops with `which GitHub repository? give --github
OWNER/NAME or set [project.pai] github`:

```sh
mkdir -p ~/.config/pai && printf '[project.pai]\ngithub = "OWNER/NAME"\n' >> ~/.config/pai/round.toml
```

Other keys (vault, trusted root, tmux session) are optional: `docs/ROUND.md` § Configuration. The
search for the clone covers `~` and `/srv`, five levels down.

**1. Preview the round, then create it.**

```sh
pai round new pai R7-YYYYMMDD --prompt "99 Templates/PAI Worker Prompt R7.md" --vault "$VAULT"           # preview; exit 1 if something is in the way
pai round new pai R7-YYYYMMDD --prompt "99 Templates/PAI Worker Prompt R7.md" --vault "$VAULT" --write   # does it
```

What it does:

- reads main's SHA with `git ls-remote` (or takes `--base SHA`);
- finds the one main clone (refuses and lists them when there are several: add `--repo PATH`);
- fills and splits the prompt per lane, and refuses if a placeholder is left;
- creates `worker/<ROUND>/<lane>` worktrees under the trusted root.

Re-running is safe. A lane shown as `CONFLICT` (a folder in the way, a branch checked out
elsewhere) stops the write until you fix it.

**2. Start each lane** in tmux, with Remote Control, and paste its prompt:

```sh
pai round launch docs --paste-prompt --write
pai round status                                  # one line per lane: running / idle / blocked-on-dialog / exited …
```

If a lane shows `blocked-on-dialog` with dialog `trust`, answer the folder-trust question in that window yourself.
`launch` sent nothing then, so run the same `pai round launch docs --paste-prompt --write` line again:
it starts nothing new and pastes the prompt (`sent (… bracketed paste True, then Enter)`). Then check
the first reply: it must list the files the worker read and its first item IDs.

**3. Every day:** `pai round status` (add `--write-vault` to refresh `PAI/Home.md`), and
`pai round next LANE --send` when a lane has stopped at a checkpoint. It reads the lane's
`RESTART.md` (newest `cp-*` folder, or the lane's work folder) and refuses while there is none.

**4. Close the round** when every PR is merged:

```sh
pai round close              # the acceptance checklist, and which worktrees would be reclaimed
pai round close --write      # reclaims only clean, idle worktrees; branches are kept
```

A worktree whose Claude session still runs, even idle, is kept (`keep … in use by …`). End that
session first (`/exit` in its window), then run `pai round close --write` again; ROUND.json is
archived as closed once no lane worktree remains.
<!-- refresh-b:end -->

## (c) Common pitfalls

Each of these was really hit on R6 day.

| Pitfall | What happens | Do this |
| --- | --- | --- |
| **Container path vs host path** | Syncthing runs in Docker; its UI shows the vault as `/vaults/MainVault`. That path does not exist on the host; there it is the folder Syncthing's volume maps to (on the R6 NAS, under `/srv/…/obsidian/MainVault`). A prompt filled with the container path sends workers to a folder that is not there. | Always use the host path. `r6-prepare.sh` works it out from its own location, or takes `VAULT=<host path>`. Check with `ls "$VAULT/Agent Inbox"` on the host. |
| **Sync delay** | Syncthing copies a note between devices seconds to minutes later. On 2026-11-22 the copy from Windows to the NAS was slow twice (reported by the owner), so a worker on the NAS could have read the old version of a note just edited on Windows. | Before telling a worker to read a note, compare checksums on both machines: `sha256sum "$VAULT/PAI/decisions/R6-20261122-D9.md"` on each, and wait until they match. Put the sha in the message to the worker ("read X, sha256 abc123…"), so it can check too. With the core lane's `pai vault wait FILE --sha SHA --timeout 120` the round machine waits until that version has arrived. |
| **What does not sync: `.stglobalignore`** | Per-device files (Obsidian's `workspace.json`) cause conflicts on every click, while worker output such as `__pycache__` should never sync. | Keep shared ignore rules in `$VAULT/.stglobalignore`, which syncs. On each device, put one line in its own `.stignore`: `#include .stglobalignore`. Ignore `.obsidian/workspace.json`, `.obsidian/workspace-mobile.json`, `.obsidian/**/*.sync-conflict-*` and `(?d)__pycache__`. Do not ignore notes: their conflict copies must sync, so that you see them. |
| **Sync-conflict files** | Two devices change one note before syncing. Syncthing keeps both: `NAME.sync-conflict-<date>-<time>-<device>.md` next to `NAME.md`. PAI never reads a conflict copy as state, and does not rewrite a note that has one beside it (reported-by-round). | Find them with `find "$VAULT" -name '*.sync-conflict-*'`. For each one, `diff` it with the note, keep what you want in `NAME.md`, and delete the copy. PAI's next `--write-vault` then updates the note again. |
| **Several clones on one machine** | Two checkouts of the same repository: worktrees, fetches and `pai` defaults can end up on the wrong one. | Name the clone explicitly: `PAI_REPO=PATH` (or `FLH_REPO=`) for the scripts; `pai init --repo PATH`; `git -C PATH …`. |
| **A check that passes when its file is missing** | The old placeholder check wrote its input with a redirect. When the prompt file was missing it created an empty file, found no placeholder in it, and said "all filled". | Check that the file exists and is non-empty first: `[ -s "$f" ] \|\| { echo missing; exit 1; }`. `r6-prepare.sh` does this. |
| **Braces in prompt text taken for placeholders** | A prompt that *talks about* placeholders (two braces around capitals) fails the "nothing left to fill" check. | Describe them in words ("two left braces followed by capitals"), as the R6 prompt does. With the prompts lane's `pai prompt render`, write `\{{NAME}}` (reported-by-prompts, docs/PROMPTS.md §2). |
| **Trust prompt in every new folder** | Claude Code asks once per new directory. A new worktree location each round means a prompt per lane per round, and a task waiting behind each. | Keep every round under one trusted root: `~/workspace/pai-workers/<RUN>/<lane>`. |
| **Deleting a worktree that is in use** | `git worktree remove --force` throws away uncommitted work, and a live session keeps writing into a deleted folder. | Remove only clean, idle worktrees: `git -C WT status --porcelain` must print nothing, and no process may have its cwd inside (`r6-worktrees.sh` checks `/proc/*/cwd`). Never delete branches. |
| **Tool versions differ from CI** | FLH on Node 22.12.0: 10 recorder tests failed that pass on Node 22.23.5 (CI's version). | Pin and check before testing: Node from `.nvmrc` (`node -v` must match), Go from `go.mod` (`go version`). Stop rather than run on another version. |

## (d) Daily operations cheat sheet

| I want to … | Command |
| --- | --- |
| see every lane of a `pai run` | `pai status` (`--brief` for one block each; `--json` for scripts) |
| see where a worker is in its items | `pai queue status ~/.cache/pai-<RUN>/<lane>/queue` |
| get a resume prompt for a fresh session | `pai queue restart ~/.cache/pai-<RUN>/<lane>/queue` |
| check a handoff's Items table against the queue | `pai queue check QUEUE HANDOFF.md` |
| record a checkpoint and its sha256 | `pai queue checkpoint QUEUE R6-XXX-NNN --handoff HANDOFF.md` |
| give one `pai run` lane its next note | `pai next a` |
| send a long message into a lane | `pai paste a message.md --enter` |
| watch every lane in a browser | `pai web` |
| a handoff draft into the vault | `pai handoff new PROJECT --task-id ID --summary "…"` (`pai handoff new --help` for all fields) |
| what would be published | `pai pr plan a`, then `pai pr check a` |
| close a round's processes (files stay) | `pai cleanup`, then `pai cleanup --apply --stop-running` |
| a fresh worktree after a merge | `pai worktree refresh` (prints the commands; changes nothing) |

**Writing the hand-over pack** (before you clear a mastermind's context): update
`COORDINATOR-LOG.md` with the last decisions. Then write, for every lane, its queue status and the
path of its latest checkpoint. `pai round brief` will generate this (planned, round lane).

**Closing a round:**

1. every lane has a sealed final checkpoint with `HANDOFF.md` and `NEXT-PR.md`;
2. the decision sheet is answered;
3. PRs are merged by you;
4. `pai cleanup --apply --stop-running` (pai run lanes), or end the tmux sessions;
5. `r6-worktrees.sh` (plan, then apply) removes the clean, idle worktrees.

## (e) Recovery

Whatever stopped, the work is safe on disk. That includes the worktree and its branch, the queue
and its log (`~/.cache/pai-<RUN>/<lane>/queue.log`, fsynced on every mark), and every sealed
checkpoint in `Agent Inbox/<RUN>/<lane>/cp-*`. Recovery means starting a session that reads that
state. It never means redoing the work.

First, for any lane, see where it is:

```sh
pai queue status ~/.cache/pai-<RUN>/<lane>/queue     # done / blocked / todo by ID, and "next: R6-…"
pai queue show   ~/.cache/pai-<RUN>/<lane>/queue     # checkpoints; each should say "matches its recorded sha256"
```

If `show` says a handoff **no longer matches**, the file was edited after it was sealed. Read the
difference before resuming.

### The worker stopped early

You see: the session is idle, and its last message is neither "final" nor "有意停在 cp-X".

1. Run `pai queue status QUEUE` and note `next:`.
2. Type into the session: `Continue from R6-XXX-NNN (pai queue next QUEUE). Rules unchanged.`
3. Success: its next message names that ID and it marks it `doing`. If it starts again from 001,
   stop it, and paste the output of `pai queue restart QUEUE` instead.

### The context is full

You see: the worker says it stops at cp-X, and `RESTART.md` is in its work folder.

1. `pai queue show QUEUE`: cp-X is listed and `matches its recorded sha256`. If cp-X is missing,
   ask the worker to seal it first, while the session is still alive.
2. Type `/clear` in that session (or start a new one in the **same** worktree, step 4 of (b)).
3. Paste the opening text from `~/.cache/pai-<RUN>/<lane>/RESTART.md`.
4. Success: the first reply lists what it re-read and the next ID from step 1.

### The 5-hour usage limit is used up

You see: Claude Code says the usage limit is reached, and when it resets.

1. Do nothing to the lane. Do **not** start a second session on it: two sessions would edit one
   worktree.
2. After the reset time, run `pai queue status QUEUE` and note `next:`. Then type into the session:
   `Continue from R6-XXX-NNN (pai queue next QUEUE). Rules unchanged.`
3. Success: its next message names that ID. If it still says the limit is reached, the reset time
   has not passed: wait and repeat step 2. If several lanes stopped, restart them one at a time.

### Remote Control disconnected

You see: claude.ai shows the session offline, or `pai status` says `rc not-connected`, while the
tmux window still runs.

1. `tmux attach -t r6` (R6-style lanes) or `pai attach LANE` (`pai run` lanes). The worker is still
   working; you can follow and type here.
2. Leave with `Ctrl-b d`. That detaches and stops nothing.
3. If the window shows a shell prompt instead of Claude Code, the session exited: continue below.

### The session exited, or the machine rebooted

You see: `tmux ls` has no session (or `no server running`), and `pai status` says `exited`.

1. For `pai run` lanes: `pai resume`. It restarts the closed workers on the same branches. Success:
   `pai status` shows the lane `running` again.
2. For R6-style lanes, start the session again in the **same** worktree:

   ```sh
   tmux new-session -d -s r6 -n <lane> -c ~/workspace/pai-workers/<RUN>/<lane>   # after a reboot
   tmux new-window -t r6 -n <lane> -c ~/workspace/pai-workers/<RUN>/<lane>       # if session r6 still exists
   tmux send-keys -t r6:<lane> 'claude --remote-control --name "PAI <RUN> <lane>"' Enter
   pai queue restart ~/.cache/pai-<RUN>/<lane>/queue                              # prints the resume prompt
   ```

   Paste that prompt (or the opening text of `RESTART.md`) into the session. Success: the first
   reply names the next ID from `pai queue status`. If a folder-trust prompt appears, answer it
   yourself (`tmux attach -t r6`).
3. Do not run `r6-worktrees.sh apply` to "reset" a lane: it skips worktrees that exist and never
   resets one.
