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

import hashlib
import json
import os
import re
import stat
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
SOURCES = ("real run", "synthetic", "fake agent", "mock endpoint", "JEV", "self-report")
SOURCE_RE = re.compile(r"^(?:real run|synthetic|fake agent|mock endpoint|JEV|self-report|reported-by-[A-Za-z0-9._-]{1,32})"
                       r"(?:\s*\(.{0,120}\))?$")
HEX40, HEX64 = re.compile(r"[0-9a-f]{40}\Z"), re.compile(r"[0-9a-f]{64}\Z")
TOTALS_ORDER = ("done", "prototype", "blocked", "owner-only", "todo")


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def items_from_queue(queue):
    """(Items section Markdown, sha256 of the queue log) from a `pai queue` file: one row per item
    with its last recorded state, and the Totals line in the order checkpoint.md uses."""
    from pai import workqueue
    try:
        doc = workqueue.load(queue)
        evs, _problems = workqueue.events(queue)
    except workqueue.QueueError as e:
        raise HandoffError(str(e))
    st = workqueue.state(doc, evs)
    table = workqueue.render_table(doc, st)  # PRM-D3: exactly what `pai queue table` prints
    try:
        with open(workqueue.log_path(queue), "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        sha = hashlib.sha256(b"").hexdigest()
    return table, sha


def render(project, task_id, created, states, summary, evidence, blockers, next_steps, proposals, decisions=(),
           needs_owner=(), where=None, sources=None):
    """Fill the shipped template (pai-handoff/2). Only the template's own $placeholders are substituted.
    `where`: round, lane, checkpoint, base_sha, head_sha, queue, queue_sha256 and items (Markdown);
    `sources`: the Source label of each supplied state (default self-report: pai checked none of them)."""
    def bullets(items, empty):
        return "\n".join(f"- {x}" for x in items) if items else f"- {empty}"

    w = dict(where or {})
    sources = sources or {}
    with open(TEMPLATE, encoding="utf-8") as f:
        template = string.Template(f.read())
    rows = "\n".join(f"| {k.capitalize()} | {_cell(states.get(k) or UNKNOWN)} | "
                     f"{(sources.get(k) or 'self-report') if states.get(k) else '-'} |" for k in STATES)
    return template.substitute(
        task_id=task_id, project=project.key, created=created, version=__version__,
        round=w.get("round") or "UNKNOWN", lane=w.get("lane") or "UNKNOWN", checkpoint=w.get("checkpoint") or "UNKNOWN",
        base_sha=w.get("base_sha") or "UNKNOWN", head_sha=w.get("head_sha") or "UNKNOWN",
        queue=w.get("queue") or "none", queue_sha256=w.get("queue_sha256") or "none",
        items=w.get("items") or "- No queue given (`pai handoff new --queue FILE`): the items are UNKNOWN.",
        summary=summary or "UNKNOWN (not supplied)", state_rows=rows,
        evidence=bullets(evidence, "None supplied."),
        blockers=bullets(blockers, "None supplied (not the same as none existing)."),
        next_steps=bullets(next_steps, "None supplied."),
        proposals=bullets(proposals, "None supplied."),
        decisions=bullets(decisions, "None recorded."),
        needs_owner=bullets(needs_owner, "None recorded."))


def create(project, task_id, states=None, summary="", evidence=(), blockers=(), next_steps=(), proposals=(), now=None,
           decisions=(), needs_owner=(), where=None, sources=None):
    """Write a new draft. With `where` round and lane, it goes to <inbox>/<round>/<lane>/<ID>-<UTC>/
    (the version 2 layout; both folders are created when missing), else to <inbox>/<ID>-<UTC>/."""
    if not TASK_ID.match(task_id or ""):
        raise HandoffError("task id must be 1-40 characters of [A-Za-z0-9._-] starting with a letter or digit")
    states = {k: _one_line(v, k) for k, v in (states or {}).items() if v}
    unknown_keys = set(states) - set(STATES)
    if unknown_keys:
        raise HandoffError(f"unknown state fields: {', '.join(sorted(unknown_keys))}")
    sources = dict(sources or {})
    for k, v in sources.items():
        if k not in STATES or not SOURCE_RE.match(v or ""):
            raise HandoffError(f"--source {k}={v!r}: the state must be one of {', '.join(STATES)} and the source one of "
                               f"{', '.join(SOURCES)} or reported-by-<lane>")
    summary = _one_line(summary, "summary")
    lists = [[_one_line(x, "list item") for x in items] for items in (evidence, blockers, next_steps, proposals, decisions,
                                                                      needs_owner)]
    w = dict(where or {})
    for k in ("round", "lane", "checkpoint"):
        if w.get(k) and not NAME.fullmatch(w[k]):
            raise HandoffError(f"--{k} must be 1-64 characters of [A-Za-z0-9._-] starting with a letter or digit")
    for k in ("base_sha", "head_sha"):
        if w.get(k) and not HEX40.match(w[k]):
            raise HandoffError(f"--{k.replace('_', '-')} must be a full 40-character commit sha")
    if w.get("queue"):
        w["items"], w["queue_sha256"] = items_from_queue(w["queue"])
        w["queue"] = os.path.abspath(w["queue"])
    inbox = os.path.realpath(project.inbox)
    if not inside(inbox, project.vault) or not os.path.isdir(inbox):
        raise HandoffError("inbox is not a directory inside the vault")
    if bool(w.get("round")) != bool(w.get("lane")):
        raise HandoffError("--round and --lane go together (the folder is <inbox>/<round>/<lane>/)")
    parent = inbox
    if w.get("round"):
        for part in (w["round"], w["lane"]):
            parent = os.path.join(parent, part)
            if os.path.islink(parent):
                raise HandoffError(f"{os.path.relpath(parent, inbox)} in the inbox is a link; pai does not write through it")
            os.makedirs(parent, 0o755, exist_ok=True)
    moment = now or datetime.now(timezone.utc)
    created = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _create_dir(parent, task_id, moment.strftime("%Y%m%dT%H%M%SZ"))
    target = os.path.join(path, "HANDOFF.md")
    # LF line endings on every platform, so a report synchronized between devices is byte-identical.
    with open(target, "x", encoding="utf-8", newline="\n") as f:
        f.write(render(project, task_id, created, states, summary, *lists, where=w, sources=sources))
    return target


# Discovery is deliberately shallow and fail-closed on an oversized Inbox.
MAX_ENTRIES = 256
MAX_FILE_BYTES = 65536
MAX_HANDOFFS = 20
PROJECT_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")


def validate_count(count):
    if type(count) is not int or not 0 <= count <= MAX_HANDOFFS:
        raise HandoffError(f"--handoffs must be between 0 and {MAX_HANDOFFS}")


V2_FORMAT = "pai-handoff/2"
V2_STATUS = ("draft", "sealed", "approved", "rejected", "superseded", "continued", "published")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")  # a round, lane or checkpoint folder name
MAX_DEPTH = 3  # <inbox>/<round>/<lane>/<checkpoint>/HANDOFF.md
MAX_TOTAL = 4096  # folders looked at in one discovery, all levels together
SIDE_FILES = ("SEAL.json", "DECISION.json")


def _front_matter(text):
    """{key: value} of the simple front matter (no YAML coercion); ValueError when malformed."""
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
    return meta


def _metadata(text):
    """Validate the front matter of a version 1 or version 2 (`format: pai-handoff/2`) handoff."""
    meta = _front_matter(text)
    if not PROJECT_KEY.fullmatch(meta.get("project", "")):
        raise ValueError("invalid project metadata")
    if meta.get("approved") not in ("true", "false"):
        raise ValueError("invalid approved metadata")
    if meta.get("format") == V2_FORMAT:
        if meta.get("status") not in V2_STATUS:
            raise ValueError("invalid status metadata")
        for k in ("round", "lane", "checkpoint"):
            if k in meta and meta[k] != "UNKNOWN" and not NAME.fullmatch(meta[k]):
                raise ValueError(f"invalid {k} metadata")
        if "task" in meta and not TASK_ID.fullmatch(meta["task"]):
            raise ValueError("invalid task metadata")
        meta.setdefault("task", "/".join(meta.get(k, "?") for k in ("round", "lane", "checkpoint")))
        meta["version"] = "2"
    elif "format" in meta:
        raise ValueError("unknown handoff format %r" % meta["format"])
    else:
        if not TASK_ID.fullmatch(meta.get("task", "")):
            raise ValueError("invalid task metadata")
        if meta.get("status") not in ("draft", "approved", "rejected", "superseded"):
            raise ValueError("invalid status metadata")
        meta["version"] = "1"
    created = meta.get("created", "")
    if created or meta["version"] == "1":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created):
            raise ValueError("invalid created metadata (expected UTC seconds)")
        datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
    return meta


# Descriptor-relative, no-follow opens (Linux, macOS, BSD). Elsewhere, notably Windows, discovery
# falls back to _discover_by_path, which refuses symlinks, junctions and other reparse points.
DIR_FD = (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
          and {os.open, os.stat} <= os.supports_dir_fd and os.scandir in os.supports_fd)
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def discover(project):
    """Return (reports, diagnostics). Never follow a directory or file symlink.

    Descriptor-relative opens also prevent check/open races and nested traversal.
    On entry overflow no subset is returned: filesystem enumeration order must
    never determine which reports win selection.
    """
    reports, issues, _skipped = discover_all(project)
    return reports, issues


def discover_all(project):
    """(reports, issues, skipped). Version 1 drafts sit at <inbox>/<ID>-<UTC>/HANDOFF.md; version 2
    handoffs at <inbox>/<round>/<lane>/<checkpoint>/HANDOFF.md, so a folder without HANDOFF.md is
    looked into, at most MAX_DEPTH levels down. `skipped` names the top-level folders with no
    handoff anywhere below them (a memory package kept in the Inbox, a notes folder): not a problem."""
    ctx = {"reports": [], "issues": [], "skipped": [], "seen": 0}
    (_discover_by_fd if DIR_FD else _discover_by_path)(project, ctx)
    reports = ctx["reports"]
    reports.sort(key=lambda r: r["label"])
    reports.sort(key=lambda r: r["meta"]["created"], reverse=True)
    return reports, ctx["issues"], sorted(ctx["skipped"])


def layout_warnings(project):
    """Warnings about where the memory package and the Inbox are (R6-COR-016): the memory package
    inside the Inbox makes discovery walk it, and the Inbox inside the memory package lets handoffs
    (unapproved reports) sit next to human decisions."""
    memory, inbox = os.path.realpath(project.memory), os.path.realpath(project.inbox)
    if memory != inbox and inside(memory, inbox):
        return [f"the memory package ({project.memory_rel}) is inside the inbox ({project.inbox_rel}): "
                "handoff discovery will see it as a folder without HANDOFF.md; keep them side by side"]
    if memory != inbox and inside(inbox, memory):
        return [f"the inbox ({project.inbox_rel}) is inside the memory package ({project.memory_rel}): "
                "unapproved handoffs would sit among human decisions; keep them side by side"]
    if memory == inbox:
        return [f"the memory package and the inbox are the same folder ({project.inbox_rel}); keep them apart"]
    return []


def _side(raw):
    """A SEAL.json / DECISION.json object, or None when absent or not a JSON object."""
    if raw is None:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def body_of(text):
    """The text after the front matter (the part a sealed handoff's sealed_sha256 covers)."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 3)
    return text[end + 5:] if end >= 0 else text


def body_sha(text):
    return hashlib.sha256(body_of(text).encode("utf-8")).hexdigest()


def with_front_matter(text, updates):
    """TEXT with front matter keys set (existing keys keep their place; new ones go last). Only
    simple `key: value` lines are written; the body is unchanged."""
    text = text.replace("\r\n", "\n")
    end = text.find("\n---\n", 3)
    if not text.startswith("---\n") or end < 0:
        raise HandoffError("no front matter to update")
    lines, done = text[4:end].split("\n"), set()
    for i, line in enumerate(lines):
        key = line.partition(":")[0]
        if key in updates:
            lines[i] = f"{key}: {updates[key]}"
            done.add(key)
    lines += [f"{k}: {v}" for k, v in updates.items() if k not in done]
    return "---\n" + "\n".join(lines) + text[end:]


def effective(meta, sha, seal, decision, issues, label, body=None):
    """The front matter as the tools last left it. A sealed version 2 handoff is identified by its
    body's sha256 (sealed_sha256; R6-COR-040), so the owner may edit the front matter (approved: true
    in Obsidian) without breaking the seal; a body that changed after sealing is `approval: stale`.
    Approved means a DECISION.json for this body (from `pai handoff approve`, with or without
    --from-vault), never the front matter alone. Seals made before R6-COR-040 (no body_sha256)
    are checked against the whole file's sha256, as they were written."""
    if meta.get("version") != "2" or (seal is None and decision is None):
        return meta  # an unsealed draft is reported as written: claims, not verified
    written_approved = meta.get("approved")
    meta = dict(meta, approved="false")
    if seal is not None:
        new_style = bool(seal.get("body_sha256"))
        ok = (body == seal["body_sha256"]) if new_style else seal.get("handoff_sha256") == sha
        if not ok:
            issues.append(f"MISMATCH {label!r}: SEAL.json was recorded for other bytes (the body changed after "
                          "sealing); approval: stale")
            return dict(meta, approval="stale", status="sealed" if meta["status"] == "draft" else meta["status"])
        meta.update(sealed_sha256=seal.get("body_sha256") or sha, sealed_at=str(seal.get("sealed_at") or ""))
        if meta["status"] in ("draft", "approved", "rejected"):
            meta["status"] = "sealed"
        if not meta.get("created") and seal.get("sealed_at"):
            meta["created"] = str(seal["sealed_at"])
        if written_approved == "true" and decision is None:
            meta["approval"] = "requested in the note; run pai handoff approve --from-vault"
    if decision is not None:
        ok = (decision.get("body_sha256") == body) if decision.get("body_sha256") else decision.get("handoff_sha256") == sha
        if not ok or decision.get("decision") not in ("approved", "rejected"):
            issues.append(f"MISMATCH {label!r}: DECISION.json is not for these bytes; it was ignored")
        else:
            meta = dict(meta, status=decision["decision"], approved="true" if decision["decision"] == "approved" else "false",
                        approval=decision["decision"], decided_by=str(decision.get("by") or ""),
                        decided_at=str(decision.get("at") or ""))
    return meta


def _report(name, raw, project, side=(None, None), mtime=None, issues=None, nested=False):
    """A report dict for raw HANDOFF.md bytes, None for another project (or, below the top level,
    for anything but a version 2 handoff); ValueError if malformed."""
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES}-byte limit")
    text = raw.decode("utf-8").replace("\r\n", "\n")  # a report edited on Windows keeps its content
    try:
        meta = _metadata(text)
    except ValueError:
        if nested:
            return None  # below the top level only version 2 handoffs are read; nothing else is reported
        raise
    if meta["project"] != project.key or (nested and meta.get("version") != "2"):
        return None
    label = name + "/HANDOFF.md"
    meta = effective(meta, hashlib.sha256(raw).hexdigest(), _side(side[0]), _side(side[1]),
                     issues if issues is not None else [], label, body_sha(text))
    if not meta.get("created"):
        meta["created"] = datetime.fromtimestamp(mtime or 0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"label": label, "meta": meta, "text": text, "sha256": hashlib.sha256(raw).hexdigest()}


def _count(ctx, n):
    ctx["seen"] += n
    if ctx["seen"] > MAX_TOTAL:
        raise _Overflow(f"LIMIT: more than {MAX_TOTAL} entries below the Inbox; discovery refused (no partial ordering)")


class _Overflow(Exception):
    pass


def _discover_by_fd(project, ctx):
    from pai.config import excluded

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def read_at(dir_fd, name, limit):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"{name} is not a regular file")
            return stream.read(limit + 1), info.st_mtime

    def optional(dir_fd, name):
        try:
            return read_at(dir_fd, name, MAX_FILE_BYTES)[0]
        except (FileNotFoundError, ValueError):
            return None
        except OSError:
            return None

    def scan(fd, prefix, depth):
        """Reports and problems found below this folder (a count); fills ctx."""
        with os.scandir(fd) as entries:
            names = []
            for entry in entries:
                names.append(entry.name)
                if len(names) > MAX_ENTRIES:
                    if depth == 1:
                        raise _Overflow(f"LIMIT: Inbox exceeds {MAX_ENTRIES} entries; discovery refused (no partial ordering)")
                    ctx["issues"].append(f"LIMIT {prefix!r}: more than {MAX_ENTRIES} entries; not looked into")
                    return 1
        _count(ctx, len(names))
        found = 0
        for name in sorted(names):
            rel = prefix + name
            label = repr(rel + "/HANDOFF.md")
            before = len(ctx["reports"]) + len(ctx["issues"])
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    ctx["issues"].append(f"REFUSED {label}: symlink entry")
                    found += 1
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    continue
                if excluded(name, project.exclude):
                    ctx["issues"].append(f"EXCLUDED {label}: exclusion pattern")
                    found += 1
                    continue
                task_fd = os.open(name, flags, dir_fd=fd)
                try:
                    try:
                        raw, mtime = read_at(task_fd, "HANDOFF.md", MAX_FILE_BYTES)
                    except FileNotFoundError:
                        sub = scan(task_fd, rel + "/", depth + 1) if depth < MAX_DEPTH else 0
                        if not sub and depth == 1:
                            ctx["skipped"].append(name)  # not a handoff folder (a memory package, notes, ...)
                        found += sub
                        continue
                    if depth not in (1, MAX_DEPTH):
                        continue  # a HANDOFF.md between the levels is not a handoff of either layout
                    report = _report(rel, raw, project, (optional(task_fd, SIDE_FILES[0]),
                                                         optional(task_fd, SIDE_FILES[1])), mtime, ctx["issues"],
                                     nested=depth > 1)
                    if report:
                        ctx["reports"].append(report)
                finally:
                    os.close(task_fd)
            except (OSError, ValueError) as exc:
                # Do not leak absolute paths from OS errors into generated context.
                reason = exc.strerror if isinstance(exc, OSError) else str(exc)
                ctx["issues"].append(f"UNAVAILABLE/MALFORMED {label}: {reason}")
            found += len(ctx["reports"]) + len(ctx["issues"]) - before
        return found

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
            scan(fd, "", 1)
        finally:
            os.close(fd)
    except _Overflow as exc:
        ctx.update(reports=[], skipped=[], issues=[str(exc)])
    except OSError as exc:
        ctx["issues"].append(f"UNAVAILABLE Inbox: {exc.strerror or 'invalid inbox path'}")


def _is_link(info):
    """A symlink, or on Windows any reparse point (junction, mount point, cloud placeholder…)."""
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def _identity(info):
    if not info.st_ino:  # some filesystems report no file identity; then a swap cannot be detected
        raise ValueError("file identity unavailable on this filesystem")
    return info.st_dev, info.st_ino


def _discover_by_path(project, ctx):
    """Path-based discovery for platforms without descriptor-relative no-follow opens (Windows).

    Same rules and output as _discover_by_fd. Every Inbox component, task directory and HANDOFF.md
    is checked with lstat and refused if it is a link or reparse point. After opening, the file and
    its task directory must still be the objects checked (device and file id), so a swap between
    check and open is refused. A residual window remains: an attacker able to write inside the
    Inbox could swap a directory and swap it back between those checks.
    """
    from pai.config import excluded

    def optional(task, name):
        path = os.path.join(task, name)
        try:
            info = os.lstat(path)
            if _is_link(info) or not stat.S_ISREG(info.st_mode):
                return None
            with open(path, "rb") as f:
                return f.read(MAX_FILE_BYTES + 1)
        except OSError:
            return None

    def scan(folder, prefix, depth):
        names = []
        with os.scandir(folder) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > MAX_ENTRIES:
                    if depth == 1:
                        raise _Overflow(f"LIMIT: Inbox exceeds {MAX_ENTRIES} entries; discovery refused (no partial ordering)")
                    ctx["issues"].append(f"LIMIT {prefix!r}: more than {MAX_ENTRIES} entries; not looked into")
                    return 1
        _count(ctx, len(names))
        found = 0
        for name in sorted(names):
            rel = prefix + name
            label = repr(rel + "/HANDOFF.md")
            task = os.path.join(folder, name)
            before = len(ctx["reports"]) + len(ctx["issues"])
            try:
                info = os.lstat(task)
                if _is_link(info):
                    ctx["issues"].append(f"REFUSED {label}: symlink entry")
                    found += 1
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    continue
                if excluded(name, project.exclude):
                    ctx["issues"].append(f"EXCLUDED {label}: exclusion pattern")
                    found += 1
                    continue
                path = os.path.join(task, "HANDOFF.md")
                if not os.path.lexists(path):
                    sub = scan(task, rel + "/", depth + 1) if depth < MAX_DEPTH else 0
                    if not sub and depth == 1:
                        ctx["skipped"].append(name)  # not a handoff folder (a memory package, notes, ...)
                    found += sub
                    continue
                if depth not in (1, MAX_DEPTH):
                    continue  # a HANDOFF.md between the levels is not a handoff of either layout
                before_st = os.lstat(path)
                if _is_link(before_st):
                    raise ValueError("HANDOFF.md is a link or reparse point")
                if not stat.S_ISREG(before_st.st_mode):
                    raise ValueError("HANDOFF.md is not a regular file")
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before_st):
                        raise ValueError("HANDOFF.md changed while it was opened")
                    raw = stream.read(MAX_FILE_BYTES + 1)
                if _identity(os.lstat(task)) != _identity(info):
                    raise ValueError("task directory changed while it was read")
                report = _report(rel, raw, project, (optional(task, SIDE_FILES[0]), optional(task, SIDE_FILES[1])),
                                 opened.st_mtime, ctx["issues"], nested=depth > 1)
                if report:
                    ctx["reports"].append(report)
            except (OSError, ValueError) as exc:
                # Do not leak absolute paths from OS errors into generated context.
                reason = (exc.strerror or "unavailable") if isinstance(exc, OSError) else str(exc)
                ctx["issues"].append(f"UNAVAILABLE/MALFORMED {label}: {reason}")
            found += len(ctx["reports"]) + len(ctx["issues"]) - before
        return found

    try:
        rel = os.path.relpath(project.inbox, project.vault)
        if rel == "." or rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
            raise OSError(0, "inbox escapes vault")
        inbox = project.vault
        for part in [""] + rel.split(os.sep):
            inbox = os.path.join(inbox, part) if part else inbox
            info = os.lstat(inbox)
            if _is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise OSError(0, "link, reparse point or non-directory in the inbox path")
        scan(inbox, "", 1)
    except _Overflow as exc:
        ctx.update(reports=[], skipped=[], issues=[str(exc)])
    except OSError as exc:
        ctx["issues"].append(f"UNAVAILABLE Inbox: {exc.strerror or 'invalid inbox path'}")


def listing(project):
    reports, issues, skipped = discover_all(project)
    lines = [f"# Handoffs: {project.key}",
             f"Limits: {MAX_ENTRIES} Inbox entries; {MAX_FILE_BYTES} bytes/file; context count 0..{MAX_HANDOFFS}.",
             "Order: created UTC descending, relative path ascending. Metadata and claims are reported, not verified."]
    for report in reports:
        m = report["meta"]
        lines.append(f"- {report['label']!r}: task={m['task']}; created={m['created']}; status={m['status']}; approved={m['approved']}"
                     + (f"; format=v2; sha256={report['sha256'][:16]}" if m.get("version") == "2" else ""))
    lines.append(f"{len(reports)} matching handoffs.")
    if skipped:
        shown = ", ".join(repr(n + "/") for n in skipped[:5]) + (f" and {len(skipped) - 5} more" if len(skipped) > 5 else "")
        lines.append(f"Skipped {len(skipped)} folder(s) without HANDOFF.md (not handoffs, e.g. a memory package): {shown}")
    lines.extend(f"WARNING: {w}" for w in layout_warnings(project))
    lines.extend(issues)
    return "\n".join(lines) + "\n", bool(issues)


# ---------------------------------------------------------------- version 2: check, seal, decide
#
# docs/HANDOFF-V2.md. A worker writes HANDOFF.md (and NEXT-PR.md) on its branch or in its work
# folder; `seal` checks it and copies it, unchanged, to <inbox>/<round>/<lane>/<checkpoint>/ with a
# SEAL.json; `approve`/`reject` record the owner's decision for exactly the bytes they read.

REQUIRED_KEYS = ("format", "status", "approved", "project", "round", "lane", "checkpoint", "base_sha", "head_sha",
                 "queue", "queue_sha256")
REQUIRED_SECTIONS = ("Summary", "State", "Items", "Evidence", "Decisions within authority", "Decisions for the owner",
                     "Blockers", "Next")
NEXT_PR_SECTIONS = ("Branch", "Commits", "PR body", "Checks before merge", "Not included")
CHECKPOINT = re.compile(r"cp-(?:[A-Za-z0-9]{1,16}|final)\Z")
SHORT_HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{7,63}(?![0-9a-f])")
ATTRIBUTION = re.compile(r"(?im)^\s*co-authored-by:.*\b(claude|anthropic)\b|generated with claude code|"
                         r"claude\.ai/(?:code/session_|chat/|share/)")
SEAL_FORMAT, DECISION_FORMAT = "pai-seal/1", "pai-decision/1"


def sections(text):
    """[(name, body)] of the `## ` sections in order (front matter and title excluded)."""
    out, name, body = [], None, []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            if name is not None:
                out.append((name, "\n".join(body).strip("\n")))
            name, body = line[3:].strip(), []
        elif name is not None:
            body.append(line)
    if name is not None:
        out.append((name, "\n".join(body).strip("\n")))
    return out


def _table_rows(body):
    rows = []
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("|") and line.endswith("|") and not re.fullmatch(r"\|[\s:|-]+\|", line):
            rows.append([c.strip() for c in re.split(r"(?<!\\)\|", line[1:-1])])
    return rows


def check_v2(text):
    """(front matter, problems) of a version 2 handoff; no problems means `seal` accepts its form."""
    from pai import workqueue
    probs = []
    try:
        meta = _front_matter(text)
    except ValueError as e:
        return {}, [str(e)]
    missing = [k for k in REQUIRED_KEYS if k not in meta]
    if missing:
        probs.append("front matter lacks %s (write UNKNOWN or none when not known)" % ", ".join(missing))
    if meta.get("format") != V2_FORMAT:
        probs.append(f"format is {meta.get('format')!r}, not {V2_FORMAT}")
    if meta.get("status") not in ("draft", "sealed"):
        probs.append(f"status {meta.get('status')!r}: a handoff to seal says draft or sealed")
    if meta.get("approved") != "false":
        probs.append("approved must be false: only `pai handoff approve` approves, after sealing")
    if not PROJECT_KEY.fullmatch(meta.get("project", "")):
        probs.append("project is not a project key")
    for k in ("round", "lane"):
        if not NAME.fullmatch(meta.get(k, "")) or meta.get(k) == "UNKNOWN":
            probs.append(f"{k} {meta.get(k)!r} is not a folder name ([A-Za-z0-9._-])")
    if not CHECKPOINT.fullmatch(meta.get("checkpoint", "")):
        probs.append(f"checkpoint {meta.get('checkpoint')!r} is not cp-<Block> or cp-final")
    for k in ("base_sha", "head_sha"):
        if not HEX40.match(meta.get(k, "")):
            probs.append(f"{k} is not a full 40-character commit sha")
    if meta.get("queue_sha256", "none") not in ("none", "UNKNOWN") and not HEX64.match(meta.get("queue_sha256", "")):
        probs.append("queue_sha256 is not a full sha256 (64 hex) or none")
    secs = sections(text)
    names = [n for n, _ in secs]
    for n in REQUIRED_SECTIONS:
        if n not in names:
            probs.append(f"section `## {n}` is missing")
    order = [n for n in names if n in REQUIRED_SECTIONS]
    if not probs and order != list(REQUIRED_SECTIONS):
        probs.append("sections are out of order: %s" % ", ".join(order))
    body = dict(secs)
    rows = _table_rows(body.get("State", ""))
    if rows[:1] != [["State", "Value", "Source"]]:
        probs.append("State is not a `| State | Value | Source |` table")
    else:
        seen = {}
        for r in rows[1:]:
            if len(r) != 3:
                probs.append("State row %r does not have three cells" % "|".join(r))
                continue
            seen.setdefault(r[0].lower(), []).append(r)
            if r[1].upper().startswith("UNKNOWN"):
                probs.append(f"State {r[0]}: write what happened (none, not run, not merged (owner-only)), not UNKNOWN "
                             "(PRM-D6)")
            if not SOURCE_RE.match(r[2]):
                probs.append(f"State {r[0]}: source {r[2]!r} is not one of {', '.join(SOURCES)} or reported-by-<lane>")
        for k in STATES:
            if len(seen.get(k, [])) != 1:
                probs.append(f"State needs exactly one {k.capitalize()} row (found {len(seen.get(k, []))})")
        extra = sorted(set(seen) - set(STATES))
        if extra:
            probs.append("State has rows that are not one of the five states: %s" % ", ".join(extra))
    items = body.get("Items", "")
    table, totals, _tline = workqueue.parse_handoff(items)
    if not table:
        probs.append("Items has no `| ID | state | evidence |` rows")
    for iid, sts in table.items():
        if len(sts) > 1:
            probs.append(f"Items lists {iid} {len(sts)} times")
    if totals is None:
        probs.append("Items has no `Totals:` line")
    else:
        for k in TOTALS_ORDER:
            n = sum(1 for v in table.values() if v[-1] == k)
            if k not in totals:
                probs.append(f"Totals line has no {k} count")
            elif totals[k] != n:
                probs.append(f"Totals says {k} {totals[k]}, the Items table has {n}")
    for line in body.get("Evidence", "").split("\n"):
        line = line.strip().lstrip("-*").strip()
        if not line or line.lower().startswith(("none", "path ", "| ---")):
            continue
        if not re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", line):
            short = SHORT_HEX.search(line)
            probs.append("Evidence line has %s: %s" % ("a truncated sha256 (%d hex)" % len(short.group(0)) if short else
                                                       "no sha256", line[:80]))
    for n in ("Blockers", "Next"):
        if n in body and not body[n].strip():
            probs.append(f"`## {n}` is empty (write none when there is nothing)")
    for line in body.get("Blockers", "").split("\n"):
        item = line.strip().lstrip("-*").strip()
        if item and item.lower() not in ("none", "none.") and not re.search(r"`[^`]+`[\s.)]*$", item):
            probs.append("Blockers: each blocker ends with the command the owner runs, in backticks (PRM-D6): "
                         + item[:80])
    probs += short_ranges(text)
    return meta, probs


SHORT_RANGE = re.compile(r"\b([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*-\d{1,5})(?:\.\.|–|—|~)(\d{1,5})\b")


def short_ranges(text, name="HANDOFF.md"):
    """PRM-D7: IDs are written in full; `R6-COR-031..035` must be `R6-COR-031..R6-COR-035` or a list."""
    return ["%s: write IDs in full, not %s..%s (PRM-D7)" % (name, m.group(1), m.group(2))
            for m in SHORT_RANGE.finditer(text)]


def check_next_pr(text, item_ids):
    """Problems of a NEXT-PR.md against the handoff's item ids."""
    from pai import workqueue
    probs = []
    names = [n for n, _ in sections(text)]
    for n in NEXT_PR_SECTIONS:
        if n not in names:
            probs.append(f"NEXT-PR.md: section `## {n}` is missing")
    families = {re.sub(r"-\d+$", "", i) for i in item_ids}
    for tok in sorted(set(re.findall(r"\b" + workqueue.ID + r"\b", text))):
        if re.sub(r"-\d+$", "", tok) in families and tok not in item_ids:
            probs.append(f"NEXT-PR.md names {tok}, which is not in the handoff's Items table")
    return probs + short_ranges(text, "NEXT-PR.md")


def _privacy_problems(name, text):
    from pai import privacy
    out = ["%s line %d: %s (%s)" % (name, r["line"], r["kind"], r["sample"]) for r in privacy.scrub(text)[1]]
    for m in ATTRIBUTION.finditer(text):
        out.append("%s line %d: AI attribution or a session link" % (name, text[:m.start()].count("\n") + 1))
    return out


def _fsync_dir(path):
    if os.name == "nt":
        return  # a directory cannot be opened for fsync on Windows; the files themselves are fsynced
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _queue_item_for(doc, checkpoint):
    """The queue checkpoint a checkpoint folder stands for: cp-A the first, cp-B the second, ...,
    cp-final the last. None when it cannot be told (then --item is needed)."""
    cps = doc.get("checkpoints") or []
    if not cps:
        return None
    if checkpoint == "cp-final":
        return cps[-1]
    tag = checkpoint[3:]
    if len(tag) == 1 and tag.isalpha():
        n = ord(tag.upper()) - ord("A")
        return cps[n] if n < len(cps) else None
    if tag.isdigit():
        n = int(tag) - 1
        return cps[n] if 0 <= n < len(cps) else None
    return None


def seal(project, handoff_path, queue=None, item=None, next_pr=None, now=None, jev_verify=None, dry_run=False):
    """Check a version 2 handoff and freeze it in the inbox. Returns {state, dir, sha256, problems,
    notes, ...}; state is `sealed`, `unchanged` (the same bytes were sealed before), `refused` or
    `preview`. Nothing is written unless every check passed."""
    from pai import workqueue
    res = {"state": "refused", "problems": [], "notes": [], "dir": None, "sha256": None}
    try:
        with open(handoff_path, "rb") as f:
            raw = f.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        raise HandoffError(f"cannot read {handoff_path} ({e.strerror or e})")
    if len(raw) > MAX_FILE_BYTES:
        raise HandoffError(f"{handoff_path} is larger than {MAX_FILE_BYTES} bytes")
    sha = _sha(raw)  # the worker's file; the seal is identified by its body (R6-COR-040)
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise HandoffError(f"{handoff_path} is not UTF-8")
    bsha = res["sha256"] = body_sha(text)
    res["source_sha256"] = sha
    meta, probs = check_v2(text)
    if meta.get("project") and meta.get("project") != project.key:
        probs.append(f"project is {meta['project']!r}, not {project.key!r}")
    probs += _privacy_problems("HANDOFF.md", text)
    src_dir = os.path.dirname(os.path.abspath(handoff_path))
    evidence = []  # PRM-D4: (relative path, bytes) of every Evidence file, copied with the seal
    for path, want, _state in evidence_files(text, []):
        rel = os.path.normpath(path)
        if os.path.isabs(os.path.expanduser(path)) or rel.startswith(".."):
            probs.append(f"Evidence {path}: copy it into the checkpoint folder and cite it by its relative path (PRM-D4)")
            continue
        try:
            with open(os.path.join(src_dir, rel), "rb") as f:
                data = f.read()
        except OSError:
            probs.append(f"Evidence {path}: not found next to the handoff (copy it into the checkpoint folder; PRM-D4)")
            continue
        if _sha(data) != want:
            probs.append(f"Evidence {path}: its sha256 is {_sha(data)}, the handoff says {want}")
            continue
        evidence.append((rel, data))
    res["evidence"] = [r for r, _ in evidence]
    item_ids = list(workqueue.parse_handoff(dict(sections(text)).get("Items", ""))[0])
    if next_pr is None:
        cand = os.path.join(os.path.dirname(os.path.abspath(handoff_path)), "NEXT-PR.md")
        next_pr = cand if os.path.isfile(cand) else None
    pr_raw = None
    if next_pr:
        try:
            with open(next_pr, "rb") as f:
                pr_raw = f.read(MAX_FILE_BYTES + 1)
            pr_text = pr_raw.decode("utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError) as e:
            raise HandoffError(f"cannot read {next_pr} as UTF-8 ({getattr(e, 'strerror', None) or e})")
        probs += check_next_pr(pr_text, item_ids) + _privacy_problems("NEXT-PR.md", pr_text)
    if queue is None and meta.get("queue") not in (None, "none", "UNKNOWN") and os.path.isfile(meta["queue"]):
        queue = meta["queue"]
    doc = st = None
    if queue:
        try:
            doc = workqueue.load(queue)
            evs, _ = workqueue.events(queue)
        except workqueue.QueueError as e:
            raise HandoffError(str(e))
        st = workqueue.state(doc, evs)
        probs += ["queue: " + p for p in workqueue.check_handoff(doc, st, text)]
        item = item or _queue_item_for(doc, meta.get("checkpoint", ""))
        if not item:
            probs.append("cannot tell which queue checkpoint %s is; pass --item ID" % meta.get("checkpoint"))
        elif item not in st:
            probs.append(f"--item {item} is not an item of {queue}")
        with open(workqueue.log_path(queue), "rb") as f:
            log_sha = _sha(f.read())
        if meta.get("queue_sha256") not in (None, "none", "UNKNOWN", log_sha):
            res["notes"].append("the queue log changed after the handoff was written (queue_sha256 differs); "
                                "the item states above were compared with the log as it is now")
    else:
        res["notes"].append("no queue: the Items table was checked for form only")
    res["problems"] = probs
    if probs:
        return res
    inbox = os.path.realpath(project.inbox)
    dest = os.path.join(inbox, meta["round"], meta["lane"], meta["checkpoint"])
    res["dir"] = dest
    for part in (os.path.join(inbox, meta["round"]), os.path.join(inbox, meta["round"], meta["lane"]), dest):
        if os.path.islink(part):
            raise HandoffError(f"{os.path.relpath(part, inbox)} in the inbox is a link; pai does not write through it")
    sealed_now = now or datetime.now(timezone.utc)
    if os.path.isdir(dest):
        try:
            with open(os.path.join(dest, "SEAL.json"), "rb") as f:
                prior = json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError):
            raise HandoffError(f"{dest} exists but was not sealed by pai (no readable SEAL.json); move it away first")
        if prior.get("source_sha256", prior.get("handoff_sha256")) != sha:
            res["problems"] = [f"{meta['checkpoint']} of {meta['round']}/{meta['lane']} is already sealed with sha256 "
                               f"{prior.get('source_sha256', prior.get('handoff_sha256'))}; a sealed checkpoint does not "
                               "change: put the "
                               "correction in the next checkpoint"]
            return res
        res["state"] = "unchanged"
    elif dry_run:
        res["state"] = "preview"
        return res
    else:
        # The inbox copy: the worker's text with status: sealed, sealed_sha256 (the body's sha256) and
        # approval: pending in its front matter; the body is byte for byte the worker's.
        sealed_raw = with_front_matter(text, {"status": "sealed", "sealed_sha256": bsha,
                                              "approval": "pending"}).encode("utf-8")
        record = {"format": SEAL_FORMAT, "handoff_sha256": _sha(sealed_raw), "body_sha256": bsha, "source_sha256": sha,
                  "next_pr_sha256": _sha(pr_raw) if pr_raw else None,
                  "project": project.key, "round": meta["round"], "lane": meta["lane"], "checkpoint": meta["checkpoint"],
                  "task": meta.get("task"), "base_sha": meta["base_sha"], "head_sha": meta["head_sha"],
                  "source": os.path.abspath(handoff_path), "queue": os.path.abspath(queue) if queue else None,
                  "queue_item": item, "sealed_at": sealed_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "generator": f"pai {__version__}", "items": len(item_ids), "notes": res["notes"],
                  "evidence": {rel: _sha(data) for rel, data in evidence},
                  "checks": ["pai-handoff/2 form", "privacy filter and AI attribution"] +
                            (["NEXT-PR.md sections and item ids"] if pr_raw else []) +
                            (["items and totals against the queue log"] if queue else [])}
        if jev_verify is not None:
            record["jev"] = jev_verify(text, st, doc)
        parent = os.path.dirname(dest)
        os.makedirs(parent, 0o755, exist_ok=True)
        tmp = os.path.join(parent, ".%s.sealing-%d" % (meta["checkpoint"], os.getpid()))
        os.mkdir(tmp, 0o755)
        try:
            _write_new(os.path.join(tmp, "HANDOFF.md"), sealed_raw)
            if pr_raw is not None:
                _write_new(os.path.join(tmp, "NEXT-PR.md"), pr_raw)
            for rel, data in evidence:
                if rel in ("HANDOFF.md", "NEXT-PR.md", "SEAL.json", "DECISION.json"):
                    continue
                os.makedirs(os.path.join(tmp, os.path.dirname(rel)), exist_ok=True)
                _write_new(os.path.join(tmp, rel), data)
            _write_new(os.path.join(tmp, "SEAL.json"), (json.dumps(record, indent=1, sort_keys=True) + "\n").encode())
            _fsync_dir(tmp)
            os.rename(tmp, dest)  # fails when another seal created dest meanwhile: nothing is overwritten
        except OSError:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)  # only the temporary folder this call created
            raise
        _fsync_dir(parent)
        res["state"] = "sealed"
        res["jev"] = record.get("jev")
    if queue and item:
        evs, _ = workqueue.events(queue)
        with open(os.path.join(dest, "SEAL.json"), encoding="utf-8") as f:
            sealed_file_sha = json.load(f).get("handoff_sha256")
        if not any(e.get("event") == "checkpoint" and e.get("id") == item and e.get("sha256") == sealed_file_sha
                   for e in evs):
            st = workqueue.state(doc, evs)
            workqueue.append(queue, {"event": "checkpoint", "id": item, "handoff": os.path.join(dest, "HANDOFF.md"),
                                     "sha256": sealed_file_sha, "body_sha256": bsha, "counts": workqueue.counts(doc, st),
                                     "done": workqueue.ids_in(doc, st, "done"), "at": workqueue.now(), "session": None})
            res["notes"].append(f"queue checkpoint {item} recorded with this sha256")
    return res


def _resolve_sealed(path):
    d = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    with open(os.path.join(d, "HANDOFF.md"), "rb") as f:
        raw = f.read(MAX_FILE_BYTES + 1)
    try:
        with open(os.path.join(d, "SEAL.json"), "rb") as f:
            seal_doc = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        raise HandoffError(f"{d} is not a sealed handoff (no readable SEAL.json): seal it first with `pai handoff seal`")
    return d, raw, seal_doc


def sealed_state(path):
    """{dir, task, queue_sha256, handoff_sha256 (the sealed body's sha256, or the file's for a seal
    made before R6-COR-040), status, approved, approval, problems} of a sealed checkpoint folder.
    Used by `pai worktree preview --handoff` and the approve commands."""
    d = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    out = {"dir": os.path.realpath(d), "task": None, "queue_sha256": None, "handoff_sha256": None, "status": None,
           "approved": False, "approval": None, "problems": []}
    try:
        d, raw, seal_doc = _resolve_sealed(d)
        text = raw.decode("utf-8").replace("\r\n", "\n")
        meta = _front_matter(text)
    except (OSError, ValueError, UnicodeDecodeError, HandoffError) as e:
        out["problems"].append(str(e) if isinstance(e, HandoffError) else "%s: %s" % (d, getattr(e, "strerror", None) or e))
        return out
    try:
        with open(os.path.join(d, "DECISION.json"), "rb") as f:
            decision = _side(f.read())
    except OSError:
        decision = None
    issues = []
    eff = effective(dict(meta, version="2"), _sha(raw), seal_doc, decision, issues, "HANDOFF.md", body_sha(text))
    out.update(task=meta.get("task") or "%s/%s/%s" % (meta.get("round"), meta.get("lane"), meta.get("checkpoint")),
               queue_sha256=meta.get("queue_sha256") if meta.get("queue_sha256") not in (None, "none", "UNKNOWN") else None,
               handoff_sha256=seal_doc.get("body_sha256") or seal_doc.get("handoff_sha256"), status=eff.get("status"),
               approved=eff.get("approved") == "true", approval=eff.get("approval"))
    out["problems"] += ["HANDOFF.md changed after sealing" if "SEAL.json" in i else i for i in issues]
    return out


def _replace(path, data):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path))


def decide(path, decision, expect_sha, by=None, reason=None, now=None):
    """Record the owner's approve/reject for the sealed handoff at PATH, refusing unless the sealed
    body's sha256 (what `seal` printed) starts with EXPECT_SHA. Writes DECISION.json and, for a seal
    with sealed_sha256, sets approved/approval/status in the note's front matter (the body is never
    touched). Returns {state, dir, sha256}; state is the decision or `unchanged`."""
    if decision not in ("approved", "rejected"):
        raise HandoffError("decision must be approved or rejected")
    if not re.fullmatch(r"[0-9a-f]{12,64}", expect_sha or ""):
        raise HandoffError("--expect-sha needs at least 12 hex digits of the sha256 you reviewed")
    if decision == "rejected" and not (reason or "").strip():
        raise HandoffError("reject needs --reason (what is wrong, so the next checkpoint can fix it)")
    d, raw, seal_doc = _resolve_sealed(path)
    text = raw.decode("utf-8").replace("\r\n", "\n")
    new_style = bool(seal_doc.get("body_sha256"))
    sha = body_sha(text) if new_style else _sha(raw)
    if sha != (seal_doc.get("body_sha256") if new_style else seal_doc.get("handoff_sha256")):
        if new_style:
            _replace(os.path.join(d, "HANDOFF.md"), with_front_matter(text, {"approval": "stale"}).encode("utf-8"))
        raise HandoffError(f"{d}/HANDOFF.md changed after sealing (sha256 {sha}); marked approval: stale, nothing "
                           "was approved")
    if not sha.startswith(expect_sha):
        raise HandoffError(f"the sealed handoff has sha256 {sha}, not {expect_sha}…: you did not review these bytes; "
                           "nothing was recorded")
    target = os.path.join(d, "DECISION.json")
    if os.path.exists(target):
        with open(target, encoding="utf-8") as f:
            prior = json.load(f)
        if prior.get("decision") == decision and prior.get("body_sha256", prior.get("handoff_sha256")) == sha:
            return {"state": "unchanged", "dir": d, "sha256": sha}
        raise HandoffError(f"{d} was already {prior.get('decision')} at {prior.get('at')}; a decision is recorded once "
                           "(a later checkpoint supersedes it)")
    moment = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {"format": DECISION_FORMAT, "decision": decision, "by": _one_line(by or "owner", "--by"), "at": moment,
           "reason": _one_line(reason or "", "--reason") or None}
    rec["body_sha256" if new_style else "handoff_sha256"] = sha
    try:
        _write_new(target, (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode())
    except FileExistsError:
        raise HandoffError(f"{d} was decided by someone else just now; read DECISION.json")
    _fsync_dir(d)
    if new_style:  # what Obsidian shows; the decision itself is DECISION.json
        _replace(os.path.join(d, "HANDOFF.md"), with_front_matter(text, {
            "status": decision, "approved": "true" if decision == "approved" else "false",
            "approval": decision}).encode("utf-8"))
    return {"state": decision, "dir": d, "sha256": sha}


def approve_from_vault(path, by=None, now=None):
    """`pai handoff approve --from-vault` (R6-COR-040): the owner set `approved: true` in the sealed
    note in Obsidian; check that the body still has its sealed_sha256 (and SEAL.json's), then record
    the approval. A changed body is marked `approval: stale` and refused."""
    d, raw, seal_doc = _resolve_sealed(path)
    text = raw.decode("utf-8").replace("\r\n", "\n")
    meta = _front_matter(text)
    if not seal_doc.get("body_sha256") or not meta.get("sealed_sha256"):
        raise HandoffError(f"{d} was sealed before approval from the vault existed (no sealed_sha256): use "
                           "`pai handoff approve PROJECT DIR --expect-sha SHA`")
    if meta.get("approved") != "true":
        return {"state": "not-approved", "dir": d, "sha256": seal_doc["body_sha256"]}
    now_sha = body_sha(text)
    if not (now_sha == meta["sealed_sha256"] == seal_doc["body_sha256"]):
        _replace(os.path.join(d, "HANDOFF.md"), with_front_matter(text, {"approval": "stale"}).encode("utf-8"))
        raise HandoffError(f"{d}: the body changed after sealing (sealed {seal_doc['body_sha256'][:16]}, now "
                           f"{now_sha[:16]}); marked approval: stale, nothing was approved")
    return decide(d, "approved", now_sha, by=by or "owner (vault)", now=now)


def evidence_files(text, bases):
    """[(path, sha256 written, state)] for each Evidence line: state is `match`, `MISMATCH` or `missing`
    (the file is looked up as written, then under each of `bases`)."""
    out = []
    for line in dict(sections(text)).get("Evidence", "").split("\n"):
        line = line.strip().lstrip("-*").strip()
        mt = re.search(r"(?<![0-9a-f])([0-9a-f]{64})(?![0-9a-f])", line)
        if not mt:
            continue
        path = line[:mt.start()].strip().strip("`")
        if not path:
            continue
        cands = [path] if os.path.isabs(os.path.expanduser(path)) else [os.path.join(b, path) for b in bases if b]
        state = "missing"
        for c in cands:
            c = os.path.expanduser(c)
            if os.path.isfile(c):
                with open(c, "rb") as f:
                    state = "match" if _sha(f.read()) == mt.group(1) else "MISMATCH"
                break
        out.append((path, mt.group(1), state))
    return out


def _load_jev():
    """swarm/jev.py from the workbench next to this package (pai itself makes no network calls)."""
    import importlib
    import sys
    from pai import config
    swarm = os.path.join(config.WORKBENCH, "swarm")
    if swarm not in sys.path:
        sys.path.insert(0, swarm)
    return importlib.import_module("jev")


def jev_verify(text, st=None, doc=None, bases=(), jev=None):
    """`pai handoff seal --jev-verify` (R6-COR-035): what the handoff says against what is recorded.
    Recorded: each Evidence line's file and sha256 (checked here) and the queue's done items with
    evidence. Said: JEV's reading of the report (done, checked, evidence). Returns {verdict: agrees |
    DISAGREES | skipped, detail, scores, evidence {match, MISMATCH, missing}}. A reference only:
    `seal` writes it into SEAL.json and never refuses because of it."""
    files = evidence_files(text, bases)
    counts = {k: sum(1 for f in files if f[2] == k) for k in ("match", "MISMATCH", "missing")}
    done_with_evidence = 0
    if st:
        done_with_evidence = sum(1 for s in st.values() if s.get("state") == "done" and s.get("evidence"))
    res = {"verdict": "skipped", "detail": "", "scores": None, "evidence": counts,
           "queue_done_with_evidence": done_with_evidence}
    try:
        jev = jev or _load_jev()
    except ImportError as e:
        return dict(res, detail="the workbench's jev module is not available (%s)" % e)
    secs = dict(sections(text))
    report = "\n\n".join("## %s\n%s" % (n, secs.get(n, "")) for n in ("Summary", "State", "Items", "Evidence"))
    sig = jev.signal("verify", "Checkpoint handoff: %s" % (secs.get("Summary", "").split("\n")[0][:120]),
                     "Report what was done and how it was checked.", report)
    if sig.get("state") != "ok":
        return dict(res, detail="JEV %s: %s" % (sig.get("state"), sig.get("detail")))
    scores = {k: sig.get(k) for k in ("done", "checked", "evidence")}
    res["scores"] = scores
    says_checked = (scores["checked"] or 0) >= 0.5
    shown = counts["match"] > 0 and counts["MISMATCH"] == 0
    if counts["MISMATCH"]:
        res.update(verdict="DISAGREES", detail="%d Evidence file(s) do not have the sha256 the handoff names"
                   % counts["MISMATCH"])
    elif says_checked and not shown:
        res.update(verdict="DISAGREES", detail="JEV reads the report as checked (%.2f), but no Evidence file was found "
                   "with its sha256" % scores["checked"])
    elif not says_checked and shown:
        res.update(verdict="DISAGREES", detail="%d Evidence file(s) match, but JEV does not read the report as checked "
                   "(%.2f): the report may not say what was run" % (counts["match"], scores["checked"] or 0))
    else:
        res.update(verdict="agrees", detail="checked %.2f, evidence %s; %d Evidence file(s) match" % (
            scores["checked"] or 0, "%.2f" % scores["evidence"] if scores["evidence"] is not None else "-",
            counts["match"]))
    return res


# ---------------------------------------------------------------- command line (registered in pai/cli.py)

def _cli_add(sub, parsers):
    """`handoff new` v2 flags and `handoff seal|approve|reject` (R6-COR-019..024, 040)."""
    n, hs = parsers["handoff new"], parsers["handoff"]
    n.add_argument("--round", help="round folder (with --lane: the draft goes to <inbox>/<round>/<lane>/)")
    n.add_argument("--lane", help="lane folder")
    n.add_argument("--checkpoint", help="cp-<Block> or cp-final, written into the front matter")
    n.add_argument("--queue", metavar="FILE", help="a `pai queue` file: the Items table and Totals line come from its log")
    n.add_argument("--base-sha", help="the round's base commit (40 hex)")
    n.add_argument("--head-sha", help="the worktree HEAD (40 hex)")
    n.add_argument("--source", action="append", default=[], metavar="STATE=LABEL",
                   help="where a state's value comes from: real run, synthetic, fake agent, mock endpoint, JEV, "
                        "self-report (default) or reported-by-<lane>")
    se = hs.add_parser("seal", help="check a pai-handoff/2 HANDOFF.md and freeze it in <inbox>/<round>/<lane>/<cp>/")
    se.add_argument("project")
    se.add_argument("file", help="the HANDOFF.md to seal (NEXT-PR.md next to it is sealed with it)")
    se.add_argument("--queue", metavar="FILE", help="compare Items/Totals with this queue and record the checkpoint "
                    "(default: the queue named in the front matter)")
    se.add_argument("--item", help="the queue checkpoint ID (default: cp-A is the queue's first checkpoint, ...)")
    se.add_argument("--next-pr", metavar="FILE", help="the NEXT-PR.md (default: the one next to the handoff)")
    se.add_argument("--jev-verify", action="store_true", help="also ask JEV whether the handoff agrees with the "
                    "recorded checks (a reference, never a gate)")
    se.add_argument("--dry-run", action="store_true", help="check only; write nothing")
    for verb in ("approve", "reject"):
        d = hs.add_parser(verb, help=f"{verb} a sealed handoff, only if it still has the sha256 you reviewed")
        d.add_argument("project")
        d.add_argument("path", help="the sealed checkpoint folder (or its HANDOFF.md)")
        d.add_argument("--expect-sha", metavar="SHA256", help="the sha256 you reviewed (12+ hex)")
        if verb == "approve":
            d.add_argument("--from-vault", action="store_true", help="you set `approved: true` in the sealed note "
                           "(Obsidian): check its body against sealed_sha256 and record the approval")
        d.add_argument("--by", help="who decides (default: owner)")
        d.add_argument("--reason", required=verb == "reject", help="why (required to reject)")


def v2_args(a):
    """The keyword arguments `handoff new` passes to create() for the v2 flags."""
    return {"where": {"round": a.round, "lane": a.lane, "checkpoint": a.checkpoint, "queue": a.queue,
                      "base_sha": a.base_sha, "head_sha": a.head_sha},
            "sources": dict(x.partition("=")[::2] for x in a.source)}


def _cli_run(a, load_project):
    import sys
    if a.cmd != "handoff" or a.hcmd not in ("seal", "approve", "reject"):
        return None
    project = load_project()
    if a.hcmd == "seal":
        jev = None
        if a.jev_verify:  # R6-COR-035: a reference written into SEAL.json, never a gate
            bases = [os.path.dirname(os.path.abspath(a.file)), os.getcwd(), project.repo]
            jev = lambda text, st, doc: jev_verify(text, st, doc, bases)  # noqa: E731
        r = seal(project, a.file, a.queue, a.item, a.next_pr, jev_verify=jev, dry_run=a.dry_run)
        for p in r["problems"]:
            print(f"PROBLEM  {p}", file=sys.stderr)
        for n in r["notes"]:
            print(f"note     {n}")
        if r["state"] == "refused":
            print(f"refused  nothing was sealed ({len(r['problems'])} problem(s) above)", file=sys.stderr)
            return 1
        if r.get("jev"):
            print(f"jev      {r['jev'].get('verdict')}: {r['jev'].get('detail')} (a reference, never a gate)")
        print(f"{r['state']:<8} {r['dir'] or '(dry run)'}")
        print(f"sha256   {r['sha256']}")
        if r["state"] in ("sealed", "unchanged"):
            print(f"approve  pai handoff approve {project.key} \"{r['dir']}\" --expect-sha {r['sha256'][:16]}")
        return 0
    if getattr(a, "from_vault", False):
        r = approve_from_vault(a.path, a.by)
        if r["state"] == "not-approved":
            print(f"pai: {r['dir']}: the note does not say `approved: true`; nothing was recorded", file=sys.stderr)
            return 1
        print("%-9s %s (body sha256 %s)" % (r["state"], r["dir"], r["sha256"]))
        return 0
    if not a.expect_sha:
        raise HandoffError("--expect-sha SHA256 is required (or, to approve: --from-vault)")
    r = decide(a.path, "approved" if a.hcmd == "approve" else "rejected", a.expect_sha, a.by, a.reason)
    print("%-9s %s (sha256 %s)" % (r["state"], r["dir"], r["sha256"]))
    return 0


def _feature():
    from pai import commands
    return commands.Feature("handoff-v2", _cli_add, _cli_run)


CLI = _feature()


def _doctor_layout(report, key, project):
    """R6-COR-016: the memory package and the inbox must sit side by side."""
    for w in layout_warnings(project):
        report.warn(f"{key}: {w}")


def _register_doctor():
    from pai import commands
    commands.doctor_check(_doctor_layout)


_register_doctor()


def claude_md_findings(repo):
    """R6-COR-064: CLAUDE.md as a symbolic link breaks where links are not checked out (core.symlinks
    false, as on Windows without developer mode): Claude Code then reads a text file holding a path.
    Returns warnings; reads files only (the repository's .git/config), runs no git."""
    out = []
    path = os.path.join(repo, "CLAUDE.md")
    symlinks = None
    try:
        with open(os.path.join(repo, ".git", "config"), encoding="utf-8", errors="replace") as f:
            section = ""
            for line in f:
                s = line.strip().lower()
                if s.startswith("["):
                    section = s
                elif section == "[core]" and s.replace(" ", "").startswith("symlinks="):
                    symlinks = s.split("=", 1)[1].strip() not in ("false", "no", "off", "0")
    except OSError:
        pass
    fix = "replace it with a one-line CLAUDE.md that says `@AGENTS.md` (Claude Code imports the file)"
    if os.path.islink(path):
        if symlinks is False:
            out.append(f"CLAUDE.md is a symbolic link but this repository has core.symlinks=false: checkouts made "
                       f"here turn it into a text file holding a path; {fix}")
        else:
            out.append(f"CLAUDE.md is a symbolic link: a checkout with core.symlinks=false (Windows) gets a text file "
                       f"holding a path instead; {fix}")
    elif os.path.isfile(path) and os.path.getsize(path) < 256:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
        if text and "\n" not in text and not text.startswith("@") and re.fullmatch(r"[\w./-]+\.md", text) and \
                os.path.isfile(os.path.join(repo, text)):
            out.append(f"CLAUDE.md only holds the path {text!r}: a symbolic link checked out as text "
                       f"(core.symlinks=false), so Claude Code does not read {text}; {fix}")
    return out


def _doctor_claude_md(report, key, project):
    for w in claude_md_findings(project.repo):
        report.warn(f"{key}: {w}")


def _register_doctor_claude_md():
    from pai import commands
    commands.doctor_check(_doctor_claude_md)


_register_doctor_claude_md()
