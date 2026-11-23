# Refresh: upgrading pai and starting every new round

<!-- doc_check: pending pai round, pai prompt, pai vault -->

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

The `pai round` version of this part is ready in [REFRESH-b-round.md](REFRESH-b-round.md); it replaces the block below after integration.

<!-- refresh-b:begin -->
A round starts from the **merged** main, in **new** worktrees, with freshly filled prompts. Steps
1–5 below are what was really used on R6 day: the scripts `r6-prepare.sh` and `r6-worktrees.sh`
from the vault's `99 Templates/`.

Set `VAULT` once, to the vault folder **on this machine** (the host path, not a container path;
see (c)). Check it: `ls "$VAULT/Agent Inbox"` must list the inbox.

```sh
VAULT=/path/to/your/vault
```

**1. Wait for the merge, then read the new base.** Merging on GitHub does not change your
machine. `r6-prepare.sh` reads main's SHA straight from GitHub, so it needs no local clone:

```sh
git ls-remote https://github.com/WorldVanquisher/personal-ai-workbench.git refs/heads/main
```

Success: one line, a 40-character SHA, then `refs/heads/main`. It must be the merge commit
you expect. Check it against the PR page.

**2. Fill and split the prompts.**

```sh
bash "$VAULT/99 Templates/r6-prepare.sh" pai
```

It fills `{{RUN_ID}}`-style placeholders, refuses if any is left, and writes one paste-ready file
per lane. Expected output:

- `RUN=R6-YYYYMMDD`, `BASE=<sha>`, `VAULT=…`;
- `输出目录：~/r6-prompts/pai-<RUN>`;
- one line per lane file, for example `docs.txt  130 行`.

If it stops:

| Message | Fix |
| --- | --- |
| `在 … 下面找不到 Agent Inbox` | the vault path is wrong; pass it: `VAULT=/path/to/vault bash …/r6-prepare.sh pai` |
| `找不到 prompt 文件` | pass the prompt note as the second argument |
| `拿不到 … 的 main SHA` | no network or no GitHub access |
| `上面这些占位还没填` | add the value to the script's `sed` list, or remove the placeholder from the note |
| `part2 的第一行不是「线名：xxx。」` | each lane part must start with that line |

**3. Preview, then make the worktrees** (and clean up old ones safely):

```sh
bash "$VAULT/99 Templates/r6-worktrees.sh"          # plan: shows what would happen, changes nothing
bash "$VAULT/99 Templates/r6-worktrees.sh" apply    # does it
```

What `apply` does:

- It removes an old worktree only when it is clean (no uncommitted or untracked files) **and**
  idle (no process has its working directory inside it).
- It never uses `--force`, never deletes a branch, and never removes an `R6-*` worktree.
- It then creates `~/workspace/pai-workers/<RUN>/<lane>` on branch `worker/<RUN>/<lane>` from BASE.

Running it again is safe. If it says `找到多个 PAI 仓库`, choose one clone:
`PAI_REPO=~/code/personal-ai-workbench bash …/r6-worktrees.sh`.

**4. Start one worker per lane** in tmux, in its worktree, with Remote Control. Start each one
under the [trusted root](GLOSSARY.md#trusted-root) so that no new folder-trust prompt appears:

```sh
tmux new-session -d -s r6 -n docs -c ~/workspace/pai-workers/<RUN>/docs
tmux send-keys -t r6:docs 'claude --remote-control --name "PAI <RUN> docs"' Enter
```

If a trust prompt appears anyway, answer it yourself in that window (`tmux attach -t r6`).

**5. Send each lane its prompt.** Paste the lane file (`~/r6-prompts/pai-<RUN>/<lane>.txt`) into
its session, from claude.ai's Remote Control view or the tmux window. The first reply must list
the files it read and its first item IDs. Check that before you let it run.

**Coming with `pai round` (round lane; in its lane tree, not merged; reported-by-round).**
`pai round new PROJECT ROUND --lane L … --prompt FILE --write` does steps 1–3 and writes ROUND.json.
`pai round status` shows one line per lane (session, trust prompt, worktree clean or not, last
checkpoint, queue totals). See `docs/ROUND.md` once merged. Until then, use the scripts above.
<!-- refresh-b:end -->

## (c) Common pitfalls

Each of these was really hit on R6 day.

| Pitfall | What happens | Do this |
| --- | --- | --- |
| **Container path vs host path** | Syncthing runs in Docker; its UI shows the vault as `/vaults/MainVault`. That path does not exist on the host; there it is the folder Syncthing's volume maps to (on the R6 NAS, under `/srv/…/obsidian/MainVault`). A prompt filled with the container path sends workers to a folder that is not there. | Always use the host path. `r6-prepare.sh` works it out from its own location, or takes `VAULT=<host path>`. Check with `ls "$VAULT/Agent Inbox"` on the host. |
| **Sync delay** | Syncthing copies a note between devices seconds to minutes later. On 2026-11-22 the copy from Windows to the NAS was slow twice (reported by the owner), so a worker on the NAS could have read the old version of a note just edited on Windows. | Before telling a worker to read a note, compare checksums on both machines: `sha256sum "$VAULT/PAI/decisions/R6-20261122-D9.md"` on each, and wait until they match. Put the sha in the message to the worker ("read X, sha256 abc123…"), so it can check too. With the core lane's `pai vault wait FILE --sha SHA --timeout 120` (not merged yet), the round machine waits until that version has arrived. |
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
