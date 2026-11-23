"""`pai prompt optimize --dry-run`: an offline prototype of the optimize loop (R6-PRM-039).

    pai prompt optimize --dry-run [NAME ...] [--jev] [--budget-requests N] [--min-gain 0.05] [--json]

Only JEV and lint, no agent. JEV returns scores, not text, so candidates come from deterministic rewrite
operators rather than from a model:

  split     a list item that packs several clauses (；/; separated, long) becomes one item per clause
  section   a lint `section` error gets a skeleton heading for the missing part (a TODO for a person)

Per candidate: the old rule and the new rule(s) are scored with the lint clarity question (the same
sample values, so the comparison is fair). A candidate is proposed only if its mean beats the old score
by `--min-gain`; without --jev every candidate is listed as unscored. Output: a unified diff per file
and the score table. Nothing is written: there is no --write by design, because the design (docs/PROMPTS.md
§5) puts every change through a decision sheet and the person's approval. JEV requests stop at
`--budget-requests` (default 60) and are logged to $PAI_JEV_USAGE_LOG with purpose `optimize`.
"""
import difflib
import json
import re

from pai import promptlib

COMMANDS = ("optimize",)
SPLIT_MIN = 60
SKELETON = {
    "start-check": "==== 开工检查 ====\n- TODO（人来写）：开工前必须读的文件，和第一条回复要列的内容。",
    "rules": "==== 规则 ====\n- TODO（人来写）",
    "stop": "==== 停止规则 ====\n- TODO（人来写）：什么时候封存 checkpoint，什么时候可以停。",
    "evidence": "==== 证据标签 ====\n- TODO（人来写）：每个数字带 real run / synthetic / mock endpoint / JEV / self-report 之一。",
    "handoff": "==== 交接和最终回复 ====\n- TODO（人来写）：按 checkpoint.md 写交接。",
}


def split_candidates(p):
    """[(old_line, [new_lines])] for compound list items in the managed text. A lead-in ending in a
    colon stays as the parent item and the clauses become nested items; semicolons inside quotes or
    parentheses are not split points."""
    out = []
    item = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.+)$")
    for line in p.body.split("\n"):
        m = item.match(line)
        if not m or len(m.group(3)) < SPLIT_MIN or "|" in line or m.group(3).startswith("##"):
            continue
        text = m.group(3)
        masked = re.sub(r'"[^"]*"|“[^”]*”|（[^）]*）|\([^)]*\)|`[^`]*`', lambda x: "x" * len(x.group(0)), text)
        cuts = [i for i, ch in enumerate(masked) if ch in "；;"]
        if not cuts:
            continue
        lead = ""
        colon = masked.find("：")
        if 0 < colon < cuts[0]:
            lead, text, cuts = text[:colon + 1], text[colon + 1:], [c - colon - 1 for c in cuts]
        parts, prev = [], 0
        for c in cuts + [len(text)]:
            parts.append(text[prev:c].strip().rstrip("。."))
            prev = c + 1
        parts = [x for x in parts if x]
        if len(parts) < 2 or any(len(x) < 6 for x in parts):
            continue
        ind, bullet = m.group(1), m.group(2)
        if lead:
            new = ["%s%s %s" % (ind, bullet, lead.strip())] + ["%s  - %s。" % (ind, x) for x in parts]
        else:
            new = ["%s%s %s。" % (ind, bullet if i == 0 else ("-" if bullet in "-*+" else bullet), x)
                   for i, x in enumerate(parts)]
        out.append((line, new))
    return out


def section_candidates(p):
    return [(sid, SKELETON[sid]) for lvl, code, msg in promptlib.lint(p) if code == "section"
            for sid, desc, _ in promptlib.SECTIONS.get(p.kind, []) if desc in msg and sid in SKELETON]


class Budget:
    def __init__(self, n):
        self.left = n
        self.used = 0

    def take(self):
        if self.left <= 0:
            return False
        self.left -= 1
        self.used += 1
        return True


def score(text, heading, budget):
    from pai import promptjev
    if not budget.take():
        return None
    try:
        ans = promptjev.ask(promptjev.sample("SECTION: %s\nRULE: %s" % (heading, text)), promptjev.CLEAR_Q, "optimize")
    except promptjev.JevError:
        return None
    return promptjev.noul(ans, "clear")


def propose(p, use_jev, budget, min_gain):
    """(new_body, rows): rows = [{kind, old, new, old_score, new_score, accepted}]."""
    body, rows = p.body, []
    for old, new in split_candidates(p):
        row = {"kind": "split", "old": old.strip(), "new": [x.strip() for x in new], "old_score": None,
               "new_score": None, "accepted": False}
        if use_jev:
            heading = ""
            for line in p.body.split("\n"):
                if promptlib.HEADING_RE.match(line) and not line.lstrip().startswith(("-", "*")):
                    heading = line.strip(" #=—*")
                if line == old:
                    break
            o = score(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", old), heading, budget)
            strip = lambda x: re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", x)  # noqa: E731
            lead = strip(new[0]) if len(new) > 1 and new[1].startswith(new[0][:len(new[0]) - len(new[0].lstrip())] + "  ") else ""
            kids = new[1:] if lead else new
            ns = [score(strip(x), heading + (" > " + lead if lead else ""), budget) for x in kids]
            if o is not None and all(x is not None for x in ns):
                row["old_score"], row["new_score"] = o, round(sum(ns) / len(ns), 3)
                row["accepted"] = row["new_score"] >= o + min_gain
        if row["accepted"]:
            body = body.replace(old, "\n".join(new), 1)
        rows.append(row)
    for sid, skel in section_candidates(p):
        rows.append({"kind": "section", "old": "(missing %s)" % sid, "new": skel.split("\n"), "old_score": None,
                     "new_score": None, "accepted": True})
        body = body.rstrip("\n") + "\n\n" + skel + "\n"
    return body, rows


def add_parsers(ps):
    o = ps.add_parser("optimize", help="offline prototype: propose rule rewrites, scored by JEV, as a diff (never writes)")
    o.add_argument("names", nargs="*", metavar="NAME")
    o.add_argument("--library", help="library folder (default: the packaged library)")
    o.add_argument("--dry-run", action="store_true", required=True, help="required: this prototype never writes")
    o.add_argument("--jev", action="store_true", help="score old and new rules with JEV (network; usage logged)")
    o.add_argument("--budget-requests", type=int, default=60, help="stop asking JEV after N requests (default 60)")
    o.add_argument("--min-gain", type=float, default=0.05, help="propose a split only if it scores this much higher")
    o.add_argument("--json", action="store_true")


def main(a):
    lib = a.library or promptlib.PACKAGED
    files = promptlib.library_files(lib)
    names = a.names or list(files)
    budget = Budget(a.budget_requests)
    report = {}
    for n in names:
        p = promptlib.read(promptlib.resolve(n, lib))
        new, rows = propose(p, a.jev, budget, a.min_gain)
        diff = list(difflib.unified_diff(p.body.split("\n"), new.split("\n"), "%s (library)" % p.name,
                                         "%s (proposed)" % p.name, lineterm="", n=1))
        report[p.name] = {"rows": rows, "diff": diff}
    if a.json:
        print(json.dumps({"format": "pai-prompt-optimize/0", "dry_run": True, "jev_requests": budget.used,
                          "files": report}, ensure_ascii=False, indent=2))
        return 0
    for n, r in report.items():
        print("== %s: %d candidate(s), %d proposed" % (n, len(r["rows"]), sum(x["accepted"] for x in r["rows"])))
        for x in r["rows"]:
            s = "" if x["old_score"] is None else " %.2f -> %.2f" % (x["old_score"], x["new_score"])
            verdict = "PROPOSE" if x["accepted"] else ("keep" if x["old_score"] is not None else "unscored")
            print("  %-8s %-9s%s  %s" % (verdict, x["kind"], s, x["old"][:70]))
        if r["diff"]:
            print("\n".join(r["diff"]))
    print("dry run: nothing written; %d JEV request(s) of %d allowed" % (budget.used, a.budget_requests))
    return 0
