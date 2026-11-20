# Real-agent smoke checklist (OWNER-ONLY, opt-in; not run by the worker)

Everything in the 1.0 readiness work was verified with fake agents. This checklist is the
smallest run with the real programs that the 1.0 criteria (PLATFORMS.md, BACKENDS.md) ask for.
It uses your accounts and may be billed; run it only when you decide to, on a disposable
project. Record each result (pass/fail and the line you saw) in the release candidate's notes.

## Before you start

- [ ] A throwaway repository with a local bare `origin` (no real remote, nothing to push):
      `git init --bare /tmp/smoke/origin.git && git clone /tmp/smoke/origin.git /tmp/smoke/repo`,
      one commit on `main`, pushed to that local origin.
- [ ] A throwaway task folder with one note whose completion is checkable, for example
      "append the line `smoke ok` to SMOKE.md, commit it, write HANDOFF.md".
- [ ] Note the versions: `claude --version`, `codex --version`, `tmux -V`, `pai --version`.
      Compare them with the versions named in `swarm/agents.py`'s docstring.
- [ ] Your own `CLAUDE_CONFIG_DIR`/`CODEX_HOME` are used; nothing in them is shared or recorded
      in the notes (no session links, no tokens).

## Claude Code, one lane (required for "Linux supported")

- [ ] `pai doctor --agent claude`: ESSENTIAL rows ok; `claude login` ok.
- [ ] `pai init --default-agent claude --tasks /tmp/smoke/tasks` in the repository.
- [ ] `pai run --dry-run`: the plan names lane a, the note, branch `pai/RUN/a`.
- [ ] `pai run --workers 1 --mode local`: lane a shows `waiting-for-trust` for the new worktree.
      Expected: no keystroke reached the prompt (`pai logs a` shows it untouched).
- [ ] `pai trust`: answer the prompt yourself. Then `pai resume`.
- [ ] `pai status`: lane a `running` then `delivered`, with "Claude's own transcript for this
      worktree records the prompt". `pai inspect a` names the transcript folder.
- [ ] The agent did the task; `git -C <worktree> log` shows its commit; nothing was pushed
      (`git ls-remote /tmp/smoke/origin.git` has only main).
- [ ] Kill the agent (`pai attach a`, exit it). `pai status`: `exited`. `pai recover`: restart,
      the task "was delivered before: it is not resent". `pai recover --apply`; check that the
      agent did not receive the task twice (its transcript has one tagged prompt).
- [ ] `pai pr draft a`: the owner page renders; nothing is pushed.
- [ ] `pai cleanup --apply --stop-running`.

## Codex, one lane (required before Codex is called supported)

- [ ] The same steps with `--agent codex`; delivery evidence is "Codex's own rollout file".
- [ ] A Codex trust prompt that appears after its first frame gets no keystroke.

## Remote Control (Claude, optional for 1.0)

- [ ] `pai run --workers 1 --mode rc`: after trust, `pai status` rc `verified`.

## Afterwards

- [ ] `pai guide --cleanup` is not needed (real run); remove `/tmp/smoke` yourself.
- [ ] Record: date, versions, each box's result. Do not paste session links or tokens.
