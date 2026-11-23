# Documentation map

Which document answers which question. Chinese versions sit beside the English ones
(`*.zh-CN.md`) where they exist.

<!-- doc_check: pending pai round, pai prompt -->

## Start here

| When | Read |
| --- | --- |
| You have never used PAI | [FIRST-USE.md](FIRST-USE.md) ([中文](FIRST-USE.zh-CN.md)): install, a practice repository, `pai doctor`, `pai guide`, `pai init`, a first round with one worker, finishing it, troubleshooting |
| You only want the shortest path | [QUICKSTART.md](../QUICKSTART.md) ([中文](../QUICKSTART.zh-CN.md)) |
| A round just merged and you start the next one; or you upgrade pai | [REFRESH.md](REFRESH.md) ([中文](REFRESH.zh-CN.md)): upgrade and rollback, every round, pitfalls, cheat sheet, recovery |
| A word is unclear (round, lane, checkpoint, NEXT-PR, JEV, trusted root …) | [GLOSSARY.md](GLOSSARY.md) ([中文](GLOSSARY.zh-CN.md)) |
| You use Obsidian as the console: Home, boards, lane and decision notes, answering and approving | [OBSIDIAN.md](OBSIDIAN.md) ([中文](OBSIDIAN.zh-CN.md)) |
| A question that is not a procedure ("can it …", "why does it …") | [FAQ.md](FAQ.md) ([中文](FAQ.zh-CN.md)) |
| You want a whole round with two lanes to copy from | [examples/two-lane-round/](../examples/two-lane-round/README.md), with its terminal transcript |

## Understand it

| When | Read |
| --- | --- |
| How AGENTS.md is split into layers (constitution, contracts, lane, proposals), and what PAI does with each | [AGENTS-LAYERS.md](AGENTS-LAYERS.md) ([中文](AGENTS-LAYERS.zh-CN.md)) |
| What PAI is, what it is not, how it differs from orchestration frameworks | [WHY-PAI.md](WHY-PAI.md) |
| Which platforms really ran, and on what evidence | [FIRST-USE-PLATFORMS.md](FIRST-USE-PLATFORMS.md) |
| How a lane's state is named and what evidence each status needs | `swarm/RUN-STATE.md` (in the archive and in a checkout) |
| What each agent backend (Claude Code, Codex) exposes | `swarm/BACKENDS.md` |

## Reference

| When | Read |
| --- | --- |
| Installing in detail, Windows, PATH | [INSTALL.md](../INSTALL.md) |
| PAI's own commands (`context`, `handoff`, `queue`, …) | [REFERENCE.md](../REFERENCE.md) |
| The workbench in full: configuration, trust, SSH, cleanup, JEV costs | `swarm/WORKBENCH.md` |
| Continuing a lane in a new session (`pai recover --continue`) | `swarm/CONTINUATION.md` |
| Every command and flag | `pai --help`, then `pai COMMAND --help` |
| What changed, and what was tested | [RELEASE_NOTES.md](../RELEASE_NOTES.md) |
| The prompt for an agent session | [SESSION_START.md](../SESSION_START.md) |

Arriving with the R6 lanes (not merged yet; each lane's own page):

- `docs/ROUND.md` (round lane): `pai round` and ROUND.json.
- `docs/PROMPTS.md` (prompts lane): the prompt library and `pai prompt`.
- `docs/HANDOFF-V2.md` (core lane): the handoff format `pai-handoff/2` and NEXT-PR.md.

## For maintainers

| When | Read |
| --- | --- |
| Before a release: what to check in the docs | [the checklist below](#before-a-release-the-documentation-checklist) |
| 0.5.0 candidate: gate, evidence, checklist | [0.5.0/](0.5.0/GATE.md) |
| 1.0 proposals: acceptance, platforms, compatibility, real-agent smoke | [1.0/](1.0/ACCEPTANCE.md) |
| Checking the manuals against the commands | `python3 -B tools/doc_check.py` |

## Before a release: the documentation checklist

Run these on the release candidate commit, in order. Each one says what passing looks like.

1. **Commands, flags, links, translations.** Run `python3 -B tools/doc_check.py`. It must end with
   `OK`. CI runs the same check on Linux (reported-by-core, R6-COR-008). A `PENDING` line means a manual still declares a command from an unmerged lane; a
   `WARNING` means that command exists now, so remove it from the `<!-- doc_check: pending … -->`
   marker.
2. **Version numbers.** Bump every install line in README*.md and docs/FIRST-USE*.md to the new
   version (`pai-workbench-X.Y.Z.tar.gz`, `@vX.Y.Z`). The bundle smoke (step 4) fails on any line that
   names another version. The REFRESH examples (`0.4.1` for "the new version", `0.4.0` for the
   rollback) are illustrations: update them when they would look older than the release.
3. **The allowlist.** Every manual users need is listed in `release/allowlist.txt` (`docs/README.md`,
   FIRST-USE, REFRESH, GLOSSARY, FAQ in both languages, WHY-PAI, FIRST-USE-PLATFORMS), and so is every
   runtime file the lanes added. `python3 -B tools/build_bundle.py --out dist` refuses unlisted
   runtime modules and absolute developer paths (a home folder, a server's data folder), so quote no machine's
   real paths in a manual.
4. **The bundle.** Run `python3 -B tools/bundle_smoke.py dist/pai-workbench-X.Y.Z.tar.gz dist/pai-workbench-X.Y.Z.zip --work /tmp/smoke-X`.
   Success: `SMOKE PASSED` with 0 failed. Its manual checks need the 7 manuals present, matching
   versions, and, when the prompt library ships, every prompt file's marker matching
   `pai prompt list` and `pai prompt lint` passing.
5. **Platform claims.** Update [FIRST-USE-PLATFORMS.md](FIRST-USE-PLATFORMS.md) from this release's
   real runs and CI (with run links), and never from older ones. A cell says **ran** only with a date
   and who ran it.
6. **Walk the manual.** Walk FIRST-USE once in a brand-new home folder
   (`HOME=$(mktemp -d)`, fake agents as in [examples/two-lane-round/](../examples/two-lane-round/README.md)).
   Every command must give the output the page shows. Any surprise becomes a row in its section 8.
7. **The fresh-reader score (optional, JEV).** Score each FIRST-USE and REFRESH section with the
   question "can a reader follow only this section without asking anyone?". Investigate any section
   that dropped since the last release.
9. **Language policy (owner decision PRM-D1 = A, 2026-11-22).**
   - The user manuals are English, each with a `.zh-CN.md` twin (step 8).
   - The prompt library (`pai/prompts/*.md`) stays **Chinese**, with no English copy.
   - The docs lane checks this at release. Run:

     ```sh
     python3 -B -c "import glob,re;[print(f, round(len(re.findall(r'[一-鿿]',t:=open(f,encoding='utf-8').read()))/max(1,len(t)),2)) for f in sorted(glob.glob('pai/prompts/*.md'))]"
     ```

     Every file must print a share above 0.05. A file near 0 has become English: translate it back,
     or ask the owner.
   - `docs/PROMPTS.md` must say that the library is Chinese.
8. **Both languages.** Every change to an English manual has its `.zh-CN.md` twin in the same commit.
   Step 1's translation check catches missing sections, code blocks and tables, but not wording.
