"""pai round collect / budget / route.

collect [--jev] [--write]
  For each lane's newest checkpoint folder (<handoff_dir>/cp-*):
    sums       SHA256SUMS checked line by line: ok | MISMATCH (names the file) | missing
    items      HANDOFF.md's `| ID | state |` rows and Totals line against the lane's queue
               (pai queue check rules): ok | DIFFERS | no queue
    evidence   how many Evidence rows carry a 64-hex sha256, and whether a Totals line is there
    jev        (--jev) JEV reads the handoff and answers two questions: does it CLAIM the work is
               done and checked, and does it SHOW evidence (commands with results, paths with
               sha256)? The deterministic checks above stay the ground truth; JEV only flags a
               handoff as DISAGREES when what it says and what it shows do not match:
                 claims >= 0.7 and (evidence < 0.5, or the sums are not ok)  -> DISAGREES
                 otherwise                                                  -> agrees
               Each request's usage is appended to $PAI_ROUND_JEV_USAGE (default
               <work_root>/jev-usage.jsonl). The key is read only from TYPESAFE_API_KEY (or the
               credential store); it is never printed or written.
  --write saves COLLECT.json and refreshes the board.

budget [--remaining USD] [--per-item USD] [--add LANE USD --note TEXT]
  Per lane: claude -p costs (`cost_usd` / `total_cost_usd` lines in <work_dir>/usage.jsonl),
  manual RC session usage (the same file, source rc-manual, added with --add), JEV requests and
  tokens (<work_dir>/jev-usage.jsonl and the round's), items done (queue). With --remaining it
  suggests how many items the next batch can take: remaining / cost per item (measured, else
  --per-item, else 0.5 USD).

route KIND [--text TEXT --jev]
  The model and effort for a kind of work, from [routing] in round.toml (defaults follow the
  mastermind's routing table). --jev adds JEV's tier for TEXT as a suggestion only.
"""
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request

import round as rnd
import round_board
import round_status as rs

CLAIM_EVIDENCE_QUESTIONS = {
    "claims": {
        "type": "noul",
        "instructions": "The handoff says its items are done and that tests or checks were run and passed.",
    },
    "evidence": {
        "type": "noul",
        "instructions": "The handoff shows concrete evidence for its claims: the exact commands that were run together "
                        "with their results or exit codes, or file paths together with sha256 digests. A statement such "
                        "as 'all tests pass' without the command and its output is NOT evidence.",
    },
}
ROUTING_DEFAULTS = {
    "deterministic": {"who": "script or pai command", "model": None, "effort": None},
    "judgement": {"who": "JEV", "model": "jev-latest", "effort": None},
    "research": {"who": "subagent", "model": "haiku", "effort": "low"},
    "feature": {"who": "worker", "model": "sonnet", "effort": "medium"},
    "design": {"who": "mastermind or worker", "model": "opus", "effort": "high"},
    "standalone": {"who": "its own session", "model": "sonnet", "effort": "medium"},
}
SHA64 = re.compile(r"\b[0-9a-f]{64}\b")


# -- JEV with usage --------------------------------------------------------------------------
def usage_path(r):
    return os.environ.get("PAI_ROUND_JEV_USAGE") or os.path.join(r["work_root"], "jev-usage.jsonl")


def jev_ask(state, questions, usage_file, what, timeout=None):
    """(answers, usage dict). One request, never retried; the usage line is appended either way."""
    import jev
    key = jev.api_key()[0]
    if not key:
        raise rnd.RoundError("no JEV key: set TYPESAFE_API_KEY (or `set -a; . ~/.config/pai/jev.env; set +a` first)")
    base = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
    body = {"model": os.environ.get("JEV_MODEL", "jev-latest"), "state": jev._redact(state), "questions": questions}
    req = urllib.request.Request(base + "/v1/systemone", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    t0 = time.time()
    rec = {"at": rnd.now(), "what": what, "endpoint": "mock" if not base.startswith("https://api.typesafe.ai") else "typesafe"}
    try:
        with urllib.request.urlopen(req, timeout=timeout or float(os.environ.get("JEV_TIMEOUT") or 30)) as resp:
            data = json.load(resp)
        rec["http"] = 200
    except urllib.error.HTTPError as e:
        rec.update(http=e.code, error="HTTP %s" % e.code)
        data = None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ValueError) as e:
        rec.update(error=type(e).__name__)
        data = None
    rec["seconds"] = round(time.time() - t0, 3)
    usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else {}
    rec.update({k: v for k, v in usage.items() if isinstance(v, (int, float))})
    os.makedirs(os.path.dirname(usage_file) or ".", exist_ok=True)
    with open(usage_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    if data is None:
        raise rnd.RoundError("JEV request failed: %s" % rec.get("error"))
    return data.get("answers") or {}, usage


def noul(ans, k):
    v = (ans.get(k) or {}).get("noul")
    return float(v) if isinstance(v, (int, float)) else None


# -- collect ---------------------------------------------------------------------------------
def check_sums(folder):
    path = os.path.join(folder, "SHA256SUMS")
    if not os.path.isfile(path):
        return "missing", []
    bad = []
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    for line in lines:
        m = re.match(r"^([0-9a-f]{64})\s+\*?(.+)$", line)
        if not m:
            bad.append("unreadable line")
            continue
        p = os.path.join(folder, m.group(2))
        if os.path.isabs(m.group(2)) or ".." in m.group(2).split("/"):
            bad.append("%s (outside the folder)" % m.group(2))
        elif not os.path.isfile(p):
            bad.append("%s (missing)" % m.group(2))
        elif rnd.sha256_file(p) != m.group(1):
            bad.append(m.group(2))
    return ("ok" if not bad and lines else "MISMATCH" if bad else "empty"), bad


def check_items(handoff_text, work_dir):
    qpath = os.path.join(work_dir or "", "queue")
    if not os.path.isfile(qpath):
        return "no queue", []
    try:
        from pai import workqueue
        doc = workqueue.load(qpath)
        evs, _ = workqueue.events(qpath)
    except Exception as e:  # noqa: BLE001
        return "unreadable queue", [str(e)]
    # Compare with the queue as it was when this handoff was checkpointed (its sha256 in the
    # log); a handoff never checkpointed is compared with the queue as it is now.
    sha = rnd.sha256_bytes(handoff_text.encode("utf-8"))
    cut = next((i for i, e in enumerate(evs) if e.get("event") == "checkpoint" and e.get("sha256") == sha), None)
    upto = evs[:cut + 1] if cut is not None else evs
    if cut is not None and isinstance(evs[cut].get("counts"), dict):
        # items appended later (pai queue add) did not exist at that checkpoint
        n = sum(v for v in evs[cut]["counts"].values() if isinstance(v, int))
        if 0 < n < len(doc["items"]):
            doc = dict(doc, items=doc["items"][:n])
    problems = list(workqueue.check_handoff(doc, workqueue.state(doc, upto), handoff_text) or [])
    label = "ok" if not problems else "DIFFERS"
    if cut is None:
        label += " (vs current queue; handoff not checkpointed)"
    return label, problems[:5]


def collect_lane(r, ln, use_jev):
    cp = rs.latest_checkpoint(ln.get("handoff_dir") or "")
    res = {"lane": ln["name"], "checkpoint": cp["name"] if cp else None}
    if not cp:
        return dict(res, sums="-", items="-", note="no checkpoint yet", jev_verdict="-")
    res["sums"], res["sums_bad"] = check_sums(cp["path"])
    hpath = os.path.join(cp["path"], "HANDOFF.md")
    if not os.path.isfile(hpath):
        return dict(res, items="-", note="no HANDOFF.md", jev_verdict="-")
    with open(hpath, encoding="utf-8", errors="replace") as f:
        text = f.read()
    res["handoff_sha256"] = rnd.sha256_bytes(text.encode("utf-8"))
    res["items"], res["items_problems"] = check_items(text, ln.get("work_dir"))
    res["totals_line"] = bool(re.search(r"^\s*(\*\*)?totals?(\*\*)?\s*:", text, re.I | re.M))
    res["evidence_sha_rows"] = len(SHA64.findall(text))
    notes = []
    if res["sums"] != "ok":
        notes.append("SHA256SUMS %s%s" % (res["sums"], ": " + ", ".join(res["sums_bad"][:3]) if res["sums_bad"] else ""))
    if res["items"].startswith("DIFFERS"):
        notes.append("Items vs queue: " + "; ".join(str(p) for p in res["items_problems"][:2]))
    if not res["totals_line"]:
        notes.append("no Totals line")
    res["jev_verdict"] = "-"
    if use_jev:
        try:
            ans, usage = jev_ask("LANE HANDOFF (%s %s):\n%s" % (r["round"], ln["name"], text[-6000:]),
                                 CLAIM_EVIDENCE_QUESTIONS, usage_path(r), "collect %s %s" % (ln["name"], cp["name"]))
            c, e = noul(ans, "claims"), noul(ans, "evidence")
            res.update(jev_claims=c, jev_evidence=e)
            if c is None or e is None:
                res["jev_verdict"] = "malformed"
            elif c >= 0.7 and (e < 0.5 or res["sums"] != "ok"):
                res["jev_verdict"] = "DISAGREES"
                notes.append("JEV: claims %.2f but evidence %.2f%s" % (c, e, ", sums " + res["sums"] if res["sums"] != "ok" else ""))
            else:
                res["jev_verdict"] = "agrees (claims %.2f, evidence %.2f)" % (c, e)
        except rnd.RoundError as ex:
            res["jev_verdict"] = "error"
            notes.append(str(ex))
    res["note"] = "; ".join(notes) or "ok"
    return res


def collect_main(a, r, out):
    results = [collect_lane(r, ln, a.jev) for ln in r["lanes"]]
    if a.json:
        print(json.dumps({"format": "pai-round-collect", "version": 1, "round": r["round"], "at": rnd.now(),
                          "lanes": results}, indent=1, ensure_ascii=False), file=out)
    else:
        print("pai round collect %s%s" % (r["round"], "" if a.write else "  (add --write to save COLLECT.json and the board)"), file=out)
        for x in results:
            print("  %-10s %-8s sums %-9s items %-9s jev %-12s %s" % (x["lane"], x["checkpoint"] or "-", x["sums"],
                                                                       x.get("items", "-"), str(x["jev_verdict"]).split(" (")[0],
                                                                       x.get("note", "")), file=out)
    if a.write:
        rnd.write_atomic(os.path.join(r["state_dir"], "COLLECT.json"),
                         json.dumps({"format": "pai-round-collect", "version": 1, "round": r["round"], "at": rnd.now(),
                                     "lanes": results}, indent=1, ensure_ascii=False) + "\n")
        r["_collect"] = {x["lane"]: x for x in results}
        round_board.write_board(r, rs.status(r), lambda s: print(s, file=out))
    if getattr(a, "write_vault", False):
        import round_vault
        round_vault.write_vault(r, rs.status(r), {"lanes": results}, lambda s: print(s, file=out))
    return 1 if any(x["sums"] == "MISMATCH" or x["jev_verdict"] == "DISAGREES" for x in results) else 0


# -- budget ----------------------------------------------------------------------------------
def read_jsonl(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    yield d
    except OSError:
        return


def lane_usage(r, ln, jev_usd_per_mtok):
    u = {"lane": ln["name"], "claude_p_usd": 0.0, "rc_usd": 0.0, "jev_requests": 0, "jev_tokens": 0}
    for d in read_jsonl(os.path.join(ln["work_dir"], "usage.jsonl")):
        cost = d.get("total_cost_usd", d.get("cost_usd"))
        if isinstance(cost, (int, float)):
            u["rc_usd" if d.get("source") == "rc-manual" else "claude_p_usd"] += float(cost)
    for d in read_jsonl(os.path.join(ln["work_dir"], "jev-usage.jsonl")):
        u["jev_requests"] += 1
        u["jev_tokens"] += int(d.get("input_tokens") or 0) + int(d.get("output_tokens") or 0)
    u["jev_usd"] = u["jev_tokens"] * jev_usd_per_mtok / 1e6
    u["usd"] = round(u["claude_p_usd"] + u["rc_usd"] + u["jev_usd"], 4)
    q = rs.queue_totals(ln["work_dir"]) or {}
    u["done"] = int(q.get("done") or 0) + int(q.get("prototype") or 0)
    u["todo"] = int(q.get("todo") or 0) + int(q.get("doing") or 0)
    return u


def budget_main(a, r, out, cfg=None):
    cfg = cfg if cfg is not None else rnd.load_config(None)[0]
    price = float((cfg.get("budget") or {}).get("jev_usd_per_mtok", 0.042))
    if getattr(a, "add", None):
        name, usd = a.add
        ln = rnd.lane(r, name)
        rec = {"at": rnd.now(), "source": "rc-manual", "cost_usd": float(usd), "note": a.note or ""}
        os.makedirs(ln["work_dir"], exist_ok=True)
        with open(os.path.join(ln["work_dir"], "usage.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("recorded %.4f USD for %s (rc-manual)" % (float(usd), name), file=out)
    rows = [lane_usage(r, ln, price) for ln in r["lanes"]]
    round_jev = list(read_jsonl(usage_path(r)))
    total = sum(x["usd"] for x in rows)
    done = sum(x["done"] for x in rows)
    per_item = (total / done) if done and total > 0 else (a.per_item or 0.5)
    measured = bool(done and total > 0)
    todo = sum(x["todo"] for x in rows)
    suggest = None
    if a.remaining is not None:
        suggest = max(0, min(todo if todo else 10 ** 6, int(a.remaining // per_item))) if per_item > 0 else todo
    data = {"round": r["round"], "lanes": rows, "round_jev_requests": len(round_jev),
            "round_jev_tokens": sum(int(d.get("input_tokens") or 0) + int(d.get("output_tokens") or 0) for d in round_jev),
            "total_usd": round(total, 4), "items_done": done, "items_left": todo, "usd_per_item": round(per_item, 4),
            "per_item_source": "measured" if measured else ("--per-item" if a.per_item else "default 0.5"),
            "remaining_usd": a.remaining, "suggested_items": suggest}
    if a.json:
        print(json.dumps(data, indent=1), file=out)
        return 0
    print("pai round budget %s" % r["round"], file=out)
    for x in rows:
        print("  %-10s claude -p %.4f  RC %.4f  JEV %d req / %d tok (%.5f)  = %.4f USD  done %d  left %d" % (
            x["lane"], x["claude_p_usd"], x["rc_usd"], x["jev_requests"], x["jev_tokens"], x["jev_usd"], x["usd"],
            x["done"], x["todo"]), file=out)
    print("  round JEV (collect/route): %d request(s), %d token(s)" % (data["round_jev_requests"], data["round_jev_tokens"]), file=out)
    print("  total %.4f USD for %d item(s): %.4f USD per item (%s)" % (total, done, per_item, data["per_item_source"]), file=out)
    if suggest is not None:
        print("  remaining %.2f USD -> next batch: about %d item(s) of the %d left" % (a.remaining, suggest, todo), file=out)
    return 0


# -- routing ---------------------------------------------------------------------------------
def routing(cfg):
    r = dict((k, dict(v)) for k, v in ROUTING_DEFAULTS.items())
    for k, v in ((cfg.get("routing") or {}).get("kinds") or {}).items():
        if not isinstance(v, dict):
            raise rnd.RoundError("[routing.kinds] %s must be a table (model, effort, who)" % k)
        r[k] = dict(r.get(k) or {}, **v)
    return r


def route_main(a, cfg, out):
    table = routing(cfg)
    if a.kind not in table:
        raise rnd.RoundError("unknown kind %r; known: %s (add more under [routing.kinds])" % (a.kind, ", ".join(sorted(table))))
    x = table[a.kind]
    print("%s: %s%s%s" % (a.kind, x.get("who") or "worker", ", model %s" % x["model"] if x.get("model") else "",
                          ", effort %s" % x["effort"] if x.get("effort") else ""), file=out)
    if a.jev:
        if not a.text:
            raise rnd.RoundError("--jev needs --text (the item to judge)")
        import jev
        ans, _ = jev_ask("TITLE: %s\n\n%s" % (a.text[:120], a.text), {"tier": jev.TRIAGE_QUESTIONS["tier"]},
                         os.environ.get("PAI_ROUND_JEV_USAGE") or os.path.join(os.path.expanduser("~"), ".cache", "pai-round-jev-usage.jsonl"),
                         "route %s" % a.kind)
        t = ans.get("tier") or {}
        print("  JEV suggests %s (confidence %s); the table above stays the rule" % (t.get("choice"), t.get("confidence")), file=out)
    return 0
