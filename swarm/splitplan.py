"""Split one goal into worker slices, check the split, and queue the accepted slices.

    pai plan propose --parent ID --goals FILE [--workers N] [--repo PATH] [--out PLAN.json] [--json]
    pai plan check PLAN [--queues DIR] [--json]
    pai plan assign PLAN SLICE WORKER
    pai plan accept PLAN --queues DIR [--slice ID ...] [--write] [--json]
    pai plan split-owner PLAN SLICE        a slice that needs live access -> local rehearsal + owner decision
    pai plan status PLAN --queues DIR [--lane L] [--json]   per lane: current, blocked, remaining, done, next

A goals file has one slice per line:
    - api: Add the export endpoint | paths: src/api/, tests/api/ | verify: python -m pytest tests/api | prereqs: none | needs: schema

`propose` reads the goals and who already owns what (each worktree's pai-managed AGENTS.md or CLAUDE.md
scope and lane), keeps the parent task ID, gives each slice a stable item number
(PARENT-001...) and a worker: the owner of its paths when a worktree already owns them, else
the least-loaded worker. It writes nothing but --out. The plan is a plain JSON file: edit it,
or `pai plan assign` a slice to another worker, until `check` is clean.

`check` refuses a slice without verification criteria, scope paths or test prerequisites,
two slices that would write the same paths, a slice whose paths another worktree owns, an
unknown or circular dependency. It warns (without refusing) about a slice whose goal mentions
live work (deploy, AWS, production, DNS, publish, release, migration, spending, credentials):
`split-owner` turns such a slice into a local rehearsal the same worker can do (it keeps the
slice's item number, paths and checks) and an owner decision that depends on it (`owner: yes`,
the next item number, in the owner's own queue as owner-only); slices that needed the original
now wait for the decision. A goals line may also say `owner: yes` itself.
`accept` (after --write) puts each accepted slice into its
worker's durable queue (`pai queue`, one file per worker in --queues), marks a slice that waits
for an unfinished one as blocked there, and records what it did in PLAN.accepted.json. Running
it again (also after an interruption, or to accept more slices later) adds nothing twice.
"""
import json
import os
import re
import shlex
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import gitprep  # noqa: E402
from pai import workqueue  # noqa: E402

FORMAT = "pai-split-plan"
# A parent ID: one word (PARENT) or a unified round-lane prefix (PAI-R6-COR, R6-COR; R6-COR-025),
# so the queue items it numbers are PAI-R6-COR-001, ... It also becomes part of queue file names.
PARENT_RE = re.compile(r"(?=.{1,40}$)[A-Za-z][A-Za-z0-9]{0,23}(?:-[A-Za-z][A-Za-z0-9]{0,23}){0,3}")
WORKER_RE = re.compile(r"[a-z][a-z0-9-]{0,23}")  # both become part of queue file names
SLICE_RE = re.compile(r"^\s*[-*+]\s+([a-z][a-z0-9-]{0,31}):\s*(.+?)\s*$")
SCOPE_RE = re.compile(r"Change only files under: (.+?)\. ")
LANE_RE = re.compile(r"this is lane `([^`]+)`")


LIVE_RE = re.compile(r"(?i)\b(deploy\w*|aws|terraform apply|production|prod|dns|publish\w*|releases?|migrat\w*|billing|"
                     r"spend\w*|credentials?|secrets?)\b")
OWNER = "owner"  # the queue (and worker name) of owner-decision slices


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
            if not sep or k not in ("paths", "verify", "prereqs", "needs", "worker", "owner"):
                raise PlanError("slice %s: %r is not one of paths/verify/prereqs/needs/worker/owner" % (sid, kv))
            if k == "owner":
                if v.strip().lower() not in ("yes", "true", "decision"):
                    raise PlanError("slice %s: owner takes yes (an owner decision), not %r" % (sid, v.strip()))
                sl["owner"], sl["worker"] = True, OWNER
                continue
            vals = [x.strip() for x in v.split(",") if x.strip()] if k in ("paths", "needs") else v.strip()
            sl[k] = vals
        if any(s["id"] == sid for s in slices):
            raise PlanError("slice %s appears twice" % sid)
        slices.append(sl)
    if not slices:
        raise PlanError("no slice lines (`- id: goal | paths: ... | verify: ...`)")
    return slices


# `pai worktree init --file NAME` writes the managed section into one of these (R6-COR-015).
SECTION_FILES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md")  # CLAUDE.local.md: R6-COR-060


def ownership(repo):
    """{scope path: {lane, worktree, branch, file}} from each linked worktree's managed section,
    in AGENTS.md or CLAUDE.md (a lane initialised with `--file CLAUDE.md` owns its scope too)."""
    import refresh
    info = refresh.describe(repo)
    owned = {}
    for w in info["worktrees"]:
        if not w["exists"]:
            continue
        for name in SECTION_FILES:
            p = os.path.join(w["path"], name)
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
            m, lane = SCOPE_RE.search(text), LANE_RE.search(text)
            if not m:
                continue
            for d in re.findall(r"`([^`]+)/`", m.group(1)):
                owned.setdefault(norm_path(d), {"lane": lane.group(1) if lane else None, "worktree": w["path"],
                                                "branch": w["branch"], "file": name})
    return owned, info["repository"]["path"]


def propose(parent, goals_text, workers=4, repo=None):
    if not PARENT_RE.fullmatch(parent or ""):
        raise PlanError("parent id %r: letters and digits, starting with a letter, optionally in up to four "
                        "dash-separated words such as PAI-R6-COR (it prefixes the queue items)" % parent)
    if not 1 <= workers <= 26:
        raise PlanError("--workers must be 1..26")
    slices = parse_goals(goals_text)
    owned, main = ownership(repo or os.getcwd())
    lanes = list(string.ascii_lowercase[:workers])
    load = {l: 0 for l in lanes}
    for n, sl in enumerate(slices, 1):  # owners first, then the least-loaded worker in order
        sl["item"] = "%s-%03d" % (parent, n)
        if sl.get("owner"):
            sl["assigned_by"] = "owner decision"
            continue
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
        if s.get("owner"):  # a decision: no scope, checks or prerequisites of its own
            for d in s.get("needs", []):
                if d not in ids:
                    probs.append({"slice": s["id"], "kind": "unknown-dependency", "detail": "needs %s, which is not a slice" % d})
            continue
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
            out.append((s["id"], "pai queue mark %s %s doing --session NAME" % (shlex.quote(q), s["item"])))
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


def live_warnings(plan):
    """Slices whose goal mentions live work and that are not owner decisions (shown, never refused)."""
    out = []
    for s in plan["slices"]:
        m = None if s.get("owner") or s.get("rehearsal") else LIVE_RE.search(s.get("goal", ""))
        if m:
            out.append({"slice": s["id"], "kind": "looks-owner-only",
                        "detail": "the goal mentions %r: if it needs live access or an owner decision, split it: "
                                  "pai plan split-owner PLAN %s (a local rehearsal for the worker, then the owner's "
                                  "decision)" % (m.group(0), s["id"])})
    return out


def split_owner(path, slice_id):
    """Replace one slice by a local rehearsal (same worker, item, paths, checks) and an owner
    decision that depends on it; whoever needed the slice now needs the decision."""
    plan = load(path)
    if slice_id in accepted(path)["slices"]:
        raise PlanError("slice %s is already in a queue; mark it blocked there and split it by hand (an owner decision)"
                        % slice_id)
    sl = next((s for s in plan["slices"] if s["id"] == slice_id), None)
    if not sl:
        raise PlanError("no slice %s" % slice_id)
    if sl.get("owner"):
        raise PlanError("slice %s is already an owner decision" % slice_id)
    reh, dec = slice_id + "-rehearsal", slice_id + "-decision"
    for new_id in (reh, dec):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", new_id) or any(s["id"] == new_id for s in plan["slices"]):
            raise PlanError("cannot name the new slice %s (too long, or it exists); rename %s first" % (new_id, slice_id))
    last = max(int(s["item"].rsplit("-", 1)[1]) for s in plan["slices"])
    rehearsal = dict(sl, id=reh, goal="Rehearse locally, with no live writes or spending: %s" % sl["goal"], rehearsal=True)
    decision = {"id": dec, "goal": "Owner decision: %s (after reviewing the rehearsal's evidence)" % sl["goal"],
                "paths": [], "verify": "the owner records the decision in the owner queue", "prereqs": "the rehearsal's "
                "evidence", "needs": [reh], "owner": True, "worker": OWNER, "assigned_by": "owner decision",
                "item": "%s-%03d" % (plan["parent"], last + 1)}
    i = plan["slices"].index(sl)
    plan["slices"][i:i + 1] = [rehearsal, decision]
    for s in plan["slices"]:
        s["needs"] = [dec if d == slice_id else d for d in s.get("needs", [])]
    save(path, plan)
    return plan


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
        if s.get("owner"):
            workqueue.append(q, {"event": "state", "id": s["item"], "state": "owner-only", "at": workqueue.now(),
                                 "reason": "owner decision: %s%s" % (s["goal"], "; waits for %s" % ", ".join(waits)
                                                                    if waits else ""), "evidence": None,
                                 "session": "pai-plan"})
        elif waits:
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


def lane_status(plan, queues, lane=None):
    """{lane: {queue, current, blocked, owner_only, remaining, done, startable}} from the plan's slices and
    each lane's queue (read only). An unknown --lane is an error, not an empty report."""
    lanes = sorted({s["worker"] for s in plan["slices"]})
    if lane and lane not in lanes:
        raise PlanError("no lane %s in this plan (lanes: %s)" % (lane, ", ".join(lanes)))
    ready = {sid for sid, _ in startable(plan, check(plan, queues)[1], queues)}
    out = {}
    for ln in [lane] if lane else lanes:
        q = os.path.join(queues, "%s-%s.queue" % (plan["parent"], ln))
        try:
            doc = workqueue.load(q)
            evs, _ = workqueue.events(q)
            st = workqueue.state(doc, evs)
        except workqueue.QueueError:
            st = {}
        rows = {"queue": q, "current": [], "blocked": [], "owner_only": [], "remaining": [], "done": [], "startable": []}
        for s in plan["slices"]:
            if s["worker"] != ln:
                continue
            x = st.get(s["item"]) or {"state": "not queued", "reason": None}
            entry = {"slice": s["id"], "item": s["item"], "goal": s["goal"]}
            if x["state"] == "doing":
                rows["current"].append(entry)
            elif x["state"] == "blocked":
                rows["blocked"].append(dict(entry, reason=x.get("reason")))
            elif x["state"] == "owner-only":
                rows["owner_only"].append(dict(entry, reason=x.get("reason")))
            elif x["state"] in ("done", "prototype"):
                rows["done"].append(entry)
            else:
                rows["remaining"].append(dict(entry, state=x["state"]))
            if s["id"] in ready:
                rows["startable"].append(s["id"])
        out[ln] = rows
    return out


def render_status(st):
    out = []
    for ln, r in st.items():
        out.append("lane %s   %s" % (ln, r["queue"]))
        out.append("  current    %s" % (", ".join("%s %s" % (x["item"], x["slice"]) for x in r["current"]) or "none"))
        for x in r["blocked"]:
            out.append("  BLOCKED    %s %s: %s" % (x["item"], x["slice"], x["reason"]))
        for x in r["owner_only"]:
            out.append("  OWNER      %s %s: %s" % (x["item"], x["slice"], x["reason"]))
        out.append("  remaining  %s" % (", ".join("%s %s (%s)" % (x["item"], x["slice"], x["state"]) for x in r["remaining"])
                                        or "none"))
        out.append("  done       %s" % (", ".join("%s %s" % (x["item"], x["slice"]) for x in r["done"]) or "none"))
        if r["startable"]:
            out.append("  ready      %s (everything they need is done)" % ", ".join(r["startable"]))
    return "\n".join(out)


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
    for w in live_warnings(plan):
        out.append("WARNING   %s %s: %s" % (w["slice"], w["kind"], w["detail"]))
    for sid, cmd in startable(plan, states or {}, queues) if queues else []:
        out.append("READY     %s: everything it needs is done; start it with: %s" % (sid, cmd))
    out.append("\n%s" % ("clean: `pai plan accept PLAN --queues DIR --write` queues it" if not probs else
                         "Fix the problems above (edit the plan, or `pai plan assign`), then check again."))
    return "\n".join(out)


def parser():
    """The `pai plan` command line (also read by tools/command_inventory.py)."""
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
    ss = sub.add_parser("status", help="per lane: current, blocked, owner-only, remaining and done items, and what can "
                        "start")
    ss.add_argument("plan")
    ss.add_argument("--queues", required=True)
    ss.add_argument("--lane")
    ss.add_argument("--json", action="store_true")
    so = sub.add_parser("split-owner", help="a slice that needs live access: a local rehearsal for the worker, then the "
                        "owner's decision")
    so.add_argument("plan")
    so.add_argument("slice")
    return ap


def main(argv=None):
    ap = parser()
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
            print(json.dumps({"plan": plan, "problems": probs, "warnings": live_warnings(plan), "queue_states": states,
                              "startable": ready}, indent=1) if a.json else render(plan, probs, states, a.queues))
            return 1 if probs else 0
        if a.pcmd == "status":
            st = lane_status(load(a.plan), a.queues, a.lane)
            print(json.dumps(st, indent=1) if a.json else render_status(st))
            return 0
        if a.pcmd == "split-owner":
            plan = split_owner(a.plan, a.slice)
            probs, _ = check(plan)
            print(render(plan, probs))
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
