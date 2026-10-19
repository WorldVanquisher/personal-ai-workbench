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
import string
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


TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "handoff.md")


def render(project, task_id, created, states, summary, evidence, blockers, next_steps, proposals):
    """Fill the shipped template. Only the template's own $placeholders are substituted."""
    def bullets(items, empty):
        return "\n".join(f"- {x}" for x in items) if items else f"- {empty}"

    with open(TEMPLATE, encoding="utf-8") as f:
        template = string.Template(f.read())
    rows = "\n".join(f"| {k.capitalize()} | {states.get(k) or UNKNOWN} |" for k in STATES)
    return template.substitute(
        task_id=task_id, project=project.key, created=created, version=__version__,
        summary=summary or "UNKNOWN (not supplied)", state_rows=rows,
        evidence=bullets(evidence, "None supplied."),
        blockers=bullets(blockers, "None supplied (not the same as none existing)."),
        next_steps=bullets(next_steps, "None supplied."),
        proposals=bullets(proposals, "None supplied."))


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


# Discovery is deliberately shallow and fail-closed on an oversized Inbox.
MAX_ENTRIES = 256
MAX_FILE_BYTES = 65536
MAX_HANDOFFS = 20
PROJECT_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")


def validate_count(count):
    if type(count) is not int or not 0 <= count <= MAX_HANDOFFS:
        raise HandoffError(f"--handoffs must be between 0 and {MAX_HANDOFFS}")


def _metadata(text):
    """Validate the existing simple front matter, without YAML coercion."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing front matter")
    meta = {}
    for line in lines[1:]:
        if line == "---":
            break
        key, sep, value = line.partition(":")
        if not sep or not key or key != key.strip() or key in meta or not value.strip():
            raise ValueError("invalid or duplicate metadata field")
        meta[key] = value.strip()
    else:
        raise ValueError("unterminated front matter")
    if not PROJECT_KEY.fullmatch(meta.get("project", "")):
        raise ValueError("invalid project metadata")
    if not TASK_ID.fullmatch(meta.get("task", "")):
        raise ValueError("invalid task metadata")
    if meta.get("status") not in ("draft", "approved", "rejected", "superseded"):
        raise ValueError("invalid status metadata")
    if meta.get("approved") not in ("true", "false"):
        raise ValueError("invalid approved metadata")
    created = meta.get("created", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created):
        raise ValueError("invalid created metadata (expected UTC seconds)")
    datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
    return meta


def discover(project):
    """Return (reports, diagnostics). Never follow a directory or file symlink.

    Descriptor-relative opens also prevent check/open races and nested traversal.
    On entry overflow no subset is returned: filesystem enumeration order must
    never determine which reports win selection.
    """
    import stat
    from pai.config import excluded

    reports, issues = [], []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        # Open each component below the trusted, resolved vault without following links.
        fd = os.open(project.vault, flags)
        try:
            rel = os.path.relpath(project.inbox, project.vault)
            if rel == "." or rel.startswith(".." + os.sep) or rel == "..":
                raise OSError("inbox escapes vault")
            for part in rel.split(os.sep):
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            with os.scandir(fd) as entries:
                names = []
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > MAX_ENTRIES:
                        return [], [f"LIMIT: Inbox exceeds {MAX_ENTRIES} entries; discovery refused (no partial ordering)"]
            for name in sorted(names):
                label = repr(name + "/HANDOFF.md")
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        issues.append(f"REFUSED {label}: symlink entry")
                        continue
                    if not stat.S_ISDIR(info.st_mode):
                        continue
                    if excluded(name, project.exclude):
                        issues.append(f"EXCLUDED {label}: exclusion pattern")
                        continue
                    task_fd = os.open(name, flags, dir_fd=fd)
                    try:
                        file_fd = os.open("HANDOFF.md", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=task_fd)
                        with os.fdopen(file_fd, "rb") as stream:
                            info = os.fstat(stream.fileno())
                            if not stat.S_ISREG(info.st_mode):
                                raise ValueError("HANDOFF.md is not a regular file")
                            raw = stream.read(MAX_FILE_BYTES + 1)
                        if len(raw) > MAX_FILE_BYTES:
                            raise ValueError(f"file exceeds {MAX_FILE_BYTES}-byte limit")
                        text = raw.decode("utf-8")
                        meta = _metadata(text)
                        if meta["project"] == project.key:
                            reports.append({"label": name + "/HANDOFF.md", "meta": meta, "text": text})
                    finally:
                        os.close(task_fd)
                except (OSError, ValueError) as exc:
                    # Do not leak absolute paths from OS errors into generated context.
                    reason = exc.strerror if isinstance(exc, OSError) else str(exc)
                    issues.append(f"UNAVAILABLE/MALFORMED {label}: {reason}")
        finally:
            os.close(fd)
    except OSError as exc:
        issues.append(f"UNAVAILABLE Inbox: {exc.strerror or 'invalid inbox path'}")
    reports.sort(key=lambda r: r["label"])
    reports.sort(key=lambda r: r["meta"]["created"], reverse=True)
    return reports, issues


def listing(project):
    reports, issues = discover(project)
    lines = [f"# Handoffs: {project.key}",
             f"Limits: {MAX_ENTRIES} Inbox entries; {MAX_FILE_BYTES} bytes/file; context count 0..{MAX_HANDOFFS}.",
             "Order: created UTC descending, relative path ascending. Metadata and claims are reported, not verified."]
    for report in reports:
        m = report["meta"]
        lines.append(f"- {report['label']!r}: task={m['task']}; created={m['created']}; status={m['status']}; approved={m['approved']}")
    lines.append(f"{len(reports)} matching handoffs.")
    lines.extend(issues)
    return "\n".join(lines) + "\n", bool(issues)
