# FAQ

English | [简体中文](FAQ.zh-CN.md)

Short answers to the questions that are not procedures. The procedures are in
[FIRST-USE.md](FIRST-USE.md) and [REFRESH.md](REFRESH.md); the words in [GLOSSARY.md](GLOSSARY.md).

## 1. Do I need Obsidian?

No. The vault is a folder of Markdown files; Obsidian is one way to read it, and any editor works.
`pai init` asks for a task notes folder: press Enter and pai uses its own.

## 2. Does PAI ever answer an agent's folder-trust prompt for me?

Never. `pai status` shows `waiting for folder trust`, and `pai trust` opens the prompt for you to
answer. `pai web` and `pai paste` refuse to paste into it. Keeping every round under one
[trusted root](GLOSSARY.md#trusted-root) means fewer prompts.

## 3. Can PAI push or merge for me?

- **Push:** only when you run `pai pr open LANE`, after the review checks you configured have
  passed (`pai pr check LANE`). It is never forced.
- **Merge:** never. You merge on GitHub.
- **Workers:** the workers in a round do not run git write commands at all.

## 4. Does it run on Windows?

The core (`pai context`, `pai handoff`, `pai queue`, `pai edit`, `pai doctor --agents`) runs on
native Windows. The workbench (`pai run`, `pai status`, `pai web`, …) needs tmux, so on Windows
install pai inside WSL. Workbench commands on native Windows stop with a message that says so.
What has really run where: [FIRST-USE-PLATFORMS.md](FIRST-USE-PLATFORMS.md).

## 5. Do I need JEV, and what does it cost?

No. JEV is an optional second opinion. When `pai init` asks for a key, press Enter to skip it.

- **Cost:** on 2026-11-22 a request used about 480 input tokens and took 0.1–0.4 s.
- **What it judges:** how a report is written, not whether it is true, so pai never lets it override
  a real check.

## 6. Where does PAI keep its state?

In files.

- **Configuration:** `~/.config/pai-workbench` (and `~/.config/pai` for the core).
- **Task notes:** your notes folder.
- **Run state:** `~/.cache/pai-swarm`.
- **Queues:** two files each, the queue and its append-only `.log`.
- **Handoffs and checkpoints:** in the vault's `Agent Inbox/`.

A cleared conversation or a reboot loses nothing. See [REFRESH.md](REFRESH.md#e-recovery).

## 7. Does PAI send my code anywhere?

- **The core:** makes no network or model calls and runs no commands.
- **The workbench:** talks to your Git remote (fetch; push only on `pai pr open`) and starts the
  agents you configured. What those agents send is between you and their provider.
- **JEV, when enabled:** sends only the text of the decision it is asked about. For triage that is
  the note; for verify, the report. Emails, secrets and session links are redacted first.

## 8. Why does `pai status` exit with 1?

Because a lane needs a look: it waits for you (a trust prompt, a task not delivered yet), or its
agent has exited, which is also how a finished worker looks. 0 means every lane's agent is running
and has its task, even while it is still working. Read the `lifecycle` line of each lane.

## 9. I answered the trust prompt, but the task still says `queued`

You answered after `pai run` had returned, so nothing was sending it. Run `pai resume`. (`pai trust`
sends the waiting task itself.)

## 10. Can I use Codex instead of Claude Code?

Yes: `pai init` asks for the default agent, and `pai run --agent codex` chooses per run. Codex lanes
have no Remote Control mode (`pai status` shows `rc not-supported`), so you follow them with
`pai attach` or `pai web`.

## 11. Is `pai web` safe on a server?

It listens on 127.0.0.1 only, behind a sign-in link that works once. From another computer, use an
SSH tunnel: `pai web --no-browser --port 8766` on the server, then
`ssh -N -L 8766:127.0.0.1:8766 YOU@SERVER` on your laptop. It is experimental.

## 12. How do I uninstall?

| Installed with | Remove |
| --- | --- |
| the archive | `rm ~/.local/bin/pai` and `rm -r ~/.local/share/pai-workbench` |
| uv | `uv tool uninstall pai-workbench` |
| pip in a venv | `rm ~/.local/bin/pai` and `rm -r ~/.venvs/pai` |

Your configuration, task notes, handoffs and run state stay until you delete them yourself.

## 13. QUICKSTART or FIRST-USE?

- **QUICKSTART.md** is the short path for someone who already knows Git, tmux and their agent.
- **FIRST-USE.md** is the full path: every step with its expected output and what to do when it
  differs. It was written from walking it in an empty home folder.

## 14. Why is AGENTS.md "the heart"?

Every agent session reads it first, for every task, so its quality decides every worker's.

- A wrong rule there is followed by every lane.
- A stale status paragraph misleads every lane.
- A 1,000-line file costs each session context before it opens a single source file.

PAI therefore splits it into a short constitution (L0), per-area contracts (L1), per-lane
instructions written by PAI (L2) and proposals that wait for you (L3). See
[AGENTS-LAYERS.md](AGENTS-LAYERS.md), and for a worked example the analysis of a real 1,074-line
file in [research/FLH-AGENTS-ANALYSIS.md](https://github.com/WorldVanquisher/personal-ai-workbench/blob/v0.5.0/docs/research/FLH-AGENTS-ANALYSIS.md).

## 15. Why "lubrication layer"?

PAI does not replace the agents or decide for you. It prepares each round, connects the agents'
work, and collects it back, so that your decisions are quick and well-founded. See [WHY-PAI.md](WHY-PAI.md).
