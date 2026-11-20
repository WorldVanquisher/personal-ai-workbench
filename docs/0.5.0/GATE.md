# 0.5.0 gate, and the separate list of what still blocks 1.0 (draft for the owner)

The gate is the owner's decision; this lists what must hold, what the worker's run shows for it
and what is left. The version, tags and publishing stay with the owner (nothing here was tagged,
pushed or published; `git for-each-ref` and `git tag -l` are unchanged by this work).

## 0.5.0 gate: every line must hold

| # | Condition | Worker's evidence (fakes, this host) | Left for the owner |
| --- | --- | --- | --- |
| G1 | `tools/verify.py` 0 failed, as a normal user and as root | `~/.cache/pai-0.5.0-r2/user/`, `root/` (numbers in the 100 handoff) | the same on the candidate commit with uv installed (`--require-all`): quickstart then PASS |
| G2 | Installed wheel and archive launcher: install, upgrade from 0.4.0, rollback, interrupted install, uninstall, both transfer directions, console | package step of G1 | - |
| G3 | CI green on Linux, macOS, Windows (3.11, 3.13) and the four userspace images | not run (needs a push) | push the candidate branch; record the run |
| G4 | Real-agent smoke, Claude lane (`docs/1.0/REAL-AGENT-SMOKE.md`) | owner-only | run it |
| G5 | Real switch run A, Claude to Codex (`docs/1.0/REAL-AGENT-SWITCH.md`) | owner-only | run it, or ship with "continuation verified with fake agents only" |
| G6 | Owner decisions: COMPATIBILITY.md, EXPERIMENTAL.md boundaries, cards/transfer/queue formats as committed, `pai web` stays experimental | proposals in the docs | decide |
| G7 | Version set to 0.5.0, release notes from `CHANGELOG-CANDIDATE.md` and MIGRATION.md | not done (version stays 0.4.0 by rule) | set, review, commit |
| G8 | Tag `v0.5.0` and publish the archives with `.sha256` | not done (owner-only) | separate decision |

Required only for a specific claim in the 0.5.0 notes: switch run B and run C (Codex as a source,
packets with real agents), the browser kit (before `pai web` leaves experimental), WSL, macOS
workbench, zsh/fish/PowerShell PATH advice: commands in `docs/1.0/PLATFORMS.md`.

## What still blocks 1.0 (separate from the 0.5.0 gate)

1. Real-agent evidence for every supported backend and mode (Claude local and Remote Control;
   Codex local), switch runs A, B and C recorded.
2. CI evidence on macOS and Windows for the committed surfaces; a macOS workbench run with the
   process-age rule ported off `/proc` (BACKLOG P2 6), or the macOS workbench left unverified.
3. A WSL run (BACKLOG P1 3).
4. Real-browser evidence for the console, or the console kept experimental in 1.0.
5. Continuation across two separate checkouts (BACKLOG 10a: a repository-level marker) and the
   worktree-process guard off Linux (10b): fix, or document as 1.0 limits.
6. One full minor release with no change to the committed formats (`pai_status_v1`,
   `pai-task-cards` v1 + `transfer` v1, `pai-queue` v1, the recovery log), per EXPERIMENTAL.md.
7. The owner's compatibility policy for keeping a previous schema available (COMPATIBILITY.md).
