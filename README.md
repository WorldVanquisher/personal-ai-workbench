# pai: personal AI workflow, version 0.2.0

A small, standard-library Python tool for agent sessions on a project:
- it gives the agent the **context** for a task (rules, human decisions,
  status and limitations) from files you register;
- it creates a **draft handoff** in your notes inbox at the end.

**Limits:**
- It reads registered files and, when requested, direct Inbox handoffs.
- It never scans a vault or home directory.
- It makes no network or model call and runs no commands, Git included.

**Requirements:** Windows, macOS or Linux with Python 3.11+. No runtime
dependencies, no administrator rights. Install, upgrade and per-device setup
are in [INSTALL.md](INSTALL.md); what's new and what has been tested is in
[RELEASE_NOTES.md](RELEASE_NOTES.md).

**License:** MIT, see [LICENSE](LICENSE).

**External prerequisites (not bundled, never installed, configured, launched
or logged in to by pai):**
- a coding agent such as Claude Code;
- a Markdown vault such as Obsidian;
- file synchronization such as Syncthing;
- your repositories.

## Quick start

Linux and macOS use `bin/pai`; Windows uses `bin\pai.cmd` from PowerShell or
cmd.exe (see INSTALL.md for the PowerShell form). `python3 bin/pai.py` or
`py -3 bin\pai.py` works everywhere.

```sh
bin/pai doctor --agents                       # Python, bundle integrity, configuration
bin/pai init flh --repo "/abs/repo" --vault "/abs/vault" \
  --memory "Agent Inbox/PAI-001-…" --inbox "Agent Inbox" \
  --markdown-root "30 Projects" --project-note "30 Projects/Project.md"     # preview
bin/pai init flh …same… --write               # creates the configuration; never overwrites
bin/pai handoff list flh                     # subsequent task reports and discovery problems
bin/pai context flh --task "what you are about to do" --handoffs 3
bin/pai handoff new flh --task-id TASK-1 --summary "…" --evidence "…"
```

The launchers work from any directory and never import code from it. Paste
`SESSION_START.md` at the start of an agent session.

## Commands

| Command | What it does |
| --- | --- |
| `init PROJECT --repo --vault --memory --inbox [...] [--write]` | Validates explicit paths and previews the TOML. Only `--write` creates the file, exclusively (`O_EXCL`), and it never overwrites. POSIX: mode 0600; Windows: the folder's ACLs apply. It looks at no other paths and creates no vault folder |
| `doctor [PROJECT] [--agents]` | Read-only checks (below). Exit 1 if any PROBLEM is found |
| `check PROJECT` | Lists each source's status: ok, MISSING, REFUSED (symlink escape or outside the permitted roots) or EXCLUDED |
| `context PROJECT --task "…" [--budget N] [--handoffs N]` | Task context in Markdown, at most N Unicode characters (details below) |
| `handoff list PROJECT` | Lists matching reports and discovery problems; exit 1 when any source is malformed, unavailable, excluded or refused |
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

**Subsequent handoffs:** `pai handoff list PROJECT` discovers only direct task
folders under the configured Inbox, reading only their `HANDOFF.md`. Projects
match validated front matter, never folder names. The existing simple metadata
requires `project`, `task`, `created` (UTC `YYYY-MM-DDTHH:MM:SSZ`), `status`
(`draft`, `approved`, `rejected` or `superseded`) and `approved` (`true` or
`false`). Duplicate keys, invalid UTF-8 and malformed fields are reported.
Directory and file symlinks are refused, including links within the Vault; on
Windows, so are junctions and other reparse points. Exclusion patterns still
apply. No recursive traversal occurs.

`context PROJECT --task "review deployment" --handoffs 3` selects up to three
reports by created time descending, then Inbox-relative path ascending. The
default is **0**, preserving the original context behavior without discovery.
Limits are **256 Inbox entries**, **65,536 bytes per file**, and **20 requested
reports**. An Inbox over the entry limit is refused as a whole. Discovery
problems appear in context; diagnostics that do not fit are counted with a
pointer to `handoff list` for full details. Missing `HANDOFF.md` files are
reported, including in Inbox folders used for memory packages.

Reports are included whole after required constraints and before lexical
sections, within the same character budget. Oversized reports are omitted,
with budget and count omissions reported in a selection ledger; long ledgers
show counts and refer to the ordered listing. If source diagnostics, the omission reserve and the
ledger cannot fit, context fails explicitly: raise `--budget`. Required
sections retain priority; their existing truncation and omission notices still
apply. Reports preserve approval metadata, separate implementation, validation,
commit, merge and deployment states, and unapproved proposals. Metadata is a
claim, not verified approval. Newer reports cannot override human decisions,
approve proposals or authorize commands. Conflicting reports remain visible;
these lexical checks do not resolve contradictions.

**`handoff new` behaviour:**
- **Exclusive creation:** concurrent runs never collide or overwrite.
- **States:** implementation, validation, commit, merge and deployment are
  separate. Each is UNKNOWN unless supplied, and none is inferred.
- **Marking:** the draft is `status: draft`, `approved: false`. Sync is
  recorded as UNVERIFIED.

## Configuration

Lookup order: `--config FILE`, then `PAI_CONFIG`, then `pai.local.toml` next
to `pai/`. Keep it outside the bundle (INSTALL.md) so upgrades never touch
it, and give each device its own, with its own paths. It holds machine paths:
keep it out of version control (it is in `.gitignore`) and out of bundles (the
allowlist excludes it). See `pai.example.toml` for every field.

## Development and release tooling (not in the bundle)

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s tests -v    # synthetic tests
```

**Release build** (version from `pai/__init__.py`; output into a new
directory under `dist/`, which is git-ignored and never overwritten):

```sh
SOURCE_DATE_EPOCH=0 python3 -B tools/build_bundle.py --out dist/0.2.0
#   dist/0.2.0/pai-workbench-0.2.0.tar.gz  (+ .sha256)   Linux, macOS
#   dist/0.2.0/pai-workbench-0.2.0.zip     (+ .sha256)   Windows
python3 -B tools/bundle_smoke.py dist/0.2.0/pai-workbench-0.2.0.tar.gz \
  dist/0.2.0/pai-workbench-0.2.0.zip --work "/tmp/pai-smoke-0.2.0"
```

Publishing (tag, GitHub release, asset upload) is a human action; previously
published assets, such as 0.1.0's, are never rebuilt or replaced.

- **`tools/build_bundle.py`:**
  - packages **only** `release/allowlist.txt` plus a generated
    `MANIFEST.json` and `SHA256SUMS`, into a tar.gz and a ZIP with identical
    contents;
  - refuses unlisted runtime modules or templates, policy-forbidden entries
    (local configuration, evidence, handoffs, caches, `.git`, secrets),
    symlinks, absolute developer paths in bundled text, and wrong line endings
    (`.cmd` must be CRLF, everything else LF; `.gitattributes` keeps checkouts so);
  - is deterministic (`SOURCE_DATE_EPOCH`, default 0; same Python/zlib) and
    writes nothing if any of the four artifacts already exists.
- **`tools/bundle_smoke.py`:** the migration acceptance check (below).
- **CI:** `.github/workflows/ci.yml` runs the tests, both builds, a rebuild
  comparison and the smoke check on Windows, macOS and Ubuntu with Python 3.11
  and 3.13, plus a real Python 3.10 refusal, cmd.exe and PowerShell launches;
  and the tests, build and smoke in Debian, Ubuntu, Fedora and Alpine
  containers (`tools/ci_userspace.sh`). It uses read-only permissions,
  timeouts and no secrets, and it publishes nothing.

**What `tools/bundle_smoke.py` checks**, on synthetic data:
- each archive's checksum, and that the tar.gz and ZIP hold the same bytes;
- safe extraction (no absolute, parent, drive or backslash paths, no links)
  into two absolute paths, one with spaces and non-ASCII characters;
- in each, through the platform launcher, from an unrelated directory holding
  a decoy `pai/` package: `init` (preview, write, refused overwrite), `doctor`,
  `context` (with a CRLF note), concurrent `handoff new` and `handoff list`;
- argument forwarding (spaces, quotes, Unicode, metacharacters, an empty
  argument), `bin/pai.py`, a missing `PAI_PYTHON`, no Python on PATH and a
  simulated Python 3.10;
- the same version, but different configurations, in both locations;
- missing roots;
- a tampered bundle and a corrupted archive.

The runtime package `pai/` imports no `subprocess`, `socket`, `urllib` or
`http` (tested). Release tooling lives in `tools/`, outside the bundle.

## Files

| Path | In the bundle | Purpose |
| --- | --- | --- |
| `pai/`, `pai/templates/handoff.md` | yes | The tool and its handoff template |
| `bin/pai`, `bin/pai.cmd`, `bin/pai.py` | yes | Launchers: POSIX, Windows, any platform (`PAI_PYTHON` selects the interpreter) |
| `LICENSE`, `README.md`, `INSTALL.md`, `RELEASE_NOTES.md`, `SESSION_START.md`, `pai.example.toml` | yes | License, documentation and examples |
| `MANIFEST.json`, `SHA256SUMS` | generated | Bundle contents and checksums |
| `tests/`, `tools/`, `release/allowlist.txt`, `.github/`, `.gitattributes` | no | Development, release tooling and CI |
| `pai.local.toml`, `trial-evidence/`, `dist/` | no (git-ignored) | Local configuration, PAI-002 trial outputs, build output |
