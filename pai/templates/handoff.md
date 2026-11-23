---
format: pai-handoff/2
status: draft
approved: false
project: ${project}
round: ${round}
lane: ${lane}
checkpoint: ${checkpoint}
task: ${task_id}
base_sha: ${base_sha}
head_sha: ${head_sha}
queue: ${queue}
queue_sha256: ${queue_sha256}
created: ${created}
generator: pai ${version}
---

# ${task_id} handoff (DRAFT, not approved)

Draft created by `pai handoff new` (format pai-handoff/2, docs/HANDOFF-V2.md). Creating it
marks no work complete and approves no knowledge. Values below are exactly what was supplied;
anything not supplied is UNKNOWN. The tool ran no Git, tests or deployment checks; the Items
table, when present, is the queue log's record, not a check of the work.

## Summary

${summary}

## State

Each state is separate; none is inferred from another.

| State | Value | Source |
| --- | --- | --- |
${state_rows}

## Items

${items}

## Evidence

${evidence}

## Decisions within authority

What was chosen, why, and how to reverse it.

${decisions}

## Decisions for the owner

Above the worker's authority: the options, their trade-offs and a recommendation. Nothing here
was done; the work that depends on it waits for the owner.

${needs_owner}

## Blockers

${blockers}

## Next

${next_steps}

## Knowledge-update proposals (UNAPPROVED)

These are proposals only. They change no formal note and constrain nothing
until the human approves them.

${proposals}

## Sync

Created locally at ${created}. Cross-device synchronization is **UNVERIFIED**
until the human confirms the file on another device.
