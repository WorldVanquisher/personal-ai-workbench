# Real agent switch: Claude Code to Codex, and back (OWNER-ONLY; not run by the worker)

Everything the worker ran used fake agents. These two runs are the smallest real proof that a task
survives a session switch between the real programs. They use your accounts and may be billed.
Run them on a disposable project only, after the one-lane smoke in REAL-AGENT-SMOKE.md passes.
Record each box's result, the program versions, and the exact line you saw. Never record session
links or tokens (pai removes them from handoffs and continuations, but check your own notes too).

## Before both runs

- [ ] A throwaway repository with a local bare origin and one commit on `main` (as in
      REAL-AGENT-SMOKE.md), and `pai init --default-agent claude --tasks /tmp/switch/tasks` in it.
- [ ] A task note that needs two sessions, with a checkable end, for example:

      ---
      status: todo
      priority: 1
      ---
      # Three numbered files
      Create ONE.md, then commit; create TWO.md, then commit; create THREE.md, then commit.
      Stop after ONE.md and write HANDOFF.md saying what is left.
      ## Acceptance criteria
      - ONE.md, TWO.md and THREE.md are on the branch, each committed once

- [ ] `claude --version`, `codex --version`, `tmux -V`, `pai --version`, `pai doctor` (both agents'
      ESSENTIAL rows ok, both logged in).

## Run A: Claude Code -> Codex

1. [ ] `pai run --workers 1 --agent claude --mode local --run-id switch-a1`, answer the folder-trust
       prompt with `pai trust`, `pai status` until lane a is `delivered`.
2. [ ] Let Claude create ONE.md, commit and write HANDOFF.md; then `pai cleanup --run switch-a1 --lane a
       --apply --stop-running`.
3. [ ] `pai recover --run switch-a1 --continue a` (preview): it lists the ONE.md commit, the dispatch
       evidence ("Claude's own transcript"), the HANDOFF.md text, and no open decision.
4. [ ] `pai recover --run switch-a1 --continue a --write`; `pai status --run switch-a1`: `handed-off`.
5. [ ] `pai run --continue NOTE --agent codex --run-id switch-a2`; answer Codex's own trust prompt
       (`pai trust`); `pai status --run switch-a2` until `delivered` with "Codex's own rollout file".
6. [ ] Codex creates TWO.md and THREE.md without recreating ONE.md:
       `git log --format=%s main..pai/switch-a2/a` shows ONE once, then TWO, THREE.
7. [ ] `pai resume --run switch-a1` prints `HANDED OFF` and starts nothing; Claude's transcript for
       that worktree holds exactly one tagged prompt `[pai switch-a1/a/NOTE]`; Codex's rollout holds
       exactly one `[pai switch-a2/a/NOTE]`.

**What run A proves:** the continuation is readable by a *different* real agent from the note alone
(no shared conversation); pai's evidence model works across adapters (Claude's transcript for
session 1, Codex's rollout for session 2); the new branch starts at the handed-off head with the
base pinned; nothing is resent to the stopped Claude session; Codex's trust prompt stays yours.

## Run B: Codex -> Claude Code

The same steps with the agents swapped: `--agent codex --run-id switch-b1`, then
`pai run --continue NOTE --agent claude --run-id switch-b2`. Also:

- [ ] Before step 4, kill Codex in its window instead of a clean stop, right after it has taken the
      prompt (so the delivery may still be `sent` or `unknown`): `pai recover --run switch-b1
      --continue a --write` must be REFUSED while the delivery is unknown; then write it with
      `--choose a=leave`, and check that the note's "Open decisions" carries the question and that
      Claude's HANDOFF.md mentions it.

**What run B proves:** the reverse direction (Codex's screen-only readiness to Claude's session
record); an interruption with uncertain delivery stops the handoff until a person decides, and the
decision reaches the next agent; still exactly one prompt per session.

## Run C (0.5.0 R2): the same switch through a transfer packet into a second HOME

Run A again, but carry the task the way you would to another machine: after step 4,

1. [ ] `git push origin pai/switch-a1/a` (yourself; pai never pushes), then
       `pai task export NOTE --transfer` (preview: what leaves, what stays, with impact; no `BLOCKS`
       line once the branch is on the remote) and `pai task export NOTE --transfer --out packet.json`.
2. [ ] In a second HOME on the same machine (`HOME=$(mktemp -d)`, with its own `PAI_WORKBENCH_HOME`,
       `PAI_SWARM_HOME`, `CLAUDE_CONFIG_DIR`/`CODEX_HOME` pointing at your real logins only if you
       accept that), `pai init`, then `pai task import packet.json`: the preview names the source
       agent's evidence and, for each agent on this machine, whether it is installed and how its
       evidence differs; `--write` creates only the note.
3. [ ] `pai run --continue NOTE --agent codex --run-id switch-c2` from that HOME; the same checks as
       steps 5 to 7. Then the reverse with `--agent codex` first and `--agent claude` second.

**What run C proves:** the packet is enough on its own (no shared pai state, no old transcript);
the destination's own configuration decides how the agent starts; one prompt per session.

## A real trust prompt during recovery: the evidence to expect (record each line)

Do this once, in run A's first session (a new worktree always asks), before answering the prompt:

| Command | Expected evidence |
| --- | --- |
| `pai status --run switch-a1 --json` | lane a `lifecycle.state` `waiting-for-trust`, `attention` true; `dispatch.state` not `delivered` (nothing was typed) |
| `pai inspect a --run switch-a1` | a `trust` row read from the lane's visible screen; `input` not ready because the folder-trust prompt is on screen; no `delivery` row |
| `pai recover --run switch-a1` | action `trust [HUMAN]` for lane a, naming the folder and `pai trust`; `--apply` changes nothing for that lane |
| `pai recover --run switch-a1 --continue a` | REFUSED while the agent is still there (stop it first), and nothing written |
| Claude's transcript and the tmux pane | no `[pai switch-a1/a/...]` prompt and no keystroke from pai while the prompt shows |
| `pai trust` (you answer) then `pai resume --run switch-a1` | lane a `delivered`, with "Claude's own transcript ... records the prompt"; exactly one tagged prompt |

The same table for Codex in run B (its trust prompt text differs; evidence then comes from its
rollout file). If any row differs, keep the screen (`pai logs a --run RUN`) and the status JSON.

## What neither run proves

Remote Control lanes (run Claude in `--mode rc` separately), WSL or macOS (repeat the runs there),
and model quality (whether the agent did good work is your review, `pai pr draft`).
