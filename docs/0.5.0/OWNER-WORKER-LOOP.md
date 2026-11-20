# The owner-to-worker loop (R4): acceptance, demonstration, diagnostics, authority

How an owner takes a merged base to a reviewed, published worker change with pai, and how to
check each part offline. Everything here runs without a network or an account: a local bare
remote stands in for GitHub's Git server and `swarm/tests/fake_gh.py` for the `gh` CLI.

## The loop

| Step | Who | Command | Writes |
| --- | --- | --- | --- |
| 1. Refresh after a merge | pai prints, the owner runs | `pai worktree refresh --repo R --worktree W` | nothing (the printed commands fetch and fast-forward) |
| 2. Check before dispatch | worker or owner | `pai worktree verify W --base SHA` | nothing |
| 3. Repository rules | owner reviews | `pai worktree init W --goal G --scope DIR --lane L` then `--write` | W/AGENTS.md (managed section only) |
| 4. Split the goal | owner reviews | `pai plan propose --parent ID --goals FILE --out PLAN`; `pai plan check PLAN` | PLAN |
| 5. Queue the slices | owner | `pai plan accept PLAN --queues DIR --write` | the workers' queues (never twice) |
| 6. Work | worker | edits inside its scope; `pai queue mark ...` | the worktree, its queue log |
| 7. Review | owner | `pai worktree preview W --path F ... --title T --out PR.json` | PR.json |
| 8. Publish | owner, explicitly | `pai worktree publish PR.json` (dry run), then `--yes --draft` or `--ready` | the commit, the push, the PR |

## Offline acceptance scenarios (R4-PAI-005)

Each runs from a checkout in under a minute and must end as stated.

| # | Scenario | Command | Passes when |
| --- | --- | --- | --- |
| A | The whole loop, owner-approved | `python3 -B tools/worktree_rehearsal.py --publish` | exit 0 and "Rehearsal complete: every check held." (refresh, verify, rules, split, queue twice, preview, dry run, publish to a local remote, one draft PR, retry reuses it) |
| B | Stop and resume from the files alone | `python3 -B tools/worktree_rehearsal.py --dir /tmp/pai-r4-stop --stop-after queue`, then `python3 -B tools/worktree_rehearsal.py --resume /tmp/pai-r4-stop` | both exit 0; `TASK-CARD.md` in the folder names the stop point and the resume command; the resumed run skips the done steps |
| C | Publication refused before anything leaves | `cd swarm/tests && python3 -B -m unittest test_wtpr.WorktreePR.test_a_commit_with_unapproved_paths_is_refused_on_the_first_attempt_and_on_retries` | OK: a hook-added file is refused on the first run and on retries; the remote branch is unchanged and `gh pr create` is never called |

## First use: from a clean install to a fake worker's handoff (about five minutes)

Everything below is offline and uses pai's fake worker; nothing outside the folders pai prints is
touched. (Install first, from the release archive: verify it, extract it, then
`bin/pai install`, as in INSTALL.md; or `uv tool install` where uv is available.)

```sh
pai --version                     # the installed version
pai guide                         # a synthetic project: plan -> fake worker -> status -> its handoff
pai guide --handoff               # stop that worker; a fresh fake worker continues the task elsewhere
pai queue init work.json --from items.md --write   # items.md: lines like "W-01. Write the plan"
pai queue mark work.json W-01 done --evidence "tests pass"
pai queue restart work.json       # the prompt a fresh session starts from
```

Each `pai guide` run ends with the exact `pai guide --cleanup DIR` that removes its folder. Then,
in your own repository: `pai worktree refresh`, `pai worktree init W --goal ...`, `pai plan propose`,
`pai worktree preview` and `pai worktree publish` (the loop above).

## One-command diagnostics (R4-PAI-072)

Each command reads only and says what is wrong in words, with the next command.

| Symptom | Run | It tells you |
| --- | --- | --- |
| "Did the fetch reach my worktree?" | `pai worktree verify W --base origin/main` | exit 0 when W contains the fetched base; exit 1 with `HEAD X does not contain the base Y: the fetch updated refs, not this worktree` (or that the base is not a commit here: the fetch was not run) |
| Several lanes, some stale | `pai worktree list` | every worktree: branch, HEAD, uncommitted paths, whether it contains the last fetched base, how many commits it has that the base does not (unpublished or refused work) |
| The fetch itself fails or is old | `pai worktree refresh --worktree W` | the last fetched base SHA, the exact fetch command to run yourself, and a guarded fast-forward or a fresh worktree |
| "Is my reviewed plan still what would be published?" | `pai worktree publish PLAN` (no `--yes`) | `DRIFT` lines (HEAD moved, a file or its mode changed, the remote URL changed, other staged paths), `REFUSED` lines (the branch was switched, the plan predates mode review), or what it would do; nothing is changed |
| "Was the plan edited after I reviewed it?" | `pai worktree publish PLAN --expect-plan SHA` (no `--yes`) | `REFUSED ... is not the plan that was reviewed` with both hashes |
| "What did an earlier publish actually do?" | `pai worktree publish PLAN --yes` again | the `outcome` line: commit, remote (read back), PR, from the journal only; a retry never repeats a completed step |
| A lane's queue | `pai plan status PLAN --queues DIR --lane L` | current, blocked (with the reason), owner-only, remaining, done, ready to start |

## Authority: what stays local, what needs the owner (R4-PAI-073)

| Action | Who | Where it is enforced |
| --- | --- | --- |
| Read: list, verify, refresh plans, previews, plan check/status, queue status/show | anyone | commands write nothing (previews say so) |
| Edit files inside the lane's scope; implementation, testing and local design decisions (unless `--reserve`d) | worker | AGENTS.md "Decisions you own"; record what, why, how to reverse (`pai handoff new --decision`) |
| Write the managed AGENTS.md section, accept a split, queue slices | owner (or a worker the owner asks) | `--write` / `accept --write`; nothing without it |
| Decisions above the worker's authority (outside scope, shared interfaces, cloud, spending, credentials, user data, migrations, DNS) | owner | AGENTS.md "Ask the owner before"; `pai plan split-owner` (rehearsal for the worker, decision in the owner queue); `pai handoff new --needs-owner` |
| Commit, push, open a PR | owner | `pai worktree publish` needs `--yes` and `--draft`/`--ready`; the commit must match the reviewed preview (paths, content, mode, message); `--expect-plan` binds it to the reviewed plan file |
| Merge, tag, release, publish packages | owner only | never done by pai |
| Fetch, refresh worktrees | owner runs the printed commands | pai prints them; it never fetches or moves a worktree itself |
| Real GitHub, real agents, cloud, other platforms | owner | tested here only with a local bare remote, the fake `gh` and fakes |
