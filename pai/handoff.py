"""Draft handoff creation under the project's inbox.

- A new directory `<TASK-ID>-<UTCSTAMP>[-N]` is created with os.mkdir, which is
  atomic and fails if the path exists. Concurrent runs therefore always get
  distinct directories.
- The file is opened with mode 'x', so nothing is ever overwritten.
- States are never inferred. Implementation, validation, commit, merge and
  deployment default to UNKNOWN; the tool runs no Git and checks nothing.
- The draft is marked `status: draft` and `approved: false`. Creating it
  completes nothing and approves nothing.
"""

import os
import re
from datetime import datetime, timezone

from pai import __version__
from pai.config import inside

TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
STATES = ("implementation", "validation", "commit", "merge", "deployment")
UNKNOWN = "UNKNOWN (not supplied; not checked by pai)"


class HandoffError(Exception):
    pass


def _one_line(value, what):
    value = (value or "").strip()
    if "\n" in value or "\r" in value:
        raise HandoffError(f"{what} must be a single line")
    if len(value) > 500:
        raise HandoffError(f"{what} is longer than 500 characters")
    return value


def _create_dir(inbox, task_id, stamp):
    base = f"{task_id}-{stamp}"
    for n in range(0, 1000):
        name = base if n == 0 else f"{base}-{n}"
        path = os.path.join(inbox, name)
        try:
            os.mkdir(path, 0o755)
            return path
        except FileExistsError:
            continue
    raise HandoffError("could not find a free handoff directory name")


def render(project, task_id, created, states, summary, evidence, blockers, next_steps, proposals):
    def bullets(items, empty):
        return "\n".join(f"- {x}" for x in items) if items else f"- {empty}"

    rows = "\n".join(f"| {k.capitalize()} | {states.get(k) or UNKNOWN} |" for k in STATES)
    return f"""---
status: draft
approved: false
task: {task_id}
project: {project.key}
created: {created}
generator: pai {__version__}
---

# {task_id} handoff (DRAFT, not approved)

Draft created by `pai handoff new`. Creating it marks no work complete and
approves no knowledge. Values below are exactly what was supplied; anything
not supplied is UNKNOWN. The tool ran no Git, tests or deployment checks.

## Summary

{summary or "UNKNOWN (not supplied)"}

## State

Each state is separate; none is inferred from another.

| State | Value |
| --- | --- |
{rows}

## Evidence

{bullets(evidence, "None supplied.")}

## Blockers

{bullets(blockers, "None supplied (not the same as none existing).")}

## Next steps

{bullets(next_steps, "None supplied.")}

## Knowledge-update proposals (UNAPPROVED)

These are proposals only. They change no formal note and constrain nothing
until the human approves them.

{bullets(proposals, "None supplied.")}

## Sync

Created locally at {created}. Cross-device synchronization is **UNVERIFIED**
until the human confirms the file on another device.
"""


def create(project, task_id, states=None, summary="", evidence=(), blockers=(), next_steps=(), proposals=(), now=None):
    if not TASK_ID.match(task_id or ""):
        raise HandoffError("task id must be 1-40 characters of [A-Za-z0-9._-] starting with a letter or digit")
    states = {k: _one_line(v, k) for k, v in (states or {}).items() if v}
    unknown_keys = set(states) - set(STATES)
    if unknown_keys:
        raise HandoffError(f"unknown state fields: {', '.join(sorted(unknown_keys))}")
    summary = _one_line(summary, "summary")
    lists = [[_one_line(x, "list item") for x in items] for items in (evidence, blockers, next_steps, proposals)]
    inbox = os.path.realpath(project.inbox)
    if not inside(inbox, project.vault) or not os.path.isdir(inbox):
        raise HandoffError("inbox is not a directory inside the vault")
    moment = now or datetime.now(timezone.utc)
    created = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _create_dir(inbox, task_id, moment.strftime("%Y%m%dT%H%M%SZ"))
    target = os.path.join(path, "HANDOFF.md")
    with open(target, "x", encoding="utf-8") as f:
        f.write(render(project, task_id, created, states, summary, *lists))
    return target
