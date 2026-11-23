#!/usr/bin/env python3
"""Experiment E2 (docs/research/EXPERIMENTS.md): how the first-reply checklist is written (PRM-D8 = A).

For the mastermind, after integration; workers do not run it for real. Variants A (current worker-base §0.3, five
headings), B (A plus a filled example), C (free form). Each run: a fresh scratch repo with the files the prompt names,
one headless `claude -p` (model haiku) that must give only its first reply, scored by the `first-reply` contract
(pai/prompts/contracts/first-reply.json) through pai.prompteval. Order is shuffled with a fixed seed.

    python3 -B experiment_e2.py --dry-run --out DIR          # fake agent, no network
    python3 -B experiment_e2.py --out DIR --runs 8 --budget 5  # real (haiku); resumable: rerun the same command

DIR/runs.jsonl is append-only (one line per finished run: id, variant, seed, cost, checks); a rerun skips ids already
there, so an interrupted experiment continues where it stopped. No new run starts once the recorded cost reaches
--budget. DIR/RESULT.json: per variant n, mean pass rate, SD, first-ids pass share, total cost (pai-experiment-e2/1).
"""
import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
from pai import prompteval  # noqa: E402

TASK = """你是 demo 仓库 R9 轮 docs 线的 worker，线缩写 DOC。先读 README.md、docs/PLAN.md 和 ITEMS.md，然后只写你的第一条回复，不改任何文件，不跑命令以外的操作。
条目在 ITEMS.md 里（R9-DOC-001 到 R9-DOC-006）。决策单在 docs/PLAN.md：D1 已回答 A，D2 没有回答，默认按推荐执行。R9-DOC-005 需要一个不存在的 key；R9-DOC-006 是 owner-only（发布）。R9-DOC-004 依赖 core 线的 R9-COR-002。
"""
VARIANTS = {
    "A": "第一条回复必须有这五个小标题，每个下面至少一行：读过的文件（路径列表）；已有答案的决策；先做的 5 个 ID（全称，按执行顺序）；owner-only 或被阻塞的 ID（每个一句原因）；依赖其他线的地方。",
    "B": "第一条回复必须有这五个小标题，每个下面至少一行：读过的文件（路径列表）；已有答案的决策；先做的 5 个 ID（全称，按执行顺序）；owner-only 或被阻塞的 ID（每个一句原因）；依赖其他线的地方。\n例子：\n**读过的文件**\n- README.md\n**已有答案的决策**\n- D1=A\n**先做的 5 个 ID**：R9-XYZ-001、R9-XYZ-002、R9-XYZ-003、R9-XYZ-004、R9-XYZ-005\n**owner-only 或被阻塞的 ID**\n- R9-XYZ-006：发布由 owner 做\n**依赖其他线的地方**\n- none",
    "C": "第一条回复说说你读了什么、准备先做什么。",
}
FILES = {"README.md": "# demo\n", "docs/PLAN.md": "# Plan\n\nD1 answered: A. D2 open (default: follow the recommendation).\n",
         "ITEMS.md": "".join("R9-DOC-%03d. item %d\n" % (i, i) for i in range(1, 7))}
FAKE = r'''#!/usr/bin/env python3
import json, sys
p = sys.argv[sys.argv.index("-p") + 1]
if "例子" in p or "五个小标题" in p:
    ids = "R9-DOC-001、R9-DOC-002、R9-DOC-003、R9-DOC-004、R9-DOC-005" if "例子" in p else "R9-DOC-001、002、003、004、005"
    r = ("**读过的文件**\n- README.md、docs/PLAN.md、ITEMS.md\n**已有答案的决策**\n- D1=A\n**先做的 5 个 ID**：%s\n"
         "**owner-only 或被阻塞的 ID**\n- R9-DOC-006 owner-only\n**依赖其他线的地方**\n- R9-COR-002" % ids)
else:
    r = "我读了 README 和计划，准备从第一条开始。"
print(json.dumps({"type": "result", "result": r, "total_cost_usd": 0.0}))
'''


def run_one(claude, variant, model, dry, timeout):
    d = tempfile.mkdtemp(prefix="pai-e2-")
    for n, body in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(d, n)) or d, exist_ok=True)
        with open(os.path.join(d, n), "w", encoding="utf-8") as f:
            f.write(body)
    cmd = [claude, "-p", TASK + "\n" + VARIANTS[variant], "--output-format", "json", "--max-turns", "6"]
    if not dry:
        cmd += ["--model", model, "--allowedTools", "Read"]
    p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {}
    return out, p.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--claude", default=os.environ.get("CLAUDE_BIN", "claude"))
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--runs", type=int, default=8, help="runs per variant (pilot: 8)")
    ap.add_argument("--budget", type=float, default=5.0, help="USD; no run starts once this much is recorded")
    ap.add_argument("--seed", type=int, default=20261122)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    log = os.path.join(a.out, "runs.jsonl")
    done = {}
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    done[r["id"]] = r
    claude = a.claude
    if a.dry_run:
        claude = os.path.join(a.out, "fake-agent")
        with open(claude, "w") as f:
            f.write(FAKE)
        os.chmod(claude, 0o755)
    plan = [(v, s) for v in sorted(VARIANTS) for s in range(a.runs)]
    random.Random(a.seed).shuffle(plan)
    contract = prompteval.load_contract("first-reply")
    spent = sum(r.get("cost_usd", 0) for r in done.values())
    for v, s in plan:
        rid = "%s-%02d" % (v, s)
        if rid in done:
            continue
        if spent >= a.budget:
            print("budget $%.2f reached; stopping before %s (rerun later to continue)" % (a.budget, rid))
            break
        out, code = run_one(claude, v, a.model, a.dry_run, a.timeout)
        cost = float(out.get("total_cost_usd") or out.get("cost_usd") or 0.0)
        spent += cost
        checks = prompteval.run_checks(str(out.get("result", "")), contract)
        rec = {"id": rid, "variant": v, "seed": s, "exit": code, "cost_usd": round(cost, 5), "usage": out.get("usage"),
               "score": round(sum(c["ok"] for c in checks) / len(checks), 3),
               "checks": {c["id"]: c["ok"] for c in checks}, "reply": str(out.get("result", ""))[:4000]}
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        done[rid] = rec
        print("%s score %.3f cost $%.4f (total $%.4f)" % (rid, rec["score"], cost, spent))
    summary = {}
    for v in sorted(VARIANTS):
        rs = [r for r in done.values() if r["variant"] == v]
        sc = [r["score"] for r in rs]
        summary[v] = {"n": len(rs), "mean": round(statistics.mean(sc), 3) if sc else None,
                      "sd": round(statistics.stdev(sc), 3) if len(sc) > 1 else None,
                      "first_ids_pass": round(sum(r["checks"].get("first-ids", False) for r in rs) / len(rs), 3) if rs else None}
    res = {"format": "pai-experiment-e2/1", "mode": "dry-run" if a.dry_run else "real", "model": a.model,
           "runs_per_variant": a.runs, "budget_usd": a.budget, "spent_usd": round(spent, 5), "variants": summary,
           "complete": len(done) == len(plan)}
    with open(os.path.join(a.out, "RESULT.json"), "w") as f:
        json.dump(res, f, indent=2)
    print(json.dumps(res["variants"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
