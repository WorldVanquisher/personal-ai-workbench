"""Agent launcher: one tmux session, and per worker one window, one worktree, one task branch
and one agent process (Claude Code with or without Remote Control, or Codex; see agents.py).

    pai_swarm.py rc plan    MANIFEST   read-only: what `up` would do, and every refusal
    pai_swarm.py rc up      MANIFEST   new run: fetch once, pin one base, new branches, start
                                       rerun:   recorded branches and base, start only what is missing
    pai_swarm.py rc status  MANIFEST   per worker: git task, lock, Claude process, RC connectivity
    pai_swarm.py rc next    MANIFEST   after GitHub reports a worker's PR merged: next queued task
    pai_swarm.py rc release MANIFEST   give the worktree locks back (never while Claude runs)
    pai_swarm.py rc cleanup MANIFEST   preview, or with --apply close exited workers' windows by
                                       their recorded tmux ids and release their locks

Preparation (fetch, pin, branch, refusals, resume record) is gitprep.py; the worktree lock
is wtlock.py; both are the ones the headless runner uses. This file adds the manifest, tmux
and the status evidence. Only `rc cleanup --apply` closes a window (an exited worker's, or a
live one with --stop-running too); nothing restarts a running agent or touches a worktree's
contents.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

import agents
import dispatch
import dispatch as dispatchmod  # watch_startup has a `dispatch` argument
import gitprep
import jev
import notes
import wtlock

SCHEMA = "pai_rc_manifest_v1"
RUN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,23}")
DEFAULT_COMMAND = ["claude", "--remote-control", "--name", "{name}"]
DISCONNECT_NOTICE = "remote control disconnected"
OWN_SCHEMA = "pai_rc_run_v1"
STARTING_GRACE = 120  # seconds a process may take before it counts as running without readiness evidence


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
        self.agent, self.mode, self.model, self.effort = "claude", "rc", None, None
        self.agent_source = self.mode_source = "default"

    @property
    def adapter(self):
        return agents.get(self.agent)

    def describe_agent(self):
        return "%s (%s%s%s)" % (self.agent, self.mode, ", model %s" % self.model if self.model else "",
                                ", effort %s" % self.effort if self.effort else "")


def _lane_values(items, what, lanes):
    """--agent/--mode/--model: LANE=VALUE for one lane, or a bare VALUE for every lane."""
    res = {}
    for item in items or []:
        k, sep, v = item.partition("=")
        if sep and k in lanes:
            res[k] = v
        elif sep:
            raise ManifestError("%s names lane %r, which the manifest does not have" % (what, k))
        elif "*" in res:
            raise ManifestError("%s without a lane may be given once (it applies to every lane)" % what)
        else:
            res["*"] = item
        if not (v if sep else item):
            raise ManifestError("%s needs a value, not %r" % (what, item))
    return res


def _pairs(items, what):
    res = {}
    for item in items or []:
        k, sep, v = item.partition("=")
        if not sep or not k or not v:
            raise ManifestError("%s must look like LANE=VALUE, not %r" % (what, item))
        res[k] = v
    return res


class Manifest:
    """The manifest file plus command-line overrides (--workers, --assign, --branch, --agent,
    --mode, --model). `data` takes the place of the file's contents (`pai run --dry-run`
    builds its manifest in memory and writes nothing)."""

    def __init__(self, path, workers=None, assign=None, branch=None, socket=None, agent=None, mode=None,
                 model=None, data=None, effort=None):
        self.path = os.path.abspath(os.path.expanduser(path))
        if data is not None:
            d = data
        else:
            try:
                with open(self.path, encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError) as e:
                raise ManifestError("cannot read manifest %s: %s" % (self.path, e))
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            raise ManifestError("manifest schema must be %s" % SCHEMA)
        self.data = d
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
        # `command` is the original Claude-only launch line. It still works exactly as before;
        # a manifest without it chooses an agent per lane instead.
        self.command = d.get("command")
        if self.command is not None and not (isinstance(self.command, list) and self.command
                                             and all(isinstance(x, str) for x in self.command)):
            raise ManifestError("command must be a list of strings (an argv, not a shell line)")
        programs = d.get("agents") or {}
        if not isinstance(programs, dict):
            raise ManifestError("agents must map an agent name to {\"command\": [program, ...]}")
        self.programs = {}
        for name, cfg in programs.items():
            if not agents.get(name):
                raise ManifestError("agents names %r; the known agents are %s" % (name, ", ".join(agents.names())))
            prog = (cfg or {}).get("command") if isinstance(cfg, dict) else None
            if prog is not None:
                if not (isinstance(prog, list) and prog and all(isinstance(x, str) for x in prog)):
                    raise ManifestError("agents.%s.command must be a list of strings (the program, an argv)" % name)
                self.programs[name] = prog
        self.env = d.get("env") or {}
        if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) and isinstance(v, str) for k, v in self.env.items()):
            raise ManifestError("env must map variable names to strings")
        self.tasks_dir = absolute(d["tasks"]) if d.get("tasks") else None
        self.review = d.get("review") or {}
        self.dispatch = bool(d.get("dispatch"))

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
        cli = {"agent": _lane_values(agent, "--agent", lanes), "mode": _lane_values(mode, "--mode", lanes),
               "model": _lane_values(model, "--model", lanes), "effort": _lane_values(effort, "--effort", lanes)}
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
            wk = Worker(lane, absolute(w.get("worktree") or lane, self.root), b,
                        self._fmt(w.get("name") or "{run_id} {lane}", vals), assign.get(lane) or w.get("task"), src)
            self._choose_agent(wk, w, d, cli)
            self.workers.append(wk)

    def _choose_agent(self, wk, w, d, cli):
        """Agent, launch mode and model for one lane. Precedence: --x LANE=V, --x V, the
        worker's field, the manifest's top-level field, the default. Agent and model are
        separate choices: a model never selects an agent."""
        def pick(key, default):
            for src, v in (("command line", cli[key].get(wk.lane)), ("command line", cli[key].get("*")),
                           ("manifest", w.get(key)), ("manifest", d.get(key))):
                if v is not None:
                    return str(v), src
            return default, "default"

        wk.agent, wk.agent_source = pick("agent", "claude")
        ad = agents.get(wk.agent)
        if not ad:
            raise ManifestError("lane %s: unknown agent %r (known: %s)" % (wk.lane, wk.agent, ", ".join(agents.names())))
        if self.command is not None:
            if wk.agent != "claude":
                raise ManifestError("lane %s: `command` is the Claude-only launch line; to run %s, remove `command` and "
                                    "name the program in agents.<name>.command" % (wk.lane, wk.agent))
            default_mode = "rc" if "--remote-control" in self.command else "local"
        else:
            default_mode = ad.default_mode()
        wk.mode, wk.mode_source = pick("mode", default_mode)
        if wk.mode not in ad.modes:
            raise ManifestError("lane %s: %s has no launch mode %r (it supports: %s). An unsupported mode is refused, "
                                "never replaced by another" % (wk.lane, wk.agent, wk.mode, ", ".join(ad.modes)))
        if self.command is not None and wk.mode != default_mode:
            raise ManifestError("lane %s: mode %s does not match the manifest's `command`, which %s --remote-control"
                                % (wk.lane, wk.mode, "has" if default_mode == "rc" else "lacks"))
        wk.model, msrc = pick("model", None)
        if wk.model is not None:
            if self.command is not None:
                raise ManifestError("lane %s: a model cannot be added to the fixed `command` launch line; put "
                                    "--model in `command`, or drop `command` and use agent/model" % wk.lane)
            problem = ad.model_problem(wk.model)
            if problem:
                raise ManifestError("lane %s: %s" % (wk.lane, problem))
        wk.effort, _ = pick("effort", None)
        if wk.effort is not None:
            if self.command is not None:
                raise ManifestError("lane %s: a reasoning effort cannot be added to the fixed `command` launch line"
                                    % wk.lane)
            problem = ad.effort_problem(wk.effort)
            if problem:
                raise ManifestError("lane %s: %s" % (wk.lane, problem))

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

    def program(self, w):
        """The lane's program argv (before mode and model options)."""
        if self.command is not None:
            return self.command[:1]
        return self.programs.get(w.agent) or [w.adapter.program]

    def argv_program(self, w):
        """The executable the lane will start (unformatted placeholders aside)."""
        return self.program(w)[0]

    def argv(self, w, rec, rl):
        vals = {"name": w.name, "lane": w.lane, "worktree": w.worktree, "branch": rl["branch"],
                "base_sha": rec["base_sha"], "task": rl.get("note") or "",
                "task_path": os.path.join(self.tasks_dir, rl["note"] + ".md") if self.tasks_dir and rl.get("note") else ""}
        if self.command is not None:
            argv = [self._fmt(x, vals) for x in self.command]
        else:
            argv = w.adapter.argv([self._fmt(x, vals) for x in self.program(w)], w.mode, w.name, w.model, w.effort)
        # tmux starts the pane with its server's PATH, not this shell's: pin the program now.
        return [shutil.which(argv[0]) or argv[0]] + argv[1:]

    def pane_env(self, w, rec, rl):
        """(variables to set, variables to remove) for the lane's process. SSH_AUTH_SOCK is
        this shell's, so a tmux server started earlier from another login cannot hand the
        worker a stale agent socket; when this shell has none, the server's is removed too."""
        env = dict(self.env)
        env.update(PAI_RC_RUN=self.run_id, PAI_RC_LANE=w.lane, PAI_TASK_ID=rec["task_id"],
                   PAI_TASK_REMOTE=rec["repo"]["remote"], PAI_TASK_BASE_SHA=rec["base_sha"],
                   PAI_TASK_BRANCH=rl["branch"], PAI_TASK_WORKTREE=w.worktree, PAI_TASK_NOTE=rl.get("note") or "",
                   PAI_AGENT=w.agent)
        # where pai reads this agent's evidence (session records, transcript, rollout): the same for
        # the agent, even when the tmux server was started from another environment (another HOME)
        if w.agent == "claude" and "CLAUDE_CONFIG_DIR" not in self.env and os.environ.get("CLAUDE_CONFIG_DIR"):
            env["CLAUDE_CONFIG_DIR"] = os.environ["CLAUDE_CONFIG_DIR"]
        if w.agent == "codex" and "CODEX_HOME" not in self.env and os.environ.get("CODEX_HOME"):
            env["CODEX_HOME"] = os.environ["CODEX_HOME"]
        unset = []
        if "SSH_AUTH_SOCK" not in self.env:
            if os.environ.get("SSH_AUTH_SOCK"):
                env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
            else:
                unset.append("SSH_AUTH_SOCK")
        return env, unset

    def claude_dir(self):
        cfg = self.env.get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude")
        return os.path.expanduser(cfg)

    def sessions_dir(self):
        return os.path.join(self.claude_dir(), "sessions")

    def codex_home(self):
        return os.path.expanduser(self.env.get("CODEX_HOME") or os.environ.get("CODEX_HOME") or os.path.join("~", ".codex"))


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


def agent_fields(w):
    return {"agent": w.agent, "mode": w.mode, "model": w.model, "effort": w.effort}


def agent_mismatch(w, rl):
    """A resume keeps the lane's recorded agent, mode and model. A record written before
    agents could be chosen is a Claude lane."""
    have = (rl.get("agent") or "claude", rl.get("mode") or w.mode if not rl.get("agent") else rl.get("mode"),
            rl.get("model") if rl.get("agent") else w.model, rl.get("effort") if rl.get("agent") else w.effort)
    want = (w.agent, w.mode, w.model, w.effort)
    if have == want:
        return []
    return ["lane %s was started with %s (%s%s%s); a resume never changes the agent, mode, model or effort (asked for "
            "%s). Use a new run_id for a different agent" % (
                w.lane, have[0], have[1], ", model %s" % have[2] if have[2] else "", ", effort %s" % have[3] if have[3] else "",
                w.describe_agent())]


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
        cont = m.data.get("continues")  # `pai run --continue`: start at the handed-off head, keep its base
        lanes = [gitprep.Lane(w.lane, w.branch, w.worktree, dict({"stem": w.branch, "note": w.task}, **agent_fields(w),
                                                                 **({"start": cont["head"]} if cont else {})))
                 for w in m.workers]
        meta = {"run": m.run_id, "gen": 1}
        if cont:
            meta["continues"] = {k: cont.get(k) for k in ("id", "from", "head", "branch", "agent")}
        return [gitprep.Spec(m.run_id, lanes=lanes, want="new", meta=meta, pinned_base=cont["base"] if cont else None,
                             **common)], problems
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
        if rl.get("handed_off"):  # continued in another session: never resumed here
            w.branch, w.task = rl["branch"], rl.get("note")
            log("HANDED OFF %-11s %s; not resumed" % (w.lane, describe_handoff(rl["handed_off"])))
            continue
        if w.branch_source in ("command line", "prompt") and w.branch != rl["branch"]:
            problems.append("lane %s is recorded on branch %s; a resume never renames it" % (w.lane, rl["branch"]))
        problems += agent_mismatch(w, rl)
        w.branch, w.task = rl["branch"], rl.get("note")
        groups.setdefault(rec["task_id"], (rec, []))[1].append(gitprep.Lane(w.lane, rl["branch"], w.worktree))
    specs = [gitprep.Spec(tid, lanes=lanes, want="resume", meta=rec.get("meta"), **common)
             for tid, (rec, lanes) in groups.items()]
    return specs, problems


# ---------------------------------------------------------------- tmux and processes

class Tmux:
    FIELDS = ("window_id", "window_name", "pane_dead", "pane_dead_status", "pane_pid", "pane_id", "pane_current_command",
              "session_id", "session_name", "window_panes", "pid", "socket_path")

    def __init__(self, socket=None):
        self.bin = os.environ.get("PAI_SWARM_TMUX") or shutil.which("tmux")
        self.socket = socket
        self.base = [self.bin or "tmux"] + self.socket_args(socket)
        self.env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}

    @staticmethod
    def socket_args(socket):
        if not socket:
            return []
        return ["-S", socket] if os.sep in socket else ["-L", socket]

    def run(self, *args):
        return subprocess.run(self.base + list(args), capture_output=True, text=True, env=self.env,
                              stdin=subprocess.DEVNULL, timeout=30)

    def has_session(self, session):
        return bool(self.bin) and self.run("has-session", "-t", "=" + session).returncode == 0

    def _rows(self, *args):
        if not self.bin:
            return []
        p = self.run(*(list(args) + ["-F", "\t".join("#{%s}" % f for f in self.FIELDS)]))
        return [dict(zip(self.FIELDS, line.split("\t"))) for line in p.stdout.splitlines()] if p.returncode == 0 else []

    def windows(self, session):
        """{window name: [window dict, ...]}; empty when the session or server does not exist."""
        res = {}
        for w in self._rows("list-windows", "-t", "=" + session):
            res.setdefault(w["window_name"], []).append(w)
        return res

    def panes(self):
        """Every pane on this server (all sessions), with its window and session ids."""
        return self._rows("list-panes", "-a")

    def command(self, *args):
        """The exact tmux command line an operator can paste, for this socket."""
        return " ".join(shlex.quote(x) for x in ["tmux"] + self.socket_args(self.socket) + list(args))

    @staticmethod
    def shell_line(argv, env, unset=()):
        # One shell word list run by tmux through `sh -c`; `exec` makes the agent the pane process.
        return "exec env " + "".join("-u %s " % shlex.quote(u) for u in unset) + \
            " ".join(shlex.quote("%s=%s" % kv) for kv in sorted(env.items())) + " " + " ".join(shlex.quote(a) for a in argv)

    IDS = "#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{pid}\t#{socket_path}"

    def start(self, session, lane, cwd, argv, env, unset=()):
        """(ok, error, ids): ids are the new window's stable tmux ids and its server."""
        target = "=%s:%s" % (session, lane)
        if self.has_session(session):
            first = ["new-window", "-d", "-P", "-F", self.IDS, "-t", "=%s:" % session, "-n", lane, "-c", cwd]
        else:
            first = ["new-session", "-d", "-P", "-F", self.IDS, "-s", session, "-n", lane, "-c", cwd, "-x", "200", "-y", "50"]
        # Same tmux invocation, so the window keeps its pane (and exit status) even if the
        # command exits at once.
        p = self.run(*(first + [self.shell_line(argv, env, unset), ";", "set-option", "-w", "-t", target,
                                "remain-on-exit", "on"]))
        ids = dict(zip(("session_id", "window_id", "pane_id", "pane_pid", "server_pid", "socket_path"),
                       (p.stdout.strip().splitlines() or [""])[0].split("\t")))
        return p.returncode == 0, p.stderr.strip(), ids

    def respawn(self, pane_id, cwd, argv, env, unset=()):
        # No -k: tmux refuses unless the pane's process has already exited.
        p = self.run("respawn-pane", "-t", pane_id, "-c", cwd, self.shell_line(argv, env, unset))
        return p.returncode == 0, p.stderr.strip()

    def capture(self, pane_id, history=False):
        """The visible screen, or with history=True the whole scrollback as well."""
        p = self.run("capture-pane", "-p", "-J", *(["-S", "-"] if history else []), "-t", pane_id)
        return p.stdout if p.returncode == 0 else ""

    def pane_pid(self, pane_id):
        p = self.run("display-message", "-p", "-t", pane_id, "#{pane_pid}")
        return p.stdout.strip() if p.returncode == 0 else ""


def process_table():
    # Full command lines whatever the terminal: ps cuts `args` to $COLUMNS (procps honours it even
    # when its output is a pipe), and a cut path no longer names the lane's program.
    env = {k: v for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")}
    try:
        p = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,args="], capture_output=True, text=True, timeout=15,
                           env=env)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    table = {}
    for line in p.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def find_claude(pane_pid, command, sessions_dir, table):
    """Pid of the agent process in a pane (the pane's process or a descendant), or None.
    `command` is the lane's program argv; Claude is also recognised by its session record."""
    want = agents.process_names(command)
    queue, seen = [pane_pid], set()
    while queue:
        pid = queue.pop(0)
        if pid in seen or pid not in table:
            continue
        seen.add(pid)
        args = table[pid][1].split()
        if (args and (os.path.basename(args[0]) in want or (len(args) > 1 and os.path.basename(args[1]) in want))) or (
                sessions_dir and os.path.isfile(
                os.path.join(sessions_dir, "%d.json" % pid))):
            return pid
        queue += [c for c, (pp, _) in table.items() if pp == pid]
    return None


def own_record(pid, worktree, sessions_dir):
    """Claude Code's session record for exactly this process (pid and start time) in this
    worktree, or None. The same identity rules as rc_evidence; its age does not matter here:
    `status` is rewritten whenever Claude's input state changes."""
    if not pid or not sessions_dir:
        return None
    try:
        with open(os.path.join(sessions_dir, "%d.json" % pid), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("pid") != pid:
        return None
    start = wtlock.proc_start(pid)
    if start is not None and str(rec.get("procStart") or "") != str(start):
        return None
    return rec if gitprep.canon(str(rec.get("cwd") or "")) == worktree else None


def probe(m, w, proc):
    """agents.Probe for the lane: what its adapter may read about input state and submissions."""
    pid = proc.get("pid") if proc.get("state") == "running" else None
    claude = w.agent == "claude"
    return agents.Probe(w.worktree, pid, own_record(pid, w.worktree, m.sessions_dir()) if claude and pid else None,
                        m.claude_dir() if claude else None, m.codex_home() if w.agent == "codex" else None)


def evidence_fn(m, w, rl, pr):
    """entry -> the agent's own evidence that this task's prompt was submitted, or None."""
    return lambda e: w.adapter.submitted(pr, e.get("tag") or dispatch.tag(m, w.lane, rl["note"]), dispatch.since(e))


def stale_seconds():
    try:
        return max(1, int(os.environ.get("PAI_RC_STALE_SECONDS") or 900))
    except ValueError:
        return 900


def _notice_path(pid, start):
    return os.path.join(wtlock.home(), "rc-notices", "%s-%s.json" % (pid, start or "x"))


def rc_evidence(pid, worktree, sessions_dir, pane_text):
    """(state, evidence). `verified` needs positive, current evidence from Claude Code's own
    session record for this exact process and directory; a running process alone proves
    nothing. A record not updated for PAI_RC_STALE_SECONDS (900 s) is inconclusive, and so is
    a record whose bridge session has not changed since a disconnect notice was seen in this
    pane (the notice may have scrolled off or been cleared since)."""
    notice = DISCONNECT_NOTICE in pane_text.lower()
    start = wtlock.proc_start(pid)
    try:
        with open(os.path.join(sessions_dir, "%d.json" % pid), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return ("disconnected", "pane shows a Remote Control disconnect notice") if notice else \
            ("unverified", "Claude wrote no session record for pid %d" % pid)
    if rec.get("pid") != pid:
        return "unverified", "the session record belongs to another process"
    if start is not None:  # where the start time is known, the record must carry the same one
        if not rec.get("procStart"):
            return "unverified", "the session record has no process start time, so it cannot be tied to pid %d" % pid
        if str(rec["procStart"]) != str(start):
            return "unverified", "the session record belongs to an earlier process with the same pid"
    if gitprep.canon(str(rec.get("cwd") or "")) != worktree:
        return "unverified", "the session record is for another directory"
    bridge = rec.get("bridgeSessionId")
    if notice:
        try:  # remember it: a notice that later disappears is not evidence of a reconnect
            os.makedirs(os.path.dirname(_notice_path(pid, start)), exist_ok=True)
            with open(_notice_path(pid, start), "w", encoding="utf-8") as f:
                json.dump({"bridge": bridge, "seen": time.time()}, f)
        except OSError:
            pass
    if not bridge:
        return ("disconnected" if notice else "not-connected",
                "Claude's session record has no Remote Control bridge session" + (
                    "; pane shows a disconnect notice" if notice else ""))
    if notice:
        return "unverified", "bridge session recorded, but the pane shows a disconnect notice"
    try:
        with open(_notice_path(pid, start), encoding="utf-8") as f:
            seen = json.load(f)
        if seen.get("bridge") == bridge:
            return "unverified", ("a disconnect notice was seen in this pane and the bridge session recorded since is "
                                  "the same one; inconclusive until Claude records a new bridge session")
    except (OSError, ValueError):
        pass
    if not isinstance(rec.get("updatedAt"), (int, float)):
        return "unverified", "the session record has no update time, so its bridge session may be stale"
    age = max(0, int(time.time() - rec["updatedAt"] / 1000))
    if age > stale_seconds():
        return "unverified", ("bridge session recorded, but the record was last updated %ds ago (stale after %ds); "
                              "inconclusive" % (age, stale_seconds()))
    return "verified", "bridge session recorded by Claude for this process and worktree, record updated %ds ago" % age


# ---------------------------------------------------------------- run ownership (stable tmux ids)

def own_path(m, cdir=None):
    cdir = cdir or gitprep.common_dir(m.repo) or m.repo
    return os.path.join(wtlock.home(), "rc", "%s-%s.json" % (hashlib.sha256(cdir.encode()).hexdigest()[:12], m.run_id))


def load_own(m, cdir=None):
    """The run's ownership record: which tmux server, session, window and pane each lane's
    worker was started in. {} when there is none (a run started before it existed)."""
    try:
        with open(own_path(m, cdir), encoding="utf-8") as f:
            own = json.load(f)
        return own if own.get("schema") == OWN_SCHEMA and own.get("run_id") == m.run_id else {}
    except (OSError, ValueError):
        return {}


def save_own(m, own, cdir=None):
    path = own_path(m, cdir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    own.update(schema=OWN_SCHEMA, run_id=m.run_id, session=m.session, socket=m.socket, repo=m.repo)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(own, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def record_window(m, w, ids, task_id):
    own = load_own(m)
    lanes = own.setdefault("lanes", {})
    pid = int(ids.get("pane_pid") or 0)
    lanes[w.lane] = {"session_id": ids.get("session_id"), "window_id": ids.get("window_id"),
                     "pane_id": ids.get("pane_id"), "pane_pid": pid, "pane_start": wtlock.proc_start(pid) if pid else None,
                     "server_pid": ids.get("server_pid"), "server_start": wtlock.proc_start(int(ids["server_pid"]))
                     if (ids.get("server_pid") or "").isdigit() else None, "socket_path": ids.get("socket_path"),
                     "agent": w.agent, "mode": w.mode, "model": w.model, "task_id": task_id, "started_at": gitprep.now()}
    save_own(m, own)


def same_server(rec_lane, win):
    """True when the window listing comes from the server the lane's window was started on."""
    return bool(rec_lane) and str(rec_lane.get("server_pid") or "") == str(win.get("pid") or "") and (
        rec_lane.get("server_start") is None or str(rec_lane["server_start"]) == str(wtlock.proc_start(int(win["pid"]))
                                                                                     if str(win.get("pid")).isdigit() else ""))


def lane_process(m, tmux, w, wins=None, table=None, own=None):
    """{'state': no-window|ambiguous|mismatched|exited|running|no-claude, 'pid', 'exit', 'window'}"""
    wins = tmux.windows(m.session) if wins is None else wins
    own = load_own(m) if own is None else own
    rec = (own.get("lanes") or {}).get(w.lane) or {}
    by_id = {x["window_id"]: x for ws in wins.values() for x in ws}
    mine = wins.get(w.lane) or []
    if len(mine) > 1:
        return {"state": "ambiguous", "detail": "%d windows are named %s" % (len(mine), w.lane)}
    recorded = rec.get("window_id") if rec.get("window_id") and any(same_server(rec, x) for x in by_id.values()) else None
    if not mine:
        if recorded and recorded in by_id:
            return {"state": "mismatched", "window": by_id[recorded],
                    "detail": "this run's window %s is now named %s" % (recorded, by_id[recorded]["window_name"])}
        return {"state": "no-window"}
    win = mine[0]
    if win["pane_dead"] == "1":
        res = {"state": "exited", "exit": win["pane_dead_status"] or "?", "window": win}
    else:
        pid = find_claude(int(win["pane_pid"] or 0), m.program(w), m.sessions_dir() if w.agent == "claude" else None,
                          process_table() if table is None else table)
        if pid is None:
            return {"state": "no-claude", "window": win, "detail": "pane runs %s" % win["pane_current_command"]}
        res = {"state": "running", "pid": pid, "window": win}
    if recorded and recorded != win["window_id"]:
        return {"state": "mismatched", "window": win,
                "detail": "the window named %s is %s, but this run started %s" % (w.lane, win["window_id"], recorded)}
    return res


def proc_age(pid):
    """Seconds since the process started (Linux), or None."""
    start = wtlock.proc_start(pid)
    try:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
        return up - int(start) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, TypeError, IndexError):
        return None


PHASES = {"no-window": "no window", "exited": "exited", "no-claude": "window without the agent",
          "ambiguous": "ambiguous", "mismatched": "mismatched", "starting": "starting",
          "waiting-for-trust": "waiting for folder trust", "running": "running"}


def lane_phase(m, w, proc, screen, pr=None):
    """(phase, input_ready, why). The process state refined by what the agent shows: its
    folder-trust prompt, its idle input box (agents.Adapter.input_state), or (while it is
    young) no sign of life yet."""
    if proc["state"] != "running":
        return proc["state"], False, "no agent process"
    ad = w.adapter
    if ad.waiting_for_trust(screen):
        return "waiting-for-trust", False, "its folder-trust prompt is on screen"
    ready, why = ad.input_state(screen, pr)
    if ready:
        return "running", True, why
    if w.agent == "claude" and ad.has_rc(w.mode):
        alive = os.path.isfile(os.path.join(m.sessions_dir(), "%d.json" % proc["pid"]))
    else:
        alive = bool(screen.strip())
    age = proc_age(proc["pid"])
    return ("starting" if not alive and age is not None and age < STARTING_GRACE else "running"), False, why


# ---------------------------------------------------------------- plan / up

def agent_problems(m):
    """Every lane's program must exist and support its launch mode, checked before anything
    is fetched, locked or started."""
    problems, cache = [], {}
    for w in m.workers:
        exe = m.argv_program(w)
        path = shutil.which(exe) or (exe if os.path.isabs(exe) and os.access(exe, os.X_OK) else None)
        if not path:
            problems.append("lane %s: %s is not installed or not on PATH (agent %s; `pai doctor` / "
                            "`pai setup --install-missing`)" % (w.lane, exe, w.agent))
            continue
        if m.command is None and len(m.program(w)) == 1:  # the agent's own CLI, not a wrapper
            why = w.adapter.probe(path, w.mode, cache)
            if why:
                problems.append("lane %s: %s" % (w.lane, why))
        caps = w.adapter.capabilities()  # the same representation `pai doctor` shows
        if m.dispatch and not caps["transcript"]["delivery_evidence"]:
            problems.append("lane %s: %s keeps no transcript pai can read, so a dispatched task could never be shown "
                            "delivered. Repair: `pai run --no-dispatch` and give it the task yourself (`pai paste`), or "
                            "choose a backend with delivery evidence (%s)" % (
                                w.lane, w.adapter.display, ", ".join(n for n in agents.names()
                                                                     if agents.get(n).capabilities()["transcript"]["delivery_evidence"])))
        if w.mode not in caps["launch"]["modes"]:
            problems.append("lane %s: %s has no launch mode %s (it has: %s)" % (w.lane, w.adapter.display, w.mode,
                                                                                ", ".join(caps["launch"]["modes"])))
    return problems


def preflight(m, tmux, new):
    """Launcher-level refusals, checked before anything is fetched or switched."""
    problems = []
    if not tmux.bin:
        problems.append("tmux is not installed (run `pai_swarm.py doctor`)")
    problems += agent_problems(m)
    wins = tmux.windows(m.session)
    for w in m.workers:
        n = len(wins.get(w.lane) or [])
        if n > 1:
            problems.append("tmux session %s has %d windows named %s; close the extra one yourself" % (m.session, n, w.lane))
        elif n and new:
            problems.append("tmux session %s already has a window named %s, but this run has not started yet; "
                            "close that window yourself or use another session name" % (m.session, w.lane))
    return problems


def ssh_preflight(m, nothing="Nothing was locked, fetched, created or started."):
    """Problems (with the exact ssh-agent steps) when the remote cannot be reached without a
    prompt; [] when it can, or when it is not an SSH remote."""
    res = gitprep.remote_auth(m.repo, m.remote, "refs/heads/" + m.base_branch)
    log("ssh       %s: %s" % (m.remote, res["detail"]))
    return [] if res["ok"] else gitprep.auth_lines(res, nothing)


def show_plan(m, specs, plans, problems, tmux):
    log("manifest  %s" % m.path)
    log("run       %s   tmux session %s%s" % (m.run_id, m.session, "  (socket %s)" % m.socket if m.socket else ""))
    wins, table, own = tmux.windows(m.session), process_table(), load_own(m)
    for pl in plans:
        log(gitprep.render_plan(pl))
        for ln in pl.spec.lanes:
            if ln.problems:
                continue
            w = m.worker(ln.lane)
            proc = lane_process(m, tmux, w, wins, table, own)
            n = w.adapter.display
            what = {"no-window": "start a window and %s" % n, "exited": "restart %s in its window (it exited)" % n,
                    "running": "keep (%s is running)" % n, "no-claude": "leave alone (window without %s)" % n,
                    "ambiguous": "REFUSED", "mismatched": "REFUSED (%s)" % proc.get("detail")}[proc["state"]]
            log("lane      %-14s worker: %s%s" % (ln.lane, what, "; task note %s" % w.task if w.task else ""))
            log("          %-14s agent:  %s, program %s%s" % ("", w.describe_agent(), m.argv_program(w),
                                                            "" if w.adapter.has_rc(w.mode) else "; Remote Control: not supported"))
            caps = w.adapter.capabilities()
            log("          %-14s evidence: readiness from %s; delivery from %s" % (
                "", caps["readiness"]["source"].replace("-", " "),
                caps["transcript"]["location"] or "NOTHING (no transcript)"))
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
    wins, table, own = tmux.windows(m.session), process_table(), load_own(m)
    for w, rec, rl in lanes:
        if rl.get("handed_off"):
            log("HANDED OFF %-11s %s; not restarted" % (w.lane, describe_handoff(rl["handed_off"])))
            continue
        gate = gitprep.verify_ready(w.worktree, rl["branch"], rec["base_sha"])
        lock = wtlock.read(w.worktree)
        if not wtlock.owned(lock, "rc", m.run_id, w.lane):
            gate.append("this run does not hold the worktree lock (%s)" % wtlock.describe(lock))
        if gate:
            log("NOT STARTED %-12s %s" % (w.lane, "; ".join(gate)))
            failed += 1
            continue
        proc = lane_process(m, tmux, w, wins, table, own)
        argv, (env, unset) = m.argv(w, rec, rl), m.pane_env(w, rec, rl)
        if proc["state"] == "no-window":
            ok, err, ids = tmux.start(m.session, w.lane, w.worktree, argv, env, unset)
            verb = "started"
        elif proc["state"] == "exited":
            ok, err = tmux.respawn(proc["window"]["pane_id"], w.worktree, argv, env, unset)
            win = proc["window"]
            ids = {"session_id": win["session_id"], "window_id": win["window_id"], "pane_id": win["pane_id"],
                   "pane_pid": tmux.pane_pid(win["pane_id"]) if ok else "", "server_pid": win["pid"],
                   "socket_path": win["socket_path"]}
            verb = "restarted (the agent had exited with status %s)" % proc["exit"]
        elif proc["state"] == "running":
            log("kept        %-12s %s is running (pid %d); session and edits untouched" % (w.lane, w.adapter.display, proc["pid"]))
            continue
        else:
            log("LEFT ALONE  %-12s %s; nothing was changed. Close the window yourself, then rerun."
                % (w.lane, proc.get("detail", proc["state"])))
            failed += 1
            continue
        if ok:
            record_window(m, w, ids, rec["task_id"])
            rl["state"], rl["started_at"] = "started", gitprep.now()
            gitprep.save_record(rec)
            log("%-11s %-12s window %s:%s (%s), %s, branch %s" % (verb.split(" ")[0], w.lane, m.session, w.lane,
                                                                   ids.get("window_id") or "?", w.describe_agent(), rl["branch"]))
        else:
            log("FAILED      %-12s tmux: %s" % (w.lane, err))
            failed += 1
    return failed


def describe_handoff(h):
    return "handed off at %s (head %s)%s" % (h.get("at"), (h.get("head") or "?")[:12], "; continued in %s" % h["continued_by"]
                                              if h.get("continued_by") else "; continue it with `pai run --continue %s`"
                                              % h.get("note"))


def mark_notes(m, rec, lanes):
    """Write the assignment into each worker's task note (Obsidian)."""
    if not m.tasks_dir:
        return
    cont = (rec.get("meta") or {}).get("continues")
    for w in lanes:
        rl = rec["lanes"][w.lane]
        path = os.path.join(m.tasks_dir, "%s.md" % rl["note"]) if rl.get("note") else None
        if path and os.path.isfile(path):
            meta = {"status": "running", "worker": w.lane, "rc_run": m.run_id, "branch": rl["branch"],
                    "base_sha": rec["base_sha"], "remote": rec["repo"]["remote"], "worktree": w.worktree}
            if cont:  # this continuation is used: the same handed-off head is never continued twice from here
                meta.update(continued_marker=cont["id"], continued_in="%s/%s" % (m.run_id, w.lane))
            notes.Task(path).update(meta=meta)


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
    if new and not m.data.get("continues"):  # the fetch below must not stop at a passphrase prompt
        bad = ssh_preflight(m)
        if bad:
            for line in bad:
                log("REFUSED   %s" % line if not line.startswith("    ") and not line.startswith("Run ") else line)
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
    only = set(getattr(a, "lane", None) or [])
    failed = ensure_workers(m, tmux, [x for x in started if not only or x[0].lane in only])
    rows = watch_startup(m, tmux, a.settle, dispatch=m.dispatch and not getattr(a, "no_dispatch", False))
    report_startup(m, tmux, rows, pai=getattr(a, "pai", False))
    log()
    code = cmd_status(m, a, quiet_exit=True, pai=getattr(a, "pai", False))
    return 1 if failed else code


def watch_startup(m, tmux, seconds, dispatch=False, lanes=None, force=()):
    """Each lane on its own: poll until no lane is still `starting` (and, with dispatch, every
    ready lane has its task and its submission evidence) or `seconds` have passed. A lane is
    sent its task only after it has been input-ready, with no trust prompt on screen, for
    dispatch.settle_seconds(). A lane waiting for folder trust or one that exited never holds
    up the others. `force`: lanes whose `unknown` dispatch the operator asked to send again.
    Returns the final status rows."""
    end = time.time() + max(0.0, seconds)
    settle, force, ready_since = dispatchmod.settle_seconds(), set(force), {}
    while True:
        rows, cur = collect_status(m, tmux)
        rows = [r for r in rows if not lanes or r["lane"] in lanes]
        now = time.time()
        if dispatch:
            for r in rows:
                w, rl = m.worker(r["lane"]), cur[r["lane"]][1]
                dispatchmod.record_delivered(m, w, rl, r["dispatch"])  # evidence found: keep it
                k = (r["lane"], r["process"].get("pid"))
                if not (r["phase"] == "running" and r["input_ready"]):
                    ready_since.pop(k, None)
                    continue
                first = ready_since.setdefault(k, now)
                want = r["dispatch"]["state"] == "pending" or (r["lane"] in force and r["dispatch"]["state"] == "unknown")
                if want and now - first >= settle:
                    r["dispatch"] = dispatch_lane(m, tmux, w, *cur[r["lane"]], force=r["lane"] in force)
                    force.discard(r["lane"])
        # A window whose agent is not found yet may still be between `sh -c` and the agent's exec.
        waiting = [r for r in rows if r["phase"] in ("starting", "no-claude") or (dispatch and (
            (r["phase"] == "running" and r["dispatch"]["state"] == "pending") or r["dispatch"]["state"] == "sent"
            or r["lane"] in force))]
        if not waiting or time.time() >= end:
            return rows
        time.sleep(0.25)


def dispatch_lane(m, tmux, w, rec, rl, force=False):
    wins, table, own = tmux.windows(m.session), process_table(), load_own(m)
    proc = lane_process(m, tmux, w, wins, table, own)
    if proc["state"] != "running":
        return dispatch.state(m, w, rl, proc, evidence_fn(m, w, rl, probe(m, w, proc)))
    pane, pid, start = proc["window"]["pane_id"], proc["pid"], wtlock.proc_start(proc["pid"])
    note = None
    if m.tasks_dir and rl.get("note") and os.path.isfile(os.path.join(m.tasks_dir, rl["note"] + ".md")):
        note = notes.Task(os.path.join(m.tasks_dir, rl["note"] + ".md"))
    return dispatch.deliver(m, tmux, w, rec, rl, proc, note, lambda: tmux.capture(pane),
                            lambda screen: w.adapter.input_state(screen, probe(m, w, proc)),  # re-reads the record
                            lambda screen: w.adapter.enter_blocked(screen, probe(m, w, proc)),
                            lambda e: evidence_fn(m, w, rl, probe(m, w, proc))(e),
                            lambda: wtlock.pid_alive(pid, start), force=force)


def attach_hint(m, tmux, r, pai=False):
    if pai:
        return "pai attach %s%s" % (r["lane"], " --run %s" % m.run_id)
    return tmux.command("attach-session", "-t", "=%s:%s" % (m.session, r["tmux"]["window_id"] or r["lane"]))


def report_startup(m, tmux, rows, pai=False):
    log("\nstartup   each lane on its own; a lane that waits does not hold up the others")
    for r in rows:
        p = r["process"]
        what = PHASES.get(r["phase"], r["phase"])
        if r["phase"] == "running":
            what += " (pid %s)" % p.get("pid")
        elif r["phase"] == "exited":
            what += " (status %s)" % p.get("exit")
        log("  %-12s %-24s %-28s task: %-10s rc: %s" % (r["lane"], "%s/%s" % (r["agent"]["name"], r["agent"]["mode"]), what,
                                                         r["dispatch"]["state"], r["rc"]["state"]))
    for r in rows:
        if r["phase"] == "waiting-for-trust":
            ad = agents.get(r["agent"]["name"])
            log("TRUST     lane %s: %s is asking whether to trust %s. Its task waits until you answer." % (
                r["lane"], r["agent"]["name"], r["worktree"]))
            log("          Open the prompt, %s:" % ad.trust_hint)
            log("            %s" % attach_hint(m, tmux, r, pai))
            log("          then %s to deliver its task." % ("`pai resume`" if pai else "rerun `rc up`"))
        elif r["dispatch"]["state"] == "unknown":
            log("DISPATCH  lane %s: unknown: %s" % (r["lane"], r["dispatch"].get("cause") or r["dispatch"]["detail"]))
            for i, step in enumerate(r["dispatch"].get("reconcile") or [], 1):
                log("          %d. %s" % (i, step))
        elif r["dispatch"]["state"] == "sent":
            log("DISPATCH  lane %s: %s" % (r["lane"], r["dispatch"]["detail"]))


# ---------------------------------------------------------------- status

def lane_status(m, tmux, w, rec, rl, wins, table, own=None, disp=None):
    st = {"lane": w.lane, "task_id": rec["task_id"], "generation": (rec.get("meta") or {}).get("gen", 1),
          "note": rl.get("note"), "remote": rec["repo"]["remote"], "remote_url": rec["repo"]["remote_url"],
          "base_branch": rec["repo"]["base_branch"], "base_sha": rec["base_sha"], "fetched_at": rec["fetched_at"],
          "branch": rl["branch"], "worktree": w.worktree, "prepared": rl["state"],
          "agent": {"name": w.agent, "mode": w.mode, "model": w.model, "program": m.argv_program(w)},
          "handed_off": rl.get("handed_off"), "continues": (rec.get("meta") or {}).get("continues")}
    if os.path.isdir(w.worktree):
        st["on_branch"] = gitprep.current_branch(w.worktree)
        st["head"] = gitprep.rev(w.worktree, "HEAD")
        st["ahead"] = gitprep.ahead(w.worktree, rec["base_sha"])
        st["uncommitted"] = len(gitprep.dirty(w.worktree))
        st["in_progress"] = gitprep.in_progress(w.worktree)
    lock = wtlock.read(w.worktree)
    st["lock"] = "this run" if wtlock.owned(lock, "rc", m.run_id, w.lane) else (
        "NOT HELD" if not lock else "held by " + wtlock.describe(lock))
    own = load_own(m) if own is None else own
    proc = lane_process(m, tmux, w, wins, table, own)
    st["process"] = {k: v for k, v in proc.items() if k != "window"}
    if proc["state"] == "running":
        age = proc_age(proc["pid"])
        st["process"]["age"] = int(age) if age is not None else None  # seconds since it started (Linux)
    win = proc.get("window") or {}
    st["tmux"] = {"socket": m.socket, "session": m.session, "window_id": win.get("window_id"),
                  "pane_id": win.get("pane_id")}
    screen = history = ""
    if proc["state"] == "running":
        screen, history = tmux.capture(win["pane_id"]), tmux.capture(win["pane_id"], history=True)
    pr = probe(m, w, proc)
    st["phase"], st["input_ready"], why = lane_phase(m, w, proc, screen, pr)
    st["trust"] = ({"state": "prompt-visible", "evidence": "%s's folder-trust prompt is on the visible screen; pai never "
                                                          "answers it" % w.adapter.display}
                   if st["phase"] == "waiting-for-trust" else
                   {"state": "not-shown", "evidence": "no folder-trust prompt on the visible screen (this alone does not "
                                                      "prove the folder is trusted)"}
                   if proc["state"] == "running" else {"state": "unknown", "evidence": "no agent process"})
    st["input"] = {"ready": st["input_ready"], "evidence": why}
    if not w.adapter.has_rc(w.mode):
        state, evidence = "not-supported", w.adapter.rc_unsupported(w.mode)
    elif st["phase"] == "waiting-for-trust":
        state, evidence = "not-connected", "the folder-trust prompt is open; Remote Control starts only after it is answered"
    elif proc["state"] == "running":
        state, evidence = rc_evidence(proc["pid"], w.worktree, m.sessions_dir(), history)
    else:
        state, evidence = "not-running", "no agent process to be connected"
    st["rc"] = {"state": state, "evidence": evidence}
    st["dispatch"] = dispatch.state(m, w, rl, proc, evidence_fn(m, w, rl, pr), disp)
    return st


def collect_status(m, tmux):
    cdir = gitprep.common_dir(m.repo)
    cur = current_lanes(cdir, m.run_id) if cdir else {}
    wins, table, own, disp = tmux.windows(m.session), process_table(), load_own(m), dispatch.load(m)
    return [lane_status(m, tmux, w, *cur[w.lane], wins, table, own, disp) for w in m.workers if w.lane in cur], cur


def lane_ok(m, r):
    return (r["process"]["state"] == "running" and r["phase"] == "running"
            and r["rc"]["state"] in ("verified", "not-supported")
            and (not m.dispatch or r["dispatch"]["state"] in ("delivered", "none")))


def cmd_status(m, a, quiet_exit=False, pai=False):
    import runstate
    tmux = Tmux(m.socket)
    rows, cur = collect_status(m, tmux)
    ok = bool(rows) and len(rows) == len(m.workers) and all(lane_ok(m, r) for r in rows)
    summary = runstate.annotate(m, rows)
    if getattr(a, "json", False):
        for r in rows:
            r["dispatch"].pop("upgrade", None)  # internal: evidence a mutating command will record
        json.dump({"schema": runstate.SCHEMA, "run": runstate.run_key(m), "run_id": m.run_id, "session": m.session,
                   "socket": m.socket, "ok": ok, "summary": summary, "lanes": rows}, sys.stdout, indent=1)
        print()
        return 0 if ok else 1
    width = shutil.get_terminal_size((100, 24)).columns
    if rows and (getattr(a, "brief", False) or width < 80):
        runstate.brief(m, rows, summary, width, log)
        return 0 if ok else 1
    log("run       %s   tmux session %s (%s)%s" % (m.run_id, m.session, "exists" if tmux.has_session(m.session) else "absent",
                                                   "  socket %s" % m.socket if m.socket else ""))
    if not rows:
        log("No task has been started for this run. `%s` shows what would happen." % ("pai run --dry-run" if pai else "rc plan"))
        return 1
    log("summary   %s" % runstate.summary_line(summary))
    for r in rows:
        p, branch_now = r["process"], r.get("on_branch")
        log("lane %s" % r["lane"])
        log("  lifecycle %s: %s" % (r["lifecycle"]["state"], r["lifecycle"]["why"]))
        log("  id        %s" % r["ids"]["task"])
        log("  agent     %s%s" % (m.worker(r["lane"]).describe_agent(), "  window %s pane %s" % (
            r["tmux"]["window_id"], r["tmux"]["pane_id"]) if r["tmux"]["window_id"] else ""))
        log("  task      %s%s" % (r["task_id"], "  note %s" % r["note"] if r["note"] else ""))
        log("  remote    %s %s" % (r["remote"], r["remote_url"]))
        log("  base      %s @ %s (%s)" % (r["base_branch"], r["base_sha"], "fetched %s" % r["fetched_at"] if r["fetched_at"]
                                             else "pinned from the handed-off session; nothing fetched"))
        log("  branch    %s%s" % (r["branch"], "" if "head" not in r else
                                  "  HEAD %s, %s commit(s) ahead of base, %d uncommitted path(s)%s" % (
                                      (r["head"] or "?")[:12], r["ahead"], r["uncommitted"],
                                      "" if branch_now == r["branch"] else "  WORKTREE IS ON %s" % (branch_now or "a detached HEAD"))))
        log("  worktree  %s%s" % (r["worktree"], "" if "head" in r else "  MISSING"))
        log("  lock      %s" % r["lock"])
        log("  state     %s%s" % (PHASES.get(r["phase"], r["phase"]), "" if r["phase"] != "waiting-for-trust" else
                                  ": %s" % attach_hint(m, tmux, r, pai)))
        log("  trust     %s: %s" % (r["trust"]["state"], r["trust"]["evidence"]))
        log("  input     %s: %s" % ("ready" if r["input"]["ready"] else "not ready", r["input"]["evidence"]))
        log("  dispatch  %s: %s" % (r["dispatch"]["state"], r["dispatch"].get("cause") or r["dispatch"]["detail"]))
        if r["dispatch"].get("evidence"):
            log("            evidence: %s" % r["dispatch"]["evidence"])
        for i, step in enumerate(r["dispatch"].get("reconcile") or [], 1):
            log("            %d. %s" % (i, step))
        log("  %-9s %s" % (r["agent"]["name"], {
            "running": "running (pid %s)" % p.get("pid"), "exited": "exited (status %s)" % p.get("exit"),
            "no-window": "no window", "no-claude": "window without the agent process (%s)" % p.get("detail"),
            "ambiguous": "ambiguous: %s" % p.get("detail"), "mismatched": "mismatched: %s" % p.get("detail")}[p["state"]]))
        log("  rc        %s: %s" % (r["rc"]["state"], r["rc"]["evidence"]))
    missing = [w.lane for w in m.workers if w.lane not in cur]
    if missing:
        log("not started: %s" % ", ".join(missing))
    if not ok and not quiet_exit:
        log("\nNot every worker is running and ready (Remote Control verified where it was requested%s). "
            "`%s` restarts only missing workers." % (", task delivered" if m.dispatch else "",
                                                     "pai resume" if pai else "rc up"))
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
        if proc["state"] in ("running", "no-claude", "ambiguous", "mismatched"):
            log("%-14s KEPT: its tmux window is still live (%s). Stop that session yourself first."
                % (w.lane, proc["state"]))
            code = 1
            continue
        wtlock.release(w.worktree, "rc", m.run_id, w.lane)
        log("%-14s lock released; branch, worktree and edits are untouched" % w.lane)
    return code


# ---------------------------------------------------------------- cleanup

def leftover_processes(m, w):
    """Pids of processes that still carry this lane's markers (PAI_RC_RUN, PAI_RC_LANE and
    PAI_TASK_WORKTREE, set on the worker and inherited by what it started). None where /proc
    cannot be read."""
    if not os.path.isdir("/proc"):
        return None
    want = {("PAI_RC_RUN=%s" % m.run_id).encode(), ("PAI_RC_LANE=%s" % w.lane).encode(),
            ("PAI_TASK_WORKTREE=%s" % w.worktree).encode()}
    found = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == os.getpid():
            continue
        try:
            with open("/proc/%s/environ" % d, "rb") as f:
                env = set(f.read().split(b"\0"))
        except OSError:
            continue
        if want <= env:
            found.append(int(d))
    return found


def cleanup_target(m, tmux, w, own, panes, table):
    """What cleanup may do for one lane, from the run's ownership record and the live tmux
    state. kind: exited | live | gone | closed | mismatched | no-record."""
    rec = (own.get("lanes") or {}).get(w.lane) or {}
    t = {"lane": w.lane, "worker": w, "rec": rec, "lock": wtlock.read(w.worktree), "window": None}
    if not rec.get("window_id"):
        if rec.get("closed_at"):
            return dict(t, kind="closed", detail="window %s was closed by cleanup at %s" % (rec.get("closed_window"),
                                                                                           rec["closed_at"]))
        return dict(t, kind="no-record", detail="no recorded tmux ids for this lane (started by an older pai-swarm?); "
                    "check its window yourself, close it, then run cleanup again")
    if not panes or not same_server(rec, panes[0]):
        return dict(t, kind="gone", detail="the tmux server window %s was started on is no longer running"
                    % rec["window_id"])
    mine = [p for p in panes if p["window_id"] == rec["window_id"]]
    if not mine:
        return dict(t, kind="gone", detail="window %s is already closed" % rec["window_id"])
    pane = mine[0]
    t["window"] = pane
    bad = []
    if len(mine) != 1:
        bad.append("window %s has %d panes; only the worker's pane %s was recorded" % (rec["window_id"], len(mine),
                                                                                     rec.get("pane_id")))
    for field, label in (("pane_id", "pane"), ("session_id", "session")):
        if pane[field] != rec.get(field):
            bad.append("the window's %s is %s, the record says %s" % (label, pane[field], rec.get(field)))
    if pane["session_name"] != m.session:
        bad.append("the window is in session %s, not %s" % (pane["session_name"], m.session))
    if pane["window_name"] != w.lane:
        bad.append("the window is named %s, not %s" % (pane["window_name"], w.lane))
    if pane["pane_dead"] != "1" and not bad:
        pid = int(pane["pane_pid"] or 0)
        now = wtlock.proc_start(pid)
        if pid == rec.get("pane_pid") and now is None:  # gone, and tmux has not marked the pane dead yet
            return dict(t, kind="exiting", detail="pid %s is exiting" % pid)
        if pid != rec.get("pane_pid") or (rec.get("pane_start") and str(now) != str(rec["pane_start"])):
            bad.append("the pane runs pid %s, not the recorded worker pid %s" % (pid, rec.get("pane_pid")))
    if bad:
        return dict(t, kind="mismatched", detail="; ".join(bad))
    if pane["pane_dead"] == "1":
        return dict(t, kind="exited", detail="exited (status %s)" % (pane["pane_dead_status"] or "?"))
    screen = tmux.capture(pane["pane_id"])
    phase = "waiting-for-trust" if w.adapter.waiting_for_trust(screen) else "running"
    return dict(t, kind="live", pid=int(pane["pane_pid"]), detail="%s (pid %s)" % (PHASES[phase], pane["pane_pid"]))


def close_window(tmux, t):
    """Close the recorded window only if, at that moment inside tmux, its pane is still dead
    and still in that window. Returns True when the window is gone afterwards."""
    win, pane = t["rec"]["window_id"], t["rec"]["pane_id"]
    cond = "#{?pane_dead,#{?#{==:#{window_id},%s},1,0},0}" % win
    tmux.run("if-shell", "-F", "-t", pane, cond, "kill-window -t %s" % win, "display-message -p pai-skip")
    return not any(p["window_id"] == win for p in tmux.panes())


def finish_lane(m, w, t, apply):
    """After the worker has exited: release this run's lock unless something the worker
    started still runs, and record the window as closed. Returns (released, message)."""
    lock = wtlock.read(w.worktree)
    if not wtlock.owned(lock, "rc", m.run_id, w.lane):
        return False, "lock: %s (not this run's; left as it is)" % wtlock.describe(lock)
    left = leftover_processes(m, w)
    if left:
        return False, "lock KEPT: %d process(es) the worker started still run (pid %s)" % (
            len(left), ", ".join(map(str, left[:5])))
    if not apply:
        return False, "would release the lock%s" % (" (leftover processes not checked: no /proc)" if left is None else "")
    wtlock.release(w.worktree, "rc", m.run_id, w.lane)
    return True, "lock released%s" % (" (leftover processes not checked: no /proc)" if left is None else "")


def cmd_cleanup(m, a):
    tmux = Tmux(m.socket)
    cdir = gitprep.common_dir(m.repo)
    cur = current_lanes(cdir, m.run_id) if cdir else {}
    own = load_own(m, cdir)
    for lane in a.lane or []:
        m.worker(lane)
    panes, table = tmux.panes(), process_table()
    server = panes[0] if panes else None
    stop = a.apply and a.stop_running
    log("cleanup   run %s   socket %s%s   session %s" % (
        m.run_id, (server or {}).get("socket_path") or m.socket or "(default tmux server)",
        " (server pid %s)" % server["pid"] if server else " (no server running)", m.session))
    targets = [cleanup_target(m, tmux, w, own, panes, table) for w in m.workers
               if (not a.lane or w.lane in a.lane) and (w.lane in cur or w.lane in (own.get("lanes") or {}))]
    if not targets:
        log("nothing recorded for this run%s" % (" and lane(s) %s" % ", ".join(a.lane) if a.lane else ""))
        return 0
    code = 0
    for t in targets:
        w = t["worker"]
        rec = t["rec"]
        lock = wtlock.read(w.worktree)
        lockdesc = "this run" if wtlock.owned(lock, "rc", m.run_id, w.lane) else ("none" if not lock else wtlock.describe(lock))
        log("  %-10s window %-5s pane %-5s %-18s %-34s lock %s" % (
            w.lane, rec.get("window_id") or rec.get("closed_window") or "-", rec.get("pane_id") or "-",
            "%s/%s" % (w.agent, w.mode), t["kind"] + ": " + t["detail"] if t["kind"] in ("mismatched", "no-record", "gone",
                                                                                         "closed") else t["detail"], lockdesc))
        if t["kind"] in ("mismatched", "no-record"):
            log("             REFUSED: nothing is closed, stopped or released for this lane")
            code = 1
            continue
        if t["kind"] == "exiting":
            log("             keep: the worker is exiting right now; run cleanup again in a moment")
            continue
        if t["kind"] == "live" and not stop:
            log("             keep: the worker is live. To stop it and close its window:")
            log("               %s" % stop_command(m, a, w))
            code = 1 if a.apply else code
            continue
        if not a.apply:
            verb = {"exited": "would close window %s" % rec.get("window_id"),
                    "live": "would stop pid %s (SIGTERM), then close window %s" % (t.get("pid"), rec.get("window_id")),
                    "gone": "window already gone", "closed": "already cleaned"}[t["kind"]]
            log("             %s; %s" % (verb, finish_lane(m, w, t, False)[1]))
            continue
        if t["kind"] == "live":
            again = cleanup_target(m, tmux, w, load_own(m, cdir), tmux.panes(), process_table())
            if again["kind"] != "live" or again.get("pid") != t.get("pid"):
                log("             REFUSED: the lane changed since it was inspected (%s: %s)" % (again["kind"], again["detail"]))
                code = 1
                continue
            os.kill(t["pid"], signal.SIGTERM)
            end = time.time() + float(getattr(a, "stop_timeout", 15))
            while time.time() < end:
                t = cleanup_target(m, tmux, w, load_own(m, cdir), tmux.panes(), process_table())
                if t["kind"] not in ("live", "exiting"):
                    break
                time.sleep(0.2)
            if t["kind"] != "exited":
                log("             NOT STOPPED: still %s after SIGTERM; window and lock kept" % t["detail"])
                code = 1
                continue
            log("             stopped: the worker exited (%s)" % t["detail"])
        if t["kind"] == "exited":  # recheck under the mutex, right before closing
            t = cleanup_target(m, tmux, w, load_own(m, cdir), tmux.panes(), process_table())
            if t["kind"] != "exited" or not close_window(tmux, t):
                log("             NOT CLOSED: the lane changed (%s: %s); nothing was closed" % (t["kind"], t["detail"]))
                code = 1
                continue
            log("             closed window %s" % t["rec"]["window_id"])
        if t["kind"] in ("closed", "gone") and not wtlock.owned(wtlock.read(w.worktree), "rc", m.run_id, w.lane):
            log("             already cleaned: nothing to do")
            continue
        released, msg = finish_lane(m, w, t, True)
        log("             %s" % msg)
        if not released and "KEPT" in msg:
            code = 1
        o = load_own(m, cdir)
        lr = (o.get("lanes") or {}).get(w.lane)
        if lr and lr.get("window_id"):
            lr.update(closed_at=gitprep.now(), closed_window=lr["window_id"], window_id=None, pane_id=None)
            save_own(m, o, cdir)
    others = [p for p in panes if p["session_name"] == m.session and p["window_id"] not in
              {(t["rec"] or {}).get("window_id") for t in targets}]
    for p in others:
        log("  %-10s window %-5s %r is not one of these targets: never touched" % ("other", p["window_id"], p["window_name"]))
    if not a.apply:
        log("\nPREVIEW: nothing was closed, stopped or released. Worktrees, branches, edits, task notes and review "
            "records are never touched. Add --apply to do what the lines above say.")
    else:
        log("\nWorktrees, branches, uncommitted edits, task notes and review records were left as they are. "
            "To start the closed workers again on the same branches: `rc up MANIFEST --resume` (or `pai resume`).")
    return code


def stop_command(m, a, w):
    return getattr(a, "stop_hint", None) or "python3 %s rc cleanup %s --lane %s --apply --stop-running" % (
        shlex.quote(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pai_swarm.py")), shlex.quote(m.path), w.lane)


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
    bad = ssh_preflight(m, "No lane was rolled over.")
    if bad:
        for line in bad:
            log(line)
        return 1
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
        mismatch = agent_mismatch(w, rl)
        if mismatch:
            log("%-14s NOT ROLLED OVER: %s" % (w.lane, mismatch[0]))
            code = 1
            continue
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
                            lanes=[gitprep.Lane(w.lane, branch, w.worktree,
                                                dict({"stem": stem, "note": note.name}, **agent_fields(w)))])
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
        if m.dispatch and not getattr(a, "no_dispatch", False):
            rows = watch_startup(m, tmux, getattr(a, "settle", 5.0), dispatch=True, lanes={w.lane for w, _, _ in rolled})
            report_startup(m, tmux, rows, pai=getattr(a, "pai", False))
        else:
            log("A kept agent session is now on the new branch: give it the new task (claude.ai/code, or attach).")
    return code


# ---------------------------------------------------------------- handoff facts

def session_mutex(m):
    """Non-blocking mutex for one tmux session: `rc up`, `rc next`, `rc release` and `rc cleanup
    --apply` never interleave, so two of them cannot both start a worker in the same lane."""
    key = hashlib.sha256(("%s\0%s" % (m.socket or "", m.session)).encode()).hexdigest()[:16]
    return wtlock.mutex("tmux-" + key, blocking=False)


def exclusive(m, fn, *args):
    with session_mutex(m) as got:
        if not got:
            log("rc: another `rc up`, `rc next` or `rc release` for tmux session %s is running (or `rc cleanup --apply`); nothing was "
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
            p.add_argument("--agent", action="append", metavar="[LANE=]AGENT",
                           help="agent for one lane, or for every lane (%s; default claude)" % ", ".join(agents.names()))
            p.add_argument("--mode", action="append", metavar="[LANE=]MODE",
                           help="launch mode (claude: rc or local; codex: local). An unsupported mode is refused")
            p.add_argument("--model", action="append", metavar="[LANE=]MODEL",
                           help="model for one lane or every lane; separate from --agent")
            p.add_argument("--effort", action="append", metavar="[LANE=]LEVEL",
                           help="reasoning effort (claude: low..max; codex: minimal..high); unsupported values are refused")
        return p

    parser("plan", "read-only: show what `up` would do", overrides=True)
    u = parser("up", "prepare worktrees and start missing workers", overrides=True)
    u.add_argument("--expect-base", metavar="SHA", help="refuse unless the pinned base is exactly this commit")
    u.add_argument("--no-input", action="store_true", help="never prompt for branch names")
    u.add_argument("--settle", type=float, default=2.0, help="seconds to wait for lanes to come up (and, with "
                   "dispatch, take their task) before the closing status (default 2)")
    u.add_argument("--lane", action="append", help="on a rerun, start only these missing lanes")
    u.add_argument("--no-dispatch", action="store_true", help="do not send tasks even if the manifest has dispatch on")
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
    n.add_argument("--settle", type=float, default=5.0, help=argparse.SUPPRESS)
    n.add_argument("--no-dispatch", action="store_true")
    c = parser("cleanup", "preview (default) or --apply: close exited workers' windows, release their locks")
    c.add_argument("--lane", action="append", help="only this lane (repeatable)")
    c.add_argument("--apply", action="store_true", help="do it; without this nothing changes")
    c.add_argument("--stop-running", action="store_true", help="with --apply: also stop live workers (SIGTERM), "
                   "including ones waiting for folder trust")
    h = parser("handoff", "file one draft PAI handoff with the launcher's own observations")
    h.add_argument("--pai", required=True, metavar="PROJECT")
    h.add_argument("--pai-bin", default=os.environ.get("PAI_BIN", ""))
    h.add_argument("--pai-config")
    h.add_argument("--task-id")
    a = ap.parse_args(argv)
    try:
        first = a.rc_cmd in ("plan", "up")
        m = Manifest(a.manifest, workers=getattr(a, "workers", None), assign=getattr(a, "assign", None),
                     branch=a.branch if first else None, socket=a.tmux_socket, agent=getattr(a, "agent", None),
                     mode=getattr(a, "mode", None), model=getattr(a, "model", None), effort=getattr(a, "effort", None))
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
        if a.rc_cmd == "cleanup":
            return exclusive(m, cmd_cleanup, a) if a.apply else cmd_cleanup(m, a)
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
