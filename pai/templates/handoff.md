---
status: draft
approved: false
task: ${task_id}
project: ${project}
created: ${created}
generator: pai ${version}
---

# ${task_id} handoff (DRAFT, not approved)

Draft created by `pai handoff new`. Creating it marks no work complete and
approves no knowledge. Values below are exactly what was supplied; anything
not supplied is UNKNOWN. The tool ran no Git, tests or deployment checks.

## Summary

${summary}

## State

Each state is separate; none is inferred from another.

| State | Value |
| --- | --- |
${state_rows}

## Evidence

${evidence}

## Blockers

${blockers}

## Next steps

${next_steps}

## Knowledge-update proposals (UNAPPROVED)

These are proposals only. They change no formal note and constrain nothing
until the human approves them.

${proposals}

## Sync

Created locally at ${created}. Cross-device synchronization is **UNVERIFIED**
until the human confirms the file on another device.
