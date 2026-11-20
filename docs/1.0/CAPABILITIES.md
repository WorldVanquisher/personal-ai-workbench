# Capabilities for the release notes: stable, experimental, unverified

One matrix for the release-facing claim of every capability. **Stable** = proposed for the 1.0
compatibility commitments (COMPATIBILITY.md) and verified on Linux in this run. **Experimental**
= ships, marked, may change (EXPERIMENTAL.md). **Unverified** = code exists, but this run has no
proof on the platform or with the program named. **Owner-only** = needs the owner (real agents,
CI, a Windows or WSL machine). Platform columns are in PLATFORMS.md.

| Capability | Class | Linux proof (this run) | What is not proven |
| --- | --- | --- | --- |
| PAI core: `init PROJECT`, `doctor PROJECT`, `context`, `handoff` (privacy filter on) | stable | core suite strict | macOS/Windows this run (CI exists) |
| Archive build, SHA256SUMS, byte-identical rebuild, `bin/pai install` with preview | stable | bundle smoke 57/57; package check 083/084 | Windows archive install this run (CI exists) |
| Package (`uv tool install` / wheel) with the workbench inside | stable | package check 081/082/086 (pip, offline) | `uv` itself this run (not installed; verified at 010 with uv 0.13.9) |
| Upgrade from 0.4.0, uninstall keeps user data | stable | package check 083-085 | Windows |
| `pai guide` (offline first run), `--recovery` | stable | test_guide under `unshare -rn` | macOS |
| `pai doctor` essential/optional/repair, backend capabilities | stable | test_doctor, test_backends | real package managers (fakes record the commands) |
| `pai init`, `pai task new/list` | stable | test_workbench | - |
| Task cards `pai task export/import` | stable | test_cards, test_portable_cards | Windows (CI runs the core part) |
| `pai run` with Claude or Codex, dispatch with transcript evidence | stable (fakes) | test_dispatch, test_workbench | **real Claude Code / Codex: owner-only** (REAL-AGENT-SMOKE.md) |
| Folder-trust prompts left to a person (`pai trust`) | stable | test_dispatch, test_recovery | real agents' current prompt texts (owner smoke) |
| `pai status` lifecycle, `--json` `pai_status_v1`, `--brief`, `pai inspect` | stable | test_runstate | macOS process age (Linux `/proc` only) |
| `pai recover` preview / apply / choose / history | stable | test_recovery (both fakes) | real agents |
| Cross-session continuation: `pai recover --continue`, `pai run --continue`, `pai guide --handoff` | stable (0.5.0 candidate slice; fakes) | test_continuation, test_guide (offline), package check from the installed wheel and archive launcher, as normal user and as root | real Claude -> Codex and Codex -> Claude (owner: REAL-AGENT-SWITCH.md); the worktree-process guard off Linux |
| Transfer packets: `pai task export --transfer`, transfer-aware `pai task import` (0.5.0 R2) | stable (proposed; fakes) | test_transfer, test_r2_recovery, test_guide (`--handoff clean`, `reverse`), package check from the installed wheel and archive launcher | real agents (REAL-AGENT-SWITCH.md run C); two checkouts continuing one packet (BACKLOG 10a) |
| Hand-paste records (`pai paste`, console) as evidence in inspect and handoffs (0.5.0 R2) | stable (proposed) | test_r2_recovery, test_console | a real agent's transcript format for pasted text (owner smoke) |
| `pai queue` work queues (core; 0.5.0 R2) | stable (proposed) | tests/test_workqueue (strict core suite), this queue's own log | native Windows run (CI on a push) |
| `pai resume`, `pai cleanup`, `pai attach`, `pai logs` | stable | test_workbench | - |
| `pai paste` (bracketed, refuses at a trust prompt) | stable | test_console, test_panes | tmux 3.5a cannot report bracketed mode (refuses multi-line unless allowed) |
| `pai pr draft` (local owner review, filters) | stable | test_draft with a refusing fake git/gh | - |
| `pai pr plan/check/open` (owner publishes) | stable | test_review, test_publish_check (fake gh) | a real GitHub: owner-only |
| `pai edit` (one-file browser editor) | stable | test_mdserver, packaging edit session | real browser this run (blocked) |
| `pai web` console: lifecycle, inspect, resume preview, paste preview, staged cue | **experimental** | test_console API; walkthrough | real-browser focus, announcements, 390 px: owner kit (`~/.cache/pai-1.0-work/browser/run.sh`) |
| `pai server` (long-running Markdown server) | experimental (checkout only) | test_mdserver | - |
| JEV (`pai jev`, `--jev-triage`) | experimental | test_jev_setup (mock) | the real service: owner-only (billed) |
| Claude Remote Control lanes (`--mode rc`) | stable (evidence model), **unverified** live | test_rc (fakes) | a live RC session: owner-only |
| Codex Remote Control | not offered | - | - |
| WSL | unverified | simulation only (detection, Ubuntu fixture) | a run on WSL: owner-only |
| macOS workbench | unverified | - | needs a macOS run |
| Native Windows workbench | not offered (WSL message) | CI asserts the refusal | - |
