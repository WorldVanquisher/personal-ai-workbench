# Refresh: upgrading pai and starting every new round

English | [简体中文](REFRESH.zh-CN.md)

[FIRST-USE.md](FIRST-USE.md) gets you to one finished round. This page is for every round after
that:

- (a) upgrade pai itself;
- (b) start a round from the merged main;
- (c) common pitfalls;
- (d) a cheat sheet for the daily commands;
- (e) what to do when a worker, a session or the machine stops.

Words: [GLOSSARY.md](GLOSSARY.md).

## (a) Upgrade pai

Upgrade the same way you installed. `pai doctor`'s first line says which way that was:
`bundle pai X intact: … match SHA256SUMS` means the archive, and
`package pai-workbench X intact: … match its RECORD` means uv or pip.

Find the new version number on the
[release page](https://github.com/WorldVanquisher/personal-ai-workbench/releases). The examples
upgrade 0.5.0 to `0.5.1`, a stand-in for whatever version comes next; put the real number in `V`.

**From the archive** (keeps every earlier version for a rollback):

```sh
V=0.5.1
mkdir -p ~/Downloads && cd ~/Downloads
U=https://github.com/WorldVanquisher/personal-ai-workbench/releases/download/v$V
curl -fLO "$U/pai-workbench-$V.tar.gz" && curl -fLO "$U/pai-workbench-$V.tar.gz.sha256"
sha256sum -c "pai-workbench-$V.tar.gz.sha256" && tar -xzf "pai-workbench-$V.tar.gz"
"pai-workbench-$V/bin/pai" install
```

If `sha256sum -c` says `FAILED`, delete both files and download again.

Expected:

- `launcher … -> …/versions/0.5.1/bin/pai (was 0.5.0)`, then
- `kept  earlier version(s) 0.5.0 in …/versions (delete them when satisfied)`.

A failure at any step leaves the previous version working. Configuration, task notes and
credentials are never touched.

**Roll back** to a version you kept: run `install` from its folder.
`ls ~/.local/share/pai-workbench/versions/` lists the kept versions.

```sh
~/.local/share/pai-workbench/versions/0.5.0/bin/pai install
```

Expected: `version … 0.5.0 (already installed and intact; kept)` and
`launcher … -> …/versions/0.5.0/bin/pai (was 0.5.1)`.

**With uv:**

```sh
V=0.5.1
uv tool install --force "git+https://github.com/WorldVanquisher/personal-ai-workbench@v$V"
```

Success: `Installed 1 executable: pai`, and `pai --version` prints `pai 0.5.1`. If uv says the tag
does not exist, check the number on the release page; the tag is `v` plus the version. To roll
back, set `V=` to the old version and run the same two lines.

**With pip in a venv** (FIRST-USE path B2):

```sh
V=0.5.1
~/.venvs/pai/bin/pip install --upgrade "git+https://github.com/WorldVanquisher/personal-ai-workbench@v$V"
```

Success: pip ends with `Successfully installed pai-workbench-0.5.1`, and `pai --version` prints
`pai 0.5.1`. If pip says `Cannot import 'setuptools.build_meta'`, run
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

**After the upgrade,** if you installed the prompt library into your vault (new in 0.5.0), first
compare your project's copy with the new one, then write the update:

```sh
pai prompt diff --dest <vault>/PAI/prompts
pai prompt init --dest <vault>/PAI/prompts --write
```

Parts you edited by hand are outside the managed markers and are kept.

## (b) Every round

**Two ways to run the next round.** If you work as in FIRST-USE, with `pai run` and task notes in
one repository, the next round is simply: update main, write the next task notes
(`pai task new … --write`), `pai run`. The cheat sheet in (d) covers the rest; nothing below is
needed. `pai round` is for a larger, coordinated round: several lanes at once, each started from
its own part of one round prompt, with checkpoints and decisions kept as notes in your vault. It
uses the same tmux and agents as `pai run`, but keeps its state in `ROUND.json` instead of a run
manifest. The rest of this part is for `pai round`; its full reference is
[ROUND.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/ROUND.md).

<!-- refresh-b:begin -->
A round starts from the **merged** main, in **new** worktrees under one trusted root, with freshly
filled prompts. The names used below:

| Name | What to put there |
| --- | --- |
| `PROJECT` | a short key for your repository, the same in every command (for example `shop`) |
| `OWNER/NAME` | the repository on GitHub, as in its URL |
| `ROUND` | a name you choose for this round (for example `R7-20261201`) |
| `VAULT` | the folder that holds your notes and its `Agent Inbox/` |
| `PROMPT` | your round prompt: a Markdown note with one part per lane (format: ROUND.md) |
| `LANE` | one lane's name, as written in the prompt |

**0. Once per machine: name the project's GitHub repository.** `pai round new` finds the main
clone by its GitHub remote. Without this setting it stops with `which GitHub repository? give
--github OWNER/NAME, or add github = "OWNER/NAME" under [project.PROJECT] in ~/.config/pai/round.toml`:

```sh
mkdir -p ~/.config/pai && printf '[project.PROJECT]\ngithub = "OWNER/NAME"\n' >> ~/.config/pai/round.toml
```

Replace `PROJECT` and `OWNER/NAME` in that line first. Other keys (vault, trusted root, tmux
session) are optional (ROUND.md § Configuration). The search for the clone covers `~` and `/srv`,
five levels down.

**1. Preview the round, then create it.**

```sh
pai round new PROJECT ROUND --prompt PROMPT --vault VAULT           # preview; exit 1 if something is in the way
pai round new PROJECT ROUND --prompt PROMPT --vault VAULT --write   # does it
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
pai round launch LANE --paste-prompt --write
pai round status                                  # one line per lane: running / idle / blocked-on-dialog / exited …
```

If a lane shows `blocked-on-dialog` with dialog `trust`, answer the folder-trust question in that window yourself.
`launch` sent nothing then, so run the same `pai round launch LANE --paste-prompt --write` line again:
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

| Pitfall | What happens | Do this |
| --- | --- | --- |
| **The vault is synced between machines** | A sync tool (Syncthing, a cloud folder) copies a note seconds to minutes later, so a worker on another machine can read the old version of a note you just edited. A tool that runs in a container may also show the vault under a path that does not exist on the host. | Give workers the host path (`ls "VAULT/Agent Inbox"` must work on the machine that runs them). Before telling a worker to read a note you just changed, wait for that version: `pai vault wait FILE --sha SHA --timeout 120` (SHA from `sha256sum FILE` where you edited it), and put the sha in your message. |
| **Sync-conflict files** | Two devices change one note before syncing, and the sync tool keeps both (Syncthing: `NAME.sync-conflict-<date>-<time>-<device>.md` next to `NAME.md`). PAI never reads a conflict copy as state, and does not rewrite a note that has one beside it. | `pai vault lint` lists them (`CONFLICT`). For each one, `diff` it with the note, keep what you want in `NAME.md`, and delete the copy. PAI's next `--write-vault` then updates the note again. |
| **Several clones on one machine** | Two checkouts of the same repository: worktrees, fetches and `pai` defaults can end up on the wrong one. | Name the clone explicitly: `pai init --repo PATH`, `pai round new … --repo PATH`, `git -C PATH …`. |
| **Braces in prompt text taken for placeholders** | A prompt that *talks about* placeholders (two braces around capitals) fails the "nothing left to fill" check. | Write `\{{NAME}}` for a literal one (`pai prompt render` keeps it as text), or describe it in words. |
| **Trust prompt in every new folder** | Claude Code asks once per new directory. A new worktree location each round means a prompt per lane per round, and a task waiting behind each. | Keep every round under one trusted root (`pai init --update --worktree-root DIR`; for `pai round`, the trusted root in round.toml). |
| **Deleting a worktree that is in use** | `git worktree remove --force` throws away uncommitted work, and a live session keeps writing into a deleted folder. | Let pai do it: `pai cleanup` (for `pai run`) and `pai round close --write` remove only clean worktrees that no process uses. Never delete branches. |
| **Tool versions differ from CI** | A test can fail on your machine and pass in CI only because a runtime version differs. | Pin and check before testing (for example Node from `.nvmrc`, Go from `go.mod`). Stop rather than run on another version. |

## (d) Daily operations cheat sheet

`QUEUE` below is a worker's queue file, kept with `pai queue` (the worker's prompt says where).

| I want to … | Command |
| --- | --- |
| see every lane of a `pai run` | `pai status` (`--brief` for one block each; `--json` for scripts) |
| see every lane of a `pai round` | `pai round status` |
| see where a worker is in its items | `pai queue status QUEUE` |
| get a resume prompt for a fresh session | `pai queue restart QUEUE` |
| check a handoff's Items table against the queue | `pai queue check QUEUE HANDOFF.md` |
| record a checkpoint and its sha256 | `pai queue checkpoint QUEUE ITEM-ID --handoff HANDOFF.md` |
| give one `pai run` lane its next note | `pai next a` |
| send a long message into a lane | `pai paste a message.md --enter` |
| watch every lane in a browser | `pai web` |
| a handoff draft into the vault | `pai handoff new PROJECT --task-id ID --summary "…"` (`pai handoff new --help` for all fields) |
| what would be published | `pai pr plan a`, then `pai pr check a` |
| close a round's processes (files stay) | `pai cleanup`, then `pai cleanup --apply --stop-running` |
| a fresh worktree after a merge | `pai worktree refresh` (prints the commands; changes nothing) |

**Writing the hand-over pack** (before you clear a coordinating session's context):
`pai round brief` prints a short package with every lane's state, and `pai round log "DECISION"
--why "REASON"` keeps the decisions in `COORDINATOR-LOG.md`.

**Closing a round:**

1. every lane has a sealed final checkpoint with `HANDOFF.md` and `NEXT-PR.md`;
2. the decision sheet is answered;
3. PRs are merged by you;
4. `pai cleanup --apply --stop-running` (`pai run` lanes);
5. `pai round close --write` (`pai round` lanes) removes the clean, idle worktrees.

## (e) Recovery

Whatever stopped, the work is safe on disk. That includes the worktree and its branch, the queue
and its log (`QUEUE.log`, fsynced on every mark), and every sealed checkpoint in
`Agent Inbox/<ROUND>/<lane>/cp-*`. Recovery means starting a session that reads that state. It
never means redoing the work.

First, for any lane, see where it is:

```sh
pai queue status QUEUE     # done / blocked / todo by ID, and "next: …"
pai queue show   QUEUE     # checkpoints; each should say "matches its recorded sha256"
```

If `show` says a handoff **no longer matches**, the file was edited after it was sealed. Read the
difference before resuming.

### The worker stopped early

You see: the session is idle, and its last message is neither "final" nor "stopping at cp-X".

1. Run `pai queue status QUEUE` and note `next:`.
2. Type into the session: `Continue from ITEM-ID (pai queue next QUEUE). Rules unchanged.`
3. Success: its next message names that ID and it marks it `doing`. If it starts again from 001,
   stop it, and paste the output of `pai queue restart QUEUE` instead.

### The context is full

You see: the worker says it stops at cp-X, and `RESTART.md` is in its work folder.

1. `pai queue show QUEUE`: cp-X is listed and `matches its recorded sha256`. If cp-X is missing,
   ask the worker to seal it first, while the session is still alive.
2. Type `/clear` in that session (or start a new one in the **same** worktree).
3. Paste the opening text of that `RESTART.md` (for a `pai round` lane: `pai round next LANE --send`).
4. Success: the first reply lists what it re-read and the next ID from step 1.

### The 5-hour usage limit is used up

You see: Claude Code says the usage limit is reached, and when it resets.

1. Do nothing to the lane. Do **not** start a second session on it: two sessions would edit one
   worktree.
2. After the reset time, run `pai queue status QUEUE` and note `next:`. Then type into the session:
   `Continue from ITEM-ID (pai queue next QUEUE). Rules unchanged.`
3. Success: its next message names that ID. If it still says the limit is reached, the reset time
   has not passed: wait and repeat step 2. If several lanes stopped, restart them one at a time.

### Remote Control disconnected

You see: claude.ai shows the session offline, or `pai status` says `rc not-connected`, while the
tmux window still runs.

1. `pai attach LANE` (`pai run` lanes), or `tmux attach -t SESSION` for a `pai round` lane, where
   `SESSION` is the part before the colon in its window, as `pai round status` shows it. The worker
   is still working; you can follow and type here.
2. Leave with `Ctrl-b d`. That detaches and stops nothing.
3. If the window shows a shell prompt instead of Claude Code, the session exited: continue below.

### The session exited, or the machine rebooted

You see: `tmux ls` has no session (or `no server running`), and `pai status` says `exited`.

1. For `pai run` lanes: `pai resume`. It restarts the closed workers on the same branches. Success:
   `pai status` shows the lane `running` again.
2. For `pai round` lanes, start the session again in the **same** worktree, then send the resume:

   ```sh
   pai round launch LANE --write        # a new session in the lane's own worktree
   pai round next LANE --send           # the resume message from its RESTART.md
   ```

   Success: the first reply names the next ID from `pai queue status`. If a folder-trust prompt
   appears, answer it yourself (`pai round status` shows `blocked-on-dialog`).
3. Never delete and re-create a lane's worktree to "reset" it: the work in it is what you resume.
