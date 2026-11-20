"""Work queues that survive context limits: a worker's numbered items, their states and the next
one to do, kept in two local files instead of in an agent's conversation.

    pai queue init FILE --from LIST [--name NAME] [--checkpoint ID ...]   preview; --write creates FILE
    pai queue status FILE [--json]           done / prototype / blocked / owner-only by ID, and what is next
    pai queue next FILE                      the next item to work on (blocked ones are skipped, not hidden)
    pai queue mark FILE ID STATE [--reason TEXT] [--evidence PATH]       record one item's state
    pai queue checkpoint FILE ID [--handoff PATH]   record a checkpoint (and the handoff's sha256)
    pai queue table FILE                     the per-ID table and totals for a handoff, from the log
    pai queue check FILE HANDOFF             compare a handoff's per-ID rows and totals with the queue
    pai queue restart FILE                   a copyable prompt to resume in a fresh session

Two files, both plain text, both safe to read at any time:
  FILE (format `pai-queue`, version 1, JSON): the queue's name, its items in order (id, title) and
      its checkpoint IDs. Written once by `init --write`, never rewritten by the other commands.
  FILE.log (JSON lines, append-only): one event per line, flushed and fsynced before the command
      returns: {"event": "state", "id", "state", "reason", "evidence", "at", "session"} or
      {"event": "checkpoint", "id", "handoff", "sha256", "counts", "done", "at", "session"}.
      An item's state is its last state event; the state of the queue is replayed from the log, so
      a stop at any moment loses at most the line being written (an incomplete last line is
      reported and ignored).

States: todo (no event yet), doing, done (implemented, with --evidence), prototype (a verified
prototype: --evidence and --reason, what is missing), blocked (--reason: the exact dependency),
owner-only (--reason: what only the owner can do). A blocked item stays listed under "blocked" in
every status and restart prompt, while `next` moves on to the next independent item.

Standard library only; no network; runs on Linux, macOS, WSL and native Windows.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

FORMAT, VERSION = "pai-queue", 1
STATES = ("todo", "doing", "done", "prototype", "blocked", "owner-only")
FINAL = ("done", "prototype", "blocked", "owner-only")
NEEDS_REASON = ("prototype", "blocked", "owner-only")
NEEDS_EVIDENCE = ("done", "prototype")
ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*)?([A-Za-z][A-Za-z0-9]*-\d{1,5})(?:\*\*)?[.:)]?\s+(.+?)\s*$")
ROW_RE = re.compile(r"^\|\s*`?([A-Za-z][A-Za-z0-9]*-\d{1,5})`?\s*\|\s*`?([a-z][a-z-]*)`?\s*\|")
TOTALS_RE = re.compile(r"^\s*(?:\*\*)?totals?(?:\*\*)?\s*:", re.I)
COUNT_RE = re.compile(r"\b(todo|doing|done|prototype|blocked|owner-only)\s*[:=]?\s*(\d+)\b|\b(\d+)\s+"
                      r"(todo|doing|done|prototype|blocked|owner-only)\b")


class QueueError(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_path(path):
    return path + ".log"


def parse_list(text):
    """[(id, title)] from a numbered list (`R2-001. Title`, `- ID: title`); other lines are ignored."""
    items, seen = [], set()
    for line in text.replace("\r\n", "\n").split("\n"):
        mt = ITEM_RE.match(line)
        if not mt:
            continue
        iid, title = mt.group(1), mt.group(2)
        if iid in seen:
            raise QueueError("item %s appears twice in the list" % iid)
        seen.add(iid)
        items.append({"id": iid, "title": title})
    return items


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as e:
        raise QueueError("cannot read %s (%s)" % (path, e.strerror or e))
    except ValueError as e:
        raise QueueError("%s is not a queue file (JSON: %s)" % (path, e))
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise QueueError("%s is not a %s file" % (path, FORMAT))
    if doc.get("version") != VERSION:
        raise QueueError("%s has queue format version %r; this pai reads version %d. Nothing was changed."
                         % (path, doc.get("version"), VERSION))
    items = doc.get("items")
    if not isinstance(items, list) or not items or not all(
            isinstance(i, dict) and isinstance(i.get("id"), str) and isinstance(i.get("title"), str) for i in items):
        raise QueueError("%s: `items` must be a non-empty list of {id, title}" % path)
    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        raise QueueError("%s: an item id appears twice" % path)
    bad = [c for c in doc.get("checkpoints") or [] if c not in ids]
    if bad:
        raise QueueError("%s: checkpoint(s) %s are not item ids" % (path, ", ".join(bad)))
    return doc


def events(path):
    """(events, problems) replayed from FILE.log; an incomplete or unreadable line is skipped and reported."""
    lp, out, problems = log_path(path), [], []
    try:
        with open(lp, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return [], []
    except OSError as e:
        raise QueueError("cannot read %s (%s)" % (lp, e.strerror or e))
    lines = raw.split(b"\n")
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        last = n == len(lines)  # no newline after it: the write was interrupted
        try:
            e = json.loads(line.decode("utf-8"))
            if not isinstance(e, dict) or e.get("event") not in ("state", "checkpoint"):
                raise ValueError("not an event")
        except (ValueError, UnicodeDecodeError):
            problems.append("%s line %d is %s; it was ignored" % (
                lp, n, "incomplete (a write was interrupted)" if last else "not a queue event"))
            continue
        if last:
            problems.append("%s line %d has no final newline (a write was interrupted); it was kept because it is "
                            "a complete event" % (lp, n))
        out.append(e)
    return out, problems


def append(path, event):
    """One event, appended, flushed and fsynced; a torn earlier line is closed off first."""
    lp = log_path(path)
    line = json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"
    fd = os.open(lp, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        size = os.fstat(fd).st_size
        prefix = b""
        if size:
            with open(lp, "rb") as f:
                f.seek(size - 1)
                if f.read(1) != b"\n":
                    prefix = b"\n"
        os.write(fd, prefix + line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return event


def add_items(path, new):
    """Append items to an existing queue (ids must be new); the queue file is rewritten
    atomically and records the addition under "additions" (a key older pai versions ignore, so
    they still read the queue). Existing items, states and log events stay as they are."""
    doc = load(path)
    have = {i["id"] for i in doc["items"]}
    dup = [i["id"] for i in new if i["id"] in have]
    if dup:
        raise QueueError("%s already has item(s) %s; nothing was added" % (path, ", ".join(dup)))
    doc["items"] = doc["items"] + [{"id": i["id"], "title": i["title"]} for i in new]
    doc["additions"] = (doc.get("additions") or []) + [{"at": now(), "ids": [i["id"] for i in new]}]
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return doc


def state(doc, evs):
    """{id: {"state", "reason", "evidence", "at", "session"}} after replaying the events."""
    st = {i["id"]: {"state": "todo", "reason": None, "evidence": None, "at": None, "session": None} for i in doc["items"]}
    for e in evs:
        if e.get("event") == "state" and e.get("id") in st and e.get("state") in STATES:
            st[e["id"]] = {k: e.get(k) for k in ("state", "reason", "evidence", "at", "session")}
    return st


def counts(doc, st):
    c = {s: 0 for s in STATES}
    for i in doc["items"]:
        c[st[i["id"]]["state"]] += 1
    return c


def ids_in(doc, st, *states):
    return [i["id"] for i in doc["items"] if st[i["id"]]["state"] in states]


def next_item(doc, st):
    """The first item that is doing, else the first todo; blocked/owner-only items are skipped."""
    for want in ("doing", "todo"):
        for i in doc["items"]:
            if st[i["id"]]["state"] == want:
                return i
    return None


def last_checkpoint(evs):
    cps = [e for e in evs if e.get("event") == "checkpoint"]
    return cps[-1] if cps else None


def summary(doc, st, evs):
    c = counts(doc, st)
    nxt = next_item(doc, st)
    cp = last_checkpoint(evs)
    sessions = sorted({e.get("session") for e in evs if e.get("session")})
    return {"schema": "pai_queue_status_v1", "name": doc.get("name"), "items": len(doc["items"]), "counts": c,
            "done": ids_in(doc, st, "done"), "prototype": ids_in(doc, st, "prototype"),
            "blocked": [{"id": i, "reason": st[i]["reason"]} for i in ids_in(doc, st, "blocked")],
            "owner_only": [{"id": i, "reason": st[i]["reason"]} for i in ids_in(doc, st, "owner-only")],
            "doing": ids_in(doc, st, "doing"), "todo": ids_in(doc, st, "todo"),
            "next": nxt and {"id": nxt["id"], "title": nxt["title"]},
            "last_checkpoint": cp and {k: cp.get(k) for k in ("id", "at", "handoff", "sha256")},
            "next_checkpoint": next((x for x in doc.get("checkpoints") or [] if not any(
                e.get("event") == "checkpoint" and e.get("id") == x for e in evs)), None),
            "sessions": sessions}


def compact(ids):
    """R2-001..R2-004, R2-007 style ranges of consecutive ids with one prefix."""
    out, run = [], []

    def split(i):
        mt = re.match(r"^(.*-)(\d+)$", i)
        return (mt.group(1), int(mt.group(2)), len(mt.group(2))) if mt else (i, None, 0)

    for i in ids:
        if run and split(run[-1])[0] == split(i)[0] and split(i)[1] is not None and split(run[-1])[1] + 1 == split(i)[1]:
            run.append(i)
            continue
        if run:
            out.append(run[0] if len(run) == 1 else "%s..%s" % (run[0], run[-1]))
        run = [i]
    if run:
        out.append(run[0] if len(run) == 1 else "%s..%s" % (run[0], run[-1]))
    return ", ".join(out) or "none"


def render_table(doc, st):
    rows = ["| ID | State | Evidence or reason |", "| --- | --- | --- |"]
    for i in doc["items"]:
        s = st[i["id"]]
        detail = "; ".join(x for x in (s["evidence"], s["reason"]) if x) or "-"
        rows.append("| %s | %s | %s |" % (i["id"], s["state"], detail.replace("|", "\\|")))
    c = counts(doc, st)
    rows += ["", "Totals: " + ", ".join("%s %d" % (k, c[k]) for k in STATES) + " (%d items)" % len(doc["items"])]
    return "\n".join(rows)


def parse_handoff(text):
    """(rows {id: [states]}, totals {state: n} or None, totals line) from a handoff's Markdown."""
    rows, totals, tline = {}, None, None
    for line in text.replace("\r\n", "\n").split("\n"):
        mt = ROW_RE.match(line)
        if mt and mt.group(2) in STATES:
            rows.setdefault(mt.group(1), []).append(mt.group(2))
        elif TOTALS_RE.match(line):
            totals, tline = {}, line.strip()
            for a, b, c, d in COUNT_RE.findall(line):
                k, n = (a, b) if a else (d, c)
                totals[k] = int(n)
    return rows, totals, tline


def check_handoff(doc, st, text):
    """Problems (list of text) between a handoff and the queue log; empty when they agree."""
    rows, totals, tline = parse_handoff(text)
    out = []
    known = {i["id"] for i in doc["items"]}
    for iid, sts in rows.items():
        if iid not in known:
            out.append("%s is in the handoff but not in the queue" % iid)
        elif len(sts) > 1:
            out.append("%s has %d rows in the handoff (%s)" % (iid, len(sts), ", ".join(sts)))
    for i in doc["items"]:
        iid, have = i["id"], st[i["id"]]["state"]
        if iid not in rows:
            if have in FINAL:
                out.append("%s is %s in the queue log but has no row in the handoff" % (iid, have))
            continue
        said = rows[iid][-1]
        if said != have:
            out.append("%s: the handoff says %s, the queue log says %s%s" % (
                iid, said, have, " (%s)" % st[iid]["reason"] if st[iid]["reason"] else ""))
    if totals is None:
        out.append("the handoff has no `Totals:` line")
    else:
        table = {s: sum(1 for v in rows.values() if v[-1] == s) for s in STATES}
        log_c = counts(doc, st)
        for s in STATES:
            if s in totals and s in FINAL and totals[s] != table[s]:  # todo/doing rows may be left out
                out.append("totals line says %s %d, but the handoff's table has %d %s row(s)" % (s, totals[s], table[s], s))
            if s in totals and totals[s] != log_c[s]:
                out.append("totals line says %s %d, but the queue log has %d" % (s, totals[s], log_c[s]))
        for s in FINAL:
            if s not in totals and table[s]:
                out.append("totals line has no %s count, but the table has %d %s row(s)" % (s, table[s], s))
    return out


def restart_prompt(path, doc, st, evs, command="pai"):
    sm = summary(doc, st, evs)
    q = '"%s"' % path if re.search(r"\s", path) else path
    lines = ["Resume the work queue %s (%s) in this fresh session." % (doc.get("name") or os.path.basename(path), path),
             "Its state is in the queue file and its append-only log, not in any earlier conversation:",
             "  done (%d): %s" % (len(sm["done"]), compact(sm["done"])),
             "  verified prototype (%d): %s" % (len(sm["prototype"]), compact(sm["prototype"]))]
    for b in sm["blocked"]:
        lines.append("  BLOCKED %s: %s (keep it visible; continue with independent items)" % (b["id"], b["reason"]))
    for b in sm["owner_only"]:
        lines.append("  OWNER-ONLY %s: %s" % (b["id"], b["reason"]))
    if sm["doing"]:
        lines.append("  in progress when it stopped: %s (check its evidence before marking it)" % ", ".join(sm["doing"]))
    lines.append("  remaining todo (%d): %s" % (len(sm["todo"]), compact(sm["todo"])))
    if sm["last_checkpoint"]:
        lc = sm["last_checkpoint"]
        lines.append("  last checkpoint: %s at %s%s" % (lc["id"], lc["at"], ", handoff %s (sha256 %s)" % (
            lc["handoff"], (lc["sha256"] or "")[:16]) if lc.get("handoff") else ""))
    if sm["next_checkpoint"]:
        lines.append("  next checkpoint: %s" % sm["next_checkpoint"])
    if sm["next"]:
        lines.append("Next item: %s %s" % (sm["next"]["id"], sm["next"]["title"]))
    else:
        lines.append("No item is left to work on; report the final state.")
    lines += ["Commands:", "  %s queue status %s" % (command, q), "  %s queue mark %s ID STATE --reason ... --evidence ..."
              % (command, q), "  %s queue table %s      (per-ID table and totals for the handoff)" % (command, q),
              "Record each item as done, prototype, blocked or owner-only with its evidence or exact reason."]
    return "\n".join(lines)


# ---------------------------------------------------------------- command line

def add_parser(sub):
    p = sub.add_parser("queue", help="a numbered work queue that survives context limits (local files)",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    qs = p.add_subparsers(dest="qcmd", required=True)
    i = qs.add_parser("init", help="create a queue file from a numbered list (preview unless --write)")
    i.add_argument("file")
    i.add_argument("--from", dest="source", required=True, help="text or Markdown file with lines like `R2-001. Title`")
    i.add_argument("--name", help="the queue's name (default: the file name)")
    i.add_argument("--checkpoint", action="append", default=[], help="checkpoint item id (repeatable)")
    i.add_argument("--write", action="store_true", help="create the file (never overwrites)")
    s = qs.add_parser("status", help="states by item id, blocked items with their reasons, and what is next")
    s.add_argument("file")
    s.add_argument("--json", action="store_true", help="machine-readable (pai_queue_status_v1)")
    n = qs.add_parser("next", help="the next item to work on")
    n.add_argument("file")
    m = qs.add_parser("mark", help="record one item's state (append-only)")
    m.add_argument("file")
    m.add_argument("id")
    m.add_argument("state", choices=[s for s in STATES if s != "todo"])
    m.add_argument("--reason", help="why (required for prototype, blocked, owner-only)")
    m.add_argument("--evidence", help="evidence path or test (required for done and prototype)")
    m.add_argument("--session", default=os.environ.get("PAI_QUEUE_SESSION"), help="session label ($PAI_QUEUE_SESSION)")
    c = qs.add_parser("checkpoint", help="record a checkpoint, with the handoff file's sha256")
    c.add_argument("file")
    c.add_argument("id")
    c.add_argument("--handoff", help="the handoff written for this checkpoint")
    c.add_argument("--session", default=os.environ.get("PAI_QUEUE_SESSION"))
    t = qs.add_parser("table", help="the per-ID table and totals, from the log")
    t.add_argument("file")
    k = qs.add_parser("check", help="compare a handoff's per-ID rows and totals with the queue log")
    k.add_argument("file")
    k.add_argument("handoff")
    ad = qs.add_parser("add", help="append new items to an existing queue (preview unless --write)")
    ad.add_argument("file")
    ad.add_argument("--from", dest="source", required=True, help="text or Markdown file with lines like `R2-101. Title`")
    ad.add_argument("--write", action="store_true")
    r = qs.add_parser("restart", help="a copyable prompt to resume in a fresh session")
    r.add_argument("file")
    return p


def main(a, out=print, err=None):
    err = err or (lambda m: print(m, file=sys.stderr))
    try:
        return _main(a, out, err)
    except QueueError as e:
        err("pai queue %s: %s" % (a.qcmd, e))
        return 2


def _main(a, out, err):
    if a.qcmd == "init":
        try:
            with open(a.source, encoding="utf-8") as f:
                items = parse_list(f.read())
        except (OSError, UnicodeDecodeError) as e:
            raise QueueError("cannot read %s (%s)" % (a.source, getattr(e, "strerror", None) or e))
        if not items:
            raise QueueError("%s has no lines like `ID. Title` (for example `R2-001. Find the seal`)" % a.source)
        doc = {"format": FORMAT, "version": VERSION, "name": a.name or os.path.splitext(os.path.basename(a.file))[0],
               "checkpoints": a.checkpoint, "items": items}
        ids = [i["id"] for i in items]
        bad = [x for x in a.checkpoint if x not in ids]
        if bad:
            raise QueueError("checkpoint(s) %s are not in the list" % ", ".join(bad))
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        out("%s %s: %d item(s) %s .. %s; checkpoints %s; log %s" % (
            "writing" if a.write else "PREVIEW (nothing written):", a.file, len(items), ids[0], ids[-1],
            ", ".join(a.checkpoint) or "none", log_path(a.file)))
        if not a.write:
            out("To create it: the same command with --write (an existing file is never overwritten).")
            return 0
        try:
            fd = os.open(a.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise QueueError("%s exists; nothing was written" % a.file)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        out("created. Next: pai queue next %s" % a.file)
        return 0
    doc = load(a.file)
    evs, problems = events(a.file)
    for p in problems:
        err("note: %s" % p)
    st = state(doc, evs)
    if a.qcmd == "status":
        sm = summary(doc, st, evs)
        if a.json:
            sm["log_problems"] = problems
            out(json.dumps(sm, indent=1, sort_keys=True, ensure_ascii=False))
            return 0
        c = sm["counts"]
        out("queue %s: %d item(s): done %d, prototype %d, blocked %d, owner-only %d, doing %d, todo %d" % (
            sm["name"], sm["items"], c["done"], c["prototype"], c["blocked"], c["owner-only"], c["doing"], c["todo"]))
        out("  done        %s" % compact(sm["done"]))
        out("  prototype   %s" % compact(sm["prototype"]))
        for b in sm["blocked"]:
            out("  BLOCKED     %s: %s" % (b["id"], b["reason"]))
        for b in sm["owner_only"]:
            out("  owner-only  %s: %s" % (b["id"], b["reason"]))
        if sm["doing"]:
            out("  doing       %s" % ", ".join(sm["doing"]))
        out("  todo        %s" % compact(sm["todo"]))
        if sm["last_checkpoint"]:
            out("  checkpoint  %s at %s" % (sm["last_checkpoint"]["id"], sm["last_checkpoint"]["at"]))
        out("next: %s" % ("%s %s" % (sm["next"]["id"], sm["next"]["title"]) if sm["next"] else "nothing left to work on%s"
                          % ("; still blocked: %s" % ", ".join(b["id"] for b in sm["blocked"]) if sm["blocked"] else "")))
        return 0
    if a.qcmd == "next":
        nxt = next_item(doc, st)
        blocked = ids_in(doc, st, "blocked")
        if not nxt:
            out("nothing left to work on%s" % ("; still blocked: %s" % ", ".join(blocked) if blocked else ""))
            return 1
        out("%s %s" % (nxt["id"], nxt["title"]))
        if blocked:
            out("(blocked, skipped: %s)" % ", ".join(blocked))
        return 0
    if a.qcmd == "mark":
        if a.id not in st:
            raise QueueError("%s is not an item of %s" % (a.id, a.file))
        if a.state in NEEDS_REASON and not (a.reason or "").strip():
            raise QueueError("%s needs --reason (the exact dependency, or what is missing); nothing was recorded" % a.state)
        if a.state in NEEDS_EVIDENCE and not (a.evidence or "").strip():
            raise QueueError("%s needs --evidence (a path, test or command); nothing was recorded" % a.state)
        e = append(a.file, {"event": "state", "id": a.id, "state": a.state, "reason": a.reason, "evidence": a.evidence,
                            "at": now(), "session": a.session})
        st[a.id] = {k: e.get(k) for k in ("state", "reason", "evidence", "at", "session")}
        nxt = next_item(doc, st)
        out("%s %s%s; next: %s" % (a.id, a.state, " (%s)" % a.reason if a.reason else "",
                                   nxt["id"] if nxt else "nothing left"))
        return 0
    if a.qcmd == "checkpoint":
        if a.id not in st:
            raise QueueError("%s is not an item of %s" % (a.id, a.file))
        sha = None
        if a.handoff:
            try:
                with open(a.handoff, "rb") as f:
                    raw = f.read()
            except OSError as e:
                raise QueueError("cannot read the handoff %s (%s)" % (a.handoff, e.strerror or e))
            sha = hashlib.sha256(raw).hexdigest()
            probs = check_handoff(doc, st, raw.decode("utf-8", "replace"))
            if probs:
                for p in probs:
                    err("MISMATCH %s" % p)
                raise QueueError("the handoff does not match the queue log (above); no checkpoint was recorded")
        append(a.file, {"event": "checkpoint", "id": a.id, "handoff": a.handoff, "sha256": sha, "counts": counts(doc, st),
                        "done": ids_in(doc, st, "done"), "at": now(), "session": a.session})
        out("checkpoint %s recorded%s" % (a.id, " (handoff sha256 %s)" % sha if sha else ""))
        out("")
        out(restart_prompt(a.file, doc, st, events(a.file)[0]))
        return 0
    if a.qcmd == "table":
        out(render_table(doc, st))
        return 0
    if a.qcmd == "check":
        try:
            with open(a.handoff, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            raise QueueError("cannot read %s (%s)" % (a.handoff, getattr(e, "strerror", None) or e))
        probs = check_handoff(doc, st, text)
        for p in probs:
            out("MISMATCH %s" % p)
        if probs:
            out("%d mismatch(es) between %s and the queue log." % (len(probs), a.handoff))
            return 1
        rows, totals, _ = parse_handoff(text)
        out("ok: %d row(s) and the totals line agree with the queue log (%s)" % (
            len(rows), ", ".join("%s %d" % (k, v) for k, v in sorted(totals.items()))))
        return 0
    if a.qcmd == "add":
        with open(a.source, encoding="utf-8") as f:
            new = parse_list(f.read())
        if not new:
            raise QueueError("%s has no numbered items" % a.source)
        if not a.write:
            doc = load(a.file)
            dup = [i["id"] for i in new if i["id"] in {x["id"] for x in doc["items"]}]
            out("would add %d item(s) to %s%s; add --write" % (len(new), a.file,
                " (already there: %s)" % ", ".join(dup) if dup else ""))
            return 1 if dup else 0
        add_items(a.file, new)
        out("added %d item(s): %s .. %s" % (len(new), new[0]["id"], new[-1]["id"]))
        return 0
    if a.qcmd == "restart":
        out(restart_prompt(a.file, doc, st, evs))
        return 0
    return 2
