# Claude Code permission modes and shell commands (R6-COR-012)

Which `--permission-mode` lets a headless worker (`pai_swarm.py run`, `claude -p`) run a shell
command that is **not** in its `--allowedTools` list, and which mode pai_swarm should use.
Lane tree; rerun on merged main needed.

## What is known, by source

| Fact | Source |
| --- | --- |
| Claude Code 2.1.330 offers `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan` | `claude --help` on the core worker host (real run, Arch Linux, 2026-11-22) |
| `--permission-prompts none`: "anything that would prompt is denied automatically; the permission mode still decides everything else" | same `claude --help` |
| pai_swarm passes `--permission-mode acceptEdits` (default), `--permission-prompts none` when the CLI has it, and `--allowedTools` = Read/Edit/Write/MultiEdit/Glob/Grep/TodoWrite + `Bash(<DEFAULT_BASH and --allow-bash patterns>)` | `swarm/pai_swarm.py` `command()`, `DEFAULT_BASH` |
| Under `acceptEdits` with only `grep *` allowed, a real worker ran `printf 'smoke ok\n' >> SMOKE.md`, `od -c SMOKE.md` and `cat SMOKE.md`; none is in `DEFAULT_BASH` or `--allow-bash` | owner real run, 2026-11-22 06:13Z, haiku, `claude -p` (融合方案 §0.1) |
| So, in that mode, Claude Code approved those three commands without a prompt, although nobody could answer a prompt | inference from the two rows above |
| Why (acceptEdits treating file-writing shell commands in the working directory as edits, or a built-in read-only command list) | **not established**: not tested per command, and this lane may not start Claude Code |

The other modes were not run by anyone in this round. Nothing below claims how `auto`, `dontAsk`,
`manual` or `plan` treat a command outside the allow list.

## What pai enforces regardless of the mode (this round)

The permission mode is Claude Code's decision; PAI does not rely on it for `paths:`.
`swarm/guard.py bash` is a PreToolUse hook. Claude Code calls it before **every** Bash call,
including one the mode would approve on its own. From R6-COR-011 on, it gets the task's
`--allow` paths and refuses writes outside them:
- redirections: `>`, `>>`, `>|`, `&>`, `2>`;
- `tee`, `sed -i`, `cp`, `mv`, `rm`, `dd of=`, `touch`, `truncate`, `mkdir`, `ln`, `install`;
- inside `bash -c` / `sh -c` / `eval` and after a `cd`;
- a target it cannot resolve (`$VAR`, a backtick) is refused when the task has paths.

Not covered: a program that writes files by itself (a build tool, `python script.py`). The
`--allowedTools` list and a review of the diff stay the controls for those
(`swarm/tests/test_guard.py`).

## Default: `acceptEdits` (R6-COR-077, owner decision D-R6-1 = A)

`pai_swarm.py run` passes `--permission-mode acceptEdits` unless told otherwise. The guard hook (below)
refuses writes outside the task's `paths:` in every mode, so the default does not depend on the mode for
that. `--permission-mode manual` stays available, for sessions where a person is present to approve.

**Headless, `manual` blocks even the writes the task asks for.** This is a real-run result, not an
inference (reported-by-mastermind, 2026-11-22, WSL Ubuntu, Claude Code 2.1.330, haiku,
`tools/real_agent_smoke.sh`):

| Mode | Result | Cost |
| --- | --- | --- |
| `manual` (the default from R6-COR-068 to R6-COR-077) | 6 passed, 1 failed: the worker could not write SMOKE.md, the write its task asks for. The permission system wanted a person to approve the edit, and in `-p` mode there is nobody to ask; the task ended in review | $0.004 |
| `acceptEdits` | 7/7 passed; the write to OUTSIDE.md, outside the task's paths, was still refused by the guard hook; JEV scored done 0.91, checked 0.96 | $0.002 |

R6-COR-068 had made `manual` the default on the assumption that Edit and Write in `--allowedTools` would
still run without a prompt; that assumption was tested with the fake claude only, and the real run
disproved it. The fake-claude test (swarm/tests/run_tests.sh) now asserts `acceptEdits` again.

## Recommendation before R6-COR-068 (kept for the record; superseded by the section above)

1. Keep `acceptEdits` as the default for now. It is the only mode with a recorded real headless
   run, and the guard hook now enforces `paths:` for shell writes.
2. Never offer `bypassPermissions`, and do not make `auto` the default. Both widen what runs
   without a prompt, and neither has been measured here.
3. Candidate for the default after one owner measurement: `dontAsk`. pai_swarm already lists it
   in `--permission-mode` help. If the measurement shows that it denies a Bash command outside
   `--allowedTools` (for example `od -c SMOKE.md` with only `grep *` allowed) while still running
   allowed commands and edits, it matches pai_swarm's model: everything a worker may run is
   listed, and anything else is refused, not silently approved.

## Owner measurement (not run by this lane)

`tools/real_agent_smoke.sh` (R6-COR-037) runs the 2026-11-22 headless smoke. To compare modes,
run it once per mode and compare the `Bash` calls each run made, from the run's JSON output:

```bash
for m in acceptEdits manual dontAsk; do PAI_SMOKE_PERMISSION_MODE=$m bash tools/real_agent_smoke.sh; done
```

Record per mode: which commands ran, which were denied, and the cost. The 2026-11-22 record for `manual` and
`acceptEdits` is in the table above; `dontAsk` has not been measured.

## Guard: what the Bash hook checks, and its known limits (R6-COR-067)

Checked (swarm/tests/test_guard.py): `>`, `>>`, `>|`, `&>`, `2>`, `exec >file`, `exec 3>>file`, heredocs
into files (`cat <<EOF > f`, `cat > f <<EOF`, `tee f <<EOF`; the heredoc's text is not read as shell),
`tee`, `sed -i`, `cp`, `mv`, `rm`, `dd of=`, `touch`, `truncate`, `mkdir`, `ln`, `install`, inside
`bash -c`/`sh -c`/`eval`, after `cd`, and `$(...)`. When the task has `paths:`, a target that cannot be
known before the command runs is refused: `$VAR` or backticks in a target, `cd` to an unknown folder,
`... | xargs rm` (and other writers after xargs), `find ... -delete` / `-exec` / `-execdir` / `-ok`.

Known limits (not checked; the allowlist of commands and the owner's review of the diff cover them):
- programs that write files by themselves: `python script.py`, `python -c`, `make`, `npm`, `git
  apply` (git writes are refused separately), editors run in batch mode;
- shell functions or aliases defined earlier in the same session and called later;
- a target path that is a symbolic link created earlier in the same command;
- obfuscation that only a shell can resolve (`printf '\x3e' | sh`, brace expansion into `>`).
