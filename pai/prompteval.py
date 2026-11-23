"""Output contracts and `pai prompt eval` (format `pai-prompt-eval/1`).

A contract (pai/prompts/contracts/NAME.json, format `pai-contract/1`) says what an output that a
prompt asks for must look like, as deterministic checks, plus optional JEV questions:

    pai prompt eval ARTIFACT [ARTIFACT ...] --contract NAME [--jev [--repeat N]] [--json] [--out DIR]

Per artifact: every check PASS/FAIL with a detail, the pass rate, and (with --jev) each JEV
question's score per repeat. With several artifacts: a summary table, the checks that fail most
often, and, for JEV questions that name a check (`maps_to`), how often JEV and the check agree,
with the disagreeing artifacts listed. Exit 0 when every check of every artifact passes, else 1.
JEV never changes PASS/FAIL.

Check types (field `type`):
  section     a heading-like line matches `pattern`
  regex       `pattern` occurs at least `min` times (default 1); `flags` "i" for case-insensitive;
              optional `section` (a heading pattern) limits the search to that section
  absent      `pattern` does not occur (optional `section` as for regex)
  frontmatter the YAML front matter has every key in `keys`
  ids         at least `min` distinct matches of `pattern`
  table_rows  the first table after the heading matching `section` has a row whose first cell
              matches each entry of `rows`
  labels      every data row of that table has a cell matching one of `labels` (column `column`,
              default: any cell)
  totals      the `| ID | state |` table and its `Totals:` line agree (same counts per state)
  full_sha256 every sha/digest mentioned in the section has 64 hex digits (no truncated digests)
  each_line   every list item in `section` matches `pattern` (or `allow`, e.g. a bare none)
  item_blocks every item has `fields`; optional items per lane between `min` and `max`
  decisions   every decision block (a line matching `start`) has every field in `fields`
  ordered_ids in the part under the heading `section` (the heading line's own text included, up to the next
              heading-like line, bold headings too): the first `min` IDs matching `pattern` are distinct and
              written in full (no `R9-DOC-001、002` shorthand); numbered list items count 1, 2, 3 ... in order
"""
import datetime
import hashlib
import json
import os
import re
import sys

from pai import promptlib, workqueue

FORMAT, CONTRACT_FORMAT = "pai-prompt-eval/1", "pai-contract/1"
CONTRACTS = os.path.join(promptlib.PACKAGED, "contracts")
COMMANDS = ("eval",)


def load_contract(name, directory=None):
    path = name if name.endswith(".json") and os.path.isfile(name) else os.path.join(directory or CONTRACTS, name + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except OSError:
        have = sorted(f[:-5] for f in os.listdir(directory or CONTRACTS) if f.endswith(".json"))
        raise promptlib.PromptError("no contract %r (have: %s)" % (name, ", ".join(have)))
    except ValueError as e:
        raise promptlib.PromptError("contract %s is not JSON: %s" % (path, e))
    if c.get("format") != CONTRACT_FORMAT or not isinstance(c.get("checks"), list):
        raise promptlib.PromptError("contract %s is not %s with a checks list" % (path, CONTRACT_FORMAT))
    ids = [k.get("id") for k in c["checks"]]
    if len(set(ids)) != len(ids) or not all(ids):
        raise promptlib.PromptError("contract %s: every check needs a unique id" % path)
    for k in c["checks"]:
        if k.get("type") not in CHECKS:
            raise promptlib.PromptError("contract %s: check %s has unknown type %r" % (path, k["id"], k.get("type")))
    return c


# ------------------------------------------------------------- helpers

def frontmatter(text):
    m = re.match(r"\A﻿?---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        k = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if k:
            out[k.group(1)] = k.group(2).strip()
    return out


def section_text(text, pattern):
    """The text from the first heading-like line matching `pattern` to the next heading of the same
    or a higher level ("" when there is no such heading)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if promptlib.HEADING_RE.match(line) and re.search(pattern, line, re.I):
            level = len(re.match(r"^\s*(?:[-*]\s+)?(#*)", line).group(1)) or 9
            body = []
            for nxt in lines[i + 1:]:
                h = re.match(r"^(#{1,6})\s", nxt)
                if h and len(h.group(1)) <= level:
                    break
                body.append(nxt)
            return "\n".join(body)
    return ""


def table(text):
    """[[cells]] of the first Markdown table in `text` (header and separator rows dropped)."""
    rows, started = [], False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not started:
                started = True
                continue  # header
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append(cells)
        elif started:
            break
    return rows


def _flags(k):
    return re.I if "i" in k.get("flags", "") else 0


# ------------------------------------------------------------- checks: (ok, detail)

def c_section(text, k):
    found = [line.strip() for line in promptlib.headings(text) if re.search(k["pattern"], line, _flags(k) or re.I)]
    return bool(found), (found[0][:80] if found else "no heading matches /%s/" % k["pattern"])


def _scope(text, k):
    return section_text(text, k["section"]) if k.get("section") else text


def c_regex(text, k):
    if k.get("section") and not _scope(text, k).strip():
        return False, "no /%s/ section" % k["section"]
    n = len(re.findall(k["pattern"], _scope(text, k), _flags(k) | re.M))
    need = k.get("min", 1)
    return n >= need, "%d match(es), need %d" % (n, need)


def c_absent(text, k):
    m = re.search(k["pattern"], _scope(text, k), _flags(k) | re.M)
    return m is None, "found at offset %d" % m.start() if m else "none found"


def c_frontmatter(text, k):
    fm = frontmatter(text)
    if fm is None:
        return False, "no front matter"
    missing = [x for x in k["keys"] if x not in fm]
    return not missing, "missing: %s" % ", ".join(missing) if missing else "all %d keys" % len(k["keys"])


def c_ids(text, k):
    ids = sorted(set(re.findall(k["pattern"], text)))
    need = k.get("min", 1)
    return len(ids) >= need, "%d distinct (%s), need %d" % (len(ids), ", ".join(ids[:6]), need)


def c_table_rows(text, k):
    rows = table(section_text(text, k["section"]))
    if not rows:
        return False, "no table under /%s/" % k["section"]
    missing = [r for r in k["rows"] if not any(re.search(r, row[0], re.I) for row in rows)]
    return not missing, "missing rows: %s" % ", ".join(missing) if missing else "%d rows" % len(rows)


def c_labels(text, k):
    rows = table(section_text(text, k["section"]))
    if not rows:
        return False, "no table under /%s/" % k["section"]
    rx = re.compile("|".join(k["labels"]), re.I)
    col = k.get("column")
    bad = [row[0] for row in rows if not any(rx.search(c) for c in ([row[col]] if col is not None and col < len(row) else row))]
    return not bad, "rows without a label: %s" % ", ".join(bad[:6]) if bad else "%d rows labelled" % len(rows)


def c_totals(text, k):
    rows, totals = {}, None
    for line in text.splitlines():
        m = workqueue.ROW_RE.match(line)
        if m:
            rows[m.group(1)] = m.group(2)
        elif workqueue.TOTALS_RE.match(line):
            totals = {}
            for a, b, c, d in workqueue.COUNT_RE.findall(line):
                totals[a or d] = int(b or c)
    if not rows:
        return False, "no `| ID | state |` rows"
    if totals is None:
        return False, "%d rows but no Totals: line" % len(rows)
    counted = {}
    for s in rows.values():
        counted[s] = counted.get(s, 0) + 1
    bad = ["%s %d≠%d" % (s, totals.get(s, 0), counted.get(s, 0)) for s in sorted(set(totals) | set(counted))
           if totals.get(s, 0) != counted.get(s, 0)]
    return not bad, "Totals disagree with rows: %s" % ", ".join(bad) if bad else "%d rows agree with Totals" % len(rows)


def c_full_sha256(text, k):
    body = section_text(text, k["section"]) if k.get("section") else text
    if not body.strip():
        return False, "no /%s/ section" % k.get("section")
    full = re.findall(r"\b[0-9a-f]{64}\b", body)
    short = [h for h in re.findall(r"(?i)(?:sha256|sha|digest|SHA256SUMS|tar)\S*\s*:?\s*([0-9a-f]{6,63})\b", body)
             if len(h) < 64]
    if short:
        return False, "truncated digest(s): %s" % ", ".join(short[:4])
    if k.get("per_line"):
        bad = [line.strip()[:50] for line in body.split("\n") if line.strip() and not line.strip().lower().lstrip("- ").startswith("none")
               and not re.search(r"\b[0-9a-f]{64}\b", line)]
        if bad:
            return False, "line(s) without a full sha256: %s" % "; ".join(bad[:3])
    return bool(full), "%d full sha256" % len(full) if full else "no sha256 at all"


def c_decisions(text, k):
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if re.search(k["start"], line)]
    if not starts:
        return k.get("min", 0) == 0, "no decision blocks"
    bad = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else min(len(lines), i + 40)
        block = "\n".join(lines[i:end])
        miss = [f for f in k["fields"] if not re.search(f, block, re.I)]
        if miss:
            bad.append("%s: %s" % (lines[i].strip()[:30], "/".join(miss)))
    return not bad, "; ".join(bad[:4]) if bad else "%d decision(s) complete" % len(starts)


def c_item_blocks(text, k):
    """Every item (a line matching `start`; group 1 = lane code) has every field in `fields` within
    its block; with `min`/`max`, each lane has that many items."""
    lines = text.split("\n")
    rx = re.compile(k["start"])
    starts = [(i, rx.search(line)) for i, line in enumerate(lines) if rx.search(line)]
    if not starts:
        return False, "no items"
    bad, per = [], {}
    for n, (i, m) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else min(len(lines), i + 8)
        block = "\n".join(lines[i:end])
        miss = [f for f in k["fields"] if not re.search(f, block, re.I)]
        if miss:
            bad.append("%s: %s" % (m.group(0).strip()[:16], "/".join(miss)))
        g = m.group(1) if m.groups() else "-"
        per[g] = per.get(g, 0) + 1
    lo, hi = k.get("min", 0), k.get("max", 10 ** 9)
    size = ["%s has %d" % (g, c) for g, c in sorted(per.items()) if not lo <= c <= hi]
    detail = "; ".join(bad[:4] + size[:4]) or "%d item(s) in %d lane(s)" % (len(starts), len(per))
    return not bad and not size, detail


def c_each_line(text, k):
    """Every list item in `section` matches `pattern`, unless the section is just `allow` (e.g. none)."""
    body = _scope(text, k)
    if not body.strip():
        return False, "no /%s/ section" % k["section"]
    items = [line.strip() for line in body.split("\n") if re.match(r"^\s*[-*+]\s+", line)]
    if not items:
        plain = [line.strip() for line in body.split("\n") if line.strip()]
        ok = bool(plain) and all(re.search(k.get("allow", "$^"), x, re.I) for x in plain)
        return ok, "no list items" + ("" if ok else "; text is not %r" % k.get("allow"))
    bad = [x[:60] for x in items if not re.search(k["pattern"], x) and not re.search(k.get("allow", "$^"), x, re.I)]
    return not bad, "item(s) not matching: %s" % " | ".join(bad[:3]) if bad else "%d item(s) ok" % len(items)


def part_text(text, pattern):
    """The heading-like line matching `pattern` (its text after the heading, e.g. `**先做的 5 个 ID**：R9-…`)
    plus every line up to the next heading-like line of any kind ("" when no heading matches). Unlike
    section_text, a bold heading also ends the part: replies write their five parts as bold lines."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if promptlib.HEADING_RE.match(line) and re.search(pattern, line, re.I):
            m = re.match(r"^\s*(?:[-*]\s+)?(?:#{1,6}\s+(.*)|\*\*[^*]+\*\*(.*)|(.*))$", line)
            rest = [g for g in m.groups() if g is not None][0] if m else ""
            body = [rest if "**" in line else ""]
            for nxt in lines[i + 1:]:
                if promptlib.HEADING_RE.match(nxt):
                    break
                body.append(nxt)
            return "\n".join(body)
    return ""


def c_ordered_ids(text, k):
    part = part_text(text, k["section"])
    if not part.strip():
        return False, "no /%s/ part" % k["section"]
    short = promptlib.ABBREV_ID_RE.search(part)
    if short:
        return False, "shorthand ID list: %s" % short.group(0)
    need = k.get("min", 5)
    ids = re.findall(k["pattern"], part)
    first = ids[:need]
    if len(first) < need:
        return False, "%d full ID(s) in the part, need %d" % (len(ids), need)
    if len(set(first)) < need:
        return False, "the first %d IDs repeat one: %s" % (need, ", ".join(first))
    nums = [int(n) for n in re.findall(r"^\s*(\d+)[.)、]\s", part, re.M)]
    if nums and nums != list(range(1, len(nums) + 1)):
        return False, "numbered items out of order: %s" % nums
    return True, "in order: %s" % ", ".join(first)


def c_ids_prefix(text, k):
    """Every item ID (`pattern`, group 1 = its prefix) uses the prefix in front matter field `field`."""
    fm = frontmatter(text) or {}
    want = fm.get(k["field"], "").strip()
    if not want:
        return False, "front matter has no %s" % k["field"]
    got = re.findall(k["pattern"], text, re.M)
    bad = sorted({g for g in got if g != want})
    return bool(got) and not bad, ("prefix(es) %s, expected %s" % (", ".join(bad), want)) if bad else "%d ID(s) use %s" % (len(got), want)


CHECKS = {"ordered_ids": c_ordered_ids, "ids_prefix": c_ids_prefix, "each_line": c_each_line, "item_blocks": c_item_blocks, "section": c_section, "regex": c_regex, "absent": c_absent, "frontmatter": c_frontmatter, "ids": c_ids,
          "table_rows": c_table_rows, "labels": c_labels, "totals": c_totals, "full_sha256": c_full_sha256,
          "decisions": c_decisions}


# ------------------------------------------------------------- behaviour metrics (Block H)

LABEL_RE = re.compile(r"real run|synthetic|fake agent|fake|mock|JEV|self-report|reported-by", re.I)
NUMBER_CLAIM_RE = re.compile(r"\b\d+\s*(?:/\s*\d+|tests?\b|OK\b|passed|failed|%|requests?|tokens?|个测试|项)", re.I)


def behaviour(path, text):
    """Deterministic behaviour metrics for a checkpoint HANDOFF.md (and the files beside it):
    stop_ok          1 if the work is finished (Totals todo 0) or the stop is documented (Next names an ID and a RESTART)
    placeholders     unfilled PAI placeholders left in the text (library-declared names)
    handoff_complete share of the v2 sections present, x 1 if the Items table agrees with its Totals line
    labelled_share   share of number claims (test counts, x/y, %, tokens) outside Items/Evidence that carry a source label
    next_pr_exec     share of NEXT-PR.md `git add` paths that are in changed-paths.txt (None without both files)"""
    from pai import promptlib
    m = {}
    tot = re.search(r"^Totals:.*?\btodo\s*(\d+)", text, re.M | re.I) or re.search(r"^Totals:.*?(\d+)\s+todo", text, re.M | re.I)
    todo = int(tot.group(1)) if tot else None
    nxt = section_text(text, r"^#+\s*Next")
    documented = bool(re.search(r"\b[A-Z][A-Z0-9]*-(?:[A-Z0-9]+-)*\d{2,}\b", nxt)) and bool(re.search(r"RESTART|restart", nxt, re.I))
    m["stop_ok"] = 1.0 if todo == 0 or documented else 0.0
    known = promptlib.library_vars()
    m["placeholders"] = len([n for n in promptlib.PLACEHOLDER_RE.findall(text) if n in known])
    secs = ["Summary", "State", "Items", "Evidence", "Decisions within authority", "Decisions for the owner", "Blockers", "Next"]
    present = sum(1 for s in secs if re.search(r"^#+\s*%s" % re.escape(s), text, re.M))
    agree = c_totals(text, {})[0]
    m["handoff_complete"] = round(present / len(secs) * (1.0 if agree else 0.5), 3)
    skip = section_text(text, r"^#+\s*Items") + section_text(text, r"^#+\s*Evidence")
    claims = [line for line in text.split("\n") if NUMBER_CLAIM_RE.search(line) and line not in skip
              and not line.startswith(("Totals", "queue_sha256", "base_sha", "head_sha"))]
    m["labelled_share"] = round(sum(1 for line in claims if LABEL_RE.search(line)) / len(claims), 3) if claims else None
    d = os.path.dirname(os.path.abspath(path))
    npr, cps = os.path.join(d, "NEXT-PR.md"), os.path.join(d, "changed-paths.txt")
    if os.path.isfile(npr) and os.path.isfile(cps):
        with open(npr, encoding="utf-8", errors="replace") as f:
            body = section_text(f.read(), r"^#+\s*Commits")
        with open(cps, encoding="utf-8", errors="replace") as f:
            changed = {line.strip() for line in f if line.strip()}
        adds = re.findall(r"^\s{2,}([\w./-]+\.\w+)\s*$", body, re.M) + re.findall(r"git add\s+([\w./-]+\.\w+)", body)
        m["next_pr_exec"] = round(sum(1 for a in adds if a in changed) / len(adds), 3) if adds else None
    else:
        m["next_pr_exec"] = None
    return m


DEFAULT_THRESHOLDS = {"stop_ok": 1.0, "placeholders_max": 0, "handoff_complete": 1.0, "labelled_share": 0.8,
                      "next_pr_exec": 1.0}


def behaviour_warnings(m, thresholds):
    t = dict(DEFAULT_THRESHOLDS, **(thresholds or {}))
    w = []
    for k in ("stop_ok", "handoff_complete", "labelled_share", "next_pr_exec"):
        if m.get(k) is not None and m[k] < t[k]:
            w.append("%s %.3f < %.3f" % (k, m[k], t[k]))
    if m["placeholders"] > t["placeholders_max"]:
        w.append("placeholders %d > %d" % (m["placeholders"], t["placeholders_max"]))
    return w


# ------------------------------------------------------------- evaluate

def run_checks(text, contract):
    out = []
    for k in contract["checks"]:
        try:
            ok, detail = CHECKS[k["type"]](text, k)
        except (re.error, KeyError, IndexError) as e:
            ok, detail = False, "check error: %s" % e
        out.append({"id": k["id"], "desc": k.get("desc", ""), "ok": bool(ok), "detail": detail})
    return out


def ask_jev(text, contract, repeat=1):
    """{state, answers: {qid: [scores]}, mean: {qid: x}} for the contract's JEV questions. A question
    with `section` is asked about that section only (else the start of the artifact): sending the
    tail of a long handoff hid its State table from JEV (docs/research/PROMPT-BASELINE.md §4)."""
    from pai import promptjev
    qs = contract.get("jev", [])
    if not qs:
        return {"state": "none", "answers": {}, "mean": {}}
    if not promptjev.available():
        return {"state": "skipped", "detail": "no workbench JEV client or no TYPESAFE_API_KEY", "answers": {}, "mean": {}}
    groups = {}
    for q in qs:
        groups.setdefault(q.get("section") or "", []).append(q)
    answers = {q["id"]: [] for q in qs}
    choices = {}
    state = "ok"
    for sec, group in groups.items():
        body = section_text(text, sec) if sec else text
        body = (body if body.strip() else text)[:promptjev.STATE_LIMIT]
        spec = {}
        for q in group:
            spec[q["id"]] = {"type": q.get("type", "noul"), "instructions": q["instructions"]}
            if q.get("criteria"):
                spec[q["id"]]["criteria"] = q["criteria"]
        for _ in range(max(1, repeat)):
            try:
                ans = promptjev.ask(body, spec, "eval:%s" % contract["name"])
            except promptjev.JevError:
                state = "partial"
                continue
            for q in spec:
                if spec[q]["type"] == "choice":
                    a = ans.get(q) or {}
                    choices.setdefault(q, []).append(a.get("choice") if a.get("choice") in spec[q].get("criteria", {}) else None)
                    answers[q].append(a.get("confidence") if isinstance(a.get("confidence"), (int, float)) else None)
                else:
                    answers[q].append(promptjev.noul(ans, q))
    mean = {q: (sum(v for v in s if v is not None) / len([v for v in s if v is not None])
                if any(v is not None for v in s) else None) for q, s in answers.items()}
    if not any(answers.values()):
        state = "error"
    out = {"state": state, "answers": answers, "mean": mean}
    if choices:
        out["choices"] = choices  # for choice questions `answers` holds the confidence of each choice
    return out


def evaluate(path, contract, use_jev=False, repeat=1, use_behaviour=False):
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8", "replace").replace("\r\n", "\n")
    checks = run_checks(text, contract)
    passed = sum(c["ok"] for c in checks)
    r = {"format": FORMAT, "artifact": path, "artifact_sha256": hashlib.sha256(data).hexdigest(),
         "contract": contract["name"], "contract_version": contract.get("version"),
         "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "checks": checks, "passed": passed, "total": len(checks),
         "score": round(passed / len(checks), 3) if checks else None}
    if use_jev:
        r["jev"] = ask_jev(text, contract, repeat)
    if use_behaviour:
        m = behaviour(path, text)
        r["behaviour"] = {"metrics": m, "warnings": behaviour_warnings(m, contract.get("behaviour_thresholds"))}
    return r


def calibrate(results, contract):
    """Agreement between JEV questions with `maps_to` and their checks: {qid: {check, n, agree, rate,
    disagree: [(artifact, jev, check_ok)]}}. JEV says yes when its mean score is >= 0.5."""
    out = {}
    for q in contract.get("jev", []):
        if not q.get("maps_to"):
            continue
        n = agree = 0
        dis = []
        for r in results:
            m = (r.get("jev") or {}).get("mean", {}).get(q["id"])
            ok = [c["ok"] for c in r["checks"] if c["id"] == q["maps_to"]]
            if m is None or not ok:
                continue
            n += 1
            if (m >= 0.5) == ok[0]:
                agree += 1
            else:
                dis.append({"artifact": r["artifact"], "jev": round(m, 2), "check_ok": ok[0]})
        out[q["id"]] = {"check": q["maps_to"], "n": n, "agree": agree,
                        "rate": round(agree / n, 3) if n else None, "disagree": dis}
    return out


def summary(results, contract):
    lines = ["contract %s v%s: %d artifact(s)" % (contract["name"], contract.get("version"), len(results))]
    for r in results:
        j = r.get("jev")
        js = ""
        if j and j["mean"]:
            js = "  jev " + " ".join("%s=%s" % (q, "%.2f" % v if v is not None else "-") for q, v in j["mean"].items())
        elif j:
            js = "  jev %s" % j["state"]
        lines.append("%-4s %2d/%-2d  %s%s" % ("PASS" if r["passed"] == r["total"] else "FAIL", r["passed"], r["total"],
                                             r["artifact"], js))
        for c in r["checks"]:
            if not c["ok"]:
                lines.append("        FAIL %-22s %s" % (c["id"], c["detail"]))
        b = r.get("behaviour")
        if b:
            lines.append("        behaviour " + " ".join("%s=%s" % (k, v) for k, v in b["metrics"].items()))
            for w in b["warnings"]:
                lines.append("        WARN %s" % w)
    if len(results) > 1:
        fails = {}
        for r in results:
            for c in r["checks"]:
                fails[c["id"]] = fails.get(c["id"], 0) + (not c["ok"])
        lines.append("failures by check: " + ", ".join("%s %d/%d" % (k, v, len(results))
                                                       for k, v in sorted(fails.items(), key=lambda x: -x[1]) if v))
        mean = sum(r["score"] for r in results) / len(results)
        lines.append("mean pass rate %.3f" % mean)
    return lines


def add_parsers(ps):
    e = ps.add_parser("eval", help="check outputs against a prompt's contract (PASS/FAIL per check, JEV beside)")
    e.add_argument("artifacts", nargs="+", metavar="ARTIFACT")
    e.add_argument("--contract", required=True, help="contract name (pai/prompts/contracts/NAME.json) or a .json path")
    e.add_argument("--contracts", help="contract folder (default: the packaged one)")
    e.add_argument("--jev", action="store_true", help="also ask the contract's JEV questions (network; usage logged)")
    e.add_argument("--repeat", type=int, default=1, help="ask each JEV question N times (stability)")
    e.add_argument("--behavior", "--behaviour", dest="behaviour", action="store_true",
                   help="also compute deterministic behaviour metrics; below the contract's thresholds is a warning only")
    e.add_argument("--json", action="store_true")
    e.add_argument("--note", help="also write the results as a PAI-managed vault note (e.g. <vault>/PAI/eval/<round>.md); "
                   "refuses to replace a file that is not PAI-managed")
    e.add_argument("--round", default="", help="round id for --note front matter")
    e.add_argument("--out", help="write one pai-prompt-eval/1 JSON per artifact, plus summary.md, into this folder")


NOTE_BEGIN = "<!-- pai:managed:begin pai-eval/1 (generated by `pai prompt eval --note`; edits are replaced) -->"
NOTE_END = "<!-- pai:managed:end -->"


def out_names(paths, contract):
    """One result file name per artifact: `<folder>.<file>.<contract>.json`, with as many parent folders in front as it
    takes to make the names unique (R6-PRM-091: four lanes' `cp-J/HANDOFF.md` used to overwrite each other)."""
    parts = [os.path.abspath(p).split(os.sep)[1:] for p in paths]
    depth = 2
    while True:
        names = [".".join(x[-depth:-1] + [os.path.splitext(x[-1])[0], contract, "json"]) for x in parts]
        if len(set(names)) == len(names) or depth > max(len(x) for x in parts):
            return names
        depth += 1


def write_note(path, results, contract, rnd, lines):
    """A PAI-managed note: front matter Dataview can query, then a plain table (readable without plugins)."""
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            if NOTE_BEGIN.split(" (")[0] not in f.read():
                raise promptlib.PromptError("%s exists and is not a PAI-managed eval note: nothing written" % path)
    mean = sum(r["score"] for r in results) / len(results)
    warn = sum(1 for r in results if (r.get("behaviour") or {}).get("warnings"))
    fm = ["---", "type: pai-eval", "template: pai-eval/1", "round: %s" % (rnd or "unknown"), "contract: %s" % contract["name"],
          "contract_version: %s" % contract.get("version"), "artifacts: %d" % len(results), "mean_pass: %.3f" % mean,
          "all_pass: %d" % sum(r["passed"] == r["total"] for r in results), "behaviour_warnings: %d" % warn,
          "generated: %s" % datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "---", ""]
    rows = ["| artifact | pass | score | behaviour warnings |", "|---|---|---|---|"]
    for r in results:
        rows.append("| %s | %d/%d | %.3f | %s |" % (r["artifact"].replace("|", "/"), r["passed"], r["total"], r["score"],
                                                  "; ".join((r.get("behaviour") or {}).get("warnings", [])) or "-"))
    body = fm + [NOTE_BEGIN, "# Eval: %s / %s" % (rnd or "unknown", contract["name"]), "", "\n".join(rows), "",
                 "```text", "\n".join(lines), "```", NOTE_END, ""]
    promptlib.write_atomic(os.path.abspath(path), "\n".join(body))


def main(a):
    c = load_contract(a.contract, a.contracts)
    results = []
    for p in a.artifacts:
        if not os.path.isfile(p):
            raise promptlib.PromptError("no such artifact: %s" % p)
        results.append(evaluate(p, c, a.jev, a.repeat, getattr(a, "behaviour", False)))
    cal = calibrate(results, c) if a.jev else {}
    lines = summary(results, c)
    for q, v in cal.items():
        lines.append("calibration %s vs %s: %s/%s agree%s" % (q, v["check"], v["agree"], v["n"],
                                                            " (%.2f)" % v["rate"] if v["rate"] is not None else ""))
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        for r, name in zip(results, out_names([r["artifact"] for r in results], c["name"])):
            promptlib.write_atomic(os.path.join(os.path.abspath(a.out), name),
                                   json.dumps(r, ensure_ascii=False, indent=2) + "\n")
        promptlib.write_atomic(os.path.join(os.path.abspath(a.out), "summary-%s.md" % c["name"]),
                               "```text\n" + "\n".join(lines) + "\n```\n")
    if getattr(a, "note", None):
        write_note(a.note, results, c, a.round, lines)
    if a.json:
        print(json.dumps({"format": FORMAT, "contract": c["name"], "results": results, "calibration": cal},
                         ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
    return 0 if all(r["passed"] == r["total"] for r in results) else 1
