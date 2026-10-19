---
status: draft
approved: false
task: PAI-005
project: personal-ai-workbench
created: 2026-10-19T20:28:48Z
---

# PAI-005 self-handoff (draft, unapproved)

Portable 0.2.0 for Windows, macOS and Linux, under the MIT License. Added
`bin/pai.cmd` (py -3 → python.exe → python3.exe on PATH only;
`PAI_PYTHON`), `bin/pai.py` (script entry point; both launchers use it, so the
caller's working directory is never on the import path; 0.1.0's `-m pai`
imported a `pai/` package from it). Containment uses path components. Windows
drive, rooted, drive-relative and data-stream paths are refused. Handoff
discovery has a path-based backend for platforms without dir_fd/O_NOFOLLOW;
it refuses reparse points and identity swaps. CRLF input is handled and LF is
written. Permission messages are platform-honest. The builder produces a tar.gz
and a ZIP with identical bytes, `.sha256` sidecars, line-ending checks and
all-or-nothing overwrite refusal. PAI-004 behaviour is preserved and its
tests also run against the new backend.

| State | Report |
| --- | --- |
| Implementation | Implemented locally; human review pending |
| Validation | Host (Arch, Python 3.14): 80 tests OK, 6 Windows-only skipped; smoke 42/42. Debian 13 container (Python 3.11.14, offline, uid 65534): 80 OK, 7 skipped; smoke 42/42; identical archive hashes |
| CI | Configured (Windows/macOS/Ubuntu × 3.11/3.13, real 3.10 refusal, cmd.exe, PowerShell, junctions; Debian/Ubuntu/Fedora/Alpine containers); NOT run; workflow YAML not machine-validated locally |
| Commit | UNKNOWN; no Git commands executed |
| Merge | UNKNOWN; not attempted |
| Deployment / release | Not attempted; no tag or publication. dist/0.1.0 assets unchanged |

Validation commands:
`python3 -B -W error::ResourceWarning -m unittest discover -s tests -v`;
`SOURCE_DATE_EPOCH=0 python3 -B tools/build_bundle.py --out <scratch>/rel/0.2.0`;
`sha256sum -c *.sha256`; `python3 -B tools/bundle_smoke.py <tar.gz> <zip> --work <scratch>`;
`docker run --rm --network none -v "$PWD:/src:ro" -e SETUP=true python:3.11-slim sh /src/tools/ci_userspace.sh`.
All fixtures are temporary synthetic data. No dependencies installed or
images pulled, no network/model calls, no subagents.

Limits: Windows, macOS, Fedora and Alpine behaviour is unobserved until CI
runs. Windows-only tests (junctions, drive paths, cmd.exe, PowerShell) never
ran locally. cmd.exe re-parses `pai.cmd` arguments (`%`, `"`, unquoted `&|<>^`),
which is documented with `bin/pai.py` as the exact-argument path. The Windows
discovery backend has a residual swap-and-restore race window, which is
documented. Files without file identity (st_ino 0) are refused. Network shares
and FAT/exFAT are untested. Determinism assumes the same zlib.

Next: human review; run CI; update RELEASE_NOTES "Tested environments" with
the observed CI results before publishing 0.2.0. No proposals approved.
