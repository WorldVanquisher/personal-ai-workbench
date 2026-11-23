#!/usr/bin/env python3
"""Experiment E3 (docs/research/EXPERIMENTS.md, R6-PRM-085): can a harder task separate first-reply wordings A and B?

E2 gave A and B both 1.00 on a 6-item task (reported-by-mastermind). E3 uses 14 items in 3 Blocks, one blocked item,
2 owner-only items, one dependency inside the lane, 3 on other lanes (one already met), and three decisions spread
over two files. Variants: A (worker-base §0.3, five headings) and B (A plus a filled example). Each run: a fresh
scratch repo, one headless `claude -p` (model haiku) that gives only its first reply. For the mastermind after
integration; workers run only --dry-run.

    python3 -B experiment_e3.py --dry-run --out DIR                     # fake agent, no network
    python3 -B experiment_e3.py --out DIR --runs 16 --budget 2          # real (haiku); rerun the same command to resume

Score per run = mean of the first-reply contract checks (v3) and four task checks (TASK_CHECKS): the first five
IDs are doable and in dependency order; the blocked and owner-only IDs are named; the unmet cross-lane
dependencies are named; all three decisions are named. DIR/runs.jsonl is append-only (reply text kept, for replay
with replay_e2.py-style tools); DIR/RESULT.json is pai-experiment-e3/1.
"""
import argparse
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
from pai import prompteval  # noqa: E402

ITEMS = {
    1: ("A", "README 安装一节改写", ""), 2: ("A", "API 参考：需要 DEMO_KEY 才能生成示例输出", ""),
    3: ("A", "FAQ 补三条", ""), 4: ("A", "配置页：字段说明", "依赖 R9-DOC-009（先有字段表）"),
    5: ("A", "术语表", ""), 6: ("B", "迁移指南", "依赖 core 线 R9-COR-003"),
    7: ("B", "CLI 帮助页", "依赖 core 线 R9-COR-005"), 8: ("B", "示例目录整理", ""),
    9: ("B", "字段表（从 schema 生成）", ""), 10: ("B", "链接检查", ""),
    11: ("C", "交接模板说明", "依赖 round 线 R9-RND-002"), 12: ("C", "CHANGELOG 草稿", ""),
    13: ("C", "发布到文档站", "owner-only"), 14: ("C", "打版本 tag", "owner-only"),
}
FILES = {
    "README.md": "# demo\n",
    "ITEMS.md": "# R9 docs 线条目\n\n" + "".join(
        "R9-DOC-%03d. [Block %s] %s%s\n" % (i, b, t, "（%s）" % n if n else "") for i, (b, t, n) in ITEMS.items()),
    "docs/PLAN.md": "# Plan\n\nD1 已回答：A。D2 未回答（默认按推荐）。D3 见 DECISIONS.md。\n",
    "DECISIONS.md": "# 决策单\n\n- D3 已回答：B（链接检查只查站内链接）。\n",
    "NOTES-FOR-docs.md": "# 来自 core 线\n\nR9-COR-003 已完成（done，cp-B）。R9-COR-005 还没做。\n",
    "STATUS.md": "环境里没有 DEMO_KEY。\n",
}
TASK = """你是 demo 仓库 R9 轮 docs 线的 worker，线缩写 DOC。先读 README.md、ITEMS.md、docs/PLAN.md、DECISIONS.md、NOTES-FOR-docs.md 和 STATUS.md，然后只写你的第一条回复，不改任何文件。
"""
FIVE = "第一条回复必须有这五个小标题，每个下面至少一行：读过的文件（路径列表）；已有答案的决策；先做的 5 个 ID（全称，按执行顺序）；owner-only 或被阻塞的 ID（每个一句原因）；依赖其他线的地方。"
VARIANTS = {
    "A": FIVE,
    "B": FIVE + "\n例子：\n**读过的文件**\n- README.md\n**已有答案的决策**\n- D1=A\n**先做的 5 个 ID**：R9-XYZ-001、R9-XYZ-002、"
         "R9-XYZ-003、R9-XYZ-004、R9-XYZ-005\n**owner-only 或被阻塞的 ID**\n- R9-XYZ-006：发布由 owner 做\n**依赖其他线的地方**\n- none",
}
NOT_FIRST = {"R9-DOC-002", "R9-DOC-007", "R9-DOC-011", "R9-DOC-013", "R9-DOC-014"}  # blocked, waiting, owner-only
BEFORE = [("R9-DOC-009", "R9-DOC-004")]  # (must come first, then)
ID_RE = r"\bR9-[A-Z]{3}-\d{3}\b"


def task_checks(reply):
    """[{id, ok, detail}] for the E3 task (beyond the first-reply contract)."""
    out = []
    first = re.findall(ID_RE, prompteval.part_text(reply, "先做|first"))[:5]
    bad = [i for i in first if i in NOT_FIRST]
    order = [(a, b) for a, b in BEFORE if b in first and (a not in first or first.index(a) > first.index(b))]
    out.append({"id": "first5-doable", "ok": len(set(first)) == 5 and not bad and not order,
                "detail": "first five %s; not doable %s; order %s" % (first, bad, order)})
    part = prompteval.part_text(reply, "owner-only|阻塞|blocked")
    miss = [i for i in ("R9-DOC-002", "R9-DOC-013", "R9-DOC-014") if i not in part]
    out.append({"id": "blocked-named", "ok": not miss, "detail": "missing %s" % miss})
    part = prompteval.part_text(reply, "依赖|depend|其他线")
    miss = [i for i in ("R9-COR-005", "R9-RND-002") if i not in part]
    out.append({"id": "deps-named", "ok": not miss, "detail": "missing %s" % miss})
    part = prompteval.part_text(reply, "决策|decision")
    miss = [d for d in ("D1", "D2", "D3") if not re.search(r"\b%s\b" % d, part)]
    out.append({"id": "decisions-named", "ok": not miss, "detail": "missing %s" % miss})
    return out


FAKE = r'''#!/usr/bin/env python3
import json, sys
p = sys.argv[sys.argv.index("-p") + 1]
good = "例子" in p
first = ("R9-DOC-001、R9-DOC-003、R9-DOC-005、R9-DOC-009、R9-DOC-004" if good
         else "R9-DOC-001、R9-DOC-002、R9-DOC-003、R9-DOC-004、R9-DOC-005")
r = ("**读过的文件**\n- README.md、ITEMS.md、docs/PLAN.md、DECISIONS.md\n**已有答案的决策**\n- D1=A，D2 按推荐%s\n"
     "**先做的 5 个 ID**：%s\n**owner-only 或被阻塞的 ID**\n- R9-DOC-002：没有 DEMO_KEY\n- R9-DOC-013、R9-DOC-014：owner-only\n"
     "**依赖其他线的地方**\n- R9-DOC-007 等 R9-COR-005%s") % ("，D3=B" if good else "", first, "；R9-DOC-011 等 R9-RND-002" if good else "")
print(json.dumps({"type": "result", "result": r, "total_cost_usd": 0.0}))
'''


def run_one(claude, variant, model, dry, timeout):
    d = tempfile.mkdtemp(prefix="pai-e3-")
    for n, body in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(d, n)) or d, exist_ok=True)
        with open(os.path.join(d, n), "w", encoding="utf-8") as f:
            f.write(body)
    cmd = [*claude, "-p", TASK + "\n" + VARIANTS[variant], "--output-format", "json", "--max-turns", "10"]
    if not dry:
        cmd += ["--model", model, "--allowedTools", "Read"]
    p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1]), p.returncode
    except (ValueError, IndexError):
        return {}, p.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--claude", default=os.environ.get("CLAUDE_BIN", "claude"))
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--runs", type=int, default=16, help="runs per variant (default 16)")
    ap.add_argument("--budget", type=float, default=2.0, help="USD; no run starts once this much is recorded (default 2: the mastermind cap, R6-PRM-092)")
    ap.add_argument("--seed", type=int, default=20261123)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    log = os.path.join(a.out, "runs.jsonl")
    done = {}
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            done = {r["id"]: r for r in (json.loads(x) for x in f if x.strip())}
    claude = [a.claude]
    if a.dry_run:
        fake = os.path.join(a.out, "fake-agent")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(FAKE)
        os.chmod(fake, 0o755)
        claude = [sys.executable, fake]  # through this Python: Windows does not run a script by its #! line
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
        reply = str(out.get("result", ""))
        checks = prompteval.run_checks(reply, contract) + task_checks(reply)
        rec = {"id": rid, "variant": v, "seed": s, "exit": code, "cost_usd": round(cost, 5), "usage": out.get("usage"),
               "score": round(sum(c["ok"] for c in checks) / len(checks), 3),
               "checks": {c["id"]: c["ok"] for c in checks}, "reply": reply[:6000]}
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
                      "per_check": {k: round(sum(r["checks"].get(k, False) for r in rs) / len(rs), 3)
                                    for k in (rs[0]["checks"] if rs else {})}}
    res = {"format": "pai-experiment-e3/1", "mode": "dry-run" if a.dry_run else "real", "model": a.model,
           "runs_per_variant": a.runs, "budget_usd": a.budget, "spent_usd": round(spent, 5), "variants": summary,
           "complete": len(done) == len(plan)}
    with open(os.path.join(a.out, "RESULT.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({v: {"n": g["n"], "mean": g["mean"], "sd": g["sd"]} for v, g in summary.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
