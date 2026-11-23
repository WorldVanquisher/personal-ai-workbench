# Maintainer notes: the R6 environment (not in the archive)

This page keeps what the user manuals used to say about the maintainer's own setup on R6 day
(2026-11-22): a NAS that runs the rounds, a vault synced with Syncthing in Docker, rounds prepared
by the scripts `r6-prepare.sh` and `r6-worktrees.sh` (kept outside this repository), lanes in one
tmux session named `r6`, worktrees under `~/workspace/pai-workers/<RUN>/<lane>`, and queues under
`~/.cache/pai-<RUN>/<lane>/`. It moved here from docs/REFRESH.md (R6-FIN-024, the newbie walk's
F15 and F16), because a new user has none of these. The general versions of the same advice are in
[REFRESH.md](../REFRESH.md) parts (c) and (e). `release/allowlist.txt` does not list this file.

Words used here: **R6**, the sixth round of PAI's own development; **lane**, **mastermind**: see
[GLOSSARY.md](../GLOSSARY.md); **FLH**, the maintainer's other project, where several of these
traps were met.

## Pitfalls met on R6 day, as they were written

| Pitfall | What happens | Do this |
| --- | --- | --- |
| **Container path vs host path** | Syncthing runs in Docker; its UI shows the vault as `/vaults/MainVault`. That path does not exist on the host; there it is the folder Syncthing's volume maps to (on the R6 NAS, under `/srv/…/obsidian/MainVault`). A prompt filled with the container path sends workers to a folder that is not there. | Always use the host path. `r6-prepare.sh` works it out from its own location, or takes `VAULT=<host path>`. Check with `ls "$VAULT/Agent Inbox"` on the host. |
| **Sync delay** | Syncthing copies a note between devices seconds to minutes later. On 2026-11-22 the copy from Windows to the NAS was slow twice (reported by the owner), so a worker on the NAS could have read the old version of a note just edited on Windows. | Before telling a worker to read a note, compare checksums on both machines: `sha256sum "$VAULT/PAI/decisions/R6-20261122-D9.md"` on each, and wait until they match. Put the sha in the message to the worker ("read X, sha256 abc123…"), so it can check too. With the core lane's `pai vault wait FILE --sha SHA --timeout 120` the round machine waits until that version has arrived. |
| **What does not sync: `.stglobalignore`** | Per-device files (Obsidian's `workspace.json`) cause conflicts on every click, while worker output such as `__pycache__` should never sync. | Keep shared ignore rules in `$VAULT/.stglobalignore`, which syncs. On each device, put one line in its own `.stignore`: `#include .stglobalignore`. Ignore `.obsidian/workspace.json`, `.obsidian/workspace-mobile.json`, `.obsidian/**/*.sync-conflict-*` and `(?d)__pycache__`. Do not ignore notes: their conflict copies must sync, so that you see them. |
| **Several clones on one machine** | Two checkouts of the same repository: worktrees, fetches and `pai` defaults can end up on the wrong one. | Name the clone explicitly: `PAI_REPO=PATH` (or `FLH_REPO=`) for the scripts; `pai init --repo PATH`; `git -C PATH …`. |
| **A check that passes when its file is missing** | The old placeholder check wrote its input with a redirect. When the prompt file was missing it created an empty file, found no placeholder in it, and said "all filled". | Check that the file exists and is non-empty first: `[ -s "$f" ] \|\| { echo missing; exit 1; }`. `r6-prepare.sh` does this. |
| **Trust prompt in every new folder** | Claude Code asks once per new directory. A new worktree location each round means a prompt per lane per round, and a task waiting behind each. | Keep every round under one trusted root: `~/workspace/pai-workers/<RUN>/<lane>`. |
| **Deleting a worktree that is in use** | `git worktree remove --force` throws away uncommitted work, and a live session keeps writing into a deleted folder. | Remove only clean, idle worktrees: `git -C WT status --porcelain` must print nothing, and no process may have its cwd inside (`r6-worktrees.sh` checks `/proc/*/cwd`). Never delete branches. |
| **Tool versions differ from CI** | FLH on Node 22.12.0: 10 recorder tests failed that pass on Node 22.23.5 (CI's version). | Pin and check before testing: Node from `.nvmrc` (`node -v` must match), Go from `go.mod` (`go version`). Stop rather than run on another version. |

## Recovering R6-style lanes (one tmux session `r6`, started by hand)


You see: claude.ai shows the session offline, or `pai status` says `rc not-connected`, while the
tmux window still runs.

1. `tmux attach -t r6` (R6-style lanes) or `pai attach LANE` (`pai run` lanes). The worker is still
   working; you can follow and type here.
2. Leave with `Ctrl-b d`. That detaches and stops nothing.
3. If the window shows a shell prompt instead of Claude Code, the session exited: continue below.

### R6 lanes: the session exited, or the machine rebooted

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

The same steps for `pai round` lanes, without the scripts: [REFRESH.md](../REFRESH.md) part (e).
