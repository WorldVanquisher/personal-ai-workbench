"""Transfer packets: one stopped task, carried to another HOME, machine or agent, with what the next
session needs and nothing it must not get.

    pai task export NOTE ... --transfer [--out FILE]       preview (nothing written) or write a packet
    pai task export --transfer --run RUN [--lane L ...]    the handed-off lanes of one run
          [--include-logs] [--without check:NAME ...]
    pai task import FILE                                   read-only preview: the task, the source and
                                                           destination capabilities, what is missing here
    pai task import FILE --write                           create the note (refused on a project or
                                                           repository conflict)
    pai run --continue NOTE [--agent claude|codex]          then continue it, as after any continuation

A packet is a task card file (`pai-task-cards` version 1, see cards.py) with one more top-level
object, `transfer` (version 1). The card is the task note as it is, so the goal, the acceptance
criteria and the `## Continuation` section that `pai recover --continue LANE --write` appended
(completed steps, evidence, open decisions, marker) travel unchanged. The `transfer` object
repeats the facts a destination checks, per task:

  card, task (RUN/LANE/NOTE), continuation (the marker id), source {run, lane, agent,
  capabilities}, repository {branch, base, head}, completed [{sha, subject}], decisions [text],
  evidence [{id, kind, ref, sha256, ...}], omitted [{what, why, impact, detail}]

plus `project` {name, roots: the repository's root commits} once. Every reference is relative to
the project: `note:NAME#continuation`, `git:HEAD:HANDOFF.md`, `run:RUN/LANE/check/NAME`; no
absolute path, user name, host or session link is written by pai. Serialization is canonical
(sorted keys, no time of export), so the same state always gives the same bytes.

What leaves by default: the note (title, description, acceptance criteria, non-private fields) and,
for checks recorded by `pai pr check` at the handed-off head, their name, exit code and the
sha256 and size of their output. What never leaves: raw agent transcripts, session links and
credentials (masked by the privacy filter; writing is refused while any remain in the note), the
launcher configuration and permissions, the approved check commands, and the private run fields.
`--include-logs` adds the check output, privacy-filtered. Each archived check output is hashed
again before it is used: a log that changed since `pai pr check` recorded it refuses the export,
with the two hashes and what to do. Private paths and links in the note's own text are reported
and left as written: pai does not rewrite the task.

Import treats the packet as data: it can only create task notes in this project's task folder.
It never writes the project or run configuration, the repository, or agent settings, and the
`transfer` object changes nothing here; `pai run --continue` takes the agent's launch settings,
permissions and checks from this machine's own configuration.
"""
import hashlib
import json
import os
import re
import shutil

import agents
import cards
import continuation
import gitprep
import notes

try:
    from pai import privacy
except ImportError:  # the workbench started without the pai package on sys.path (its own tests)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pai import privacy

VERSION = 1
IMPACTS = ("none", "limits", "blocks")
TASK_KEYS = {"card", "task", "continuation", "source", "repository", "completed", "decisions", "evidence", "omitted",
             "section_sha256"}
PRIVATE_TEXT = [
    ("home folder path", re.compile(r"(?<![\w.])(?:/home/[^/\s`'\")]+|/Users/[^/\s`'\")]+|/root)(?:/[^\s`'\")]*)?")),
    ("Windows user folder", re.compile(r"\b[A-Za-z]:\\+Users\\+[^\\\s`'\"]+[^\s`'\"]*", re.I)),
    ("temporary folder path", re.compile(r"(?<![\w.])(?:/tmp|/var/tmp|/private/var/folders)/[^\s`'\")]+")),
    ("file link", re.compile(r"\bfile://[^\s)>\]]+", re.I)),
    ("local or private network link", re.compile(
        r"\bhttps?://(?:localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|[\w-]+\.(?:local|internal|lan|corp))(?::\d+)?[^\s)>\]]*", re.I)),
]  # session links are not here: they are never allowed out (session_findings), so they are masked, not listed
ALWAYS_OMITTED = [
    {"what": "raw agent transcripts and session records", "why": "never exported", "impact": "none",
     "detail": "the continuation section carries the completed steps, the evidence and the open decisions"},
    {"what": "launcher configuration, agent permissions and approved check commands", "why": "never exported",
     "impact": "none", "detail": "the destination uses its own configuration; a card can name checks, never commands"},
    {"what": "private run fields (worker, worktree, remote, review records)", "why": "private", "impact": "none",
     "detail": "the continuation marker carries the branch, base and head the next session needs"},
]


class TransferError(Exception):
    pass


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def roots(repo):
    """The repository's root commits (its identity across clones), sorted."""
    out = gitprep.out(repo, "rev-list", "--max-parents=0", "--all") if repo else None
    return sorted(set((out or "").split()))


def private_findings(doc):
    """[(card, field, line, kind, text)] for private paths and links in the cards' own text."""
    found = []

    def scan(card, field, values):
        for i, v in enumerate(values, 1):
            for kind, rx in PRIVATE_TEXT:
                for mt in rx.finditer(str(v)):
                    found.append((card, field, i, kind, mt.group(0)))

    for c in doc["cards"]:
        scan(c["name"], "title", [c["title"]])
        scan(c["name"], "description", c["description"])
        scan(c["name"], "acceptance", c["acceptance"])
        for k, v in sorted((c.get("fields") or {}).items()):
            scan(c["name"], "fields.%s" % k, v if isinstance(v, list) else [v])
    return found


def session_findings(doc):
    """[(card, field, line, kind, text)] for session links anywhere in the cards (never allowed out)."""
    found = []
    for c in doc["cards"]:
        for field in ("title", "description", "acceptance"):
            vals = [c[field]] if field == "title" else c[field]
            for i, v in enumerate(vals, 1):
                for kind, rx in privacy.SESSION:
                    for mt in rx.finditer(v):
                        found.append((c["name"], field, i, kind, mt.group(0)))
    return found


def section_of(text, mk):
    """The note's continuation section that ends with marker `mk` (heading through marker line)."""
    lines = text.split("\n")
    end = next((i for i in range(len(lines) - 1, -1, -1) if continuation.MARK in lines[i]
                and continuation.latest(lines[i]) == mk), None)
    if end is None:
        return None
    start = next((i for i in range(end, -1, -1) if lines[i].startswith("## Continuation from ")), None)
    return None if start is None else "\n".join(lines[start:end + 1])


def decisions_in(section):
    out, on = [], False
    for line in section.split("\n"):
        if line.startswith("### "):
            on = line.startswith("### Open decisions")
            continue
        if on and line.startswith("- [ ] "):
            out.append(line[6:].strip())
    return out


def capabilities(name):
    ad = agents.get(name) if name else None
    if not ad:
        return {"backend": name, "known": False}
    c = ad.capabilities()
    return {"backend": name, "known": True, "readiness": c["readiness"]["source"],
            "delivery_evidence": c["transcript"]["kind"], "remote_control": bool(c["launch"]["remote_control_modes"])}


def describe_caps(c):
    if not c.get("known"):
        return "%s (not a backend this pai knows)" % c.get("backend")
    return "%s: readiness from %s; delivery %s" % (
        c["backend"], "its session record" if c["readiness"] == "session-record" else "the screen only",
        "shown from its %s" % c["delivery_evidence"] if c["delivery_evidence"] else "never shown (no transcript)")


# ---------------------------------------------------------------- export

def select(folder, names, run_id, lanes, load_run):
    """[(note name, task, marker)] for the export, or raises TransferError with every reason."""
    problems, chosen = [], []
    if run_id:
        try:
            m = load_run(run_id)
        except Exception as e:  # noqa: BLE001  a missing or unreadable run is reported, not raised further
            raise TransferError("run %s cannot be read here (%s)" % (run_id, e))
        cdir = gitprep.common_dir(m.repo)
        import rc
        cur = rc.current_lanes(cdir, m.run_id) if cdir else {}
        want = lanes or sorted(l for l, (_, rl) in cur.items() if rl.get("handed_off"))
        if not want:
            raise TransferError("run %s has no handed-off lane; first, in that run: pai recover --run %s --continue "
                                "LANE --write" % (run_id, run_id))
        for lane in want:
            if lane not in cur:
                problems.append("lane %s has no task record in run %s" % (lane, run_id))
            elif not cur[lane][1].get("handed_off"):
                problems.append("lane %s of run %s is not handed off (pai recover --run %s --continue %s --write "
                                "first); it is left in its run" % (lane, run_id, run_id, lane))
            else:
                names = list(names or []) + [cur[lane][1]["note"]]
    if problems:
        raise TransferError("; ".join(problems))
    if not names:
        raise TransferError("name the task note(s) to transfer, or --run RUN [--lane LANE ...]")
    for n in sorted(set(names)):
        path = os.path.join(folder, n + ".md")
        if not os.path.isfile(path):
            problems.append("no task note %s in %s" % (n, folder))
            continue
        t = notes.Task(path)
        mk = continuation.latest(t.body)
        if not mk:
            problems.append("%s has no continuation to transfer; in the session that stopped: pai recover --run RUN "
                            "--continue LANE --write" % n)
            continue
        if str(t.meta.get("continued_marker") or "") == mk["id"]:
            problems.append("%s was already continued here (%s, in %s); a packet would start it a second time "
                            "elsewhere" % (n, mk["id"], t.meta.get("continued_in") or "an earlier run"))
            continue
        if lanes and run_id and mk["lane"] not in lanes:
            continue
        chosen.append((n, t, mk))
    runs = sorted({mk["run"] for _, _, mk in chosen})
    if len(runs) > 1:
        problems.append("the notes come from different runs (%s): export one run at a time, so the destination "
                        "continues one consistent set" % ", ".join(runs))
    if problems:
        raise TransferError("; ".join(problems))
    return chosen


def check_evidence(m, lane, head, include_logs, without):
    """(evidence, omitted, refusals) for the checks this HOME recorded at the handed-off head."""
    evidence, omitted, refusals = [], [], []
    try:
        import review
        data = review.Ctx(m, lane).load()
    except Exception:  # noqa: BLE001  no review record: nothing recorded to carry
        data = {}
    if (data.get("snapshot") or {}).get("head") != head:
        if data.get("checks"):
            omitted.append({"what": "checks recorded at another head", "why": "not the handed-off head",
                            "impact": "limits", "detail": "the next session should run `pai pr check` again"})
        return evidence, omitted, refusals
    for c in data.get("checks") or []:
        name, ref = c["name"], "run:%s/%s/check/%s" % (m.run_id, lane, c["name"])
        if "check:%s" % name in without:
            omitted.append({"what": "check %s" % name, "why": "left out with --without check:%s" % name,
                            "impact": "limits", "detail": "the next session does not see this check's result; "
                                                          "it should run it again"})
            continue
        log_path = c.get("log")
        try:
            with open(log_path, "rb") as f:
                raw = f.read()
        except (OSError, TypeError):
            refusals.append("the recorded output of check %s (%s) is gone; pai cannot show the result it recorded. "
                            "Run the checks again (pai pr check --run %s %s) or leave it out with --without check:%s"
                            % (name, ref, m.run_id, lane, name))
            continue
        now_sha = sha(raw)
        if now_sha != c.get("output_sha256"):
            refusals.append("the output of check %s changed after `pai pr check` recorded it (recorded sha256 %s, now "
                            "%s, file %s): pai will not pass on evidence it did not record. Run the checks again "
                            "(pai pr check --run %s %s), or leave this one out with --without check:%s"
                            % (name, (c.get("output_sha256") or "?")[:16], now_sha[:16], os.path.basename(log_path),
                               m.run_id, lane, name))
            continue
        e = {"id": "check:%s" % name, "kind": "check", "ref": ref, "exit": c["exit"], "passed": bool(c["passed"]),
             "sha256": now_sha, "bytes": len(raw), "verified": "sha256 matched the record at export"}
        if include_logs:
            text, removed = privacy.scrub(raw.decode("utf-8", "replace"))
            e["text"] = text.split("\n")
            e["text_sha256"] = sha(text)
            e["removed"] = len(removed)
        else:
            omitted.append({"what": "output of check %s" % name, "why": "check output stays unless --include-logs",
                            "impact": "limits", "detail": "the next session sees the result and the sha256, not the "
                                                          "output"})
        evidence.append(e)
    return evidence, omitted, refusals


def build(proj, folder, names=None, run_id=None, lanes=None, include_logs=False, without=(), out_dir=None,
          load_run=None):
    """(packet document, report) for `pai task export --transfer`. Raises TransferError."""
    without = set(without or ())
    chosen = select(folder, names, run_id, lanes, load_run)
    doc, withheld = cards.export(folder, [n for n, _, _ in chosen], None, False, proj.get("name"), proj.get("repo"),
                                 out_dir)
    repo = proj.get("repo")
    report = {"refusals": [], "omitted_ids": [], "leaves": []}
    tasks = []
    for n, t, mk in chosen:
        section = section_of(t.body, mk) or ""
        ev = [{"id": "continuation", "kind": "continuation", "ref": "note:%s#continuation" % n,
               "sha256": sha(section), "included": "in the card's description"}]
        blob = gitprep.out(repo, "rev-parse", "%s:HANDOFF.md" % mk["head"]) if repo and gitprep.rev(repo, mk["head"]) else None
        if blob:
            ev.append({"id": "agent-handoff", "kind": "agent-handoff", "ref": "git:%s:HANDOFF.md" % mk["head"][:12],
                       "git_blob": blob.strip(), "included": "quoted in the continuation, privacy-filtered"})
        omitted = [dict(o) for o in ALWAYS_OMITTED]
        m = None
        if load_run:
            try:
                m = load_run(mk["run"])
            except Exception:  # noqa: BLE001  the run is not in this HOME (a packet of a packet, a moved note)
                m = None
        if m is not None:
            e2, o2, r2 = check_evidence(m, mk["lane"], mk["head"], include_logs, without)
            ev += e2
            omitted += o2
            report["refusals"] += ["%s: %s" % (n, r) for r in r2]
        else:
            omitted.append({"what": "the run record of %s" % mk["run"], "why": "not in this HOME", "impact": "limits",
                            "detail": "only what the continuation section says is carried (no recorded check output)"})
        here = gitprep.out(repo, "branch", "-r", "--contains", mk["head"]) if repo and gitprep.rev(repo, mk["head"]) else ""
        if not (here or "").strip():
            omitted.append({"what": "the commits up to head %s" % mk["head"][:12], "why": "a packet carries no commits; "
                            "no remote-tracking branch here contains that head", "impact": "blocks",
                            "detail": "the destination needs branch %s at %s before `pai run --continue`: push it "
                                      "yourself, or carry it as a bundle (git bundle create FILE %s..%s)"
                                      % (mk["branch"], mk["head"][:12], mk["base"][:12], mk["branch"])})
        tasks.append({"card": n, "task": mk.get("from") or "%s/%s/%s" % (mk["run"], mk["lane"], n),
                      "continuation": mk["id"], "section_sha256": sha(section),
                      "source": {"run": mk["run"], "lane": mk["lane"], "agent": mk.get("agent"),
                                 "capabilities": capabilities(mk.get("agent"))},
                      "repository": {"branch": mk["branch"], "base": mk["base"], "head": mk["head"]},
                      "completed": [{"sha": c["sha"], "subject": c["subject"]} for c in mk.get("completed") or []],
                      "decisions": decisions_in(section), "evidence": ev,
                      "omitted": sorted(omitted, key=lambda o: (IMPACTS.index(o["impact"]) * -1, o["what"]))})
    doc["transfer"] = {"version": VERSION, "project": {"name": proj.get("name"), "roots": roots(repo)},
                       "tasks": sorted(tasks, key=lambda x: x["card"])}
    report["withheld"] = withheld
    return doc, report


def cmd_export(proj, folder, a, log, load_run):
    out_dir = os.path.dirname(os.path.abspath(a.out)) if a.out else os.getcwd()
    try:
        doc, report = build(proj, folder, a.names, a.run, a.lane, a.include_logs, a.without, out_dir, load_run)
    except (TransferError, cards.CardError) as e:
        log("pai task export --transfer: %s. Nothing was written." % e)
        return 2
    text = cards.dump(doc)
    hits = cards.flagged(doc) + session_findings(doc)
    private = private_findings(doc)
    log("TRANSFER %s: %d task(s) of project %s" % ("(writing %s)" % a.out if a.out else "PREVIEW (nothing written)",
                                                  len(doc["transfer"]["tasks"]), proj.get("name")))
    for tk in doc["transfer"]["tasks"]:
        card = next(c for c in doc["cards"] if c["name"] == tk["card"])
        log("\n  %s   continues %s (%s) at %s on %s" % (tk["card"], tk["task"], tk["source"]["agent"],
                                                        tk["repository"]["head"][:12], tk["repository"]["branch"]))
        log("    leaves   the note: title, %d description line(s), %d acceptance item(s)%s" % (
            len(card["description"]), len(card["acceptance"]),
            ", fields %s" % ", ".join(sorted(card.get("fields") or {})) if card.get("fields") else ""))
        log("    leaves   %d completed step(s), %d open decision(s)" % (len(tk["completed"]), len(tk["decisions"])))
        for e in tk["evidence"]:
            log("    leaves   evidence %-14s %s%s" % (e["id"], e["ref"], "  sha256 %s" % e["sha256"][:16] if e.get("sha256")
                                                      else "  git blob %s" % e["git_blob"][:12] if e.get("git_blob") else ""))
        for o in tk["omitted"]:
            log("    stays    %s (%s) -> %s: %s" % (o["what"], o["why"], {"none": "no effect", "limits": "LIMITS the "
                                                    "next session", "blocks": "BLOCKS the next session until fixed"}[
                o["impact"]], o["detail"]))
    if report["withheld"]:
        log("\n  private front-matter fields left out: %s" % ", ".join(report["withheld"]))
    for card, field, line, kind, _ in hits:
        log("WARNING  card %s, %s line %d: looks like a %s (masked below; never written)" % (card, field, line, kind))
    for card, field, line, kind, s in private:
        log("PRIVATE  card %s, %s line %d: %s %r: left as written (pai does not rewrite the task); edit the note if "
            "it should not leave this machine" % (card, field, line, kind, s[:60]))
    for r in report["refusals"]:
        log("REFUSED  %s" % r)
    if not a.out:
        log("\n" + cards.mask(text, hits).rstrip("\n"))
        log("\nTo write it: the same command with --out FILE.%s" % (
            " Fix the WARNING and REFUSED lines first." if hits or report["refusals"] else ""))
        return 0 if not (hits or report["refusals"]) else 1
    if report["refusals"]:
        log("\npai task export --transfer: nothing was written (REFUSED above).")
        return 1
    if hits:
        log("\npai task export --transfer: nothing was written: credentials or session links are in the note. Remove "
            "them from the note first; a transfer packet never carries them.")
        return 1
    try:
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        log("pai task export --transfer: %s exists; nothing was written (choose another --out)." % a.out)
        return 1
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    log("\nwrote %s (sha256 %s). On the other side: pai task import %s   (a preview; then --write)" % (
        a.out, sha(text), os.path.basename(a.out)))
    return 0


# ---------------------------------------------------------------- import

def validate(doc):
    """The packet's `transfer` object, checked (raises cards.CardError with what to do)."""
    tr = doc.get("transfer")
    if not isinstance(tr, dict):
        raise cards.CardError("`transfer` must be an object")
    v = tr.get("version")
    if v != VERSION:
        raise cards.CardError("unsupported transfer packet version %r: this pai (transfer version %d) cannot read it. "
                              "Nothing was imported. Use the same or a newer pai here (pai --version), or ask for a "
                              "packet made with this version" % (v, VERSION))
    extra = sorted(set(tr) - {"version", "project", "tasks"})
    if extra:
        raise cards.CardError("unknown transfer field(s) %s for version %d" % (", ".join(extra), VERSION))
    pr = tr.get("project")
    if not isinstance(pr, dict) or not isinstance(pr.get("name"), str) or not isinstance(pr.get("roots"), list):
        raise cards.CardError("transfer.project must name the project and its repository roots")
    if not all(isinstance(r, str) and re.fullmatch(r"[0-9a-f]{40,64}", r) for r in pr["roots"]):
        raise cards.CardError("transfer.project.roots must be commit ids")
    names = {c["name"]: c for c in doc["cards"]}
    tasks = tr.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise cards.CardError("transfer.tasks must be a non-empty list")
    for tk in tasks:
        if not isinstance(tk, dict) or set(tk) - TASK_KEYS:
            raise cards.CardError("transfer task: unknown field(s) %s" % ", ".join(sorted(set(tk) - TASK_KEYS)))
        if tk.get("card") not in names:
            raise cards.CardError("transfer task %r has no card in this file" % tk.get("card"))
        repo = tk.get("repository") or {}
        for k in ("branch", "base", "head"):
            if not isinstance(repo.get(k), str):
                raise cards.CardError("transfer task %s: repository.%s is missing" % (tk["card"], k))
        for k in ("base", "head"):
            if not re.fullmatch(r"[0-9a-f]{40,64}", repo[k]):
                raise cards.CardError("transfer task %s: repository.%s is not a commit id" % (tk["card"], k))
        text = "\n".join(names[tk["card"]]["description"])
        mk = continuation.latest(text)
        if not mk or mk["id"] != tk.get("continuation") or mk["head"] != repo["head"] or mk["base"] != repo["base"]:
            raise cards.CardError("card %s does not carry continuation %s that its transfer entry names (the packet "
                                  "was edited after export); ask for a fresh export" % (tk["card"], tk.get("continuation")))
        section = section_of(text, mk) or ""
        if tk.get("section_sha256") != sha(section):
            raise cards.CardError("card %s: its continuation section does not match the sha256 recorded at export (the "
                                  "packet was edited after export); ask for a fresh export" % tk["card"])
        for o in tk.get("omitted") or []:
            if o.get("impact") not in IMPACTS:
                raise cards.CardError("transfer task %s: omitted item with impact %r" % (tk["card"], o.get("impact")))
    return tr


def destination(proj, repo, tk, available=None):
    """(lines, refusals, blockers) describing what this side has for one task."""
    lines, blockers = [], []
    r = tk["repository"]
    for what in ("base", "head"):
        ok = bool(repo and gitprep.rev(repo, r[what]))
        lines.append("    repository  %s %s %s" % (what, r[what][:12], "is here" if ok else "is NOT here"))
        if not ok:
            blockers.append("%s %s of %s is not in %s: bring branch %s here first (git fetch REMOTE %s, or git fetch "
                            "BUNDLE %s), then pai run --continue" % (what, r[what][:12], tk["task"], repo, r["branch"],
                                                                     r["branch"], r["branch"]))
    src = tk["source"].get("capabilities") or {}
    lines.append("    source      %s" % describe_caps(src) if src else "    source      %s" % tk["source"].get("agent"))
    for name in agents.names():
        ad = agents.get(name)
        found = (available or shutil.which)(ad.program) if ad.program else None
        c = capabilities(name)
        diff = []
        if src.get("known") and src.get("readiness") != c["readiness"]:
            diff.append("readiness differs from the source")
        if src.get("known") and src.get("delivery_evidence") != c["delivery_evidence"]:
            diff.append("delivery evidence is its %s, not a %s" % (c["delivery_evidence"], src.get("delivery_evidence")))
        lines.append("    destination %s, %s%s" % (describe_caps(c), "installed here" if found else "NOT installed here",
                                                   " (%s)" % "; ".join(diff) if diff else ""))
    return lines, blockers


def import_report(doc, proj, repo, other_project=False, available=None):
    """(lines, refusals, blockers) for the transfer part of `pai task import`."""
    tr = validate(doc)
    lines, refusals, blockers = [], [], []
    want = tr["project"]
    if want["name"] != proj.get("name") and not other_project:
        refusals.append("this packet was made for project %s, but project %s is selected here. Select that project "
                        "(--project %s), or, if %s is the same repository under another name, add --other-project"
                        % (want["name"], proj.get("name"), want["name"], proj.get("name")))
    have = roots(repo)
    if want["roots"] and have and not set(want["roots"]) & set(have):
        refusals.append("this packet comes from another repository (root commit %s; %s has %s): a task never moves "
                        "between repositories" % (want["roots"][0][:12], repo, ", ".join(h[:12] for h in have[:3])))
    lines.append("TRANSFER packet: %d task(s) from project %s (repository %s)" % (
        len(tr["tasks"]), want["name"], "matches" if want["roots"] and set(want["roots"]) & set(have) else "UNKNOWN or "
                                                                                                       "different"))
    for tk in tr["tasks"]:
        lines.append("  %s   continues %s at %s on %s" % (tk["card"], tk["task"], tk["repository"]["head"][:12],
                                                          tk["repository"]["branch"]))
        dl, bl = destination(proj, repo, tk, available)
        lines += dl
        blockers += bl
        lines.append("    completed   %d step(s): %s" % (len(tk["completed"]), "; ".join(
            "%s %s" % (c["sha"][:12], c["subject"]) for c in tk["completed"]) or "none"))
        for d in tk.get("decisions") or []:
            lines.append("    DECISION    %s" % d)
        for e in tk.get("evidence") or []:
            lines.append("    evidence    %-14s %s%s" % (e["id"], e["ref"], " (%s, exit %s, sha256 %s)" % (
                "passed" if e.get("passed") else "FAILED", e.get("exit"), e["sha256"][:16]) if e["kind"] == "check" else ""))
        for o in tk.get("omitted") or []:
            if o["impact"] != "none":
                lines.append("    left out    %s -> %s: %s" % (o["what"], o["impact"].upper(), o["detail"]))
        lines.append("    next        pai run --continue %s --agent %s   (or another --agent; this machine's own "
                     "launch settings and checks apply)" % (tk["card"], tk["source"].get("agent") or "claude"))
    return lines, refusals, blockers
