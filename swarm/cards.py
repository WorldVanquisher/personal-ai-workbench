"""Portable task cards: a project's task notes as one small, versioned, deterministic file that
another checkout, machine or person can preview and import.

    pai task export [NAME ...]             preview the cards (nothing is written)
    pai task export ... --out FILE         write them (never over an existing file)
    pai task import FILE                   preview: would-create, unchanged, CONFLICT per note
    pai task import FILE --write           create the notes (refused while any note conflicts)

Format `pai-task-cards`, version 1 (JSON, UTF-8, canonical: keys sorted, two-space indent,
non-ASCII kept as it is, one line per text line, cards sorted by name, a final newline; the same
notes always give the same bytes, so a card file diffs cleanly in review):

    {"format": "pai-task-cards", "version": 1,
     "project": {"name": "demo", "repository": "../demo"},
     "cards": [{"name": "add-json-flag", "title": "Add a --json flag", "status": "todo",
                "priority": 2, "paths": ["src/export.py"],
                "description": ["What to do, line by line"],
                "acceptance": ["`make test` passes"]}],
     "private": {...},
     "transfer": {...}}          only in a transfer packet (`--transfer`; see transfer.py)

- name: the note's file name without .md; title: its `# ` heading; description: the rest of the
  body; acceptance: the items under a `## Acceptance criteria` (or `## Acceptance`, `## Done
  when`) heading; fields: every other front-matter key that is not private (for example the review
  keys `checks`, `acceptance`, `pr_title`), as text, numbers, true/false or lists of those.
- project.repository and every entry of paths are relative, with `/` separators, so they mean the
  same on Linux, macOS, WSL and Windows: repository is relative to the card file's folder, paths
  to the repository. Absolute paths, `..` that leaves the repository, backslashes and unsupported
  versions are refused on import.
- Privacy-sensitive fields stay out of a default export: what pai writes into a note while it
  runs (worker, rc_run, branch, base_sha, remote, worktree, every jev_* score, handoff, session,
  cost_usd, last_run, handed_off_from, continued_marker, continued_in, the review keys and the
  `## Review ... · checks ...` sections `pai pr check` appends, which name the worktree path and
  the approved check commands) and personal fields
  (owner, assignee, author, reviewer, email, learner, student, and keys with those words). With
  --include-private they go into the separate top-level `private` group, by card name.
- Before any preview is shown, text that looks like a credential (API keys and tokens, private
  key blocks, passwords in URLs or assignments) is reported by card, field and line, and masked
  in the preview; writing a card file with such text needs --allow-flagged.
"""
import json
import os
import re

import notes

FORMAT, VERSION = "pai-task-cards", 1
RUN_FIELDS = ("worker", "rc_run", "branch", "base_sha", "remote", "worktree", "jev_intake", "review", "review_commit",
              "snapshot_sha256", "jev_review")
FIELD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
PERSONAL = re.compile(r"(owner|assignee|author|reviewer|email|e-mail|learner|student|phone|contact)", re.I)
KNOWN = ("status", "priority", "paths")
ACCEPT_RE = re.compile(r"^##\s+(acceptance criteria|acceptance|done when)\s*$", re.I)
# what `pai pr check` appends to a note: run-specific (worktree path, approved check commands), so private
REVIEW_RE = re.compile(r"^## Review \d{4}-\d\d-\d\dT[\d:]+Z · checks (?:passed|FAILED)\s*$")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?")
SECRETS = [
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("API key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("password in a URL", re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("secret assignment", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
                                     r"\s*[:=]\s*['\"]?[^\s'\"]{6,}")),
]


class CardError(Exception):
    pass


# What the runners record about one run in a note's front matter (R6-COR-017): where its handoff
# draft is (a vault path), the Claude session, the cost, the run and continuation ids, and every
# JEV score (`jev_*`). They describe a run on this machine, not the task, so they stay private.
RUN_RECORD_FIELDS = ("handoff", "session", "session_id", "cost_usd", "last_run", "handed_off_from",
                     "continued_marker", "continued_in")


def private_key(k):
    return (k in RUN_FIELDS or k in RUN_RECORD_FIELDS or k.startswith(("jev_", "jev-"))
            or bool(PERSONAL.search(k)))


def valid_name(name):
    """A note file name pai may create: no path, no leading dot, no control characters."""
    return (isinstance(name, str) and 0 < len(name) <= 64 and name not in (".", "..") and not name.startswith((".", "_"))
            and not re.search(r"[\x00-\x1f\x7f/\\:*?\"<>|]", name))


def check_rel(p, what):
    """A relative, `/`-separated path that stays inside its root; returns it normalised."""
    if not isinstance(p, str) or not p.strip():
        raise CardError("%s %r is empty" % (what, p))
    if "\\" in p:
        raise CardError("%s %r uses a backslash; card files use / on every system" % (what, p))
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or p.startswith("~"):
        raise CardError("%s %r is absolute; a card holds only paths relative to the repository" % (what, p))
    parts = []
    for x in p.split("/"):
        if x in ("", "."):
            continue
        if x == "..":
            if not parts:
                raise CardError("%s %r leaves the repository (..)" % (what, p))
            parts.pop()
        else:
            parts.append(x)
    if not parts:
        raise CardError("%s %r names the repository itself" % (what, p))
    return "/".join(parts)


def _lines(text):
    out = [l.rstrip() for l in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


def from_note(task):
    """(card, private fields) of a notes.Task."""
    body = task.body
    lines = _lines(body)
    title = task.title
    out_desc, accept, in_acc = [], [], False
    seen_title, review, in_review = False, [], False
    for line in lines:
        if not seen_title and line.startswith("# "):
            seen_title = True
            continue
        if REVIEW_RE.match(line):
            in_review = True
            review.append([line])
            continue
        if in_review and line.startswith("## "):
            in_review = False
        if in_review:
            review[-1].append(line)
            continue
        if ACCEPT_RE.match(line):
            in_acc = True
            continue
        if in_acc and line.startswith("## "):
            in_acc = False
        if in_acc:
            if line.strip():
                accept.append(BULLET_RE.sub("", line, count=1).strip())
        else:
            out_desc.append(line)
    card = {"name": task.name, "title": title, "status": task.status, "priority": task.priority,
            "paths": [check_rel(p, "paths entry") for p in task.paths], "description": _lines("\n".join(out_desc)),
            "acceptance": accept}
    fields = {k: v for k, v in sorted(task.meta.items()) if k not in KNOWN and not private_key(k) and v is not None}
    if fields:
        card["fields"] = fields
    private = {k: v for k, v in sorted(task.meta.items()) if k not in KNOWN and private_key(k) and v is not None}
    if review:
        private["review_sections"] = [l for sec in review for l in _lines("\n".join(sec)) + [""]][:-1]
    return card, private


def to_note(card):
    """The note text pai writes for a card (front matter, heading, description, acceptance)."""
    text = "# %s\n" % card["title"]
    if card["description"]:
        text += "\n" + "\n".join(card["description"]) + "\n"
    if card["acceptance"]:
        text += "\n## Acceptance criteria\n\n" + "\n".join("- %s" % a for a in card["acceptance"]) + "\n"
    meta = {"status": card["status"], "priority": card["priority"]}
    meta.update(sorted((card.get("fields") or {}).items()))
    if card["paths"]:
        meta["paths"] = card["paths"]
    return notes.set_meta(text, meta)


def dump(doc):
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def flagged(doc):
    """[(card, field, line number, kind, masked text)] for text that looks like a credential."""
    found = []

    def scan(card, field, values):
        for i, v in enumerate(values, 1):
            for kind, rx in SECRETS:
                for mt in rx.finditer(str(v)):
                    found.append((card, field, i, kind, mt.group(0)))

    for c in doc["cards"]:
        scan(c["name"], "title", [c["title"]])
        scan(c["name"], "description", c["description"])
        scan(c["name"], "acceptance", c["acceptance"])
        scan(c["name"], "paths", c["paths"])
    for name, fields in (doc.get("private") or {}).items():
        for k, v in fields.items():
            scan(name, "private.%s" % k, v if isinstance(v, list) else [v])
    return found


def mask(text, hits):
    for *_, s in hits:
        text = text.replace(s, s[:4] + "..." + "[masked %d chars]" % len(s))
    return text


def export(folder, names=None, status=None, include_private=False, project=None, repo=None, out_dir=None):
    """The card document for the notes in `folder` (all, the named ones, or those with `status`)."""
    tasks = {t.name: t for t in notes.load_tasks(folder)}
    if names:
        missing = [n for n in names if n not in tasks]
        if missing:
            raise CardError("no task note named %s in %s" % (", ".join(missing), folder))
    chosen = [tasks[n] for n in sorted(names or tasks)]
    if status:
        chosen = [t for t in chosen if t.status == status]
    cards, private = [], {}
    for t in chosen:
        if not valid_name(t.name):
            raise CardError("task note %r has a name a card cannot carry" % t.name)
        c, p = from_note(t)
        cards.append(c)
        if p and include_private:
            private[t.name] = p
    doc = {"format": FORMAT, "version": VERSION, "cards": cards}
    if project:
        ref = {"name": project}
        if repo and out_dir:
            ref["repository"] = os.path.relpath(repo, out_dir).replace(os.sep, "/")
        doc["project"] = ref
    if include_private and private:
        doc["private"] = private
    withheld = sorted({k for t in chosen for k, v in t.meta.items() if k not in KNOWN and private_key(k) and v is not None})
    if any(REVIEW_RE.match(l) for t in chosen for l in _lines(t.body)):
        withheld.append("review sections written by `pai pr check` (worktree path, check commands)")
    return doc, ([] if include_private else withheld)


def load(text):
    """Validate a card file's text and return its document (raises CardError)."""
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise CardError("not a card file (JSON: %s)" % e)
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise CardError("not a %s file (format %r)" % (FORMAT, doc.get("format") if isinstance(doc, dict) else None))
    if doc.get("version") != VERSION:
        raise CardError("unsupported format version %r: this pai reads version %d. Nothing was imported."
                        % (doc.get("version"), VERSION))
    extra = sorted(set(doc) - {"format", "version", "project", "cards", "private", "transfer"})
    if extra:
        raise CardError("unknown top-level field(s) %s for version %d. Nothing was imported." % (", ".join(extra), VERSION))
    cards = doc.get("cards")
    if not isinstance(cards, list):
        raise CardError("`cards` must be a list")
    seen = set()
    for c in cards:
        if not isinstance(c, dict):
            raise CardError("every card must be an object")
        name = c.get("name")
        if not valid_name(name):
            raise CardError("card name %r cannot be a note file name (no path, leading dot or special characters)"
                            % (name,))
        if name in seen:
            raise CardError("card %s appears twice" % name)
        seen.add(name)
        if not isinstance(c.get("title"), str) or not c["title"].strip() or "\n" in c["title"]:
            raise CardError("card %s: title must be one non-empty line" % name)
        for k in ("description", "acceptance"):
            v = c.setdefault(k, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise CardError("card %s: %s must be a list of text lines" % (name, k))
        c["paths"] = [check_rel(p, "card %s: paths entry" % name) for p in c.get("paths") or []]
        c.setdefault("status", "todo")
        if not isinstance(c["status"], str) or not re.fullmatch(r"[a-z][a-z-]{0,23}", c["status"]):
            raise CardError("card %s: status %r is not a status" % (name, c["status"]))
        c.setdefault("priority", 1)
        if not isinstance(c["priority"], int) or isinstance(c["priority"], bool):
            raise CardError("card %s: priority must be a whole number" % name)
        fields = c.get("fields", {})
        if not isinstance(fields, dict):
            raise CardError("card %s: fields must be an object" % name)
        for k, v in fields.items():
            ok = isinstance(v, (str, int, float, bool)) or (isinstance(v, list) and all(
                isinstance(x, (str, int, float, bool)) for x in v))
            if not FIELD_RE.fullmatch(k) or k in KNOWN or not ok or (isinstance(v, str) and "\n" in v):
                raise CardError("card %s: field %r is not a front-matter key with a plain value" % (name, k))
            if private_key(k):
                raise CardError("card %s: field %r is private; it belongs in the `private` group" % (name, k))
        extra = sorted(set(c) - {"name", "title", "status", "priority", "paths", "description", "acceptance", "fields"})
        if extra:
            raise CardError("card %s: unknown field(s) %s for version %d" % (name, ", ".join(extra), VERSION))
    ref = (doc.get("project") or {}).get("repository")
    if ref is not None:
        if not isinstance(ref, str) or ref.startswith("/") or re.match(r"^[A-Za-z]:", ref) or "\\" in ref:
            raise CardError("project.repository %r must be relative, with / separators" % (ref,))
    return doc


def import_plan(doc, folder):
    """[(state, card, path)] with state create | unchanged | conflict."""
    res = []
    for c in sorted(doc["cards"], key=lambda c: c["name"]):
        path = os.path.join(folder, c["name"] + ".md")
        if os.path.lexists(path):
            try:  # the same card, however the existing note is laid out, is not a conflict
                have = from_note(notes.Task(path))[0] if os.path.isfile(path) else None
            except (OSError, UnicodeDecodeError, CardError):
                have = None
            res.append(("unchanged" if have == dict(c, paths=list(c["paths"])) else "conflict", c, path))
        else:
            res.append(("create", c, path))
    return res


# ---------------------------------------------------------------- commands (pai task export / import)

def cmd_export(proj, folder, a, log):
    out_dir = os.path.dirname(os.path.abspath(a.out)) if a.out else os.getcwd()
    try:
        doc, withheld = export(folder, a.names, a.status, a.include_private, proj.get("name"), proj.get("repo"), out_dir)
    except CardError as e:
        log("pai task export: %s. Nothing was written." % e)
        return 2
    text = dump(doc)
    hits = flagged(doc)
    for card, field, line, kind, _ in hits:
        log("WARNING  card %s, %s line %d: looks like a %s (masked below)" % (card, field, line, kind))
    if hits:
        log("")
    if not a.out:
        log("PREVIEW (nothing written): %d card(s) from %s%s" % (len(doc["cards"]), folder, "" if not withheld else
            "; private fields left out: %s (--include-private puts them in a separate `private` group)"
            % ", ".join(withheld)))
        log(mask(text, hits).rstrip("\n"))
        log("\nTo save it: the same command with --out FILE.%s" % (" Flagged text must be removed first, or pass "
                                                                    "--allow-flagged." if hits else ""))
        return 0 if not hits else 1
    if hits and not a.allow_flagged:
        log("pai task export: not written: %d piece(s) of text look like credentials (above). Remove them from the "
            "notes, or pass --allow-flagged if they are not secrets." % len(hits))
        return 1
    try:
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        log("pai task export: %s exists; nothing was written (choose another --out)." % a.out)
        return 1
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    log("wrote %s: %d card(s)%s" % (a.out, len(doc["cards"]), "" if not withheld else
                                     "; private fields left out: %s" % ", ".join(withheld)))
    return 0


def cmd_import(folder, a, log, proj=None):
    try:
        with open(os.path.expanduser(a.file), encoding="utf-8") as f:
            doc = load(f.read())
        if "transfer" in doc:
            import transfer
            tlines, trefused, tblockers = transfer.import_report(doc, proj or {}, (proj or {}).get("repo"),
                                                                 getattr(a, "other_project", False))
    except OSError as e:
        log("pai task import: cannot read %s (%s). Nothing was imported." % (a.file, e.strerror or e))
        return 2
    except UnicodeDecodeError:
        log("pai task import: %s is not UTF-8. Nothing was imported." % a.file)
        return 2
    except CardError as e:
        log("pai task import: %s: %s" % (a.file, e))
        return 2
    if "transfer" in doc:
        for line in tlines:
            log(line)
        for b in tblockers:
            log("  BLOCKS   %s" % b)
        for r in trefused:
            log("REFUSED  %s" % r)
        if trefused:
            log("\npai task import: nothing was %s: the packet does not belong to this project (REFUSED above)."
                % ("written" if a.write else "planned"))
            return 1
        log("")
    plan = import_plan(doc, folder)
    hits = flagged(doc)
    for card, field, line, kind, _ in hits:
        log("WARNING  card %s, %s line %d: looks like a %s" % (card, field, line, kind))
    if doc.get("private"):
        log("note     the file has a `private` group (%s); it is not imported into notes" % ", ".join(sorted(doc["private"])))
    log("%s %d card(s) from %s into %s" % ("IMPORT" if a.write else "PREVIEW (nothing written):", len(plan), a.file, folder))
    for state, c, path in plan:
        log("  %-10s %s   (%s, p%s)" % ({"create": "create", "unchanged": "unchanged", "conflict": "CONFLICT"}[state],
                                         path, c["status"], c["priority"]))
    conflicts = [p for st, _, p in plan if st == "conflict"]
    for p in conflicts:
        log("CONFLICT %s exists with different content; it is never overwritten. Rename one of them, or skip it with "
            "--skip-existing." % p)
    create = [(c, p) for st, c, p in plan if st == "create"]
    if not a.write:
        log("\nTo create the %d new note(s): the same command with --write.%s" % (
            len(create), " Conflicts block --write unless you add --skip-existing." if conflicts else ""))
        return 0 if not conflicts else 1
    if conflicts and not a.skip_existing:
        log("\npai task import: nothing was written: %d conflict(s) above." % len(conflicts))
        return 1
    made = 0
    for c, p in create:
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            log("  skipped    %s (it appeared meanwhile; not overwritten)" % p)
            continue
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(to_note(c))
        made += 1
    log("\ncreated %d note(s)%s. Next: pai task list" % (made, "; left %d conflicting note(s) as they are" % len(conflicts)
                                                        if conflicts else ""))
    return 0
