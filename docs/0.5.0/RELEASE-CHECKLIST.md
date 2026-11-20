# 0.5.0 release checklist (what remains; draft for the owner)

Status on 2026-11-19, from the worker's handoff `PAI-0.5.0-CONTINUATION-HANDOFF.md`. The tree
still says 0.4.0; nothing is staged, committed, tagged or released. A sealed copy of this file and
the evidence it points to is in `~/.cache/pai-0.5.0-continuation/` (`SHA256SUMS`).

## A. Done in the worktree, with this run's evidence (worker)

- [x] Cross-session continuation slice: `pai recover --continue`, `pai run --continue`,
      `pai guide --handoff clean|interrupted`, `swarm/CONTINUATION.md`; tests `test_continuation`,
      `test_guide`; installed wheel and archive launcher from a clean HOME (package check).
- [x] Everything in the 1.0 readiness handoffs 010-100 (see `PAI-1.0-READINESS-HANDOFF-100.md`).
- [x] Full `tools/verify.py` as a normal user and as root, recorded separately
      (`user/verify.log`, `root/verify.log`); package check both ways (`user/`, `root/`).
- [x] Doctor exit-code compatibility, the supported status JSON contract, the platform evidence
      still needed: `docs/1.0/COMPATIBILITY.md`, `swarm/RUN-STATE.md`, `docs/1.0/PLATFORMS.md`.

- [x] R2 queue (cross-agent continuity, 100 items): transfer packets, both directions, hand-paste
      evidence, re-check under the lock, `pai queue`, console parity, 0.4.0 fixture, rollback, exit
      codes; see `PAI-0.5.0-R2-HANDOFF-100.md`, `docs/0.5.0/GATE.md`, `CHANGELOG-CANDIDATE.md`.

## B. Owner decisions (before anything is committed)

- [ ] Review the slices in the handoffs; commit them yourself (the proposed commands are there).
- [ ] Accept or change: COMPATIBILITY.md (including the doctor exit-code change and the status
      contract), EXPERIMENTAL.md, PLATFORMS.md criteria, BACKENDS.md criteria, ACCEPTANCE.md.
- [ ] Task cards v1 `fields` (added before release; no released card file lacks it): accept.
- [ ] Formats proposed as committed in 0.5.0: `transfer` v1 inside `pai-task-cards` v1, `pai-queue`
      v1, the hand-paste record, the recovery log's `superseded by` end records: accept or keep
      experimental (EXPERIMENTAL.md "Feature boundaries for 0.5.0").
- [ ] Keep `pai web` experimental for 0.5.0 (recommended) or run the browser kit first.
- [ ] Set the version: `pai/__init__.py` `__version__ = "0.5.0"`, the `@v0.4.0` install lines in
      README*.md, INSTALL.md and QUICKSTART*.md, and a RELEASE_NOTES.md section built from
      `docs/1.0/MIGRATION.md` and `docs/1.0/CAPABILITIES.md`.

## C. Owner runs on the release candidate commit (record each result)

Required for 0.5.0:
- [ ] CI green: `ubuntu-latest`, `macos-latest`, `windows-latest` x Python 3.11 and 3.13, and the
      four userspace images (push the candidate branch).
- [ ] `python3 -B tools/verify.py --work /tmp/pai-verify-rc` on Linux with uv 0.13.9 installed:
      0 failed; quickstart PASS (it is SKIPPED on the worker's host: no uv). The path-zsh/fish/pwsh
      steps may be SKIPPED where those shells are absent.
- [ ] Real-agent smoke, Claude lane: `docs/1.0/REAL-AGENT-SMOKE.md`.
- [ ] Real agent switch, run A (Claude -> Codex): `docs/1.0/REAL-AGENT-SWITCH.md`. Without it the
      release notes say "continuation verified with fake agents only". Run C (through a transfer
      packet into a second HOME) and the trust-prompt evidence table are in the same file.

Required only for the claim named:
- [ ] Run B (Codex -> Claude, uncertain delivery): before claiming Codex as a supported source.
- [ ] Browser kit `sh ~/.cache/pai-1.0-work/browser/run.sh <worktree>`: before promoting `pai web`.
- [ ] WSL: the command in PLATFORMS.md "Evidence still needed": before claiming WSL.
- [ ] macOS workbench: the command in PLATFORMS.md: before claiming the macOS workbench.
- [ ] `python3 -B tools/path_hint_check.py zsh|fish|pwsh`: before claiming those shells' PATH advice.

## D. Release (owner only, a separate decision)

- [ ] Build: `python3 -B tools/build_bundle.py --out dist` (byte-identical rebuild is a CI step).
- [ ] Tag `v0.5.0` on the reviewed commit; publish the archives and their `.sha256` files.
- [ ] Release notes list stable / experimental / unverified per CAPABILITIES.md, the migration
      notes, and for every platform what was verified, how, and what was not.

## E. Known limits that ship with 0.5.0 (documented, not blockers)

- Continuation guard across two separate checkouts (BACKLOG 10a); worktree-process guard is
  Linux-only (10b).
- macOS workbench process age (`/proc`), WSL unverified, native Windows workbench not offered.
- Real-browser behaviour of `pai web` unverified (experimental).
