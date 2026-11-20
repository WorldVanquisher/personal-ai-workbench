"""Split one goal into worker slices, check the split, and queue the accepted slices.

    pai plan propose --parent ID --goals FILE [--workers N] [--repo PATH] [--out PLAN.json] [--json]
    pai plan check PLAN [--queues DIR] [--json]
    pai plan assign PLAN SLICE WORKER
    pai plan accept PLAN --queues DIR [--slice ID ...] [--write] [--json]

A goals file has one slice per line:
    - api: Add the export endpoint | paths: src/api/, tests/api/ | verify: python -m pytest tests/api | prereqs: none | needs: schema

`propose` reads the goals and who already owns what (each worktree's pai-managed AGENTS.md
scope and lane), keeps the parent task ID, gives each slice a stable item number
(PARENT-001...) and a worker: the owner of its paths when a worktree already owns them, else
the least-loaded worker. It writes nothing but --out. The plan is a plain JSON file: edit it,
or `pai plan assign` a slice to another worker, until `check` is clean.

`check` refuses a slice without verification criteria, scope paths or test prerequisites,
two slices that would write the same paths, a slice whose paths another worktree owns, an
unknown or circular dependency. `accept` (after --write) puts each accepted slice into its
worker's durable queue (`pai queue`, one file per worker in --queues), marks a slice that waits
for an unfinished one as blocked there, and records what it did in PLAN.accepted.json. Running
it again (also after an interruption, or to accept more slices later) adds nothing twice.
"""
import json
import os
import re
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import gitprep  # noqa: E402
from pai import workqueue  # noqa: E402

FORMAT = "pai-split-plan"
PARENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,23}")
WORKER_RE = re.compile(r"[a-z][a-z0-9-]{0,23}")  # both become part of queue file names
SLICE_RE = re.compile(r"^\s*[-*+]\s+([a-z][a-z0-9-]{0,31}):\s*(.+?)\s*$")
SCOPE_RE = re.compile(r"Change only files under: (.+?)\. ")
LANE_RE = re.compile(r"this is lane `([^`]+)`")


class PlanError(Exception):
    pass


def norm_path(p):
    p = p.strip().replace("\\", "/").strip("/")
    return p


def overlaps(a, b):
    a, b = norm_path(a), norm_path(b)
    return a == b or a.startswith(b + "/") or b.startswith(a + "/") or not a or not b


def parse_goals(text):
    slices = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = SLICE_RE.match(line)
        if not m:
            continue
        sid, rest = m.group(1), m.group(2)
        parts = [x.strip() for x in rest.split("|")]
        sl = {"id": sid, "goal": parts[0]}
        for kv in parts[1:]:
            k, sep, v = kv.partition(":")
            k = k.strip().lower()
            if not sep or k not in ("paths", "verify", "prereqs", "needs", "worker"):
                raise PlanError("slice %s: %r is not one of paths/verify/prereqs/needs/worker" % (sid, kv))
            vals = [x.strip() for x in v.split(",") if x.strip()] if k in ("paths", "needs") else v.strip()
            sl[k] = vals
        if any(s["id"] == sid for s in slices):
            raise PlanError("slice %s appears twice" % sid)
        slices.append(sl)
    if not slices:
        raise PlanError("no slice lines (`- id: goal | paths: ... | verify: ...`)")
    return slices


def ownership(repo):
    """{scope path: {lane, worktree, branch}} from each linked worktree's managed AGENTS.md."""
    import refresh
    info = refresh.describe(repo)
    owned = {}
    for w in info["worktrees"]:
        p = os.path.join(w["path"], "AGENTS.md")
        if not w["exists"] or not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        m, lane = SCOPE_RE.search(text), LANE_RE.search(text)
        if not m:
            continue
        for d in re.findall(r"`([^`]+)/`", m.group(1)):
            owned[norm_path(d)] = {"lane": lane.group(1) if lane else None, "worktree": w["path"], "branch": w["branch"]}
    return owned, info["repository"]["path"]


def propose(parent, goals_text, workers=4, repo=None):
    if not PARENT_RE.fullmatch(parent or ""):
        raise PlanError("parent id %r: letters and digits, starting with a letter (it prefixes the queue items)" % parent)
    if not 1 <= workers <= 26:
        raise PlanError("--workers must be 1..26")
    slices = parse_goals(goals_text)
    owned, main = ownership(repo or os.getcwd())
    lanes = list(string.ascii_lowercase[:workers])
    load = {l: 0 for l in lanes}
    for n, sl in enumerate(slices, 1):  # owners first, then the least-loaded worker in order
        sl["item"] = "%s-%03d" % (parent, n)
        owner = next((o for p in sl.get("paths", []) for d, o in owned.items() if overlaps(p, d)), None)  # first path decides
        if sl.get("worker"):
            sl["assigned_by"] = "goals file"
        elif owner and owner["lane"]:
            sl["worker"], sl["assigned_by"] = owner["lane"], "owns %s (%s)" % (owner["worktree"], owner["branch"])
        else:
            continue
        load[sl["worker"]] = load.get(sl["worker"], 0) + 1
    for sl in slices:
        if not sl.get("worker"):
            sl["worker"] = min(lanes, key=lambda l: (load[l], lanes.index(l)))
            sl["assigned_by"] = "least loaded"
            load[sl["worker"]] += 1
    return {"format": FORMAT, "version": 1, "parent": parent, "repository": main, "workers": lanes,
            "ownership": owned, "slices": slices}


def check(plan, queues=None):
    """(problems [{slice, kind, detail}], per-slice queue states)."""
    probs = []
    ids = [s["id"] for s in plan["slices"]]
    for s in plan["slices"]:
        if not s.get("verify"):
            probs.append({"slice": s["id"], "kind": "no-verification", "detail": "no verification criteria (verify: ...)"})
        if not s.get("paths"):
            probs.append({"slice": s["id"], "kind": "no-scope", "detail": "no scope paths (paths: ...)"})
        if "prereqs" not in s:
            probs.append({"slice": s["id"], "kind": "no-prereqs", "detail": "test prerequisites not recorded (prereqs: none, "
                                                                              "or what must exist first)"})
        for d in s.get("needs", []):
            if d not in ids:
                probs.append({"slice": s["id"], "kind": "unknown-dependency", "detail": "needs %s, which is not a slice" % d})
        for d, o in (plan.get("ownership") or {}).items():
            if o.get("lane") and o["lane"] != s.get("worker") and any(overlaps(p, d) for p in s.get("paths", [])):
                probs.append({"slice": s["id"], "kind": "owned-elsewhere",
                              "detail": "%s is owned by lane %s (%s); assign the slice there or change its paths"
                                        % (d, o["lane"], o["worktree"])})
    for i, a in enumerate(plan["slices"]):
        for b in plan["slices"][i + 1:]:
            shared = sorted({max(norm_path(p), norm_path(q), key=len) for p in a.get("paths", [])
                             for q in b.get("paths", []) if overlaps(p, q)})
            if shared:
                probs.append({"slice": "%s+%s" % (a["id"], b["id"]), "kind": "shared-write-paths",
                              "detail": "%s and %s would both write %s" % (a["id"], b["id"], ", ".join(shared))})
    graph = {s["id"]: [d for d in s.get("needs", []) if d in ids] for s in plan["slices"]}
    seen, stack, cycles = set(), [], []

    def visit(n):
        if n in stack:
            cycles.append(stack[stack.index(n):] + [n])
            return
        if n in seen:
            return
        seen.add(n)
        stack.append(n)
        for m in graph[n]:
            visit(m)
        stack.pop()
    for n in graph:
        visit(n)
    for c in cycles:
        probs.append({"slice": c[0], "kind": "dependency-cycle", "detail": " -> ".join(c)})
    states = {}
    if queues:
        for s in plan["slices"]:
            q = os.path.join(queues, "%s-%s.queue" % (plan["parent"], s["worker"]))
            try:
                doc = workqueue.load(q)
                evs, _ = workqueue.events(q)
                states[s["id"]] = workqueue.state(doc, evs).get(s["item"], {}).get("state", "not queued")
            except workqueue.QueueError:
                states[s["id"]] = "not queued"
    return probs, states


def startable(plan, states, queues):
    """Slices that `accept` queued as blocked only because they wait for others ("waits for ..."),
    now that all of those are done: (slice, the command that starts it). A slice blocked for any
    other reason (CI, an owner decision) is never offered."""
    out = []
    for s in plan["slices"]:
        if states.get(s["id"]) != "blocked" or not s.get("needs") or not all(states.get(d) == "done" for d in s["needs"]):
            continue
        q = os.path.join(queues, "%s-%s.queue" % (plan["parent"], s["worker"]))
        try:
            doc = workqueue.load(q)
            evs, _ = workqueue.events(q)
            reason = workqueue.state(doc, evs).get(s["item"], {}).get("reason") or ""
        except workqueue.QueueError:
            continue
        if reason.startswith("waits for "):
            out.append((s["id"], "pai queue mark '%s' %s doing --session NAME" % (q, s["item"])))
    return out


def load(path):
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    if plan.get("format") != FORMAT:
        raise PlanError("%s is not a %s file" % (path, FORMAT))
    if not PARENT_RE.fullmatch(str(plan.get("parent") or "")):
        raise PlanError("%s: parent %r is not an id (letters and digits)" % (path, plan.get("parent")))
    bad = [str(s.get("worker")) for s in plan.get("slices") or [] if not WORKER_RE.fullmatch(str(s.get("worker") or ""))]
    if bad:
        raise PlanError("%s: worker name(s) %s: lower-case letters, digits and -" % (path, ", ".join(bad)))
    return plan


def save(path, plan, new=False):
    data = json.dumps(plan, indent=1, ensure_ascii=False) + "\n"
    if new:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        return
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def accepted_path(path):
    return path + ".accepted.json"


def accepted(path):
    try:
        with open(accepted_path(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"slices": {}}


def assign(path, slice_id, worker):
    plan = load(path)
    if slice_id in accepted(path)["slices"]:
        raise PlanError("slice %s is already in a queue; mark it blocked there before moving it (an owner decision)" % slice_id)
    sl = next((s for s in plan["slices"] if s["id"] == slice_id), None)
    if not sl:
        raise PlanError("no slice %s" % slice_id)
    if not WORKER_RE.fullmatch(worker or ""):
        raise PlanError("worker %r: lower-case letters, digits and -, starting with a letter" % worker)
    if worker not in plan["workers"]:
        plan["workers"].append(worker)
    sl["worker"], sl["assigned_by"] = worker, "owner"
    save(path, plan)
    return plan


def accept(path, queues, only=None, write=False, log=print):
    plan = load(path)
    probs, _ = check(plan)
    chosen = [s for s in plan["slices"] if not only or s["id"] in only]
    unknown = sorted(set(only or []) - {s["id"] for s in plan["slices"]})
    if unknown:
        raise PlanError("no slice(s) %s" % ", ".join(unknown))
    mine = [p for p in probs if any(part in [s["id"] for s in chosen] for part in p["slice"].split("+"))]
    if mine:
        for p in mine:
            log("REFUSED   %s: %s" % (p["slice"], p["detail"]))
        log("\npai plan accept: nothing was queued; fix the plan (edit it or `pai plan assign`) and check again.")
        return 1
    rec = accepted(path)
    done = {sid for sid in rec["slices"]}
    for s in chosen:
        q = os.path.join(queues, "%s-%s.queue" % (plan["parent"], s["worker"]))
        if s["id"] in done:
            log("already   %s -> %s %s" % (s["id"], q, s["item"]))
            continue
        waits = [d for d in s.get("needs", []) if d not in done or not _done(plan, queues, d)]
        title = "%s: %s (verify: %s; prereqs: %s%s)" % (s["id"], s["goal"], s["verify"], s.get("prereqs") or "none",
                                                          "; needs: %s" % ", ".join(s["needs"]) if s.get("needs") else "")
        log("%s %s -> %s %s%s" % ("queue    " if write else "would    ", s["id"], q, s["item"],
                                   " (blocked: waits for %s)" % ", ".join(waits) if waits else ""))
        if not write:
            continue
        os.makedirs(queues, exist_ok=True)
        item = {"id": s["item"], "title": title}
        if os.path.exists(q):
            doc = workqueue.load(q)
            if s["item"] not in [i["id"] for i in doc["items"]]:
                workqueue.add_items(q, [item])
        else:
            tmp = q + ".%d.tmp" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"format": workqueue.FORMAT, "version": workqueue.VERSION, "name": os.path.basename(q),
                           "checkpoints": [], "items": [item]}, f, indent=2)
                f.write("\n")
            os.replace(tmp, q)
        if waits:
            workqueue.append(q, {"event": "state", "id": s["item"], "state": "blocked", "at": workqueue.now(),
                                 "reason": "waits for %s" % ", ".join(waits), "evidence": None, "session": "pai-plan"})
        rec["slices"][s["id"]] = {"queue": q, "item": s["item"], "worker": s["worker"], "at": workqueue.now()}
        save(accepted_path(path), rec)  # after each slice: an interruption never queues one twice
    if not write:
        log("\nPREVIEW: nothing was queued. Add --write.")
    return 0


def _done(plan, queues, sid):
    s = next(x for x in plan["slices"] if x["id"] == sid)
    q = os.path.join(queues, "%s-%s.queue" % (plan["parent"], s["worker"]))
    try:
        doc = workqueue.load(q)
        evs, _ = workqueue.events(q)
        return workqueue.state(doc, evs).get(s["item"], {}).get("state") == "done"
    except workqueue.QueueError:
        return False


def render(plan, probs, states=None, queues=None):
    out = ["parent    %s (repository %s)" % (plan["parent"], plan["repository"]), "workers   %s" % ", ".join(plan["workers"])]
    for s in plan["slices"]:
        out.append("slice     %-10s %-12s worker %-3s %s" % (s["id"], s["item"], s.get("worker"), s["goal"]))
        out.append("          paths %s; verify %s; prereqs %s; needs %s; %s%s" % (
            ", ".join(s.get("paths", [])) or "-", s.get("verify") or "-", s.get("prereqs", "-"),
            ", ".join(s.get("needs", [])) or "-", s.get("assigned_by", ""),
            "; queue %s" % states[s["id"]] if states and s["id"] in states else ""))
    for p in probs:
        out.append("PROBLEM   %s %s: %s" % (p["slice"], p["kind"], p["detail"]))
    for sid, cmd in startable(plan, states or {}, queues) if queues else []:
        out.append("READY     %s: everything it needs is done; start it with: %s" % (sid, cmd))
    out.append("\n%s" % ("clean: `pai plan accept PLAN --queues DIR --write` queues it" if not probs else
                         "Fix the problems above (edit the plan, or `pai plan assign`), then check again."))
    return "\n".join(out)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="pai plan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="pcmd", required=True)
    pr = sub.add_parser("propose")
    pr.add_argument("--parent", required=True)
    pr.add_argument("--goals", required=True)
    pr.add_argument("--workers", type=int, default=4)
    pr.add_argument("--repo")
    pr.add_argument("--out")
    pr.add_argument("--json", action="store_true")
    ck = sub.add_parser("check")
    ck.add_argument("plan")
    ck.add_argument("--queues")
    ck.add_argument("--json", action="store_true")
    asg = sub.add_parser("assign")
    asg.add_argument("plan")
    asg.add_argument("slice")
    asg.add_argument("worker")
    ac = sub.add_parser("accept")
    ac.add_argument("plan")
    ac.add_argument("--queues", required=True)
    ac.add_argument("--slice", action="append")
    ac.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.pcmd == "propose":
            with open(a.goals, encoding="utf-8") as f:
                plan = propose(a.parent, f.read(), a.workers, a.repo)
            probs, _ = check(plan)
            print(json.dumps(dict(plan, problems=probs), indent=1) if a.json else render(plan, probs))
            if a.out:
                save(a.out, plan, new=True)
                if not a.json:
                    print("plan      %s written (edit it, then pai plan check %s)" % (a.out, a.out))
            return 1 if probs else 0
        if a.pcmd == "check":
            plan = load(a.plan)
            probs, states = check(plan, a.queues)
            ready = [{"slice": sid, "start": cmd} for sid, cmd in startable(plan, states, a.queues)] if a.queues else []
            print(json.dumps({"plan": plan, "problems": probs, "queue_states": states, "startable": ready}, indent=1)
                  if a.json else render(plan, probs, states, a.queues))
            return 1 if probs else 0
        if a.pcmd == "assign":
            plan = assign(a.plan, a.slice, a.worker)
            probs, _ = check(plan)
            print(render(plan, probs))
            return 1 if probs else 0
        return accept(a.plan, a.queues, a.slice, a.write)
    except (PlanError, workqueue.QueueError, OSError, ValueError) as e:
        print("pai plan: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
