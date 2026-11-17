# Owner-run smoke test: real Claude Code, Codex, tmux, SSH and GitHub

The automated suites use fakes. This checklist covers what they cannot prove (WORKBENCH.md,
section 12). **You run it, by hand**, on a scratch repository: it starts real agent
sessions, may send one billed JEV request, pushes, opens a PR and needs your merge. It
touches no FLH repository, no existing live-validation directory and none of your tmux
sessions: everything uses its own directories, its own pai homes and its own tmux server.

Stop at the first result that differs from **Expect** and write it down. Never paste a
claude.ai session link or an API key anywhere, including the results table.

## 0. Scratch setup (about 5 minutes)

```sh
export PAI=/path/to/personal-ai-workbench/bin/pai            # your checkout's launcher
export GH_REPO=OWNER/pai-smoke                                # a new private repository of yours
export S=~/pai-smoke                                          # must not exist yet
export PAI_SWARM_HOME=$S/state PAI_WORKBENCH_HOME=$S/config PAI_WORKBENCH_DATA=$S/data
if [ -e "$S" ]; then echo "$S exists: choose another S"; else mkdir -p "$S/Tasks"; fi
gh repo create "$GH_REPO" --private --add-readme
gh repo clone "$GH_REPO" "$S/repo" -- --origin origin        # SSH or HTTPS, as your gh is set up
for n in a b c; do printf -- '---\nstatus: todo\npriority: %s\nchecks: [verify]\n---\n# Smoke %s\n\nAppend the line "smoke %s" to NOTES.md and commit it on this branch.\n\n## PR\n\nAdds one line to NOTES.md.\n' \
  "$([ $n = c ] && echo 1 || echo 5)" $n $n > "$S/Tasks/smoke-$n.md"; done
cd "$S/repo"
```

## 1. Init, doctor, setup

```sh
$PAI init --worktree-root "$S/lanes" --tasks "$S/Tasks" --check verify="test -s NOTES.md"
```

Expect: it lists claude and codex with install state and "Remote Control: not supported" for
codex; offers "not set yet"; after choosing claude it asks for a model and an effort (press
Enter for both); at `Enter JEV API key (optional):` press Enter (section 9 covers JEV).

```sh
$PAI doctor
$PAI setup --dry-run
$PAI config show --effective
```

Expect: your real OS and package manager; claude and codex installed / logged in rows; no
install is run.

## 2. Dry run, then a mixed batch

```sh
$PAI run --dry-run --workers 2 --agent claude --agent codex
$PAI run --workers 2 --agent claude --agent codex
```

Expect: the dry run changes nothing (`git -C "$S/repo" branch` unchanged). The real run
fetches once, creates `pai/<run>/a` and `/b`; lane a (Claude) shows **waiting for folder
trust** with `pai attach a`; lane b (Codex) may show its own trust prompt too. Record
exactly what each agent showed.

## 3. Trust from one terminal, then dispatch

```sh
$PAI trust
```

Expect: it lists the repository and both worktree paths, opens each waiting prompt in
turn and comes back by itself after you answer (in Claude, move to "Yes, I trust this
folder"). Then each agent receives exactly one line starting `[pai <run>/<lane>/smoke-...]`
in its input box and starts working. Record whether the line was submitted (not just typed)
in each agent.

```sh
$PAI status
$PAI resume            # must not send either task again
```

Expect: lane a `rc verified` and the session visible in claude.ai/code; lane b
`rc not-supported`; both `dispatch delivered`; `resume` sends nothing new.

## 4. SSH (skip if your remote is HTTPS)

```sh
ssh-add -l                       # note your keys
ssh-add -D                       # unload them for this test (re-add afterwards)
$PAI run --dry-run               # read-only, still fine
$PAI next a --dry-run            # expect: the SSH preflight refuses and prints ssh-add steps
ssh-add ~/.ssh/YOUR_KEY          # as printed; type the passphrase once
```

Expect: no passphrase prompt from pai at any point; the refusal names `ssh-agent`/`ssh-add`
steps; nothing changed.

## 5. Publish one lane

Wait until lane a's agent has committed (`$PAI logs a`), then:

```sh
$PAI pr plan a
$PAI pr check a
$PAI pr open a --draft
$PAI pr open a --draft           # rerun: reuses the branch and the PR
```

Expect: the plan publishes nothing; open runs `verify`, pushes once, opens one draft PR; no
"Generated with" footer or session link in the PR. Mark it ready and **squash-merge it in
GitHub yourself**.

## 6. Next task

```sh
$PAI next a --dry-run
$PAI next a
```

Expect: only after the merge; lane a moves to `pai/<run>/a-2` from the fetched squash
commit and the same Claude session receives `smoke-c` once.

## 7. Cleanup and resume

```sh
$PAI cleanup
$PAI cleanup --apply
$PAI cleanup --lane b --apply --stop-running
$PAI resume --lane b
$PAI cleanup --apply --stop-running --lane a --lane b
$PAI cleanup --apply             # again: harmless
```

Expect: the preview changes nothing; live workers are kept until `--stop-running`;
worktrees, branches and edits remain; `resume` brings lane b back on its branch.

## 8. Config editor

```sh
$PAI config edit                  # change run.workers to 3, save, quit
$PAI config show --effective      # run.workers = 3, source project
$PAI config edit                  # set run.workers = "many": expect INVALID, file unchanged, draft kept
$PAI config edit                  # fix it; expect saved
```

## 9. JEV (optional; one request, may be billed)

```sh
$PAI init --update                # in a terminal; at "Enter JEV API key (optional):" type the key
$PAI jev status                   # where the key comes from (never the key)
$PAI jev check                    # one request; answer y
```

Record the result line and the `usage` it printed. Never paste the key into a chat.

## 10. Teardown

```sh
$PAI cleanup --apply --stop-running
gh repo delete "$GH_REPO"         # only the scratch repository; confirm the name
rm -r -- "$S"                     # check the path first
```

## Results

| Step | Result | Notes |
| --- | --- | --- |
| 1 init/doctor/setup | | |
| 2 dry run, mixed start | | |
| 3 trust, dispatch submitted (Claude / Codex) | | |
| 4 SSH refusal without prompt | | |
| 5 pr open, rerun reuse | | |
| 6 next after squash merge | | |
| 7 cleanup, resume | | |
| 8 config editor | | |
| 9 JEV check (result, usage) | | |
