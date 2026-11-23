# First use: from nothing to a finished first round

English | [简体中文](FIRST-USE.zh-CN.md)

This page takes you from an empty machine to one finished round with one worker. Every step gives
the command, what you should see, and what to do if you see something else. It was written by
walking the steps in a brand-new home folder twice, once from the release archive and once from
the Python package, and every place where that walk got stuck is answered here (2026-11-22, pai
0.4.0, Linux, fake agents).

Words like *lane*, *worker*, *handoff* and *trusted root* are in the [glossary](GLOSSARY.md). Which
platforms this has really run on: [FIRST-USE-PLATFORMS.md](FIRST-USE-PLATFORMS.md).

> **Only a human does this.** pai prepares everything else, but it never does these five things
> for you, and a worker must not either:
>
> 1. **Answer folder-trust prompts.** When an agent asks whether to trust a folder, you read the
>    question and answer it (`pai trust`). See [step 6](#6-your-first-round-one-worker).
> 2. **Log in.** `claude` then `/login`, or `codex login`. pai only checks that you did.
> 3. **Merge.** pai can open a PR after its checks pass (`pai pr open`); merging it on GitHub is yours.
> 4. **Set the budget.** How many workers, which model, how much paid usage per round. pai shows
>    the cost it can see; it never raises a limit.
> 5. **Answer the decision sheet.** Questions with options and a recommendation; you tick one.

## 1. What you need

- Linux, WSL (Ubuntu), or macOS (the workbench on macOS has not been run yet; see the
  platform page). On native Windows, install pai inside WSL.
- Python 3.11 or newer, `git`, `tmux`. Check with:

  ```sh
  python3 --version && git --version && tmux -V
  ```

  Expected: three lines, such as `Python 3.12.3`, `git version 2.43.0`, `tmux 3.4`. You need Python
  3.11 or higher, any git 2.x and any tmux 3.x.

  If one is missing (`command not found`) or too old:

  - Ubuntu or WSL: `sudo apt-get install -y python3 git tmux`
  - Fedora: `sudo dnf install -y python3 git tmux`
  - Arch: `sudo pacman -S python git tmux`
  - macOS: `brew install python git tmux`

  Then run the check line again. After step 2, `pai setup --install-missing` can do the same for git
  and tmux. A Python older than 3.11 makes `pai` stop with a message that names the version it found.
- For real work, later: Claude Code or Codex installed and logged in. **Not needed for this page**:
  the rehearsal in step 4 uses a fake worker.

## 2. Install pai

Pick **one** of the two paths. Not sure which? Take path A: it needs no network after the
download and keeps the previous version for a rollback.

### Path A: the release archive (no network after the download, no Python packaging)

Download `pai-workbench-<version>.tar.gz` and its `.sha256` from the
[release page](https://github.com/WorldVanquisher/personal-ai-workbench/releases), or with these
commands. A checkout can also build them: `python3 -B tools/build_bundle.py --out dist`.

```sh
mkdir -p ~/Downloads && cd ~/Downloads
U=https://github.com/WorldVanquisher/personal-ai-workbench/releases/download/v0.4.0
curl -fLO "$U/pai-workbench-0.4.0.tar.gz" && curl -fLO "$U/pai-workbench-0.4.0.tar.gz.sha256"
sha256sum -c pai-workbench-0.4.0.tar.gz.sha256     # macOS: shasum -a 256 -c …
tar -xzf pai-workbench-0.4.0.tar.gz
pai-workbench-0.4.0/bin/pai install
```

Expected: `pai-workbench-0.4.0.tar.gz: OK`, then `install` prints `76 files match SHA256SUMS`, the
version folder (`~/.local/share/pai-workbench/versions/0.4.0`) and the launcher (`~/.local/bin/pai`).

If it says **`~/.local/bin is not on PATH`**, run the two lines it prints:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc    # for every new terminal
export PATH="$HOME/.local/bin:$PATH"                          # for this terminal, now
```

If `sha256sum -c` prints `FAILED`, delete both files and download them again; do not install. If
`bin/pai install` stops with an error, nothing was changed: the message names the file or folder
(for example an unwritable `~/.local/share`). Fix that and run the same line again.

The manuals (README, QUICKSTART, INSTALL, this page) stay in the extracted folder and in the
installed version folder.

### Path B: the Python package

The package has **no local copy of these manuals**: read them on GitHub, under `docs/`. First see
whether you have uv: `uv --version`. If it prints a version, use B1; if `command not found`, use B2.

**B1, with [uv](https://docs.astral.sh/uv/):**

```sh
uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.4.0
```

Success: `Installed 1 executable: pai`. If uv warns that its bin folder is not on your PATH, run
`uv tool update-shell` and open a new terminal.

**B2, without uv (a virtual environment and pip):**

```sh
python3 -m venv ~/.venvs/pai
~/.venvs/pai/bin/pip install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.4.0
mkdir -p ~/.local/bin && ln -s ~/.venvs/pai/bin/pai ~/.local/bin/pai
```

Success: pip's last line is `Successfully installed pai-workbench-0.4.0`, and `ln` prints nothing.
`~/.local/bin` must be on your PATH: use the two `PATH` lines from path A. Nothing prints them for
this path.

If B2 fails:

- `python3 -m venv` says `ensurepip is not available`: install the venv module
  (`sudo apt-get install -y python3-venv` on Ubuntu or WSL), then repeat.
- pip says `Cannot import 'setuptools.build_meta'`: pip could not download setuptools, which it needs
  to build the package. Connect to the network, or run `~/.venvs/pai/bin/pip install setuptools`,
  then repeat the install line.
- `ln` says `File exists`: an older `pai` is already there. Look at it with `ls -l ~/.local/bin/pai`,
  remove it if it is yours, and repeat the `ln` line.

### Check the install

```sh
pai --version
```

Expected: `pai 0.4.0`. If you get **`pai: command not found`**, open a new terminal, or run the
`export PATH=…` line above.

## 2a. A Git checkout with a remote

pai works inside a Git checkout whose `origin` has a `main` branch, because each run starts from a
fetched `origin/main`. Use a clone of your project:

```sh
git clone https://github.com/YOU/yourproject.git ~/code/yourproject
cd ~/code/yourproject
```

To practise without touching a real project, make a throw-away one with a local "remote":

```sh
git init -q --bare -b main ~/code/practice-origin.git
git clone -q ~/code/practice-origin.git ~/code/practice && cd ~/code/practice
echo '# practice' > README.md && git add README.md && git commit -qm init && git push -q origin main
```

Git saying `You appear to have cloned an empty repository` is expected here. With a local remote,
pai cannot open a GitHub PR. Everything else in this page works.

Check it:

```sh
git remote -v && git log --oneline -1 origin/main
```

Success: two `origin` lines and one commit (`init`). If `origin/main` is unknown, the push did not
happen: run `git push -u origin main`. If `git commit` says `Please tell me who you are`, set your
name once (`git config --global user.name "Your Name"` and `user.email`), then repeat the last line.

## 3. Check your machine: `pai doctor`

```sh
cd ~/code/practice        # your checkout
pai doctor
```

Expected: an `ESSENTIAL` block whose lines say `ok` (pai, pai on PATH, git, tmux, ps, and your
agent if installed), an `OPTIONAL` block (gh), and at the end:

```
info           project                …/practice is a Git checkout without pai defaults yet   -> pai init
doctor changed nothing. (No pai project here: `pai init` in your checkout.)
```

`pai doctor` changes nothing. A missing `claude` or `codex` is fine for this page.

If a line in `ESSENTIAL` is not `ok`, it ends with `->` and the command that fixes it. Run that
command, then `pai doctor` again, until the block is all `ok`. The usual ones:

- `tmux` or `git` missing: `pai setup --install-missing`.
- the "pai on PATH" line points to another pai: fix PATH as in step 2.
- the `pai` line itself is not `ok` (its files no longer match SHA256SUMS or the package
  RECORD): the installed copy changed. Install again (step 2).

## 4. Rehearse offline: `pai guide`

```sh
pai guide
```

This takes about 5 seconds. pai builds a disposable demo under `/tmp/pai-guided-demo-…` with a
synthetic project, one task note and a **fake** worker, and walks through dry-run, run, status,
the worker's handoff, and cleanup with the normal commands. Expected last lines:

```
Done: the guided run went plan -> dispatch -> delivered -> completed, with a fake worker.
Remove this demo: pai guide --cleanup /tmp/pai-guided-demo-XXXX
```

Run the printed `--cleanup` line when you have looked around. It takes **one** folder; two demos
need two commands.

If it does not end with `Done:`:

- `pai guide needs tmux (the run itself uses them). Nothing was created.` (or `git`): install it
  (step 1), then run `pai guide` again.
- It stops at step 3 (dispatch) after about 20 seconds: run `pai doctor` and fix every line that is
  not `ok`, then `pai guide` again. Before the retry, remove the half-made demo with the
  `pai guide --cleanup …` line it printed.
- Anything else: keep the demo folder, and report the last 20 lines of the output together with the
  output of `pai doctor`.

## 5. Save this checkout's defaults: `pai init`

```sh
pai init
```

It asks a few questions. Enter keeps the value in `[brackets]`. Answer like this:

| Question | What to type |
| --- | --- |
| `Default agent [not set yet]` | `1` for claude or `2` for codex |
| `Default model …`, `Reasoning effort …` | Enter (the agent's own default) |
| `Remote [origin]`, `Base branch [main]` | Enter |
| `Task notes folder (an Obsidian folder, or Enter for pai's own …)` | Enter. Obsidian is **not** needed; notes are plain Markdown |
| `Review checks, NAME=COMMAND …  [-]` | **a real check**, for example `test=make test` (see below) |
| `Save to …/projects/<name>.toml? [Y/n]` | Enter |
| `Enter JEV API key (optional)` | Enter. JEV is an optional second opinion ([glossary](GLOSSARY.md#jev)) |

**Set a review check now.** `pai pr` refuses to publish anything without one
(`BLOCKED no approved check is configured`). A run copies the checks when it starts, so a check
added later only applies to the **next** `pai run`. For the practice repository,
`files=test -f CONTRIBUTING.md` is enough.

Expected end:

```
saved       ~/.config/pai-workbench/projects/practice.toml
Next:
  pai doctor
  pai task new "What the agent should do" --body "How to check it" --write
  …
```

Then run `pai doctor` again. Its last lines now include `ok  project  practice: …`.

If `pai init` stops instead:

- `the base branch of origin is not recorded locally`: add `--base main` (see below).
- `… is not inside a Git repository. Run it in your checkout, or pass --repo PATH.`: `cd` into
  your checkout (step 2a) and run it again.
- To change an answer later: `pai init --update` with only the values you want to change, for
  example `pai init --update --check "test=make test"`. `pai config edit` opens the whole file.

Without a terminal (scripts), use:

```sh
pai init --default-agent claude --base main --check "test=make test" --no-input
```

`--base main` is required when the clone started empty. Otherwise it stops with
`the base branch of origin is not recorded locally. Pass --base BRANCH.`

## 6. Your first round: one worker

```sh
pai task new "Add a contributing note" --body "Create CONTRIBUTING.md with three lines on how to run the tests." --write
pai run --dry-run --workers 1
pai run --workers 1 --yes
pai status
```

What each one shows:

- `task new … --write`: the note, then `saved. Next: pai run --dry-run`. Without `--write` it is
  only a preview. It never overwrites an existing note.
- `run --dry-run`: `lane a … worktree …/practice-pai-lanes/a (new directory: the agent will ask
  for folder trust)` and `DRY RUN: nothing was fetched, created, written or started`. The run ID
  it prints is not reused; the real run gets a new one.
- `run --workers 1 --yes`: `started a …`, then per lane one of:
  - `task: delivered`: the worker has the task. Go to step 7.
  - `waiting for folder trust`: the agent asks whether to trust the new lane folder, and
    `pai run` exits with 1. Continue below.
- `status`: one block per lane: `lifecycle`, `trust`, `input`, `dispatch`, `rc`. It **exits with 1**
  while a lane is still working or waiting for you. That is normal.

**When a lane waits for folder trust:**

```sh
pai trust
```

It opens the waiting prompt in your terminal. Read it, move to **"Yes, I trust this folder"** (the
cursor starts on "No, exit"), and press Enter. `pai trust` then sends the waiting task (as
`swarm/WORKBENCH.md` §4 describes it; this page's test runs confirmed the `pai resume` route below). If you
answered somewhere else instead (`pai attach a`, `pai web`, or the claude.ai session) **after**
`pai run` had returned, the task is still `queued`. Send it with:

```sh
pai resume
```

Expected after either: `dispatch  delivered: task add-a-contributing-note delivered`.

To avoid this prompt in later rounds, keep worktrees under one folder that you have trusted once
(a [trusted root](GLOSSARY.md#trusted-root)). `pai init --update --worktree-root DIR` sets it.

## 7. Finish the round

**How to tell the worker is done.** `pai status` showing `input ready` only means the agent is
waiting for input, not that the task is finished. Look at what it produced:

```sh
git -C ~/code/practice-pai-lanes/a log --oneline -3     # the lane's commits
pai pr plan a                                           # what would be published, and what is missing
```

`pai pr plan a` changes nothing. It lists the commits, the PR title, the configured checks, and
any `BLOCKED` line with its reason. It exits with 1 while anything is blocked. Then:

```sh
pai pr check a      # runs the review checks; expected: check test PASSED … review checks passed at <sha>
pai pr open a       # pushes the branch and opens the PR (GitHub remotes only); you merge it on GitHub
```

With the practice repository's local remote, `pr plan` says
`BLOCKED cannot ask GitHub about existing PRs: the remote is not a GitHub repository`. That is
expected: stop after `pai pr check a`.

Stop the worker and close its window. Worktrees, branches and notes are kept:

```sh
pai cleanup                        # preview: what would be stopped
pai cleanup --apply --stop-running
```

If `pai pr check a` prints `FAILED` for a check, the worker's change does not pass. Look at the
check's output above that line. Either send the worker a follow-up in its window
(`pai attach a`, or `pai paste a fix.md --enter`), or fix it yourself in the lane folder and
commit. Then run `pai pr check a` again. If `pai cleanup` says `keep: the worker is live`, it
needs `--apply --stop-running` as shown.

You have finished a round. What next:

- **Every following round**: [REFRESH.md](REFRESH.md): upgrade pai, start from the merged main,
  new worktrees, prompts, restarts.
- **Words**: [GLOSSARY.md](GLOSSARY.md). **Everything else**: [docs/README.md](README.md).
- **Several workers**: `pai run --workers 2` with two task notes; `pai web` shows all lanes in
  your browser. A whole two-lane round to copy from, with its terminal transcript:
  [examples/two-lane-round/](../examples/two-lane-round/README.md).

## 8. When something goes wrong

Find the first line of the message you got in the **Symptom** column, run the command in **Fix**,
then repeat the step that failed. Each row was hit for real while this page was being tested.
If your symptom is not here, run `pai doctor`; most failures show up there with their fix. If
not, keep the full output and `pai doctor`'s output, and report both.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `pai: command not found` right after install | `~/.local/bin` is not on PATH in this terminal | `export PATH="$HOME/.local/bin:$PATH"`, and add it to `~/.bashrc` (step 2) |
| `sha256sum: WARNING: 1 computed checksum did NOT match` | incomplete or damaged download | download both files again; never install an archive that fails this |
| pip: `Cannot import 'setuptools.build_meta'` | building without network or without setuptools (`--no-build-isolation`) | let pip build in isolation (needs network once), or `pip install setuptools` in the venv first |
| `pai install`: "an installed Python package …, not an extracted release archive" | you installed with uv or pip (path B) | upgrade the same way you installed ([REFRESH.md](REFRESH.md#a-upgrade-pai)) |
| `pai doctor`: `-> pai init` | this checkout has no pai defaults yet | step 5 |
| `pai init --no-input`: `the base branch of origin is not recorded locally. Pass --base BRANCH.` | the clone was empty when made, so `origin/HEAD` is unknown | add `--base main` |
| `pai task new …`: `no pai project here … Run pai init` | `pai init` has not been saved for this checkout, or you are in another folder | `cd` into the checkout; step 5 |
| lane shows `waiting for folder trust` | the agent is asking about the new lane folder | `pai trust` (step 6); pai never answers it for you |
| lane shows `queued: … the next pai resume … sends it` | the trust prompt was answered after `pai run` returned | `pai resume` |
| `pai status` exits 1 | a lane is still working or waiting for you | normal; read the `lifecycle` line |
| `pai pr plan`: `BLOCKED no approved check is configured` | no review check, or it was added after this run started | `pai init --update --check "test=make test"`, then the next `pai run` uses it |
| `pai pr plan`: `BLOCKED … the remote is not a GitHub repository` | `origin` is a local or non-GitHub remote | expected for practice; for real work clone from GitHub |
| `pai next` → `error: the following arguments are required: lane` | `pai next` gives one lane its next task note | `pai next a` (add `--dry-run` to see which note first) |
| `pai guide --cleanup …`: `unrecognized arguments` | more than one folder given (for example a `*` glob) | one `pai guide --cleanup DIR` per folder |
| a Python `BrokenPipeError` traceback after `pai status \| head` | a known bug: status output piped into a reader that closes early | harmless; to page through it, `pai status > status.txt` and open the file (fix proposed to the core lane) |
| cleanup mentions `rc up MANIFEST --resume` | an internal command name | use `pai resume` |
| `pai doctor` shows `linux (arch ), …` | the distribution has no version number (Arch is rolling) | harmless |


## 9. Optional: Obsidian as the console

<!-- doc_check: pending pai round, pai decide, pai handoff approve -->

Once you run rounds with several lanes, PAI can keep the round's state as notes in your vault:
`PAI/Home.md`, one board per round, one note per lane and one per decision. Obsidian (or any
editor) then becomes the place where you look, approve and choose. This comes with the round lane
of R6 and is not in pai 0.4.0 (reported-by-round). The full guide is [OBSIDIAN.md](OBSIDIAN.md).

1. On the machine that runs the round, write the notes:

   ```sh
   pai round status ROUND.json --write-vault
   ```

   Success: `PAI/Home.md` exists in the vault. Its `updated:` field shows the time of this run.
2. Open `PAI/Home.md`. It has four blocks: open rounds, lanes that need you (with the reason),
   open decisions, unapproved handoffs. Without plugins you see plain tables; with Dataview, live ones.
3. **Answer a decision.** In `PAI/decisions/<round>-<ID>.md`, set `choice:` in the front matter to
   one of the `options`. Then run `pai decide answer --from-note "PAI/decisions/<round>-<ID>.md" --write`.
   Success: the note says `status: answered`.
4. **Approve a handoff.** Read `Agent Inbox/<round>/<lane>/cp-X/HANDOFF.md`, set `approved: true` in its
   front matter, then run `pai handoff approve PROJECT "Agent Inbox/<round>/<lane>/cp-X" --from-vault`.
   Success: `approval: approved` and a `DECISION.json` beside it. A body edited after sealing is refused,
   and the note is marked `approval: stale` (core lane, reported-by-core).

If a command refuses, nothing was written. The message names the reason: an edited note, a
choice that is not an option, or a decision text that changed. [OBSIDIAN.md](OBSIDIAN.md) section 5
says what to do for each.
