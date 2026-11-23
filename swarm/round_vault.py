"""The round's state as plain Markdown notes in the vault (owner decision D9 (a): Obsidian is the
console, the data stays plain Markdown).

    <vault>/PAI/Home.md                         active rounds, lanes needing the owner, open decisions,
                                                unapproved handoffs (Dataview query + plain fallback)
    <vault>/PAI/rounds/<ROUND>/ROUND.md         the round: one row per lane
    <vault>/PAI/rounds/<ROUND>/<lane>.md        one lane: front matter lane, status, current_id, last_cp,
                                                totals, blockers, needs_owner (+ needs_owner_why)
    <vault>/PAI/decisions/<ROUND>-<ID>.md       one decision: status open|answered, options, choice,
                                                default, deadline, blocks, source, source_sha

Rules for every note PAI writes here:
  - the state is the front matter and plain Markdown tables. Dataview blocks are only a view;
    every Dataview block has a plain table next to it, so the note reads the same without plugins;
  - a PAI note carries `pai_managed: <kind>/1` and `pai_sha256` in its front matter and its body
    sits between the pai:managed markers. pai_sha256 covers everything PAI wrote (front matter
    and body, without pai_sha256 itself and without the fields the owner may fill: a decision's
    `choice` and `choice_note`);
  - a note without `pai_managed` is the owner's and is never written (SKIPPED unmanaged);
  - a managed note whose content no longer matches its pai_sha256 was edited by hand: it is kept
    as it is (SKIPPED edited). Delete it to let PAI write it again;
  - broken markers (missing, repeated or out of order) are SKIPPED broken, never repaired;
  - when a sync conflict copy sits next to a note (Syncthing: NAME.sync-conflict-<date>-<time>-<device>.md;
    also "NAME (conflicted copy ...).md"), that note is SKIPPED conflict until the owner resolves it;
  - writing the same state twice changes nothing (the `updated` stamp moves only with the content).
"""
import glob
import hashlib
import json
import os
import re
import time

import decide
import round as rnd

BEGIN, END = "<!-- pai:managed:begin pai-vault/1 -->", "<!-- pai:managed:end -->"
OWNER_FIELDS = {"decision/1": ("choice", "choice_note")}
VOLATILE = ("updated", "pai_sha256")
CONFLICT_RES = (re.compile(r"\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+$"), re.compile(r" \(conflicted copy[^)]*\)$"))


# -- front matter ----------------------------------------------------------------------------
def yaml_value(v):
    """JSON is YAML: strings, numbers, booleans, null, flow lists and maps."""
    return json.dumps(v, ensure_ascii=False)


def parse_value(raw):
    raw = raw.strip()
    if raw == "":
        return ""
    try:
        return json.loads(raw)
    except ValueError:
        if len(raw) >= 2 and raw[0] == raw[-1] == "'":
            return raw[1:-1].replace("''", "'")
        return raw


def split(text):
    """(front matter dict, ordered keys, body) or (None, [], text) when there is none."""
    if not text.startswith("---\n"):
        return None, [], text
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, [], text
    fm, keys = {}, []
    for line in text[4:end].split("\n"):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$", line)
        if m:
            fm[m.group(1)] = parse_value(m.group(2))
            keys.append(m.group(1))
    return fm, keys, text[end + 5:]


def content_sha(fm, body, kind):
    skip = set(VOLATILE) | set(OWNER_FIELDS.get(kind, ()))
    lines = ["%s: %s" % (k, yaml_value(fm[k])) for k in sorted(fm) if k not in skip]
    return hashlib.sha256(("\n".join(lines) + "\n---\n" + body).encode("utf-8")).hexdigest()


def render(fm, body, kind):
    fm = dict(fm, pai_managed=kind)
    fm["pai_sha256"] = content_sha(fm, body, kind)
    order = ["pai_managed"] + [k for k in fm if k not in ("pai_managed", "pai_sha256", "updated")] + ["updated", "pai_sha256"]
    head = "\n".join("%s: %s" % (k, yaml_value(fm[k])) for k in order if k in fm)
    return "---\n%s\n---\n%s" % (head, body)


def markers_ok(body):
    return body.count(BEGIN) == 1 and body.count(END) == 1 and body.index(BEGIN) < body.index(END)


def conflict_copies(path):
    d, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    out = glob.glob(os.path.join(glob.escape(d), glob.escape(stem) + ".sync-conflict-*" + ext))
    out += glob.glob(os.path.join(glob.escape(d), glob.escape(stem) + " (conflicted copy*" + ext))
    return sorted(out)


def is_conflict_copy(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return any(rx.search(stem) for rx in CONFLICT_RES)


def check(path, kind):
    """(state, fm, body) for an existing note: managed | unmanaged | edited | broken | other-kind."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fm, _, body = split(text)
    if not fm or "pai_managed" not in fm:
        return "unmanaged", fm, body
    if fm["pai_managed"] != kind:
        return "other-kind", fm, body
    if not markers_ok(body):
        return "broken", fm, body
    if content_sha(fm, body, kind) != fm.get("pai_sha256"):
        return "edited", fm, body
    return "managed", fm, body


def write_note(path, kind, fm, body, log=lambda s: None, now=None):
    """created | updated | unchanged | SKIPPED <why>. `body` must hold the managed markers."""
    assert markers_ok(body), "a managed note's body needs exactly one pair of markers"
    rel = path
    if is_conflict_copy(path):
        return "SKIPPED conflict copy"
    copies = conflict_copies(path)
    if copies:
        log("  SKIPPED  %s: sync conflict copy %s; resolve it first" % (rel, os.path.basename(copies[0])))
        return "SKIPPED conflict"
    old_text, old_fm = None, None
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isfile(path):
            log("  SKIPPED  %s: not a regular file" % rel)
            return "SKIPPED not a file"
        state, old_fm, _ = check(path, kind)
        old_fm = old_fm or {}  # an owner's note may have no front matter at all
        if state != "managed":
            why = {"unmanaged": "not written by PAI (no pai_managed)", "other-kind": "managed as %s" % old_fm.get("pai_managed"),
                   "broken": "the pai:managed markers are broken", "edited": "edited by hand since PAI wrote it "
                   "(delete it to let PAI write it again)"}[state]
            log("  SKIPPED  %s: %s" % (rel, why))
            return "SKIPPED " + state
        with open(path, encoding="utf-8") as f:
            old_text = f.read()
        for k in OWNER_FIELDS.get(kind, ()):
            if old_fm.get(k) not in (None, ""):
                fm[k] = old_fm[k]
        # unchanged when everything PAI writes is the same (the owner's fields are theirs, as typed)
        if content_sha(dict(fm, pai_managed=kind), body, kind) == old_fm.get("pai_sha256"):
            return "unchanged"
    text = render(dict(fm, updated=now or rnd.now()), body, kind)
    rnd.write_atomic(path, text)
    log("  %-8s %s" % ("updated" if old_text is not None else "created", rel))
    return "updated" if old_text is not None else "created"


# -- what goes into the notes ----------------------------------------------------------------
def lane_file(name):
    """<lane>.md, except for a lane whose name is ROUND in any case: on Windows and macOS (case-
    insensitive file systems, and a vault synced between them) round.md and ROUND.md are one file."""
    return (name + "-lane.md") if name.casefold() == "round" else name + ".md"


def pai_dir(r):
    return os.path.join(r["vault"], "PAI")


def link(r, path, label):
    rel = os.path.relpath(path, r["vault"]).replace(os.sep, "/")
    # every link sits in a table cell, where Obsidian needs the alias pipe escaped
    return "[[%s\\|%s]]" % (rel[:-3] if rel.endswith(".md") else rel, label)


def cell(v):
    return re.sub(r"(?<!\\)\|", r"\\|", str("-" if v is None or v == "" else v)).replace("\n", " ")


def lane_facts(row, col):
    q = row.get("queue") or {}
    cp = row.get("checkpoint") or {}
    why, blockers = [], ["%s: %s" % (b["id"], b.get("reason") or "") for b in q.get("blocked_items") or []]
    if row["state"] == "blocked-on-dialog":
        why.append("folder-trust dialog is waiting for you" if row.get("dialog") == "trust"
                   else "a Claude Code dialog (%s) is waiting for you" % row.get("dialog"))
    if row["state"] == "owner-draft":
        why.append("unsent text in the input box (%d characters)" % row.get("draft_chars", 0))
    if col and str(col.get("jev_verdict", "")).startswith("DISAGREES"):
        why.append("JEV: the handoff's claims and its evidence disagree")
    if col and col.get("sums") == "MISMATCH":
        why.append("SHA256SUMS mismatch in %s" % col.get("checkpoint"))
    if blockers:
        why.append("%d blocked item(s)" % len(blockers))
    if q.get("owner-only"):
        why.append("%d owner-only item(s)" % q["owner-only"])
    if row["state"] in ("exited", "no-window") and q.get("todo"):
        why.append("session %s with %d item(s) left" % (row["state"], q["todo"]))
    totals = {k: q.get(k, 0) for k in ("done", "prototype", "blocked", "owner-only", "doing", "todo")} if q else {}
    current = (q.get("doing_ids") or [None])[0] or q.get("next")
    return {"lane": row["lane"], "status": row["state"], "dialog": row.get("dialog"), "queued": row["state"] == "queued",
            "current_id": current, "last_cp": cp.get("name"),
            "totals": totals, "blockers": blockers, "needs_owner": bool(why), "needs_owner_why": why}


def lane_note(r, ln, row, col):
    f = lane_facts(row, col)
    fm = dict(f, round=r["round"], project=r["project"], branch=ln.get("branch"), worktree_state=row.get("worktree"))
    q = row.get("queue") or {}
    rows = [("状态", "%s（%s）" % (row["state"], row.get("why") or "")), ("需要 owner", "是：" + "；".join(f["needs_owner_why"])
            if f["needs_owner"] else "否"), ("当前条目", f["current_id"]), ("最近 checkpoint", f["last_cp"]),
            ("queue", ", ".join("%s %d" % (k, v) for k, v in f["totals"].items()) if f["totals"] else "-"),
            ("worktree", row.get("worktree")), ("tmux", row.get("window") or ln.get("tmux_target")),
            ("会话名", ln.get("session_name")), ("分支", ln.get("branch")),
            ("交接目录", link(r, ln["handoff_dir"], os.path.basename(ln["handoff_dir"])) if ln.get("handoff_dir") else "-")]
    if col:
        rows.append(("交接核对", "sums %s · items %s · JEV %s" % (col.get("sums"), col.get("items"), col.get("jev_verdict"))))
    body = ["", BEGIN, "# %s · %s" % (r["round"], ln["name"]), "",
            "PAI 生成（`pai round status --write-vault`）。状态在上面的 front matter 里；改了这一页，PAI 就不再覆盖它。", "",
            "| 项 | 值 |", "|---|---|"] + ["| %s | %s |" % (k, cell(v)) for k, v in rows]
    if f["blockers"]:
        body += ["", "## Blockers", ""] + ["- %s" % b for b in f["blockers"]]
    body += [END, ""]
    return fm, "\n".join(body)


def round_note(r, facts):
    fm = {"round": r["round"], "project": r["project"], "status": r.get("status", "open"), "base": r["base_sha"],
          "lanes": len(r["lanes"]), "needs_owner": sum(1 for f in facts if f["needs_owner"]),
          "round_json": r.get("_path")}
    body = ["", BEGIN, "# %s %s" % (r["project"].upper(), r["round"]), "",
            "base `%s` · ROUND.json `%s`" % (r["base_sha"][:12], r.get("_path")), "",
            "| lane | 状态 | 当前条目 | checkpoint | done / todo | 需要 owner |", "|---|---|---|---|---|---|"]
    for f in facts:
        t = f["totals"]
        body.append("| %s | %s | %s | %s | %s | %s |" % (
            link(r, os.path.join(pai_dir(r), "rounds", r["round"], lane_file(f["lane"])), f["lane"]), f["status"],
            cell(f["current_id"]), cell(f["last_cp"]), "%s / %s" % (t.get("done", "-"), t.get("todo", "-")) if t else "-",
            "是：" + "；".join(f["needs_owner_why"]) if f["needs_owner"] else "否"))
    body += [END, ""]
    return fm, "\n".join(body)


def options_of(d):
    raw = d["fields"].get("options") or ""
    return [o.strip() for o in re.split(r"[；;]", raw) if o.strip()]


def decision_notes(r, log):
    """One note per decision of the round's sheets. Returns [(path, result)]."""
    import round_board
    out, seen = [], {}
    for sheet in round_board.sheets(r):
        if not os.path.isfile(sheet) or is_conflict_copy(sheet):
            continue
        for d in decide.parse(decide.read(sheet)[1]):
            seen.setdefault(d["id"], []).append(sheet)
            name = "%s-%s" % (r["round"], d["id"])
            if len(seen[d["id"]]) > 1:
                name += "-" + re.sub(r"[^A-Za-z0-9]+", "-", os.path.splitext(os.path.basename(sheet))[0]).strip("-")[:20] or "x"
            path = os.path.join(pai_dir(r), "decisions", name + ".md")
            fm = {"round": r["round"], "id": d["id"], "status": "answered" if d["answered"] else "open",
                  "question": (d["title"] + ("：" + d["text"] if d["text"] else "")).strip(), "options": options_of(d),
                  "choice": "", "choice_note": "", "recommendation": d["fields"].get("recommendation", ""),
                  "default": d["fields"].get("default", ""), "deadline": d["fields"].get("deadline", ""),
                  "blocks": [x.strip() for x in re.split(r"[、,，;；\s]+", d["fields"].get("blocks", "")) if x.strip()],
                  "answer": d["fields"].get("answer", ""), "source": os.path.relpath(sheet, r["vault"]),
                  "source_sha": d["sha"][:16]}
            body = ["", BEGIN, "# %s %s" % (d["id"], d["title"]), "", fm["question"], "",
                    "| 项 | 内容 |", "|---|---|", "| 选项 | %s |" % cell("；".join(fm["options"])),
                    "| 推荐 | %s |" % cell(fm["recommendation"]), "| 默认做法 | %s |" % cell(fm["default"]),
                    "| 阻塞 | %s |" % cell("、".join(fm["blocks"])), "| 来源 | %s（sha %s） |" % (
                        link(r, sheet, os.path.basename(sheet)), fm["source_sha"][:12]),
                    "| 状态 | %s%s |" % (fm["status"], "：" + fm["answer"] if fm["answer"] else ""), "",
                    "回答：在上面的 front matter 里填 `choice:`（可以再填 `choice_note:`），然后运行",
                    "`pai decide answer --note \"%s\" --write`。PAI 会先核对来源决策单的这一段没有变过（sha %s），再写回去。"
                    % (os.path.relpath(path, r["vault"]), fm["source_sha"][:12]), END, ""]
            out.append((path, write_note(path, "decision/1", fm, "\n".join(body), log)))
    return out


def handoffs_pending(r_list):
    """[(round, lane, checkpoint, path)] of HANDOFF.md files whose front matter says approved: false."""
    out = []
    for r in r_list:
        for ln in r["lanes"]:
            for p in sorted(glob.glob(os.path.join(glob.escape(ln.get("handoff_dir") or "/nonexistent"), "cp-*", "HANDOFF.md"))):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        fm, _, _ = split(f.read(4000))
                except OSError:
                    continue
                if fm and fm.get("approved") in (False, "false"):
                    out.append((r, ln["name"], os.path.basename(os.path.dirname(p)), p))
    return out


def notes_in(folder):
    out = []
    for p in sorted(glob.glob(os.path.join(glob.escape(folder), "*.md"))):
        if is_conflict_copy(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                fm, _, _ = split(f.read())
        except OSError:
            continue
        if fm and fm.get("pai_managed"):
            out.append((p, fm))
    return out


def home_note(r, log):
    base = pai_dir(r)
    rounds = []
    for p in sorted(glob.glob(os.path.join(glob.escape(os.path.join(base, "rounds")), "*", "ROUND.md"))):
        fm = dict(notes_in(os.path.dirname(p))).get(p)
        if fm and fm.get("pai_managed") == "round/1":
            rounds.append((p, fm))
    lanes = [(p, fm) for rp, _ in rounds for p, fm in notes_in(os.path.dirname(rp)) if fm.get("pai_managed") == "lane/1"]
    decisions = notes_in(os.path.join(base, "decisions"))
    rjs = []
    for _, fm in rounds:
        try:
            if fm.get("status") == "open" and fm.get("round_json"):
                rjs.append(rnd.load_round(fm["round_json"]))
        except rnd.RoundError:
            continue
    pending = handoffs_pending(rjs)
    L = lambda p, label: link(r, p, label)  # noqa: E731
    body = ["", BEGIN, "# PAI", "",
            "PAI 生成（`pai round status --write-vault`）。每一块都有 Dataview 查询（装了 Dataview 时显示）和纯文本表格（不装插件也能看，生成时的快照）。状态以笔记的 front matter 为准，查询只是视图。", "",
            "## 进行中的轮次", "", "```dataview",
            'TABLE project, base, lanes, needs_owner AS "需要 owner" FROM "PAI/rounds" WHERE pai_managed = "round/1" AND status = "open"',
            "```", "", "| 轮次 | 项目 | lane | 需要 owner |", "|---|---|---|---|"]
    body += ["| %s | %s | %s | %s |" % (L(p, fm["round"]), fm.get("project"), fm.get("lanes"), fm.get("needs_owner"))
             for p, fm in rounds if fm.get("status") == "open"] or ["| （没有） | | | |"]
    body += ["", "## 需要 owner 处理的 lane", "", "```dataview",
             'TABLE round, status, current_id, needs_owner_why AS "原因" FROM "PAI/rounds" WHERE pai_managed = "lane/1" AND needs_owner = true',
             "```", "", "| lane | 轮次 | 状态 | 原因 |", "|---|---|---|---|"]
    body += ["| %s | %s | %s | %s |" % (L(p, fm.get("lane")), fm.get("round"), fm.get("status"), cell("；".join(fm.get("needs_owner_why") or [])))
             for p, fm in lanes if fm.get("needs_owner") is True] or ["| （没有） | | | |"]
    body += ["", "## 还没回答的决策", "", "```dataview",
             'TABLE round, question, default, deadline FROM "PAI/decisions" WHERE pai_managed = "decision/1" AND status = "open"',
             "```", "", "| 决策 | 轮次 | 问题 | 默认做法 |", "|---|---|---|---|"]
    body += ["| %s | %s | %s | %s |" % (L(p, fm.get("id")), fm.get("round"), cell(fm.get("question")), cell(fm.get("default")))
             for p, fm in decisions if fm.get("pai_managed") == "decision/1" and fm.get("status") == "open"] or ["| （没有） | | | |"]
    body += ["", "## 还没批准的交接", "", "```dataview",
             'TABLE lane, checkpoint, status FROM "Agent Inbox" WHERE approved = false AND checkpoint SORT file.mtime DESC',
             "```", "", "| 交接 | 轮次 | lane | checkpoint |", "|---|---|---|---|"]
    body += ["| %s | %s | %s | %s |" % (L(p, "HANDOFF"), rr["round"], lane, cp) for rr, lane, cp, p in pending] or ["| （没有） | | | |"]
    body += [END, ""]
    fm = {"title": "PAI", "rounds_open": sum(1 for _, fm in rounds if fm.get("status") == "open"),
          "lanes_need_owner": sum(1 for _, fm in lanes if fm.get("needs_owner") is True),
          "decisions_open": sum(1 for _, fm in decisions if fm.get("status") == "open"), "handoffs_unapproved": len(pending)}
    return write_note(os.path.join(base, "Home.md"), "home/1", fm, "\n".join(body), log)


def write_vault(r, rows, collect=None, log=print):
    """Write the round's notes, its decision notes and PAI/Home.md. Returns {path: result}."""
    col = {x["lane"]: x for x in (collect or {}).get("lanes", [])} if collect else {}
    res, facts = {}, []
    folder = os.path.join(pai_dir(r), "rounds", r["round"])
    for ln, row in zip(r["lanes"], rows):
        fm, body = lane_note(r, ln, row, col.get(ln["name"]))
        facts.append(lane_facts(row, col.get(ln["name"])))
        p = os.path.join(folder, lane_file(ln["name"]))
        res[p] = write_note(p, "lane/1", fm, body, log)
    fm, body = round_note(r, facts)
    p = os.path.join(folder, "ROUND.md")
    res[p] = write_note(p, "round/1", fm, body, log)
    for p, result in decision_notes(r, log):
        res[p] = result
    res[os.path.join(pai_dir(r), "Home.md")] = home_note(r, log)
    return res


def last_collect(r):
    p = os.path.join(r["state_dir"], "COLLECT.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# -- answering from a decision note ----------------------------------------------------------
def answer_from_note(path, write=False, log=print):
    """Read `choice` from a PAI decision note and answer the source sheet with the sha the note
    was generated from. Refused (nothing written) when the note's PAI part was edited, the choice
    is empty or not one of the options, or the source decision changed."""
    path = os.path.abspath(path)
    if is_conflict_copy(path) or conflict_copies(path):
        raise decide.DecideError("REFUSED: %s has a sync conflict copy; resolve it first" % path)
    state, fm, body = check(path, "decision/1")
    if state != "managed":
        raise decide.DecideError("MISMATCH: %s is %s; only an unedited PAI decision note (apart from choice and "
                                 "choice_note) can answer" % (path, state))
    choice = str(fm.get("choice") or "").strip()
    if not choice:
        raise decide.DecideError("fill `choice:` in %s first" % path)
    opts = fm.get("options") or []
    if opts and not any(choice == o or o.startswith(choice + " ") or o.startswith(choice + "：") or o.split()[0] == choice
                        for o in opts):
        raise decide.DecideError("choice %r is not one of the options: %s" % (choice, "；".join(opts)))
    if fm.get("status") != "open":
        raise decide.DecideError("REFUSED: %s is already %s" % (fm.get("id"), fm.get("status")))
    vault = rnd.vault_from(path)
    if not vault:
        raise decide.DecideError("cannot find the vault above %s" % path)
    sheet = os.path.join(vault, fm["source"])
    _, d = decide.answer(sheet, fm["id"], fm["source_sha"], choice, fm.get("choice_note") or None, write)
    log("%s %s in %s: %s" % ("answered" if write else "preview (add --write):", fm["id"], fm["source"], choice))
    if write:
        new_fm = {k: v for k, v in fm.items() if k not in ("pai_managed", "pai_sha256", "updated")}
        new_fm.update(status="answered", answer=choice, answered_at=rnd.now())
        new_body = body.replace("| 状态 | open |", "| 状态 | answered：%s |" % cell(choice))
        rnd.write_atomic(path, render(dict(new_fm, updated=rnd.now()), new_body, "decision/1"))
    return d
