# Backlog after the 1.0 readiness work (prioritized)

Only gaps this run proved (by a test, a refusal, a blocked step or a measured result). P1 blocks a
1.0 claim; P2 should land before or soon after 1.0; P3 is improvement.

## P1: blocks a 1.0 claim

1. **Real-agent smoke (owner-only).** Every lane test uses fakes. Linux "supported" needs one
   recorded Claude lane run (docs/1.0/REAL-AGENT-SMOKE.md); Codex "supported" needs its own.
2. **CI on the candidate (owner-only).** macOS and Windows evidence exists only as CI steps that
   were not run in this session (056). Push a branch and record the run.
3. **WSL run (owner-only).** WSL behaviour is simulation-only: planner and missing-pai fixtures.
   Run `tools/onboarding_acceptance.py`, `pai guide` and the planner tests on WSL Ubuntu.
4. **Version decision (owner).** The tree says 0.4.0; the package check used 0.5.0 in a scratch
   copy only. Decide 0.5.0 (then 1.0 later) or a 1.0 release candidate.

5a. **Real agent switch (owner-only).** `docs/1.0/REAL-AGENT-SWITCH.md` runs A (Claude to Codex)
    and B (Codex to Claude, with an uncertain delivery) prove continuation with the real programs.

## P2: before or soon after 1.0

5. **Real-browser check of `pai web`** (045, 046, 048, 049 are "verified prototype"): run the
   owner kit (`~/.cache/pai-1.0-work/browser/run.sh`); it was written but `docker run` was not
   permitted in this session. Required before `pai web` leaves experimental.
6. **macOS workbench.** Process start time and age come from `/proc` (`wtlock.proc_start`,
   `rc.proc_age`): on macOS the pid-reuse guard and the 120 s not-accepting-input rule are absent.
   Port with `ps -o lstart=,etime=` and add a macOS workbench CI job (tmux from Homebrew).
7. **Strict suite on macOS/Windows CI.** CI still runs `-W error::ResourceWarning -m unittest`
   there (scoped after the verification batch); switch once a run shows them clean.
8. **Browser recovery is synchronous.** `POST /api/recover` waits up to 10 s in one request; make
   it a background job with progress, or keep it to "preview + copy the command".
9. **Doctor and the project's launch mode.** Claude Remote Control is always essential in doctor;
   with `agents.claude.mode = "local"` configured it should be optional.
10. **Path hints in zsh, fish and PowerShell** (059): `tools/path_hint_check.py SHELL` runs the
    advice in that shell (bash passes on the worker's host; `verify.py` reports the others as
    SKIPPED when the shell is missing). Run it where those shells are installed, or in CI.

10a. **Continuation across separate checkouts.** Two clones (or worktree roots) can each continue
    the same task card once: the guard is the shared worktree and the note's `continued_marker`.
    A repository-level marker (a pai-owned ref) would close it; that is a design decision.
10b. **Worktree-process guard off Linux.** `pai run --continue` checks `/proc` for a process still
    working in the lane's worktree; macOS needs `lsof` or `ps -o` equivalents.

## P3: improvements

11. **Native Windows task cards behind an opt-in (057).** The slice is proven importable without
    POSIX modules; routing `pai task` on Windows needs `workbench.py` to stop importing `fcntl`
    users (`wtlock`) for that command, and an owner decision on the opt-in.
12. **Offline builds in `verify.py`.** `quickstart` needs uv and `package` needs setuptools; on an
    offline machine both are SKIPPED unless a local setuptools folder is given.
13. **Codex readiness** comes from the screen only; look for a Codex status file comparable to
    Claude's session record (a capability gap doctor reports today).
14. **`pai pr draft`**: optional `--diff` to embed the full diff; per-file check of the task's
    `paths` against the changed files.
15. **Recovery log scope.** It is local to `PAI_SWARM_HOME`; two machines sharing a run are not
    coordinated (documented, not solved).
16. **Status text** gained lines; anyone scraping it must move to `--json` (migration note).
17. ~~**`verify.py` headless step** left `swarm/__pycache__` in the checkout~~: fixed in the R2 queue
    (verify sets PYTHONDONTWRITEBYTECODE for every step).
18. **Rolling back to 0.4.x after a handoff.** 0.4.x does not know `handed-off`; in the package check
    its `pai resume` of a handed-off run was refused only because the continuation held the
    worktree. A note in MIGRATION.md says not to resume handed-off runs with 0.4.x; a stronger guard
    would need a field 0.4.x already honours (it skips only `retired` lanes, which would hide the
    lane from 0.5.0's status too).
19. **Transfer packets carry no commits.** The preview says BLOCKS when no remote-tracking branch
    holds the head and suggests `git bundle`; an optional `--bundle FILE` that writes the bundle
    beside the packet (owner decision: it moves code, not only notes).
20. **Directive detection in quoted handoffs** (`continuation.DIRECTIVE_RE`) is a short English list;
    it flags, it never blocks, and the prompt's "records, not instructions" sentence is the real
    guard.
