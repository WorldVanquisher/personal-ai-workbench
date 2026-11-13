"""Remote Control launcher: one tmux session, and per worker one window, one worktree, one
task branch and one `claude --remote-control` process.

    pai_swarm.py rc plan    MANIFEST   read-only: what `up` would do, and every refusal
    pai_swarm.py rc up      MANIFEST   new run: fetch once, pin one base, new branches, start
                                       rerun:   recorded branches and base, start only what is missing
    pai_swarm.py rc status  MANIFEST   per worker: git task, lock, Claude process, RC connectivity
    pai_swarm.py rc next    MANIFEST   after GitHub reports a worker's PR merged: next queued task
    pai_swarm.py rc release MANIFEST   give the worktree locks back (never while Claude runs)

Preparation (fetch, pin, branch, refusals, resume record) is gitprep.py; the worktree lock
is wtlock.py; both are the ones the headless runner uses. This file adds the manifest, tmux
and the status evidence. It never kills a window, never restarts a running Claude and
never touches a worktree's contents.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

import gitprep
import jev
import notes
import wtlock

SCHEMA = "pai_rc_manifest_v1"
RUN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,23}")
DEFAULT_COMMAND = ["claude", "--remote-control", "--name", "{name}"]
DISCONNECT_NOTICE = "remote control disconnected"


class ManifestError(Exception):
    pass


def log(msg=""):
    print(msg, flush=True)


# ---------------------------------------------------------------- manifest

class Worker:
    def __init__(self, lane, worktree, branch, name, task, branch_source):
        self.lane, self.worktree, self.branch, self.name = lane, worktree, branch, name
        self.task, self.branch_source = task, branch_source
        self.note = None  # notes.Task once assigned


def _pairs(items, what):
    res = {}
    for item in items or []:
        k, sep, v = item.partition("=")
        if not sep or not k or not v:
            raise ManifestError("%s must look like LANE=VALUE, not %r" % (what, item))
        res[k] = v
    return res


class Manifest:
    """The manifest file plus command-line overrides (--workers, --assign, --branch)."""

    def __init__(self, path, workers=None, assign=None, branch=None, socket=None):
        self.path = os.path.abspath(os.path.expanduser(path))
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError) as e:
            raise ManifestError("cannot read manifest %s: %s" % (self.path, e))
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            raise ManifestError("manifest schema must be %s" % SCHEMA)
        self.run_id = str(d.get("run_id") or "")
        if not RUN_RE.fullmatch(self.run_id):
            raise ManifestError("run_id %r must match %s" % (self.run_id, RUN_RE.pattern))
        here = os.path.dirname(self.path)

        def absolute(p, base=here):
            p = os.path.expanduser(str(p))
            return gitprep.canon(p if os.path.isabs(p) else os.path.join(base, p))

        r = d.get("repository") or {}
        if not r.get("path"):
            raise ManifestError("repository.path is required")
        self.repo = absolute(r["path"])
        self.remote, self.base_branch = r.get("remote") or "origin", r.get("base_branch") or "main"
        self.github, self.url = r.get("github"), r.get("url")
        if not d.get("worktree_root"):
            raise ManifestError("worktree_root is required (every worker worktree must be inside it)")
        self.root = absolute(d["worktree_root"])
        t = d.get("tmux") or {}
        self.session = self._fmt(t.get("session") or "rc-{run_id}", {})
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", self.session):
            raise ManifestError("tmux session name %r may only use letters, digits, - and _" % self.session)
        self.socket = socket or t.get("socket")
        self.command = d.get("command") or list(DEFAULT_COMMAND)
        if not (isinstance(self.command, list) and self.command and all(isinstance(x, str) for x in self.command)):
            raise ManifestError("command must be a list of strings (an argv, not a shell line)")
        self.env = d.get("env") or {}
        if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) and isinstance(v, str) for k, v in self.env.items()):
            raise ManifestError("env must map variable names to strings")
        self.tasks_dir = absolute(d["tasks"]) if d.get("tasks") else None
        self.review = d.get("review") or {}

        raw, count = d.get("workers"), workers if workers is not None else d.get("count")
        if raw is None:
            if not count:
                raise ManifestError("give a `workers` list, or a worker count (`count` or --workers N)")
            raw = [{"lane": "worker-%d" % i} for i in range(1, int(count) + 1)]
        if not isinstance(raw, list) or not raw or not all(isinstance(w, dict) for w in raw):
            raise ManifestError("workers must be a non-empty list of objects")
        if count is not None and int(count) != len(raw):
            raise ManifestError("worker count is %s but the manifest lists %d worker(s)" % (count, len(raw)))
        assign, branch = _pairs(assign, "--assign"), _pairs(branch, "--branch")
        lanes = [str(w.get("lane") or "") for w in raw]
        for flag, given in (("--assign", assign), ("--branch", branch)):
            for k in given:
                if k not in lanes:
                    raise ManifestError("%s names lane %r, which the manifest does not have" % (flag, k))
        self.workers = []
        for w in raw:
            lane = str(w.get("lane") or "")
            if not gitprep.LANE_RE.fullmatch(lane):
                raise ManifestError("lane name %r must match %s" % (lane, gitprep.LANE_RE.pattern))
            vals = {"lane": lane}
            if lane in branch:
                b, src = branch[lane], "command line"
            elif w.get("branch"):
                b, src = self._fmt(w["branch"], vals), "manifest"
            else:
                b, src = self._fmt(d.get("branch_template") or "rc/{run_id}/{lane}", vals), "default"
            self.workers.append(Worker(lane, absolute(w.get("worktree") or lane, self.root), b,
                                       self._fmt(w.get("name") or "{run_id} {lane}", vals),
                                       assign.get(lane) or w.get("task"), src))

    def _fmt(self, template, vals):
        try:
            return str(template).format_map(dict(vals, run_id=self.run_id))
        except (KeyError, IndexError, ValueError) as e:
            raise ManifestError("bad placeholder in %r: %s" % (template, e))

    def worker(self, lane):
        for w in self.workers:
            if w.lane == lane:
                return w
        raise ManifestError("the manifest has no lane %r" % lane)

    def argv(self, w, rec, rl):
        vals = {"name": w.name, "lane": w.lane, "worktree": w.worktree, "branch": rl["branch"],
                "base_sha": rec["base_sha"], "task": rl.get("note") or "",
                "task_path": os.path.join(self.tasks_dir, rl["note"] + ".md") if self.tasks_dir and rl.get("note") else ""}
        argv = [self._fmt(x, vals) for x in self.command]
        # tmux starts the pane with its server's PATH, not this shell's: pin the program now.
        return [shutil.which(argv[0]) or argv[0]] + argv[1:]

    def pane_env(self, w, rec, rl):
        env = dict(self.env)
        env.update(PAI_RC_RUN=self.run_id, PAI_RC_LANE=w.lane, PAI_TASK_ID=rec["task_id"],
                   PAI_TASK_REMOTE=rec["repo"]["remote"], PAI_TASK_BASE_SHA=rec["base_sha"],
                   PAI_TASK_BRANCH=rl["branch"], PAI_TASK_WORKTREE=w.worktree, PAI_TASK_NOTE=rl.get("note") or "")
        return env

    def sessions_dir(self):
        cfg = self.env.get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude")
        return os.path.join(os.path.expanduser(cfg), "sessions")


# ---------------------------------------------------------------- recorded state

def current_lanes(cdir, run_id):
    """{lane: (task record, lane record)} for the run's live (not retired) lane tasks."""
    res = {}
    for rec in gitprep.all_records():
        meta = rec.get("meta") or {}
        if rec["repo"]["common_dir"] != cdir or meta.get("run") != run_id:
            continue
        for lane, rl in rec["lanes"].items():
            if rl["state"] != "retired" and (lane not in res or meta.get("gen", 1) > res[lane][0]["meta"].get("gen", 1)):
                res[lane] = (rec, rl)
    return res


def ask_branches(workers, input_fn=input, out=sys.stderr):
    """Interactive runs: confirm or replace each worker's branch name. Enter keeps the default.
    A name given on the command line is not asked about."""
    for w in workers:
        if w.branch_source == "command line":
            continue
        out.write("Branch for worker %s [%s]: " % (w.lane, w.branch))
        out.flush()
        answer = input_fn().strip()
        if answer:
            w.branch, w.branch_source = answer, "prompt"


def assign_tasks(m):
    """Bind task notes to workers: named assignments first, then the queue (todo, by
    priority) for workers without one. Returns problems."""
    if not m.tasks_dir:
        named = [w.lane for w in m.workers if w.task]
        return ["workers %s have a task assignment but the manifest has no `tasks` folder" % ", ".join(named)] if named else []
    if not os.path.isdir(m.tasks_dir):
        return ["tasks folder not found: %s" % m.tasks_dir]
    by_name = {t.name: t for t in notes.load_tasks(m.tasks_dir)}
    problems, taken = [], set()
    for w in m.workers:
        if not w.task:
            continue
        t = by_name.get(w.task)
        if t is None:
            problems.append("lane %s: task note %r not found in %s" % (w.lane, w.task, m.tasks_dir))
        elif w.task in taken:
            problems.append("task %s is assigned to more than one worker" % w.task)
        elif t.status != "todo":
            problems.append("lane %s: task %s has status %s, not todo" % (w.lane, w.task, t.status))
        else:
            w.note = t
            taken.add(w.task)
    queue = sorted((t for t in by_name.values() if t.status == "todo" and t.name not in taken),
                   key=lambda t: (-t.priority, t.name))
    for w in m.workers:
        if not w.task and queue:
            w.note = queue.pop(0)
            w.task = w.note.name
    return problems


def jev_intake(m, write):
    """Optional Jev triage of the notes about to be handed to workers. Advisory: a low
    score or an unavailable Jev never blocks a start."""
    for w in m.workers:
        if not w.note:
            continue
        sig = jev.signal("triage", w.note.title, w.note.body)
        if sig["state"] != "ok":
            verdict = "unavailable (%s: %s)" % (sig["state"], sig["detail"])
        else:
            verdict = "actionable %.2f%s" % (sig["actionable"], "" if sig["actionable"] >= 0.5 else
                                             ": the note may need more detail before a worker starts")
        log("jev       %-14s %s: %s (optional signal, not a gate)" % (w.lane, w.note.name, verdict))
        if write:
            w.note.update(meta={"jev_intake": verdict.split(":")[0]})


def build_specs(m, want=None, interactive=False, input_fn=input):
    """([gitprep.Spec], problems). A run with no record is one new batch task for all
    workers; otherwise each worker resumes its recorded task (the first batch, or a later
    task prepared by `rc next`)."""
    cdir = gitprep.common_dir(m.repo)
    if not cdir:
        return [], ["not a git repository: %s" % m.repo]
    cur = current_lanes(cdir, m.run_id)
    common = dict(mode="rc", repo=m.repo, remote=m.remote, base_branch=m.base_branch, github=m.github,
                  url=m.url, worktree_root=m.root, lock_owner=m.run_id)
    if not cur:
        if want == "resume":
            return [], ["run %s has no recorded task here, so there is nothing to resume" % m.run_id]
        problems = assign_tasks(m)
        if interactive and not problems:
            ask_branches(m.workers, input_fn)
        lanes = [gitprep.Lane(w.lane, w.branch, w.worktree, {"stem": w.branch, "note": w.task}) for w in m.workers]
        return [gitprep.Spec(m.run_id, lanes=lanes, want="new", meta={"run": m.run_id, "gen": 1}, **common)], problems
    if want == "new":
        return [], ["run %s was already started; rerun without --new to resume it, or use a new run_id" % m.run_id]
    problems = []
    have, listed = set(cur), {w.lane for w in m.workers}
    if have != listed:
        problems.append("the manifest's lanes (%s) are not the recorded run's (%s); a changed set of workers is "
                        "a new run: use a new run_id" % (", ".join(sorted(listed)), ", ".join(sorted(have))))
        return [], problems
    groups = {}
    for w in m.workers:
        rec, rl = cur[w.lane]
        if w.branch_source in ("command line", "prompt") and w.branch != rl["branch"]:
            problems.append("lane %s is recorded on branch %s; a resume never renames it" % (w.lane, rl["branch"]))
        w.branch, w.task = rl["branch"], rl.get("note")
        groups.setdefault(rec["task_id"], (rec, []))[1].append(gitprep.Lane(w.lane, rl["branch"], w.worktree))
    specs = [gitprep.Spec(tid, lanes=lanes, want="resume", meta=rec.get("meta"), **common)
             for tid, (rec, lanes) in groups.items()]
    return specs, problems


# ---------------------------------------------------------------- tmux and processes

class Tmux:
    FIELDS = ("window_id", "window_name", "pane_dead", "pane_dead_status", "pane_pid", "pane_id", "pane_current_command")

    def __init__(self, socket=None):
        self.bin = os.environ.get("PAI_SWARM_TMUX") or shutil.which("tmux")
        self.base = [self.bin or "tmux"]
        if socket:
            self.base += ["-S", socket] if os.sep in socket else ["-L", socket]
        self.env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}

    def run(self, *args):
        return subprocess.run(self.base + list(args), capture_output=True, text=True, env=self.env,
                              stdin=subprocess.DEVNULL, timeout=30)

    def has_session(self, session):
        return bool(self.bin) and self.run("has-session", "-t", "=" + session).returncode == 0

    def windows(self, session):
        """{window name: [window dict, ...]}; empty when the session or server does not exist."""
        if not self.bin:
            return {}
        p = self.run("list-windows", "-t", "=" + session, "-F", "\t".join("#{%s}" % f for f in self.FIELDS))
        res = {}
        for line in p.stdout.splitlines() if p.returncode == 0 else []:
            w = dict(zip(self.FIELDS, line.split("\t")))
            res.setdefault(w["window_name"], []).append(w)
        return res

    @staticmethod
    def shell_line(argv, env):
        # One shell word list run by tmux through `sh -c`; `exec` makes Claude the pane process.
        return "exec env " + " ".join(shlex.quote("%s=%s" % kv) for kv in sorted(env.items())) + " " + \
            " ".join(shlex.quote(a) for a in argv)

    def start(self, session, lane, cwd, argv, env):
        target = "=%s:%s" % (session, lane)
        if self.has_session(session):
            first = ["new-window", "-d", "-t", "=%s:" % session, "-n", lane, "-c", cwd]
        else:
            first = ["new-session", "-d", "-s", session, "-n", lane, "-c", cwd, "-x", "200", "-y", "50"]
        # Same tmux invocation, so the window keeps its pane (and exit status) even if the
        # command exits at once.
        p = self.run(*(first + [self.shell_line(argv, env), ";", "set-option", "-w", "-t", target, "remain-on-exit", "on"]))
        return p.returncode == 0, p.stderr.strip()

    def respawn(self, pane_id, cwd, argv, env):
        # No -k: tmux refuses unless the pane's process has already exited.
        p = self.run("respawn-pane", "-t", pane_id, "-c", cwd, self.shell_line(argv, env))
        return p.returncode == 0, p.stderr.strip()

    def capture(self, pane_id):
        p = self.run("capture-pane", "-p", "-t", pane_id)
        return p.stdout if p.returncode == 0 else ""


def process_table():
    try:
        p = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,args="], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    table = {}
    for line in p.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def find_claude(pane_pid, command, sessions_dir, table):
    """Pid of the Claude process in a pane (the pane's process or a descendant), or None."""
    want = os.path.basename(command[0])
    queue, seen = [pane_pid], set()
    while queue:
        pid = queue.pop(0)
        if pid in seen or pid not in table:
            continue
        seen.add(pid)
        args = table[pid][1].split()
        if (args and os.path.basename(args[0]) == want) or os.path.isfile(os.path.join(sessions_dir, "%d.json" % pid)):
            return pid
        queue += [c for c, (pp, _) in table.items() if pp == pid]
    return None


def rc_evidence(pid, worktree, sessions_dir, pane_text):
    """(state, evidence). `verified` needs positive evidence from Claude Code's own session
    record for this exact process and directory; a running process alone proves nothing."""
    notice = DISCONNECT_NOTICE in pane_text.lower()
    try:
        with open(os.path.join(sessions_dir, "%d.json" % pid), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return ("disconnected", "pane shows a Remote Control disconnect notice") if notice else \
            ("unverified", "Claude wrote no session record for pid %d" % pid)
    start = wtlock.proc_start(pid)
    if rec.get("pid") != pid:
        return "unverified", "the session record belongs to another process"
    if start is not None:  # where the start time is known, the record must carry the same one
        if not rec.get("procStart"):
            return "unverified", "the session record has no process start time, so it cannot be tied to pid %d" % pid
        if str(rec["procStart"]) != str(start):
            return "unverified", "the session record belongs to an earlier process with the same pid"
    if gitprep.canon(str(rec.get("cwd") or "")) != worktree:
        return "unverified", "the session record is for another directory"
    if not rec.get("bridgeSessionId"):
        return ("disconnected" if notice else "not-connected",
                "Claude's session record has no Remote Control bridge session" + (
                    "; pane shows a disconnect notice" if notice else ""))
    if notice:
        return "unverified", "bridge session recorded, but the pane shows a disconnect notice"
    age = ""
    if isinstance(rec.get("updatedAt"), (int, float)):
        age = ", record updated %ds ago" % max(0, int(time.time() - rec["updatedAt"] / 1000))
    return "verified", "bridge session recorded by Claude for this process and worktree" + age


def lane_process(m, tmux, w, wins=None, table=None):
    """{'state': no-window|ambiguous|exited|running|no-claude, 'pid', 'exit', 'window'}"""
    wins = tmux.windows(m.session) if wins is None else wins
    mine = wins.get(w.lane) or []
    if not mine:
        return {"state": "no-window"}
    if len(mine) > 1:
        return {"state": "ambiguous", "detail": "%d windows are named %s" % (len(mine), w.lane)}
    win = mine[0]
    if win["pane_dead"] == "1":
        return {"state": "exited", "exit": win["pane_dead_status"] or "?", "window": win}
    pid = find_claude(int(win["pane_pid"] or 0), m.command, m.sessions_dir(), process_table() if table is None else table)
    if pid is None:
        return {"state": "no-claude", "window": win, "detail": "pane runs %s" % win["pane_current_command"]}
    return {"state": "running", "pid": pid, "window": win}


# ---------------------------------------------------------------- plan / up

def preflight(m, tmux, new):
    """Launcher-level refusals, checked before anything is fetched or switched."""
    problems = []
    if not tmux.bin:
        problems.append("tmux is not installed (run `pai_swarm.py doctor`)")
    exe = m.command[0]
    if not (shutil.which(exe) or (os.path.isabs(exe) and os.access(exe, os.X_OK))):
        problems.append("%s is not on PATH (run `pai_swarm.py doctor`)" % exe)
    wins = tmux.windows(m.session)
    for w in m.workers:
        n = len(wins.get(w.lane) or [])
        if n > 1:
            problems.append("tmux session %s has %d windows named %s; close the extra one yourself" % (m.session, n, w.lane))
        elif n and new:
            problems.append("tmux session %s already has a window named %s, but this run has not started yet; "
                            "close that window yourself or use another session name" % (m.session, w.lane))
    return problems


def show_plan(m, specs, plans, problems, tmux):
    log("manifest  %s" % m.path)
    log("run       %s   tmux session %s%s" % (m.run_id, m.session, "  (socket %s)" % m.socket if m.socket else ""))
    wins, table = tmux.windows(m.session), process_table()
    for pl in plans:
        log(gitprep.render_plan(pl))
        for ln in pl.spec.lanes:
            if ln.problems:
                continue
            w = m.worker(ln.lane)
            proc = lane_process(m, tmux, w, wins, table)
            what = {"no-window": "start a window and Claude", "exited": "restart Claude in its window (it exited)",
                    "running": "keep (Claude is running)", "no-claude": "leave alone (window without Claude)",
                    "ambiguous": "REFUSED"}[proc["state"]]
            log("lane      %-14s worker: %s%s" % (ln.lane, what, "; task note %s" % w.task if w.task else ""))
    for p in problems:
        log("REFUSED   %s" % p)


def cmd_plan(m, a):
    tmux = Tmux(m.socket)
    specs, problems = build_specs(m, getattr(a, "want", None))
    plans = [gitprep.plan(s) for s in specs]
    new = any(pl.path == "new" for pl in plans)
    problems += preflight(m, tmux, new) if specs else []
    show_plan(m, specs, plans, problems, tmux)
    if a.jev_triage and new:
        jev_intake(m, write=False)
    blocked = bool(problems) or any(pl.blocked for pl in plans)
    log("\nPLAN ONLY: nothing was fetched, created, switched or started.%s"
        % (" Blocked: fix the REFUSED lines first." if blocked else ""))
    return 1 if blocked else 0


def ensure_workers(m, tmux, lanes):
    """Start what is missing; keep what runs. `lanes` is [(worker, record, lane record)].
    Returns the number of lanes that could not be started."""
    failed = 0
    wins, table = tmux.windows(m.session), process_table()
    for w, rec, rl in lanes:
        gate = gitprep.verify_ready(w.worktree, rl["branch"], rec["base_sha"])
        lock = wtlock.read(w.worktree)
        if not wtlock.owned(lock, "rc", m.run_id, w.lane):
            gate.append("this run does not hold the worktree lock (%s)" % wtlock.describe(lock))
        if gate:
            log("NOT STARTED %-12s %s" % (w.lane, "; ".join(gate)))
            failed += 1
            continue
        proc = lane_process(m, tmux, w, wins, table)
        argv, env = m.argv(w, rec, rl), m.pane_env(w, rec, rl)
        if proc["state"] == "no-window":
            ok, err = tmux.start(m.session, w.lane, w.worktree, argv, env)
            verb = "started"
        elif proc["state"] == "exited":
            ok, err = tmux.respawn(proc["window"]["pane_id"], w.worktree, argv, env)
            verb = "restarted (Claude had exited with status %s)" % proc["exit"]
        elif proc["state"] == "running":
            log("kept        %-12s Claude is running (pid %d); session and edits untouched" % (w.lane, proc["pid"]))
            continue
        else:
            log("LEFT ALONE  %-12s %s; nothing was changed. Close the window yourself, then rerun."
                % (w.lane, proc.get("detail", proc["state"])))
            failed += 1
            continue
        if ok:
            rl["state"], rl["started_at"] = "started", gitprep.now()
            gitprep.save_record(rec)
            log("%-11s %-12s window %s:%s, branch %s" % (verb.split(" ")[0], w.lane, m.session, w.lane, rl["branch"]))
        else:
            log("FAILED      %-12s tmux: %s" % (w.lane, err))
            failed += 1
    return failed


def mark_notes(m, rec, lanes):
    """Write the assignment into each worker's task note (Obsidian)."""
    if not m.tasks_dir:
        return
    for w in lanes:
        rl = rec["lanes"][w.lane]
        path = os.path.join(m.tasks_dir, "%s.md" % rl["note"]) if rl.get("note") else None
        if path and os.path.isfile(path):
            notes.Task(path).update(meta={"status": "running", "worker": w.lane, "rc_run": m.run_id,
                                          "branch": rl["branch"], "base_sha": rec["base_sha"],
                                          "remote": rec["repo"]["remote"], "worktree": w.worktree})


def cmd_up(m, a):
    tmux = Tmux(m.socket)
    interactive = sys.stdin.isatty() and not a.no_input
    try:
        specs, problems = build_specs(m, a.want, interactive)
    except EOFError:
        log("\nrc up: no answer to the branch prompt; nothing was started (use --no-input to accept the defaults)")
        return 1
    except KeyboardInterrupt:
        log("\nrc up: interrupted at the branch prompt; nothing was started")
        return 130
    plans = [gitprep.plan(s, probe=False) for s in specs]
    new = any(pl.path == "new" for pl in plans)
    problems += preflight(m, tmux, new) if specs else []
    if problems or any(pl.blocked for pl in plans):
        show_plan(m, specs, plans, problems, tmux)
        log("\nrc up: REFUSED. Nothing was fetched, created, switched or started.")
        return 1
    started = []
    for spec in specs:
        try:
            pl, rec = gitprep.apply(spec, expect_base=a.expect_base, log=log)
        except gitprep.Blocked as e:
            for p in e.problems:
                log("REFUSED   %s" % p)
            log("\nrc up: stopped before any worker was started. Fix the problem and run the same command "
                "again; a task that was already pinned resumes on its recorded base.")
            return 1
        log("%s  task %s: %s" % ("NEW   " if pl.path == "new" else "RESUME", spec.task_id, gitprep.describe(rec)))
        if pl.path == "new":
            mark_notes(m, rec, [m.worker(l.lane) for l in spec.lanes])
            if a.jev_triage:
                jev_intake(m, write=True)
        started += [(m.worker(l.lane), rec, rec["lanes"][l.lane]) for l in spec.lanes]
    failed = ensure_workers(m, tmux, started)
    time.sleep(a.settle)
    log()
    code = cmd_status(m, a, quiet_exit=True)
    return 1 if failed else code


# ---------------------------------------------------------------- status

def lane_status(m, tmux, w, rec, rl, wins, table):
    st = {"lane": w.lane, "task_id": rec["task_id"], "generation": (rec.get("meta") or {}).get("gen", 1),
          "note": rl.get("note"), "remote": rec["repo"]["remote"], "remote_url": rec["repo"]["remote_url"],
          "base_branch": rec["repo"]["base_branch"], "base_sha": rec["base_sha"], "fetched_at": rec["fetched_at"],
          "branch": rl["branch"], "worktree": w.worktree, "prepared": rl["state"]}
    if os.path.isdir(w.worktree):
        st["on_branch"] = gitprep.current_branch(w.worktree)
        st["head"] = gitprep.rev(w.worktree, "HEAD")
        st["ahead"] = gitprep.ahead(w.worktree, rec["base_sha"])
        st["uncommitted"] = len(gitprep.dirty(w.worktree))
        st["in_progress"] = gitprep.in_progress(w.worktree)
    lock = wtlock.read(w.worktree)
    st["lock"] = "this run" if wtlock.owned(lock, "rc", m.run_id, w.lane) else (
        "NOT HELD" if not lock else "held by " + wtlock.describe(lock))
    proc = lane_process(m, tmux, w, wins, table)
    st["process"] = {k: v for k, v in proc.items() if k != "window"}
    if proc["state"] == "running":
        state, evidence = rc_evidence(proc["pid"], w.worktree, m.sessions_dir(), tmux.capture(proc["window"]["pane_id"]))
    else:
        state, evidence = "not-running", "no Claude process to be connected"
    st["rc"] = {"state": state, "evidence": evidence}
    return st


def collect_status(m, tmux):
    cdir = gitprep.common_dir(m.repo)
    cur = current_lanes(cdir, m.run_id) if cdir else {}
    wins, table = tmux.windows(m.session), process_table()
    return [lane_status(m, tmux, w, *cur[w.lane], wins, table) for w in m.workers if w.lane in cur], cur


def cmd_status(m, a, quiet_exit=False):
    tmux = Tmux(m.socket)
    rows, cur = collect_status(m, tmux)
    ok = bool(rows) and len(rows) == len(m.workers) and all(
        r["process"]["state"] == "running" and r["rc"]["state"] == "verified" for r in rows)
    if getattr(a, "json", False):
        json.dump({"run_id": m.run_id, "session": m.session, "ok": ok, "lanes": rows}, sys.stdout, indent=1)
        print()
        return 0 if ok else 1
    log("run       %s   tmux session %s (%s)" % (m.run_id, m.session, "exists" if tmux.has_session(m.session) else "absent"))
    if not rows:
        log("No task has been started for this run. `rc plan` shows what `rc up` would do.")
        return 1
    for r in rows:
        p, branch_now = r["process"], r.get("on_branch")
        log("lane %s" % r["lane"])
        log("  task      %s%s" % (r["task_id"], "  note %s" % r["note"] if r["note"] else ""))
        log("  remote    %s %s" % (r["remote"], r["remote_url"]))
        log("  base      %s @ %s (fetched %s)" % (r["base_branch"], r["base_sha"], r["fetched_at"]))
        log("  branch    %s%s" % (r["branch"], "" if "head" not in r else
                                  "  HEAD %s, %s commit(s) ahead of base, %d uncommitted path(s)%s" % (
                                      (r["head"] or "?")[:12], r["ahead"], r["uncommitted"],
                                      "" if branch_now == r["branch"] else "  WORKTREE IS ON %s" % (branch_now or "a detached HEAD"))))
        log("  worktree  %s%s" % (r["worktree"], "" if "head" in r else "  MISSING"))
        log("  lock      %s" % r["lock"])
        log("  claude    %s" % {"running": "running (pid %s)" % p.get("pid"), "exited": "exited (status %s)" % p.get("exit"),
                                "no-window": "no window", "no-claude": "window without a Claude process (%s)" % p.get("detail"),
                                "ambiguous": "ambiguous: %s" % p.get("detail")}[p["state"]])
        log("  rc        %s: %s" % (r["rc"]["state"], r["rc"]["evidence"]))
    missing = [w.lane for w in m.workers if w.lane not in cur]
    if missing:
        log("not started: %s" % ", ".join(missing))
    if not ok and not quiet_exit:
        log("\nNot every worker is running with verified Remote Control. `rc up` restarts only missing workers.")
    return 0 if ok else 1


# ---------------------------------------------------------------- release

def cmd_release(m, a):
    tmux = Tmux(m.socket)
    wins, table = tmux.windows(m.session), process_table()
    code = 0
    for w in m.workers:
        if a.lane and w.lane not in a.lane:
            continue
        lock = wtlock.read(w.worktree)
        if not wtlock.owned(lock, "rc", m.run_id, w.lane):
            log("%-14s not locked by this run (%s)" % (w.lane, wtlock.describe(lock)))
            continue
        proc = lane_process(m, tmux, w, wins, table)
        if proc["state"] in ("running", "no-claude", "ambiguous"):
            log("%-14s KEPT: its tmux window is still live (%s). Stop that session yourself first."
                % (w.lane, proc["state"]))
            code = 1
            continue
        wtlock.release(w.worktree, "rc", m.run_id, w.lane)
        log("%-14s lock released; branch, worktree and edits are untouched" % w.lane)
    return code


# ---------------------------------------------------------------- next task after a merged PR

def gh(*args, timeout=60):
    exe = os.environ.get("PAI_SWARM_GH") or shutil.which("gh")
    if not exe:
        raise RuntimeError("gh is not installed (run `pai_swarm.py doctor`)")
    p = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError("gh %s failed: %s" % (" ".join(args[:2]), gitprep.redact_url(p.stderr.strip()[-300:])))
    return p.stdout


def slug_of(m, rec):
    slug = m.github or gitprep.github_slug(gitprep.out(m.repo, "config", "--get", "remote.%s.url" % m.remote) or "")
    if not slug:
        raise RuntimeError("the remote is not a GitHub repository; set repository.github in the manifest")
    return slug


def prs_for(slug, branch):
    return json.loads(gh("pr", "list", "--repo", slug, "--head", branch, "--state", "all", "--json",
                         "number,url,state,mergedAt,baseRefName,headRefOid") or "[]")


def merged_pr(prs, tip, base_branch):
    """(pr, None) when GitHub says the branch's PR is merged into the task's base branch;
    (None, why not) otherwise."""
    if any(p["state"] == "OPEN" for p in prs):
        return None, "its PR is still open"
    done = [p for p in prs if p["state"] == "MERGED" and p.get("mergedAt")]
    if not done:
        return None, "its PR was closed without merging" if prs else "it has no PR"
    elsewhere = [p for p in done if p.get("baseRefName") != base_branch]
    done = [p for p in done if p.get("baseRefName") == base_branch]
    if not done:
        return None, "its PR was merged into %s, not %s" % (elsewhere[0].get("baseRefName"), base_branch)
    if not any(p["headRefOid"] == tip for p in done):
        return None, "the branch has commits that were not in the merged PR (local tip %s)" % tip[:12]
    return next(p for p in done if p["headRefOid"] == tip), None


def next_branch(m, stem, taken):
    """stem-2, stem-3, ...: the first name used by no local branch, task record or remote branch."""
    remote = gitprep.out(m.repo, "ls-remote", "--heads", m.remote, stem + "-*") or ""
    used = {line.split("refs/heads/", 1)[1] for line in remote.splitlines() if "refs/heads/" in line} | set(taken)
    n = 2
    while "%s-%d" % (stem, n) in used or gitprep.branch_sha(m.repo, "%s-%d" % (stem, n)):
        n += 1
    return "%s-%d" % (stem, n)


def cmd_next(m, a, input_fn=input):
    tmux = Tmux(m.socket)
    cdir = gitprep.common_dir(m.repo)
    cur = current_lanes(cdir, m.run_id) if cdir else {}
    if not cur:
        log("rc next: run %s has not been started" % m.run_id)
        return 1
    override = _pairs(a.branch, "--branch")
    queue = []
    if m.tasks_dir and os.path.isdir(m.tasks_dir):
        queue = sorted((t for t in notes.load_tasks(m.tasks_dir) if t.status == "todo"), key=lambda t: (-t.priority, t.name))
    taken = {rl["branch"] for rec in gitprep.all_records() for rl in rec["lanes"].values()}
    ask = sys.stdin.isatty() and not a.no_input and not a.plan  # like `rc plan`, --plan never asks
    code, chosen, rolled = 0, [], []
    # First decide every lane (GitHub's answer, the next note, the branch name), asking on a
    # terminal; only then change anything, so an unanswered prompt leaves every lane as it was.
    for w in m.workers:
        if (a.lane and w.lane not in a.lane) or w.lane not in cur:
            continue
        rec, rl = cur[w.lane]
        tip = gitprep.branch_sha(m.repo, rl["branch"])
        try:
            pr, why = merged_pr(prs_for(slug_of(m, rec), rl["branch"]), tip or "", rec["repo"]["base_branch"])
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            log("%-14s NOT ROLLED OVER: could not ask GitHub (%s)" % (w.lane, e))
            code = 1
            continue
        if not pr:
            log("%-14s not rolled over: %s (branch %s). Its task stays as it is." % (w.lane, why, rl["branch"]))
            code = 1
            continue
        note = None
        if a.task:
            note = next((t for t in queue if t.name == a.task), None)
            if note is None:
                log("%-14s NOT ROLLED OVER: task %s is not a todo note in %s" % (w.lane, a.task, m.tasks_dir))
                code = 1
                continue
        elif queue:
            note = queue[0]
        if note is None:
            log("%-14s PR %s is merged, but no task is queued (status: todo); nothing was prepared" % (w.lane, pr["url"]))
            continue
        stem = rl.get("stem") or rl["branch"]
        branch = override.get(w.lane) or next_branch(m, stem, taken)
        if w.lane not in override and ask:
            sys.stderr.write("Branch for worker %s, next task %s [%s]: " % (w.lane, note.name, branch))
            sys.stderr.flush()
            try:
                branch = input_fn().strip() or branch
            except (EOFError, KeyboardInterrupt) as e:
                log("\nrc next: %s at the branch prompt; no worker was rolled over"
                    % ("interrupted" if isinstance(e, KeyboardInterrupt) else "no answer"))
                return 130 if isinstance(e, KeyboardInterrupt) else 1
        queue.remove(note)
        taken.add(branch)
        chosen.append((w, rec, rl, pr, note, stem, branch))
    for w, rec, rl, pr, note, stem, branch in chosen:
        gen = (rec.get("meta") or {}).get("gen", 1) + 1
        spec = gitprep.Spec("%s.%s.%d" % (m.run_id, w.lane, gen), mode="rc", repo=m.repo, remote=m.remote,
                            base_branch=m.base_branch, github=m.github, url=m.url, worktree_root=m.root,
                            want="new", meta={"run": m.run_id, "gen": gen}, lock_owner=m.run_id,
                            lanes=[gitprep.Lane(w.lane, branch, w.worktree, {"stem": stem, "note": note.name})])
        if a.plan:
            pl = gitprep.plan(spec)
            log("%-14s PR %s is merged; next task %s" % (w.lane, pr["url"], note.name))
            log(gitprep.render_plan(pl))
            code = code or (1 if pl.blocked else 0)
            continue
        try:
            pl, new = gitprep.apply(spec, log=log)
        except gitprep.Blocked as e:
            log("%-14s NOT ROLLED OVER (PR %s is merged): %s" % (w.lane, pr["url"], "; ".join(e.problems)))
            log("%-14s the worktree, its branch %s and any edits are unchanged" % ("", rl["branch"]))
            code = 1
            continue
        rl.update(state="retired", superseded_by=new["task_id"], merged_pr=pr["url"])
        gitprep.save_record(rec)
        if rl.get("note") and os.path.isfile(os.path.join(m.tasks_dir, rl["note"] + ".md")):
            notes.Task(os.path.join(m.tasks_dir, rl["note"] + ".md")).update(meta={"status": "done", "pr": pr["url"], "pr_state": "merged"})
        mark_notes(m, new, [w])
        log("%-14s PR %s merged -> task %s on new branch %s" % (w.lane, pr["url"], note.name, branch))
        log("%-14s %s" % ("", gitprep.describe(new, w.lane)))
        rolled.append((w, new, new["lanes"][w.lane]))
    if a.plan:
        log("\nPLAN ONLY: nothing was fetched, switched or started.")
        return code
    if rolled:
        code = max(code, 1 if ensure_workers(m, tmux, rolled) else 0)
        log("A kept Claude session is now on the new branch: give it the new task from claude.ai/code.")
    return code


# ---------------------------------------------------------------- handoff facts

def session_mutex(m):
    """Non-blocking mutex for one tmux session: `rc up`, `rc next` and `rc release` never
    interleave, so two of them cannot both start a worker in the same lane."""
    key = hashlib.sha256(("%s\0%s" % (m.socket or "", m.session)).encode()).hexdigest()[:16]
    return wtlock.mutex("tmux-" + key, blocking=False)


def exclusive(m, fn, *args):
    with session_mutex(m) as got:
        if not got:
            log("rc: another `rc up`, `rc next` or `rc release` for tmux session %s is running; nothing was "
                "changed. Run this again when it has finished." % m.session)
            return 1
        return fn(m, *args)


def handoff_facts(m):
    """(summary, evidence lines, commit state) observed by the launcher, for `pai handoff new`."""
    rows, _ = collect_status(m, Tmux(m.socket))
    evidence, commits = [], []
    for r in rows:
        evidence.append("lane %s: task %s, remote %s (%s), base %s @ %s, task branch %s, worktree %s"
                        % (r["lane"], r["task_id"], r["remote"], r["remote_url"], r["base_branch"], r["base_sha"],
                           r["branch"], r["worktree"]))
        evidence.append("lane %s: Claude process %s; Remote Control %s (%s); lock %s"
                        % (r["lane"], r["process"]["state"], r["rc"]["state"], r["rc"]["evidence"], r["lock"]))
        commits.append("%s: HEAD %s, %s ahead of base, %s uncommitted path(s)"
                       % (r["lane"], (r.get("head") or "?")[:12], r.get("ahead"), r.get("uncommitted")))
    summary = "RC run %s: %d worker(s) in tmux session %s" % (m.run_id, len(rows), m.session)
    return summary, evidence, "observed by rc status, not by a worker: " + "; ".join(commits) if commits else None


# ---------------------------------------------------------------- command line

def main(argv, pai_cls=None):
    import argparse
    ap = argparse.ArgumentParser(prog="pai_swarm.py rc", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="rc_cmd", required=True)

    def parser(name, help_, overrides=False):
        p = sub.add_parser(name, help=help_)
        p.add_argument("manifest")
        p.add_argument("--tmux-socket", help="tmux socket name (-L) or path (-S); default: your normal tmux server")
        if overrides:
            p.add_argument("--workers", type=int, help="worker count: must match the manifest's list, or "
                           "generates worker-1..N when the manifest lists none")
            p.add_argument("--assign", action="append", metavar="LANE=TASK", help="give a worker a task note by name")
            p.add_argument("--branch", action="append", metavar="LANE=NAME", help="branch name for a worker's new task")
            g = p.add_mutually_exclusive_group()
            g.add_argument("--new", dest="want", action="store_const", const="new", help="refuse if the run was already started")
            g.add_argument("--resume", dest="want", action="store_const", const="resume", help="refuse if the run has no record")
            p.add_argument("--jev-triage", action="store_true",
                           help="also ask Jev whether each assigned note is ready (needs TYPESAFE_API_KEY; advisory)")
        return p

    parser("plan", "read-only: show what `up` would do", overrides=True)
    u = parser("up", "prepare worktrees and start missing workers", overrides=True)
    u.add_argument("--expect-base", metavar="SHA", help="refuse unless the pinned base is exactly this commit")
    u.add_argument("--no-input", action="store_true", help="never prompt for branch names")
    u.add_argument("--settle", type=float, default=2.0, help="seconds to wait before the closing status (default 2)")
    s = parser("status", "git task, lock, Claude process and RC connectivity per worker")
    s.add_argument("--json", action="store_true")
    r = parser("release", "release this run's worktree locks (refused while a window is live)")
    r.add_argument("--lane", action="append")
    n = parser("next", "after a merged PR: put the worker on the next queued task, on a new branch")
    n.add_argument("--lane", action="append")
    n.add_argument("--task", help="task note to take instead of the head of the queue")
    n.add_argument("--branch", action="append", metavar="LANE=NAME", help="branch name instead of <stem>-<n>")
    n.add_argument("--plan", action="store_true", help="read-only: ask GitHub and show what would happen")
    n.add_argument("--no-input", action="store_true")
    h = parser("handoff", "file one draft PAI handoff with the launcher's own observations")
    h.add_argument("--pai", required=True, metavar="PROJECT")
    h.add_argument("--pai-bin", default=os.environ.get("PAI_BIN", ""))
    h.add_argument("--pai-config")
    h.add_argument("--task-id")
    a = ap.parse_args(argv)
    try:
        first = a.rc_cmd in ("plan", "up")
        m = Manifest(a.manifest, workers=getattr(a, "workers", None), assign=getattr(a, "assign", None),
                     branch=a.branch if first else None, socket=a.tmux_socket)
        if a.rc_cmd == "next":
            for lane in list(_pairs(a.branch, "--branch")) + list(a.lane or []):
                m.worker(lane)
        if a.rc_cmd == "plan":
            return cmd_plan(m, a)
        if a.rc_cmd == "up":
            return exclusive(m, cmd_up, a)
        if a.rc_cmd == "status":
            return cmd_status(m, a)
        if a.rc_cmd == "release":
            return exclusive(m, cmd_release, a)
        if a.rc_cmd == "next":
            return exclusive(m, cmd_next, a)
        summary, evidence, commit = handoff_facts(m)
        if not evidence:
            log("rc handoff: run %s has not been started; nothing to report" % m.run_id)
            return 1
        path = pai_cls(a.pai, a.pai_bin, a.pai_config).handoff(
            a.task_id or "RC-%s" % m.run_id, summary, {"commit": commit}, evidence, [],
            ["Owner: review each lane, then `pr plan` / `pr open`; merging stays with the owner"], [])
        log("draft handoff: %s" % path)
        return 0
    except ManifestError as e:
        print("rc: %s" % e, file=sys.stderr)
        return 2
