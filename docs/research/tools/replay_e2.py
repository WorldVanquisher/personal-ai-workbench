#!/usr/bin/env python3
"""Replay recorded E2 replies against the current first-reply contract (R6-PRM-084). No model is run.

    python3 -B replay_e2.py RUNS.jsonl [--jev [--repeat N]] [--out RESULT.json]

RUNS.jsonl is experiment_e2.py's log (one line per run, with the reply text). Each reply is scored again with
pai/prompts/contracts/first-reply.json as it is now; the result keeps the score recorded at run time beside it,
so a contract change shows as old against new per variant. With --jev, the contract's JEV questions are asked too
(JEV only; it never changes PASS/FAIL). Output format pai-replay-e2/1.
"""
import argparse
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
from pai import prompteval  # noqa: E402


def replay(runs, contract, jev=False, repeat=1):
    rows = []
    for r in runs:
        checks = prompteval.run_checks(r.get("reply", ""), contract)
        row = {"id": r["id"], "variant": r["variant"],
               "old": {"score": r.get("score"), "first-ids": (r.get("checks") or {}).get("first-ids")},
               "new": {"score": round(sum(c["ok"] for c in checks) / len(checks), 3),
                       "first-ids": next(c["ok"] for c in checks if c["id"] == "first-ids"),
                       "detail": next(c["detail"] for c in checks if c["id"] == "first-ids")}}
        if jev:
            row["jev"] = prompteval.ask_jev(r.get("reply", ""), contract, repeat).get("mean", {})
        rows.append(row)
    variants = {}
    for v in sorted({r["variant"] for r in rows}):
        vr = [r for r in rows if r["variant"] == v]
        agg = {"n": len(vr)}
        for side in ("old", "new"):
            scores = [r[side]["score"] for r in vr if r[side]["score"] is not None]
            fid = [r[side]["first-ids"] for r in vr if r[side]["first-ids"] is not None]
            agg[side] = {"mean": round(statistics.mean(scores), 3) if scores else None,
                         "sd": round(statistics.pstdev(scores), 3) if len(scores) > 1 else None,
                         "first_ids": round(sum(fid) / len(fid), 3) if fid else None}
        if jev:
            vals = [r["jev"].get("names_ids") for r in vr if r["jev"].get("names_ids") is not None]
            agg["jev_names_ids"] = round(statistics.mean(vals), 3) if vals else None
        variants[v] = agg
    out = {"format": "pai-replay-e2/1", "contract": "%s v%s" % (contract["name"], contract.get("version")),
           "variants": variants, "runs": rows}
    if jev:
        pairs = [(r["new"]["first-ids"], r["jev"].get("names_ids")) for r in rows if r["jev"].get("names_ids") is not None]
        out["jev_agrees_with_first_ids"] = round(sum((s >= 0.5) == ok for ok, s in pairs) / len(pairs), 3) if pairs else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs")
    ap.add_argument("--jev", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    with open(a.runs, encoding="utf-8") as f:
        runs = [json.loads(line) for line in f if line.strip()]
    res = replay(runs, prompteval.load_contract("first-reply"), a.jev, a.repeat)
    if a.out:
        # the folder may not exist yet, as with experiment_e3.py (R6-FIN-019; mastermind's first real replay)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    for v, g in res["variants"].items():
        print("%s n=%d  old mean %s first_ids %s  ->  new mean %s (sd %s) first_ids %s%s" % (
            v, g["n"], g["old"]["mean"], g["old"]["first_ids"], g["new"]["mean"], g["new"]["sd"], g["new"]["first_ids"],
            "  jev names_ids %s" % g["jev_names_ids"] if "jev_names_ids" in g else ""))
    if "jev_agrees_with_first_ids" in res:
        print("JEV agrees with first-ids on %s of runs" % res["jev_agrees_with_first_ids"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
