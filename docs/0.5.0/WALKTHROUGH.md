# Two minutes with pai: stop a session, continue it somewhere else

For a new user. Everything below runs offline with fake agents, in a folder pai makes and can
remove; nothing outside it is touched and no account is needed. (Install first: QUICKSTART.md.)

## 1. Watch a task move from one agent to another (about 20 seconds)

```sh
pai guide --handoff
```

A fake Claude session does step 1 of a three-step task and is stopped. pai writes what happened
into the task note (completed commits, evidence, open questions), packs the note into a transfer
packet, opens it in a second, empty HOME, shows you what arrived and what that machine has, and a
fake Codex session finishes steps 2 and 3. Read the last lines: one prompt per session, step 1
not redone. `pai guide --handoff reverse` does the same from Codex to Claude.

## 2. See what happens when nobody knows if the task arrived (about 20 seconds)

```sh
pai guide --handoff interrupted
```

This time session 1 dies right after it took the task and kept no transcript. `pai status` says
`unknown`, not "delivered" and not "failed". pai refuses to hand the task off until you decide; the
demo chooses `--choose a=leave`, so the question travels with the task and the next session reports
it. Nothing is sent twice.

## 3. The same on your own project

```sh
pai status                                 # what state each lane is in, and why
pai inspect a                              # the evidence behind it, and where it was read
pai cleanup --lane a --apply --stop-running
pai recover --continue a                   # preview; add --write to record it in the task note
pai run --continue NOTE --agent codex      # a fresh session continues from the same commit
```

To continue on another machine: push the branch yourself, then `pai task export NOTE --transfer
--out packet.json` here and `pai task import packet.json` there (a preview; `--write` creates the
note). pai never pushes, fetches, or answers a trust prompt for you.

## 4. Keep a long list of work across sessions

```sh
pai queue init work.json --from items.md --write      # items.md: lines like "W-01. Title"
pai queue mark work.json W-01 done --evidence "tests pass"
pai queue next work.json
pai queue restart work.json                            # paste this into a fresh session
```

## When you are done

`pai guide --cleanup DIR` removes a demo folder (the guide prints the exact command). More:
`swarm/CONTINUATION.md` (shipped with pai) and `pai --help`.
