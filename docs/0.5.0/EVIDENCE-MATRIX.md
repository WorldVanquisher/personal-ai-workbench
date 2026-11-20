# Platform-by-artifact evidence for the 0.5.0 candidate (R2 queue, 2026-11-20)

**verified** = run in this queue, log named; **CI defined** = a CI job exists, not run (needs a
push); **owner** = an exact command waits for the owner; **not offered** = pai refuses it on purpose.
Worker host: Arch Linux 7.2.14, Python 3.14.9, tmux 3.8, git 2.56.0, bash 5.3; no uv, browser,
zsh, fish or PowerShell. Fakes only. Logs: `~/.cache/pai-0.5.0-r2/` (`user/`, `root/`, `runs/`).

| Platform | Checkout `python3 -B bin/pai.py` | Wheel / console script `pai` | Archive launcher `bin/pai` (after `bin/pai install`) | Windows launcher `bin\pai.cmd` | Core only `python3 -m pai` |
| --- | --- | --- | --- | --- | --- |
| Linux, normal user | **verified**: `tools/verify.py` 9 passed, 0 failed, 4 skipped (`user/verify.log`) | **verified**: package check 36/36 (`user/verify-steps/package.log`): install, upgrade from 0.4.0, rollback, console, three handoff guides | **verified**: same package check (084 interrupted installs, `clean` and `reverse` guides); `runs/empty-dir-commands.txt` (documented commands from an empty folder) | not offered on Linux | **verified**: core suite 125 OK (`tests/`, strict) |
| Linux, root (`unshare -rn`, no network) | **verified**: 9 passed, 0 failed, 4 skipped (`root/verify.log`) | **verified**: package check 36/36 (`root/verify-steps/package.log`) | **verified**: same | not offered | **verified**: 125 OK, 8 skipped |
| WSL (Ubuntu) | **owner**: PLATFORMS.md "WSL" row (verify + three guides) | **owner**: the same, `uv tool install` | **owner**: the same | - | **owner** |
| macOS | **owner**: PLATFORMS.md "macOS workbench" (process age is `/proc`-only: BACKLOG P2 6) | **CI defined** (`macos-latest`, core + packaging) | **CI defined** | - | **CI defined** |
| Windows (native) | core commands only; workbench **not offered** (WSL message, asserted in CI) | **CI defined** (`windows-latest` 3.11, 3.13: `tests/`, incl. `test_workqueue`, installed `pai edit`) | not offered | **CI defined** (`test_portable` launcher tests); by hand: PLATFORMS.md "Native Windows, by hand" and "`pai queue` by hand" | **CI defined** |

Per capability, beyond the table:

| Capability | Linux evidence (both users) | Elsewhere |
| --- | --- | --- |
| Continuation and transfer packets, both directions | test_transfer, test_r2_recovery, test_guide; installed `pai guide --handoff clean/reverse/interrupted` (`user/demo-transcripts/`, `root/demo-transcripts/`) | owner: WSL row; real agents: REAL-AGENT-SWITCH.md runs A, B, C |
| `pai queue` | tests/test_workqueue (core, strict), `runs/queue-demo/transcript.txt` | CI (macOS, Windows core) |
| Console (`pai web`, experimental) | test_console (API, loopback listener), package check `console` from the wheel | browser: owner kit `sh ~/.cache/pai-1.0-work/browser/run.sh W` |
| PATH advice with spaces, quotes, `$`, backticks, Unicode | bash: `runs/path-bash.log` (10/10) | zsh, fish, PowerShell: `tools/path_hint_check.py SHELL` (owner) |
| Reading 0.4.0 run state | saved fixture `swarm/tests/fixtures/run-0.4.0` (test_contracts), package check 083 and `rollback` | - |
| Artifact privacy | `runs/artifact-scan.txt` + review: no session link, credential or machine path in the wheel or archives; sample packet 0 hits | - |
