# PAI quickstart

English | [简体中文](QUICKSTART.zh-CN.md)

**You need:** Linux, macOS or WSL; Python 3.11+, `git`, `tmux` and [uv](https://docs.astral.sh/uv/);
Claude Code or Codex installed and logged in; a Git checkout that has a remote (for example a
clone from GitHub). On native Windows only `pai edit` (section 5) and PAI's context commands
run: use WSL (Ubuntu) for everything else, in a folder under the Linux file system.

## 1. Install

```sh
uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.4.0
```

If uv says its bin folder is not on your PATH, run `uv tool update-shell` (or the
`export PATH=…` line it prints) and **open a new terminal**. Then `pai --version` answers
`pai 0.4.0`. No uv? The [release page](https://github.com/WorldVanquisher/personal-ai-workbench/releases/tag/v0.4.0)
has the same pai as a `.tar.gz` and a `.zip`, each with a `.sha256`; [INSTALL.md](INSTALL.md)
shows the three lines to verify and install one (no sudo, nothing piped into a shell).

## 2. Check your machine

```sh
cd ~/code/myapp      # your Git checkout
pai doctor           # changes nothing; every problem comes with the command that fixes it
```

Before `pai init` it reports this checkout as not set up yet: that is the next step. Missing
`git`, `tmux` or `gh`? `pai setup --install-missing` installs them with your system's package
manager. Agent logins stay yours (`claude`, then `/login`; or `codex login`).

## 3. Your first task

```sh
pai init             # Enter keeps each suggested value; choose your agent when asked
pai task new "Add a contributing note" --body "Create CONTRIBUTING.md with three lines on how to run the tests." --write
pai run --dry-run    # shows what would happen; changes nothing
pai run --workers 1 --yes   # one worktree, branch and agent window for this task
pai status
```

`pai status` exits with 1 while a lane is still at work or waiting for you; that is normal.
If it says **waiting for folder trust**, the agent is asking whether to trust its folder: read
the question and answer it yourself (`pai trust` opens it in this terminal, or type it in
`pai web` below); pai then sends the task. When the agent is done, `pai pr plan a` shows what
would be published and what is still missing (it needs a review check, which `pai init` asks
for); nothing is pushed until you run `pai pr open a`.

## 4. Watch and talk to the agents in your browser

```sh
pai web              # opens your browser on this run; Ctrl-C stops it
```

- Every lane's screen, live, with its colours. Select text to copy it, as on any web page.
- **Click the screen to type into the lane:** letters, Enter, Esc, Ctrl-C, the arrow keys and
  your input method all go to the agent. The buttons below the screen send the same keys.
- **Pasting** (Ctrl-V, Cmd-V) or the **Message** box sends the text as one piece, however
  long: nothing is submitted until you choose **Paste and Enter** or press Enter yourself.
- **Shell:** a shell in the lane's worktree (`git status`, the tests). **Task note:** opens
  the note in the Markdown editor; **Notes** lists all of them.
- A folder-trust prompt is shown as such; pasting stays off until you have answered it.
- On a server without a desktop: `pai web --no-browser --port 8766` there, then
  `ssh -N -L 8766:127.0.0.1:8766 YOU@SERVER` on your laptop, and open the printed link.

From a terminal, `pai paste a message.md` sends a file into lane `a` the same way (add
`--enter` to submit it).

## 5. Open one Markdown file in your browser

```sh
pai edit README.md   # an editor with a live preview; Save shows the diff and asks you to confirm
```

It serves only that file, on this machine only, and never writes over a version that changed
on disk meanwhile. It also runs on native Windows. On a server, add `--no-browser --port 8765`
and use an SSH tunnel as above.

## 6. Upgrade and uninstall

- **Upgrade:** the install line with the new version (for example `@v0.4.1`). Your
  configuration and task notes stay.
- **Uninstall:** `uv tool uninstall pai-workbench`. It removes pai itself; your configuration
  and notes stay.

More: `pai --help`, [INSTALL.md](INSTALL.md), and `swarm/WORKBENCH.md` (the full guide).
`pai web`, `pai paste` and `pai edit` are experimental.
