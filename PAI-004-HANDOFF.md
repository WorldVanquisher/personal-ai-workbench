---
status: draft
approved: false
task: PAI-004
project: personal-ai-workbench
created: 2026-10-19T19:55:00Z
---

# PAI-004 self-handoff (draft, unapproved)

Implemented `pai handoff list PROJECT` and opt-in context `--handoffs N`
(default 0). Discovery validates the existing simple metadata, matches exact
project keys, reads only direct Inbox folders/HANDOFF.md, refuses symlinks
with descriptor-relative opens, and bounds entries (256), bytes/file (65536)
and requested reports (20). Ordering is created UTC descending, path ascending.
Reports remain whole, with approval metadata, independent states and proposals
preserved. Human constraints precede reports; count/budget omissions and
source problems are explicit. Newer claims confer no authority.

| State | Report |
| --- | --- |
| Implementation | Implemented locally; human review pending |
| Validation | 46 tests passed, 0 failures/errors/skips, in 2.565s; includes 11 PAI-004 tests |
| Commit | UNKNOWN; no Git commands executed |
| Merge | UNKNOWN; not attempted |
| Deployment | UNKNOWN; not attempted |

Validation command:
`python3 -B -W error::ResourceWarning -m unittest discover -s tests -v`
All fixtures are temporary synthetic data. The existing suite's bundle tests
ran in temporary directories; no separate release acceptance was performed.
No dependencies, network/model calls, subagents, scheduling or deployment.
Released assets, actual configuration and external project/Vault files were
not modified. No release created.

Changed files: pai/cli.py, pai/config.py, pai/context.py, pai/handoff.py,
tests/test_handoff_context.py, README.md, SESSION_START.md, PAI-004-HANDOFF.md.

Limits: all direct folders without HANDOFF.md are reported, including memory
folders. Listing exits 1 on discovery problems. Long diagnostic/selection
lists show counts and refer to the ordered listing. A budget too small for
source diagnostics, omission reserve and ledger fails explicitly. Required
sections retain existing clipping/omission behavior. Claims are not verified
and semantic contradictions are not automatically resolved.

Next: human review; staging/commit remain human actions. No proposals approved.
