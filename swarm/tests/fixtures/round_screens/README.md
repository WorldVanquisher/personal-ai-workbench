# Screens for `pai round status` (swarm/round_status.py)

| file | source |
|---|---|
| real-running-2.1.330.txt | real capture (`tmux capture-pane -p -J`), 2026-11-22, a working PAI R6 lane; session link and home path redacted |
| real-idle-2.1.327.txt | real capture, same day, an FLH R6 lane at its start screen (Claude Code v2.1.327 banner) waiting for input; link redacted |
| synthetic-trust-2.1.txt | synthetic: the 2.1.x dialog as the R6 owner quoted it ("Quick safety check", "Yes, I trust this folder", "No, exit"), same wording as swarm/tests/fake_claude_rc.py |
| synthetic-trust-older.txt | synthetic: the older wording kept in swarm/agents.py ("Do you trust the files in this folder?", "Yes, proceed", "No, exit") |
| synthetic-quoted-in-transcript.txt | synthetic: a worker whose own prompt quotes "Yes, I trust this folder" and "No, exit", with Claude's normal footer: NOT a trust dialog |
| real-ansi-placeholder-2.1.330.txt | real capture with colours (`capture-pane -e`), 2026-11-22: an FLH lane's empty input box showing the dim `Try "…"` placeholder |
| real-ansi-dim-suggestion-2.1.330.txt | real capture with colours: a PAI lane whose input box shows dim text (Claude Code's prompt suggestion; nobody typed it) |
| real-ansi-busy-empty-2.1.330.txt | real capture with colours: a working lane, empty input box |
| synthetic-ansi-owner-draft.txt | synthetic: the dim-suggestion capture with the dim attribute removed, as typed text renders |
| synthetic-teach-auto-mode.txt | synthetic: the "Teach auto mode about your environment?" dialog named by the mastermind; its option texts are guessed (no capture was available) |
| synthetic-queued.txt | synthetic: a working lane with "Press up to edit queued messages" |

No real trust dialog was captured: launching a real Claude Code session in an untrusted folder is
owner-only in R6. Replace the synthetic files with a capture when one is available.
