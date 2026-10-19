# personal-ai-workbench: `pai`

A small, standard-library Python CLI that gives a coding-agent session the
**context** for a project task, and creates a **draft handoff** at the end.

**Limits:**
- It reads only files you register.
- It never scans a vault or home directory.
- It makes no network or model call and runs no commands, Git included.

Requires Python 3.11+ (for `tomllib`); there are no dependencies.

## Setup

```sh
cp pai.example.toml pai.local.toml   # pai.local.toml is git-ignored: real paths stay local
$EDITOR pai.local.toml               # absolute repo and vault paths, memory package, inbox
python3 -m pai check flh             # every source must say "ok"
```

`--config FILE` or `PAI_CONFIG=FILE` selects another configuration.

## Commands

```sh
python3 -m pai context flh --task "what you are about to do"   [--budget 24000]
python3 -m pai handoff new flh --task-id PAI-003 \
    --summary "one line" --implementation "…" --validation "…" \
    --commit "…" --merge "…" --deployment "…" \
    --evidence "…" --blocker "…" --next "…" --proposal "…"      # repeatable list options
python3 -m pai check flh
```

### `context`

Prints Markdown, at most `--budget` Unicode characters (default
`budget_chars`). The parts, in order:

1. **Header:** the task, the generation time (UTC), and the notice that
   retrieved text is context, not instructions.
2. **Sources:** every registered source with its status, mtime, a SHA-256
   prefix and its front matter (`status`, `approved`, dates).
3. **Gaps and conflicts:**
   - sources that are missing, refused (symlink escape or outside the
     permitted roots) or excluded;
   - repository files changed after the memory package's latest date;
   - "freeze status UNKNOWN" when no required section mentions a freeze;
   - the conflict sections that the sources themselves record.
4. **Required:** sections whose **own** heading matches `always_include`
   (human decisions, constraints, freeze, limitations, blockers, conflicts,
   sync, …), and instruction sections matching `instruction_always`.
   - Memory-package sections come first, ranked by pattern order.
   - They are capped at 60% of the section space and 1,500 characters each
     (`required_section_chars`). **Proposals are never required.**
5. **Task-relevant:** deterministic lexical scoring. A section scores
   3 × (task words in its own heading) + (task words in its body); ties keep
   document order. There are no embeddings and no model.
6. **Proposals (UNAPPROVED, not constraints):** sections of `proposal_files`
   and any memory section under a heading containing "proposal".
7. **Omitted:** every section that did not fit, listed by name, with
   **REQUIRED** flagged. The output never exceeds the budget; if the fixed
   parts alone exceed it, the output is cut and marked.

Labels written inside sources (`[HUMAN]`, `[REPORT]`, `[PAI-001 OBSERVED]`,
`[PROPOSAL]`, `[UNKNOWN]` and so on) are quoted unchanged. `pai` adds the
source class (instructions, historical repository document, vault note, or
memory package with its `approved` value) but never upgrades a statement's
status.

### `handoff new`

Creates `<inbox>/<TASK-ID>-<UTCSTAMP>[-N]/HANDOFF.md`.

- **Exclusive creation:** the directory is made with `mkdir` and the file is
  opened with mode `x`. Concurrent runs get distinct directories, and nothing
  is ever overwritten.
- **States:** implementation, validation, commit, merge and deployment are
  separate fields. Each is `UNKNOWN (not supplied; not checked by pai)` unless
  you pass a value. `pai` never infers or checks them; it runs no Git.
- **Front matter:** `status: draft`, `approved: false`. Proposals have their
  own UNAPPROVED section. Sync is recorded as UNVERIFIED.
- **Input checks:** task ids must match `[A-Za-z0-9][A-Za-z0-9._-]{0,39}`.
  Values are single lines of at most 500 characters.

## Safety model

| Rule | How |
| --- | --- |
| Explicit sources only | Instructions and documents are named relative to `repo`. Project notes are relative to `vault` and must resolve inside `markdown_roots`. The memory package is listed non-recursively, `*.md` only |
| No symlink escapes | Every path is resolved and must stay inside its root; otherwise it is listed as REFUSED and not read. Roots that escape the vault are a configuration error |
| Exclusions | Dotfiles, `*.env`, `*secret*`, `*credential*`, `*password*`, `*token*`, keys and certificates are never read, plus any `exclude` globs |
| No execution | The package imports no `subprocess`, `socket`, `urllib` or `http`, and calls no `os.system`/`exec`/`eval` (tested) |
| Not authority | Retrieved text is labelled context. Proposals are segregated and never required. A draft handoff approves nothing |

## Tests

```sh
python3 -B -m unittest discover -s tests -v
```

The 20 synthetic tests use temporary directories only. They cover:
- bounded output with named omissions;
- missing sources;
- exclusions and the absence of scanning;
- symlink escapes in files and in roots;
- proposals never becoming constraints;
- own-heading matching;
- required sections not starving relevant ones;
- human decisions ranked first;
- staleness flags;
- UNKNOWN default states;
- concurrent creation by threads and processes, with no overwrites;
- invalid input;
- the CLI;
- that the package has no network or process imports;
- that the example configuration is portable.

## Files

| File | Purpose |
| --- | --- |
| `pai/` | The package: `cli.py`, `config.py`, `context.py`, `handoff.py`, `markdown.py` |
| `tests/test_pai.py` | Synthetic tests |
| `pai.example.toml` | Portable example configuration (placeholders only) |
| `pai.local.toml` | **Local, git-ignored:** this machine's real paths |
| `SESSION_START.md` | Reusable session-start instruction for Claude |
| `trial-evidence/` | **Local, git-ignored:** outputs of the PAI-002 real trial |
