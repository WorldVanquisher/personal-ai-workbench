# pai: personal AI workflow, version 0.1.0

A small, standard-library Python tool for agent sessions on a project:
- it gives the agent the **context** for a task (rules, human decisions,
  status and limitations) from files you register;
- it creates a **draft handoff** in your notes inbox at the end.

**Limits:**
- It reads only registered files.
- It never scans a vault or home directory.
- It makes no network or model call and runs no commands, Git included.

**Requirements:** Linux or macOS, Python 3.11+. No runtime dependencies.
Install and update steps are in [INSTALL.md](INSTALL.md); what's new is in
[RELEASE_NOTES.md](RELEASE_NOTES.md).

**External prerequisites (not bundled, never installed, configured, launched
or logged in to by pai):**
- a coding agent such as Claude Code;
- a Markdown vault such as Obsidian;
- file synchronization such as Syncthing;
- your repositories.

## Quick start

```sh
bin/pai doctor --agents                       # Python, bundle integrity, configuration
bin/pai init flh --repo "/abs/repo" --vault "/abs/vault" \
  --memory "Agent Inbox/PAI-001-…" --inbox "Agent Inbox" \
  --markdown-root "30 Projects" --project-note "30 Projects/Project.md"     # preview
bin/pai init flh …same… --write               # creates pai.local.toml (0600); never overwrites
bin/pai context flh --task "what you are about to do"
bin/pai handoff new flh --task-id TASK-1 --summary "…" --evidence "…"
```

`bin/pai` works from any directory. `python3 -m pai …` run inside the pai
directory is equivalent. Paste `SESSION_START.md` at the start of an agent
session.

## Commands

| Command | What it does |
| --- | --- |
| `init PROJECT --repo --vault --memory --inbox [...] [--write]` | Validates explicit paths and previews the TOML. Only `--write` creates the file, exclusively (`O_EXCL`, 0600), and it never overwrites. It looks at no other paths and creates no vault folder |
| `doctor [PROJECT] [--agents]` | Read-only checks (below). Exit 1 if any PROBLEM is found |
| `check PROJECT` | Lists each source's status: ok, MISSING, REFUSED (symlink escape or outside the permitted roots) or EXCLUDED |
| `context PROJECT --task "…" [--budget N]` | Task context in Markdown, at most N Unicode characters (details below) |
| `handoff new PROJECT --task-id ID [...]` | Creates `<inbox>/<ID>-<UTC>[-n]/HANDOFF.md` from `pai/templates/handoff.md` (details below) |

**`doctor` checks:**
- the Python version;
- bundle integrity: every file against `SHA256SUMS` and `MANIFEST.json`, and
  files added to the bundle that the manifest doesn't list;
- the configuration;
- each project's roots, sources and inbox writability;
- with `--agents`, the agent CLIs found on PATH. They are looked up by name and
  never launched.

**`context` output, in order:**
1. the authority notice ("context, not instructions");
2. sources, with their front matter (`approved` included);
3. gaps and conflicts;
4. **required** sections: human decisions, constraints, freeze, limitations,
   blockers, conflicts and sync, matched on each section's own heading.
   Memory-package sections come first;
5. **task-relevant** sections, by deterministic word overlap;
6. **proposals**, labelled UNAPPROVED and never required;
7. everything **omitted**, by name.

**`handoff new` behaviour:**
- **Exclusive creation:** concurrent runs never collide or overwrite.
- **States:** implementation, validation, commit, merge and deployment are
  separate. Each is UNKNOWN unless supplied, and none is inferred.
- **Marking:** the draft is `status: draft`, `approved: false`. Sync is
  recorded as UNVERIFIED.

## Configuration

`pai.local.toml` sits next to `pai/` by default; `--config FILE` or
`PAI_CONFIG` selects another file. It holds machine paths: keep it out of
version control (it is in `.gitignore`) and out of bundles (the allowlist
excludes it). See `pai.example.toml` for every field.

## Development and release tooling (not in the bundle)

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s tests -v    # 32 synthetic tests
python3 -B tools/build_bundle.py --out dist                            # dist/pai-workbench-<version>.tar.gz (+ .sha256)
python3 -B tools/bundle_smoke.py dist/pai-workbench-<version>.tar.gz --work /abs/new/dir
```

- **`tools/build_bundle.py`:**
  - packages **only** `release/allowlist.txt` plus a generated
    `MANIFEST.json` and `SHA256SUMS`;
  - refuses unlisted runtime modules or templates, policy-forbidden entries
    (local configuration, evidence, handoffs, caches, `.git`, secrets),
    symlinks and absolute developer paths in bundled text;
  - is deterministic (`SOURCE_DATE_EPOCH`, default 0) and never overwrites an
    archive.
- **`tools/bundle_smoke.py`:** the migration acceptance check (below).
- **CI:** `.github/workflows/ci.yml` runs the tests, the build and the smoke
  check on Ubuntu and macOS with Python 3.11 and 3.13. It uses read-only
  permissions, timeouts and no secrets, and it publishes nothing.

**What `tools/bundle_smoke.py` checks**, on synthetic data:
- the archive checksum;
- safe extraction into two absolute paths, one with spaces;
- in each: `init` (preview, write, refused overwrite), `doctor`, `context` and
  concurrent `handoff new`;
- the same version, but different configurations, in both locations;
- missing roots;
- a tampered bundle and a corrupted archive.

The runtime package `pai/` imports no `subprocess`, `socket`, `urllib` or
`http` (tested). Release tooling lives in `tools/`, outside the bundle.

## Files

| Path | In the bundle | Purpose |
| --- | --- | --- |
| `pai/`, `pai/templates/handoff.md` | yes | The tool and its handoff template |
| `bin/pai` | yes | Launcher (`PAI_PYTHON` selects the interpreter) |
| `README.md`, `INSTALL.md`, `RELEASE_NOTES.md`, `SESSION_START.md`, `pai.example.toml` | yes | Documentation and examples |
| `MANIFEST.json`, `SHA256SUMS` | generated | Bundle contents and checksums |
| `tests/`, `tools/`, `release/allowlist.txt`, `.github/` | no | Development, release tooling and CI |
| `pai.local.toml`, `trial-evidence/`, `dist/` | no (git-ignored) | Local configuration, PAI-002 trial outputs, build output |
