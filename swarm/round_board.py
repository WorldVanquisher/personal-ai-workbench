"""The round's Markdown pages in the vault: the board, the mastermind's log and the brief.

  BOARD.md             one row per lane plus the open decisions. Only the block between the
                       pai:round:board markers is generated; anything written outside it is kept.
  COORDINATOR-LOG.md   append-only, one line per decision: UTC time | 决定 | 理由.
  brief                printed (and optionally saved): what a fresh mastermind reads first.

All three live in the round's state folder (<vault>/Agent Inbox/<ROUND>/).
"""
import os
import re
import time

import decide
import round as rnd
import round_status

BEGIN, END = "<!-- pai:round:board:begin -->", "<!-- pai:round:board:end -->"
LOG_NAME, BOARD_NAME, DECISIONS_NAME = "COORDINATOR-LOG.md", "BOARD.md", "DECISIONS.md"


def sheets(r):
    """Decision sheets of the round: DECISIONS.md in the state folder plus ROUND.json's
    `decision_sheets` (absolute, or relative to the vault)."""
    out = [os.path.join(r["state_dir"], DECISIONS_NAME)]
    for p in r.get("decision_sheets") or []:
        out.append(p if os.path.isabs(p) else os.path.join(r["vault"], p))
    return out


def open_decisions(r):
    res = []
    for path in sheets(r):
        try:
            for d in decide.open_items(path):
                res.append(dict(d, sheet=path))
        except decide.DecideError as e:
            res.append({"id": "?", "title": str(e), "text": "", "sha": "", "fields": {}, "sheet": path})
    return res


def cell(s):
    return str(s if s is not None else "-").replace("|", "\\|").replace("\n", " ")


def board_block(r, rows):
    rows_by = {x["lane"]: x for x in rows or []}
    out = [BEGIN, "", "生成于 %s（`pai round status --board` 或 `pai round collect --write` 会刷新这一块；块外的内容不会被改）。"
           % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "",
           "| lane | 会话名 | tmux | 状态 | worktree | checkpoint | queue | 交接目录 |", "|---|---|---|---|---|---|---|---|"]
    for ln in r["lanes"]:
        x = rows_by.get(ln["name"])
        cp = (x or {}).get("checkpoint")
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % tuple(cell(v) for v in (
            ln["name"], ln.get("session_name"), (x or {}).get("window") or ln.get("tmux_target"),
            (x or {}).get("state", "未查"), (x or {}).get("worktree", "未查"),
            (cp["name"] + ("（有 RESTART）" if cp.get("restart") else "")) if cp else "-",
            round_status.totals_text((x or {}).get("queue")) if x else "未查",
            os.path.relpath(ln["handoff_dir"], r["vault"]) if ln.get("handoff_dir") else "-")))
    collected = r.get("_collect") or {}
    if collected:
        out += ["", "### 交接核对（pai round collect）", "", "| lane | checkpoint | SHA256SUMS | JEV | 说明 |", "|---|---|---|---|---|"]
        for name, c in sorted(collected.items()):
            out.append("| %s | %s | %s | %s | %s |" % tuple(cell(v) for v in (
                name, c.get("checkpoint"), c.get("sums"), c.get("jev_verdict"), c.get("note"))))
    dec = open_decisions(r)
    out += ["", "### 待回答的决策（%d）" % len(dec), ""]
    for d in dec:
        out.append("- **%s %s**（sha `%s`，%s）%s%s" % (
            d["id"], d["title"], d["sha"][:12], os.path.relpath(d["sheet"], r["vault"]),
            "：推荐 " + d["fields"]["recommendation"] if d["fields"].get("recommendation") else "",
            "；默认 " + d["fields"]["default"] if d["fields"].get("default") else ""))
    if not dec:
        out.append("（没有）")
    out += ["", "回答：`pai decide answer SHEET ID --sha SHA --choice …`（核对 sha，防止批准的不是看过的那一份）。", "", END]
    return "\n".join(out)


def write_board(r, rows, log, path=None):
    """Rewrite the generated block of BOARD.md (create the page if missing). Returns the path."""
    path = path or os.path.join(r["state_dir"], BOARD_NAME)
    block = board_block(r, rows)
    try:
        with open(path, encoding="utf-8") as f:
            old = f.read()
    except FileNotFoundError:
        old = None
    if old and BEGIN in old and END in old:
        new = old[:old.index(BEGIN)] + block + old[old.index(END) + len(END):]
    elif old:
        new = old.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new = ("---\ntitle: %s %s 看板\nround: %s\nproject: %s\nbase: %s\n---\n\n# %s %s 看板\n\n"
               "ROUND.json：`%s`\n\n%s\n\n## 备注\n\n（这里写的东西不会被覆盖。）\n" % (
                   r["project"].upper(), r["round"], r["round"], r["project"], r["base_sha"], r["project"].upper(),
                   r["round"], os.path.relpath(r.get("_path") or rnd.round_path(r["vault"], r["round"]), r["vault"]), block))
    rnd.write_atomic(path, new)
    log("  board       %s" % path)
    return path


# -- COORDINATOR-LOG.md ----------------------------------------------------------------------
def one_line(s):
    return " ".join(str(s).split()).replace("|", "/")


def daily_log_path(r, day):
    return os.path.join(r["vault"], "PAI", "log", "%s.md" % day)


def write_daily(r, day, entries, log=lambda s: None):
    """PAI/log/<day>.md: a managed note; its front matter `entries` ([time, round, decision, why])
    is the state, the table below repeats it for reading without plugins."""
    import round_vault as rv
    body = ["", rv.BEGIN, "# PAI 日志 %s" % day, "", "mastermind 的决定，一行一条（`pai round log`）。PAI 生成；改了这一页，PAI 就不再往里写，"
            "新的条目只进 COORDINATOR-LOG.md。", "", "| 时间（UTC） | 轮次 | 决定 | 理由 |", "|---|---|---|---|"]
    body += ["| %s | %s | %s | %s |" % tuple(rv.cell(x) for x in e) for e in entries]
    body += [rv.END, ""]
    return rv.write_note(daily_log_path(r, day), "log/1", {"date": day, "entries": entries}, "\n".join(body), log)


def daily_entries(r, day):
    """The entries of PAI/log/<day>.md when it is an unedited PAI note, else None."""
    import round_vault as rv
    path = daily_log_path(r, day)
    if not os.path.isfile(path):
        return []
    state, fm, _ = rv.check(path, "log/1")
    if state != "managed" or not isinstance(fm.get("entries"), list):
        return None
    return [e for e in fm["entries"] if isinstance(e, list) and len(e) == 4]


def log_decision(r, decision, why, at=None, day=None):
    """One decision: a row in PAI/log/<local date>.md (owner decision PRM-D11 = C) and one index
    line in COORDINATOR-LOG.md that points to it."""
    if not decision.strip() or not why.strip():
        raise rnd.RoundError("a log entry needs both the decision and --why")
    at = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    day = day or time.strftime("%Y-%m-%d", time.localtime())
    entry = [at, r["round"], one_line(decision), one_line(why)]
    entries = daily_entries(r, day)
    where = "PAI/log/%s.md" % day
    if entries is None:
        where = "COORDINATOR-LOG.md only (PAI/log/%s.md was edited by hand, so PAI no longer writes it)" % day
    else:
        res = write_daily(r, day, entries + [entry])
        if res.startswith("SKIPPED"):
            where = "COORDINATOR-LOG.md only (PAI/log/%s.md: %s)" % (day, res)
    path = os.path.join(r["state_dir"], LOG_NAME)
    line = "- %s | 决定：%s | 理由：%s | 日志：[[PAI/log/%s]]\n" % (at, entry[2], entry[3], day)
    os.makedirs(r["state_dir"], exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write("# %s 协调日志（索引）\n\n只追加，不改旧行。每行：时间（UTC）| 决定 | 理由 | 当天的日志页 PAI/log/<日期>.md。\n\n"
                    % r["round"])
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return "%s: %s (%s)" % (path, line.strip(), where)


def recent_log(r, days=2, n=10):
    """The last `n` entries of the last `days` daily pages; falls back to COORDINATOR-LOG.md."""
    import glob
    folder = os.path.join(r["vault"], "PAI", "log")
    pages = sorted(p for p in glob.glob(os.path.join(glob.escape(folder), "????-??-??.md")))[-days:]
    out = []
    for p in pages:
        ents = daily_entries(r, os.path.basename(p)[:-3]) or []
        out += ["- %s | %s | 决定：%s | 理由：%s" % tuple(e) for e in ents]
    return out[-n:] if out else log_entries(r, n)


def log_entries(r, n=10):
    try:
        with open(os.path.join(r["state_dir"], LOG_NAME), encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f if l.startswith("- ")][-n:]
    except OSError:
        return []


# -- brief -----------------------------------------------------------------------------------
def tokens(text):
    """Rough token estimate: ~4 ASCII characters per token, one per other character (CJK)."""
    ascii_n = sum(1 for c in text if ord(c) < 128)
    return ascii_n // 4 + (len(text) - ascii_n)


def integration_summary(r):
    """A few lines from the round's last INTEGRATION.json (pai round integrate --write), or []."""
    import json
    try:
        with open(os.path.join(r["state_dir"], "INTEGRATION.json"), encoding="utf-8") as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return []
    t = rep.get("test") or {}
    out = ["%s：顺序 %s，%d 个补丁，%d 个冲突，测试 %s。" % (
        rep.get("at"), ",".join(rep.get("order") or []), len(rep.get("patches") or []), len(rep.get("conflicts") or []),
        "退出码 %s" % t.get("exit") if t else "没跑")]
    out += ["- 冲突：%s（%s）" % (c["patch"], "、".join(c.get("files") or [])) for c in (rep.get("conflicts") or [])[:6]]
    import round_integrate as ri
    later = [ln["name"] for ln in r.get("lanes", []) if (ri.last_checkpoint_event(ln) or {}).get("at", "") > (rep.get("at") or "")]
    if rep.get("stale") or later:
        out[0] = "**STALE** " + out[0]
        out += ["- STALE：%s" % x for x in (rep.get("stale") or [])[:4]]
        if later:
            out.append("- STALE：集成之后 %s 又交了 checkpoint，要重跑 pai round integrate" % "、".join(later))
    return out


def next_steps(rows, dec):
    steps = []
    for x in rows:
        cp = x.get("checkpoint") or {}
        if x["state"] == "blocked-on-dialog":
            steps.append("%s：%s在屏幕上，由 owner 回答（%s）" % (
                x["lane"], "信任提示" if x.get("dialog") == "trust" else "Claude Code 弹窗（%s）" % x.get("dialog"),
                x.get("window") or x.get("target")))
        elif x["state"] == "owner-draft":
            steps.append("%s：输入框里有还没发出去的草稿（%d 字），由 owner 发送或清掉" % (x["lane"], x.get("draft_chars", 0)))
        elif x["state"] == "queued":
            steps.append("%s：有排队中的消息，等它处理完" % x["lane"])
        elif x["state"] in ("exited", "no-window"):
            steps.append("%s：%s；`pai round launch %s` 预览重启%s" % (
                x["lane"], x["state"], x["lane"], "，再 `pai round next %s`" % x["lane"] if cp.get("restart") else ""))
        elif x["state"] == "idle" and cp.get("restart"):
            steps.append("%s：空闲且 %s 有 RESTART.md：`pai round next %s` 续跑" % (x["lane"], cp["name"], x["lane"]))
        elif x["state"] == "idle":
            steps.append("%s：空闲，看一眼它的最后输出或交接" % x["lane"])
    if dec:
        steps.append("有 %d 个决策没回答：先请 owner 回答 %s" % (len(dec), ", ".join(d["id"] for d in dec[:6])))
    steps.append("`pai round collect` 核对交接；之后 `pai round brief` 刷新这份接手包")
    return steps


def brief(r, max_tokens=2000, rows=None):
    """(text, estimated tokens). Shrinks (fewer log lines, shorter decisions) to fit max_tokens."""
    rows = round_status.status(r) if rows is None else rows
    dec = open_decisions(r)
    for n_log, long_dec in ((10, True), (10, False), (5, False), (3, False), (0, False)):
        out = ["# 接手包：%s %s" % (r["project"].upper(), r["round"]), "",
               "base `%s`，vault `%s`，ROUND.json `%s`，生成于 %s。" % (
                   r["base_sha"][:12], r["vault"], r.get("_path") or rnd.round_path(r["vault"], r["round"]),
                   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
               "", "## 状态（tmux + /proc + 文件，real run）", "",
               "| lane | 状态 | worktree | checkpoint | queue 下一条 / Totals |", "|---|---|---|---|---|"]
        for x in rows:
            cp = x.get("checkpoint")
            q = x.get("queue") or {}
            out.append("| %s | %s | %s | %s | %s |" % (
                x["lane"], x["state"], x["worktree"], (cp["name"] + ("+R" if cp.get("restart") else "")) if cp else "-",
                "%s；%s" % (q.get("next") or "-", round_status.totals_text(q)) if q else "-"))
        out += ["", "## 待回答的决策（%d）" % len(dec), ""]
        for d in dec:
            out.append("- %s %s（sha %s）%s" % (d["id"], d["title"], d["sha"][:12],
                                               "：推荐 " + d["fields"].get("recommendation", "") if long_dec and
                                               d["fields"].get("recommendation") else ""))
        if not dec:
            out.append("（没有）")
        integ = integration_summary(r)
        if integ:
            out += ["", "## 集成预演（INTEGRATION.md）", ""] + integ
        logs = recent_log(r, 2, n_log) if n_log else []
        out += ["", "## 最近两天的日志（PAI/log，最后 %d 条）" % len(logs), ""] + (logs or ["（没有）"])
        out += ["", "## 下一步", ""] + ["%d. %s" % (i, s) for i, s in enumerate(next_steps(rows, dec), 1)]
        text = "\n".join(out) + "\n"
        n = tokens(text)
        if n <= max_tokens:
            return text, n
    return text, n
