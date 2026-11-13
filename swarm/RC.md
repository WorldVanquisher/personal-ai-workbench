# pai-swarm operator workflow: RC workers, git tasks, review, PRs

Everything here is `python3 swarm/pai_swarm.py <command>`. Standard library only, POSIX
only (Linux for `/proc`-based checks), Python 3.9 or newer, with `git`, `tmux` and `ps`
(procps; `rc status` finds each window's Claude with it, and without it reports none). The PAI CLI it calls for context and handoffs needs Python 3.11 or
newer; `PAI_PYTHON` names that interpreter when `python3` is older. It is development
tooling: it is not in the release bundle and `pai/` does not import it.

```sh
SWARM=~/workspace/pai-workers/fable/swarm          # wherever this checkout lives
M=~/flh-runs/20261113-night.json                   # your manifest (copy of examples/rc-flh.example.json)
```

What it never does: reset, clean, stash, force-push, delete a branch or worktree, kill a
tmux window, restart a running Claude, merge a PR, or log you in anywhere.

## 1. First run

```sh
python3 $SWARM/pai_swarm.py doctor                    # read-only
python3 $SWARM/pai_swarm.py setup                     # the same, plus the install command per missing tool
python3 $SWARM/pai_swarm.py setup --install-missing   # runs those commands; only for missing tools
```

`doctor` separates `MISSING` (no binary), `NOT LOGGED IN` (binary present, no login) and
`RC NOT READY` (Claude Code is logged in, but not with a claude.ai subscription login, or it
has no `remote-control` command). Installs come from the system package manager (pacman on
this NAS; apt-get, dnf, apk, Homebrew elsewhere) and, for Claude Code,
`https://claude.ai/install.sh`. Logins are yours: `gh auth login`, and `/login` inside `claude`.

## 2. Manifest

`examples/rc-flh.example.json` is a four-worker manifest for FLH. Fields:

| Field | Meaning |
| --- | --- |
| `run_id` | Names the run. A new id is a new task; a known id resumes. Letters, digits, `-`, `_`; 24 at most |
| `repository.path`, `.remote`, `.base_branch` | The clone, the remote to fetch (default `origin`) and its branch (default `main`) |
| `repository.github` | `OWNER/REPO`. The remote URL must be that repository, and `gh` is asked about it. `repository.url` pins an exact URL instead (mirrors, non-GitHub hosts) |
| `worktree_root` | Every worker worktree must be inside it |
| `tmux.session` | Session name; `{run_id}` is filled in |
| `command` | The argv started in each window. Placeholders: `{name} {lane} {branch} {worktree} {base_sha} {task} {task_path}` |
| `workers[]` | `lane` (window and worktree name), optional `worktree`, `branch`, `name`, `task` (a task note's file name without `.md`) |
| `count` | Must equal the number of workers; with no `workers` list it generates `worker-1..N` |
| `tasks` | Obsidian folder of task notes. Workers without a named task take the next `status: todo` notes by priority |
| `review` | `checks`: name → argv; `required_checks`: names that always run; `check_timeout_min` |

Command-line overrides for `rc plan` and `rc up`: `--workers N`, `--assign LANE=TASK`,
`--branch LANE=NAME`. Started from a terminal, `rc up` asks for each worker's branch name
(Enter keeps the default) before it fetches or creates anything; Ctrl-D or Ctrl-C at a
prompt starts nothing. `--no-input` never asks, and a resume never asks. Precedence: command
line, then your answer, then the manifest, then `rc/{run_id}/{lane}`.

Each window runs `exec env VAR=… command…` through tmux's default shell, which is your
`$SHELL` or else your account's login shell. That shell must accept POSIX `sh -c` syntax:
bash, zsh and dash do. With `nologin` or another non-POSIX shell, the window exits at once
(`claude exited (status 1)` in `rc status`); nothing else is affected.

## 3. New task, four-worker batch, resume

Each start is NEW or RESUME, decided by whether the run id has a record under
`~/.cache/pai-swarm/tasks/` (`$PAI_SWARM_HOME` moves it).

**New (one worker or a batch of four: the same command, the manifest decides):**

```sh
python3 $SWARM/pai_swarm.py rc plan $M              # read-only: no fetch, no branch, no tmux server
python3 $SWARM/pai_swarm.py rc up   $M --new        # --new refuses if this run id was already started
```

`up`, in this order, stopping at the first refusal with nothing started:

1. validates the repository identity, worktree root, lane and branch names and duplicate
   assignments; refuses a dirty worktree, a Git operation in progress, a branch that already
   exists or belongs to another task, a worktree locked by another run;
2. takes the worktree lock for every worker;
3. runs `git fetch origin +refs/heads/main:refs/remotes/origin/main` once, and pins the
   fetched commit for the whole batch (a stale local `origin/main` is never used; a rewritten
   remote `main` is followed). If the fetch fails, the locks are returned and no worker starts;
4. records the task (remote, base SHA, branch and worktree per worker);
5. creates each branch from the pinned commit: a new worktree, or `git switch -c` in an
   existing clean one. The previous branch is left as it was;
6. checks each worktree again (on its task branch, pinned base an ancestor) and only then
   starts `claude --remote-control --name …` in a tmux window named after the lane.

A single new task is the same with a one-worker manifest, or headless:

```sh
python3 $SWARM/pai_swarm.py run --repo ~/workspace/flh-workers/release --tasks ~/Vault/PAI/Swarm \
  --task-id FLH-131 --github WorldVanquisher/french-learning-hub --workers 2 --new
```

**Resume (after a reboot, a lost window, or a start that stopped partway):**

```sh
python3 $SWARM/pai_swarm.py rc up $M --resume       # no fetch; recorded branches and base
python3 $SWARM/pai_swarm.py run --repo ~/workspace/flh-workers/release --tasks ~/Vault/PAI/Swarm \
  --task-id FLH-131 --resume
```

A resume uses the recorded branch and base even if `origin/main` has moved, needs no
network, and leaves uncommitted edits exactly as they are. It starts only workers whose
window is gone or whose Claude process has exited; running sessions are kept.

## 4. Status

```sh
python3 $SWARM/pai_swarm.py rc status $M            # add --json for scripts; exit 0 only if all are up and verified
python3 $SWARM/pai_swarm.py tasks                   # every recorded git task, RC and headless
```

Per worker: task id, remote and URL, base branch and SHA, task branch, worktree, HEAD and
uncommitted paths, lock, and two separate lines:

- `claude`: `running (pid)`, `exited (status N)`, `no window`, or a window without Claude.
- `rc`: `verified` only when Claude Code's own session record for that exact process (same
  pid and, on Linux, the same process start time) and worktree carries a Remote Control
  bridge session and the pane shows no disconnect notice.
  `not-connected` and `disconnected` mean the process is alive without Remote Control.
  `unverified` means there is no evidence either way. A running process is never taken as
  connected.

`rc handoff $M --pai flh --pai-bin ~/workspace/personal-ai-workbench/bin/pai.py` files one
draft PAI handoff with those facts (remote, base SHA, branch, worktree, process and RC state
per lane). The headless runner adds the same line to each task's handoff evidence.

## 5. Review and PR

Task notes carry acceptance fields (see `_project-review.md`, written by `init --tasks DIR`):

```yaml
checks: [verify]          # names from the manifest's review.checks; required checks always run
acceptance:
  - Answer overview opens from the workbook page
```

```sh
python3 $SWARM/pai_swarm.py pr check $M --lane workbooks     # run the approved checks, record results in the note
python3 $SWARM/pai_swarm.py pr plan  $M --lane workbooks     # read-only: commits, checks, publication check, remote, PR
python3 $SWARM/pai_swarm.py pr open  $M --lane workbooks     # checks again, then push (never forced) and open the PR
```

- Only commands listed in the manifest run. A name the manifest does not have blocks the
  review; text from a note is never executed. The repository's own instruction files
  outrank a note.
- `pr check` appends `## Review` to the note: each check's command, exit code and time, the
  commit (SHA, author, date, subject), the snapshot SHA-256 and tree, and the acceptance
  items as unticked boxes for the owner.
- `pr open` stops before the first push if the worktree is dirty or off its task branch, a
  check fails, `gh` is not logged in, the remote branch or an existing PR does not match, or
  the publication check finds Claude attribution or a session link in the commits, the PR
  title or the PR body. `--allow-ai-attribution` opts in for that one run.
- A rerun reuses the branch and the PR it already made.
- The PR description is the note's `## PR` section plus the check table; nothing else from
  the note is published.
- It never merges.

## 6. Next task after a merge

```sh
python3 $SWARM/pai_swarm.py rc next $M --lane workbooks --plan   # read-only: asks GitHub, shows the plan
python3 $SWARM/pai_swarm.py rc next $M --lane workbooks          # --task NOTE and --branch LANE=NAME override
```

Only when `gh` reports the lane's PR as **merged into the task's base branch**, with the
branch's tip as its head, does
the worker move on: fetch, pin the new `origin/main` (which now holds the squash commit),
create `<stem>-2` (then `-3`, …; the first name free locally, on the remote and in the
records), assign the next queued note, keep the running Claude session. An open PR, a PR
closed without merging, no PR, or local commits that were not in the merged PR change
nothing. A dirty worktree blocks the rollover and is left as it is.

From a terminal, `rc next` first asks for every rolled-over lane's branch name (Enter keeps
`<stem>-N`), and changes nothing until all are answered: Ctrl-D or Ctrl-C at any prompt rolls
over no lane. `--plan` and `--no-input` never ask. Each lane's next note is reserved as it is
asked about, so a lane whose preparation is then refused leaves its note `todo` for the next
run rather than handing it to the following lane.

## 7. Publication check

```sh
python3 $SWARM/publish_check.py --repo REPO --range BASE..HEAD \
  --pr-title "feat: …" --pr-body-file body.md [--squash-message-file msg.txt] [--allow-ai-attribution]
```

Rejects `Co-authored-by` trailers naming Claude or Anthropic, Claude or Anthropic as commit
author or committer, Claude session links, bare session ids (`session_01…`, e.g. a link
wrapped across two lines) and `Claude-Session…:` trailers, and the "Generated with Claude
Code" footer line (with or without a link), case-insensitively. Human co-authors whose name
merely starts with Claude ("Claude Dupont") and ordinary sentences about using AI pass; a
co-author named only "Claude" is rejected even with a human address. It names the commit or field and
the line, and never prints a session link. Exit 0 clean or opted in, 1 rejected, 3 could not
inspect (treat as rejected). The opt-in is the flag on that invocation; no environment
variable, setting or manifest field replaces it.

**From an existing owner-run publisher**, after its other gates and before its first push:

```sh
python3 "$PAI_PUBLISH_CHECK" --repo "$CLONE" --range "$BASE_SHA..$HEAD" \
  --pr-title "$title" --pr-body-file "$bodyf" "${ALLOW_AI[@]}" || die "publication check failed: nothing was pushed"
```

`examples/open-overnight-prs.guard.diff` is that change for the FLH overnight publisher
(apply it to your copy with `patch`; it also adds `--allow-ai-attribution` pass-through and
needs `export PAI_PUBLISH_CHECK=$SWARM/publish_check.py`). `examples/publisher-gate.sh` is a
minimal publisher with the same call.

**Squash message, when you merge in GitHub's UI.** GitHub builds the squash message from
the PR title and the commit messages (or the PR description, by repository setting) and
appends a `Co-authored-by` line for every co-author found in the commits. So:

```sh
# before you click: the commits and description GitHub will draw from
gh pr view 131 --repo OWNER/REPO --json body --jq .body > /tmp/pr-body.md
python3 $SWARM/publish_check.py --repo ~/workspace/flh-workers/workbooks \
  --range origin/main..HEAD --pr-body-file /tmp/pr-body.md
# the exact text in GitHub's commit message box: paste it, then Ctrl-D
python3 $SWARM/publish_check.py --squash-message-file -
# after the merge: the commit that landed
git -C ~/workspace/french-learning-app fetch origin main
python3 $SWARM/publish_check.py --repo ~/workspace/french-learning-app \
  --commit "$(gh pr view 131 --repo OWNER/REPO --json mergeCommit --jq .mergeCommit.oid)"
```

The last one can only report: the commit is already public.

## 8. A blocked worktree

Nothing is fixed for you. By refusal message:

| Message | What to do |
| --- | --- |
| `worktree is dirty (N …, first: PATH)` | `git -C WT status`. Commit it on its branch, or move it out yourself. Then rerun |
| `Git operation in progress (merge …)` | Finish it, or `git -C WT merge --abort` / `rebase --abort` yourself |
| `worktree is locked by Remote Control task R, lane L` | `rc status R.json`; end that Claude session (`/exit` in its window); `rc release R.json --lane L` |
| `worktree is locked by headless task T … pid N` | That run is alive: wait for it or stop it. A dead run's lock is taken over automatically |
| `… pid N not running, but a process it started still holds the worktree (see fuser -v PATH)` | The orchestrator was killed but a worker it started still runs there. `fuser -v PATH` names it; let it finish or stop it yourself. The lock frees itself when the last one exits |
| `… would be overwritten by checkout: FILE … Move those files out` | An ignored file in the worktree sits where the new base tracks a file; git would have replaced it silently. Move it out yourself, then rerun (nothing was overwritten) |
| `another … for tmux session S is running` (from `rc up`, `rc next` or `rc release`) | Wait for that command to finish, then rerun. Nothing was changed |
| `the checks changed tracked files: …` (`pr check` / `pr open`) | A check rewrote files (a formatter?). `git -C WT diff`; commit or revert that yourself, then rerun. Nothing was pushed |
| `its PR was merged into X, not main` (`rc next`) | The work is not on the task's base. Merge it there, or start the next task yourself with a new `run_id` |
| `locked by an unreadable lock file (PATH)` | Look at the file; delete it yourself if nothing runs there |
| `branch B already exists … never reused` | Use a new `run_id`, or `--branch LANE=NAME` |
| `worktree is on X, not the recorded task branch B` | `git -C WT switch B` if X was a mistake |
| `Git lists WT as a worktree but the directory is gone` | `git -C REPO worktree prune` |
| `git fetch origin main failed` | `git -C REPO fetch origin main` to see why; fix the network or credentials; rerun. Nothing was started |
| `git worktree add … failed` partway through | Rerun the same `rc up`: prepared lanes are kept, the rest are created from the same pinned base |
| `LEFT ALONE … pane runs …` (a window without Claude) | Close that tmux window yourself, then `rc up` |

## 9. Jev (optional)

`rc plan|up --jev-triage` asks Jev whether each assigned note is ready; `pr check|open
--jev-verify` asks whether the recorded results look finished. Both print and record the
answer as a signal: `agrees`, `DISAGREES`, or `unavailable` (no `TYPESAFE_API_KEY`, timeout,
malformed reply). Neither changes an exit code. A failed check stays failed. Inputs are
redacted first (email addresses, `key=value` secrets, claude.ai links, URL credentials).

Owner-only smoke test against the real API (sends one synthetic example note):

```sh
export TYPESAFE_API_KEY=…                       # yours; nothing here stores it
D=$(mktemp -d) && python3 $SWARM/pai_swarm.py init --tasks "$D" \
  && sed -i 's/^status: todo/status: inbox/' "$D/example-slugify.md" \
  && python3 $SWARM/pai_swarm.py triage --tasks "$D"
# expect one line: `todo  example-slugify  actionable=0.xx model=…`
```

## 10. Tests

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s swarm/tests -v   # 89 tests
PAI_REPO=$PWD bash swarm/tests/run_tests.sh                                   # headless end to end, 23 checks
# the same two with Python 3.9 (or any image's python3), as an unprivileged user in a throwaway container:
docker run --rm -v "$PWD:/src:ro" python:3.9-slim-bookworm sh /src/swarm/tests/container_check.sh
```

Disposable repositories with a local bare `origin`, fake Claude, fake `gh`, a fake Jev
endpoint, fake installers, and a tmux server on its own socket. No live Remote Control
session, no network, nothing installed, your tmux server untouched. `tests/test_tty.py`
drives the branch prompts of `rc up` and `rc next` through a real pseudo-terminal (Enter,
typed names, Ctrl-D, Ctrl-C).

`examples/ci-swarm-tests.yml` is a proposed GitHub Actions workflow for these suites
(ubuntu-latest, Python 3.9 and 3.13, read-only token, no secrets, tripwires in front of the
real `claude` and `gh`). It is not active until the owner copies it to `.github/workflows/`.

What the fakes cannot prove (a live Remote Control session, `rc status` against
claude.ai/code, a real push, PR, squash merge and `rc next`) is in `LIVE-VALIDATION.md`, an
owner-run checklist on a scratch repository.
