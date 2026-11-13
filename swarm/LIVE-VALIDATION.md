# Live validation checklist (owner-run)

The test suites use fakes only: a fake Claude, a fake `gh`, local bare repositories and a
private tmux socket. This checklist is the part the fakes cannot prove. It needs a real
Claude Remote Control session, real tmux windows, a real GitHub repository, a real push and
a real squash merge. **The owner runs it, by hand.** An agent must not run it, because it
starts live sessions, pushes, opens a PR and merges.

It never touches FLH. Everything lives in a scratch GitHub repository, a scratch directory,
its own pai-swarm state directory and its own tmux server (`tmux -L pai-live`). Your normal
tmux sessions, `~/.cache/pai-swarm` and the FLH worktrees are not involved.

It takes about 45 minutes. Stop at the first result that differs from **Expect**, and
record it.

When you record results, describe what claude.ai/code showed but never paste a session URL.
That includes the results table below if it goes into a commit or a PR.

## 0. Scratch setup

Use a new private repository of your own. New repositories allow squash merging; keep it
that way.

```sh
export SWARM=~/workspace/pai-workers/fable/swarm     # this checkout
export GH_REPO=OWNER/pai-swarm-live                  # the scratch repository (yours to name)
export L=~/pai-live                                  # scratch directory; must not exist yet
export PAI_SWARM_HOME=$L/home                        # task records and locks for this check only
if [ -e "$L" ]; then echo "$L exists: choose another L"; else mkdir -p "$L/workers" "$L/Tasks"; fi
gh repo create "$GH_REPO" --private --add-readme     # one commit on the default branch; below assumes it is main
gh repo clone "$GH_REPO" "$L/repo"
claude --version; gh --version | head -1; tmux -V; git --version; python3 --version
```

Make three task notes. The worker for lane `a` takes `live-a`, lane `b` takes `live-b`, and
`live-c` waits in the queue for `rc next`:

```sh
for n in a b c; do cat > "$L/Tasks/live-$n.md" <<EOF
---
status: todo
priority: $([ $n = c ] && echo 1 || echo 5)
checks: [verify]
acceptance:
  - NOTES.md has the line for lane $n
pr_title: "docs: live check note $n"
---
# Live check $n

Append the line "live check $n" to NOTES.md and commit it.

## PR

Adds one line to NOTES.md for the pai-swarm live check.
EOF
done
```

The manifest (two workers, own socket, one approved check):

```sh
cat > "$L/live.json" <<EOF
{
  "schema": "pai_rc_manifest_v1",
  "run_id": "live1",
  "repository": {"path": "$L/repo", "remote": "origin", "base_branch": "main", "github": "$GH_REPO"},
  "worktree_root": "$L/workers",
  "tmux": {"session": "live-{run_id}", "socket": "pai-live"},
  "command": ["claude", "--remote-control", "--name", "{name}"],
  "tasks": "$L/Tasks",
  "workers": [
    {"lane": "a", "branch": "live/{run_id}/a", "name": "LIVE {run_id} a", "task": "live-a"},
    {"lane": "b", "branch": "live/{run_id}/b", "name": "LIVE {run_id} b", "task": "live-b"}
  ],
  "review": {"required_checks": ["verify"], "checks": {"verify": ["test", "-s", "NOTES.md"]}}
}
EOF
export M=$L/live.json
```

## 1. Doctor and a read-only plan

| # | Run | Expect |
| --- | --- | --- |
| 1.1 | `python3 $SWARM/pai_swarm.py doctor` | git, tmux, claude and gh present; gh logged in; Remote Control ready. Exit 0 |
| 1.2 | `git -C $L/repo for-each-ref > $L/refs.before; python3 $SWARM/pai_swarm.py rc plan $M; diff $L/refs.before <(git -C $L/repo for-each-ref)` | `PLAN ONLY`; two `create worktree + new branch` lines; `diff` prints nothing |
| 1.3 | `tmux -L pai-live ls; ls $PAI_SWARM_HOME` | `no server running`; `$PAI_SWARM_HOME` does not exist (the plan wrote nothing) |

## 2. Start from a terminal (the branch prompt)

| # | Run | Expect |
| --- | --- | --- |
| 2.1 | `python3 $SWARM/pai_swarm.py rc up $M --new` in a real terminal | `Branch for worker a [live/live1/a]:` |
| 2.2 | Press Enter | `Branch for worker b [live/live1/b]:` |
| 2.3 | Type `live/live1/b-typed` and press Enter | One `fetched origin main` line, a `NEW` line for task `live1`, `started` lines for a and b, then the status table. Exit 0, or 1 if a session had not connected yet (3.1 decides) |
| 2.4 | `git -C $L/repo ls-remote origin main; git -C $L/workers/a rev-parse HEAD; git -C $L/workers/b rev-parse HEAD; git -C $L/workers/b branch --show-current` | All three SHAs equal; b is on `live/live1/b-typed` |
| 2.5 | A second `rc up $M --new` | Refused (`already started`); nothing changes |

## 3. Status compared with claude.ai/code

Open claude.ai/code (or the app) next to the terminal.

| # | Run | Expect |
| --- | --- | --- |
| 3.1 | `python3 $SWARM/pai_swarm.py rc status $M` (repeat for up to a minute) | Both lanes `claude running (pid N)` and `rc verified`; exit 0 |
| 3.2 | Look at claude.ai/code | Two sessions named `LIVE live1 a` and `LIVE live1 b`, both connected. Record whether they match 3.1 |
| 3.3 | `tmux -L pai-live attach -t live-live1` (detach with the prefix key, then `d`) | Windows `a` and `b`, each a Claude Code session with Remote Control on, in `$L/workers/a` and `b` |
| 3.4 | If your Claude Code version can turn Remote Control off inside a session, do it in window `b`, then `rc status $M` | Lane b is **not** `verified` (`not-connected`, `disconnected` or `unverified`); claude.ai/code shows b offline. If it still says `verified`, record it: that is a false positive |
| 3.5 | After 3.4: `tmux -L pai-live clear-history -t =live-live1:b`, clear the screen in that window, then `rc status $M` | Still not `verified`. If it now says `verified`, record it: that is the open point "the disconnect notice scrolled off" (the session record kept `bridgeSessionId`) |
| 3.6 | Turn Remote Control back on in b (or `/exit` it and run `rc up $M --resume`) | b is `verified` again |

## 4. Work, and the publication check refusing

| # | Run | Expect |
| --- | --- | --- |
| 4.1 | From claude.ai/code, tell session `LIVE live1 a`: "Append the line `live check a` to NOTES.md and commit it." | A commit on `live/live1/a` in `$L/workers/a` |
| 4.2 | `git -C $L/workers/a log -1 --format=%B` | Note whether Claude Code added a `Co-Authored-By: Claude …` trailer or a session link. It does by default unless your Claude settings turn that off |
| 4.3 | Deliberate refusal on lane b: `echo "live check b" >> $L/workers/b/NOTES.md && git -C $L/workers/b add NOTES.md && git -C $L/workers/b commit -m "docs: live check b" -m "Co-authored-by: Claude <noreply@anthropic.com>"` | A local commit |
| 4.4 | `python3 $SWARM/pai_swarm.py pr open $M --lane b` | `REJECTED … ai-co-author`; `publication check: REJECTED`; exit 1 |
| 4.5 | `git -C $L/repo ls-remote origin 'refs/heads/live/*'; gh pr list --repo $GH_REPO --state all` | Nothing for lane b: no remote branch and no PR |

## 5. Review and a real PR (lane a)

| # | Run | Expect |
| --- | --- | --- |
| 5.1 | If 4.2 showed a trailer or a link: `python3 $SWARM/pai_swarm.py pr plan $M --lane a` | `REJECTED`, naming the commit and line; any session link is redacted. Then reword it yourself (it is not published yet): `git -C $L/workers/a commit --amend` and delete those lines |
| 5.2 | `python3 $SWARM/pai_swarm.py pr check $M --lane a` | `verify` passes; `$L/Tasks/live-a.md` gains a `## Review` section with the command, exit code, commit and snapshot digest |
| 5.3 | `python3 $SWARM/pai_swarm.py pr plan $M --lane a` | Read-only: commits, checks, `publication check: OK`, the remote branch to create, title `docs: live check note a`. `ls-remote` still shows no lane a branch |
| 5.4 | `python3 $SWARM/pai_swarm.py pr open $M --lane a` | Checks run again; branch pushed (not forced); PR URL printed. Exit 0 |
| 5.5 | `gh pr view --repo $GH_REPO live/live1/a --json baseRefName,headRefName,headRefOid,title,body` | Base `main`; head `live/live1/a` at the worktree's HEAD; the title from the note; the body is the note's `## PR` text plus the check table, with no session link or Claude footer |
| 5.6 | `gh pr view --repo $GH_REPO live/live1/a --json body --jq .body > $L/body.md && python3 $SWARM/publish_check.py --repo $L/workers/a --range origin/main..HEAD --pr-body-file $L/body.md` | `publication check: OK` |
| 5.7 | `python3 $SWARM/pai_swarm.py pr open $M --lane a` again | Reuses the branch and the PR: no second PR, no force-push |

## 6. Squash merge (by you, in GitHub)

| # | Run | Expect |
| --- | --- | --- |
| 6.1 | In the PR, choose "Squash and merge". Before confirming, copy the whole commit message box and run `python3 $SWARM/publish_check.py --squash-message-file -`, paste, Ctrl-D | `publication check: OK`. If it is rejected, edit the box, then check again |
| 6.2 | Confirm the merge, then `git -C $L/repo fetch origin main && python3 $SWARM/publish_check.py --repo $L/repo --commit "$(gh pr view --repo $GH_REPO live/live1/a --json mergeCommit --jq .mergeCommit.oid)"` | `publication check: OK` (this one can only report: the commit is public) |

## 7. Next task after the merge

| # | Run | Expect |
| --- | --- | --- |
| 7.1 | `python3 $SWARM/pai_swarm.py rc status $M --json > $L/status.before` | Records lane a's Claude pid |
| 7.2 | `python3 $SWARM/pai_swarm.py rc next $M --lane a --plan` | No prompt; `PR … is merged; next task live-c`; plan for `live/live1/a-2` from the new `main`; `PLAN ONLY`. Nothing switched |
| 7.3 | `python3 $SWARM/pai_swarm.py rc next $M --lane b` | `not rolled over: it has no PR`; b unchanged |
| 7.4 | `python3 $SWARM/pai_swarm.py rc next $M --lane a` in a terminal; press Enter at `Branch for worker a, next task live-c [live/live1/a-2]:` | `merged -> task live-c on new branch live/live1/a-2` |
| 7.5 | `git -C $L/workers/a branch --show-current; git -C $L/workers/a rev-parse HEAD; git -C $L/repo ls-remote origin main` | `live/live1/a-2`; HEAD equals the remote `main`, the squash commit |
| 7.6 | `python3 $SWARM/pai_swarm.py rc status $M` and claude.ai/code | Lane a: same pid as in 7.1, `verified`, task `live1.a.2`; the same claude.ai session, still connected. The old branch `live/live1/a` is untouched |
| 7.7 | `grep -e '^status:' -e '^pr:' -e '^branch:' $L/Tasks/live-a.md $L/Tasks/live-c.md` | live-a `status: done` with the PR URL; live-c `status: running`, `branch: live/live1/a-2` |

## 8. Resume after a lost window

| # | Run | Expect |
| --- | --- | --- |
| 8.1 | `tmux -L pai-live kill-window -t =live-live1:b` (scratch server only) | claude.ai/code shows session b ended or offline |
| 8.2 | `python3 $SWARM/pai_swarm.py rc up $M --resume` | `RESUME`; no fetch; only b started, on `live/live1/b-typed` with its commit from 4.3; lane a's pid unchanged |
| 8.3 | `rc status $M` and claude.ai/code | Both `verified`; a new session `LIVE live1 b` is connected |

## 9. Clean up (yours to do)

```sh
# in each window: /exit
python3 $SWARM/pai_swarm.py rc status $M          # both: exited or no window
python3 $SWARM/pai_swarm.py rc release $M         # both locks released; branches and worktrees untouched
tmux -L pai-live kill-server                       # the scratch server only
# then, if you want: delete the scratch repository on GitHub, and remove $L after looking at it
```

## Results

Copy this table into the PR or the handoff. Never include session URLs.

| Step | Observed | OK? |
| --- | --- | --- |
| Versions (claude, gh, tmux, git, python3) | | |
| 1.1–1.3 doctor, read-only plan | | |
| 2.1–2.5 terminal prompt, `rc up --new` | | |
| 3.1–3.2 `rc status` vs claude.ai/code, connected | | |
| 3.4–3.5 after a disconnect, and with the pane cleared | | |
| 4.2 Claude Code's own commit trailer present? | | |
| 4.4–4.5 refusal, nothing pushed | | |
| 5.2–5.7 check, plan, open, rerun | | |
| 6.1–6.2 squash message and merged commit | | |
| 7.2–7.7 `rc next` rollover | | |
| 8.1–8.3 resume | | |
