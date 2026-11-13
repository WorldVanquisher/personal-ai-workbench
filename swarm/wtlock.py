"""Worktree locks shared by headless pai-swarm runs and Remote Control lanes.

One lock file per worktree (keyed by its real path) under `<home>/locks/`. Both starters
take the lock before they touch the worktree, so a headless run and an RC lane can never
work in the same checkout at once.

  headless  held while the `run` process or any worker it started lives. The run holds
            an flock on `<home>/live/<key>.live` and its workers inherit that descriptor, so
            killing only the orchestrator does not free a worktree its workers still edit.
            When the pid is gone and nobody holds the flock, the lock is stale and may be
            taken over.
  rc        held by a task and lane until `rc release`. The Claude session outlives the
            command that started it, so the lock does not expire on its own.

`<home>` is $PAI_SWARM_HOME or ~/.cache/pai-swarm. Standard library only; POSIX only.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import time


_live = {}  # worktree -> descriptor holding its live flock, for the life of a headless run


class LockHeld(Exception):
    def __init__(self, worktree, record):
        self.worktree, self.record = worktree, record
        super().__init__("%s is locked by %s" % (worktree, describe(record)))


def home():
    h = os.environ.get("PAI_SWARM_HOME") or os.path.join("~", ".cache", "pai-swarm")
    return os.path.realpath(os.path.expanduser(h))


def lock_path(worktree):
    key = hashlib.sha256(os.path.realpath(worktree).encode()).hexdigest()[:24]
    return os.path.join(home(), "locks", key + ".json")


def live_path(worktree):
    key = hashlib.sha256(os.path.realpath(worktree).encode()).hexdigest()[:24]
    return os.path.join(home(), "live", key + ".live")


def live_fds():
    """Descriptors a headless run passes to its workers (subprocess pass_fds)."""
    return tuple(_live.values())


def held(path):
    """True when some process holds the flock on `path`. Fails closed."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@contextlib.contextmanager
def mutex(name="locks", blocking=True):
    """Serialises short check-then-write sections between cooperating processes.
    With blocking=False it yields False instead of waiting when another process holds it.
    A blocking mutex that cannot be taken raises LockHeld: nothing proceeds without it."""
    os.makedirs(home(), exist_ok=True)
    path = os.path.join(home(), name + ".mutex")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as e:
            if blocking:
                raise LockHeld(path, {"kind": "mutex-error", "path": path, "error": e.strerror or str(e)})
            yield False
            return
        yield True
    finally:
        os.close(fd)


def proc_start(pid):
    """Process start time in clock ticks (Linux), so a reused pid is not mistaken for the
    original process. None where /proc is not available."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            s = f.read()
        return s[s.rindex(")") + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def pid_alive(pid, start=None):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    now = proc_start(int(pid))
    return not (start and now and str(start) != str(now))


def read(worktree):
    """The lock record, None when unlocked. An unreadable file counts as a lock (fail closed)."""
    p = lock_path(worktree)
    try:
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
        if not isinstance(rec, dict) or "kind" not in rec:
            raise ValueError("not a lock record")
        return rec
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"kind": "unreadable", "task_id": "?", "lane": "?", "path": p}


def stale(rec):
    """A headless lock whose run has died and whose workers have all exited."""
    if not rec or rec.get("kind") != "headless" or pid_alive(rec.get("pid"), rec.get("proc_start")):
        return False
    return not (rec.get("worktree") and held(live_path(rec["worktree"])))


def owned(rec, kind, task_id, lane):
    return bool(rec) and (rec.get("kind"), rec.get("task_id"), rec.get("lane")) == (kind, task_id, lane)


def describe(rec):
    if not rec:
        return "nobody"
    if rec.get("kind") == "unreadable":
        return "an unreadable lock file (%s); inspect it" % rec.get("path")
    if rec.get("kind") == "mutex-error":
        return "nobody known: the pai-swarm mutex %s could not be taken (%s)" % (rec.get("path"), rec.get("error"))
    who = "%s task %s, lane %s" % ("Remote Control" if rec.get("kind") == "rc" else rec.get("kind"),
                                   rec.get("task_id"), rec.get("lane"))
    if rec.get("kind") == "headless":
        if pid_alive(rec.get("pid"), rec.get("proc_start")):
            who += ", pid %s" % rec.get("pid")
        elif rec.get("worktree") and held(live_path(rec["worktree"])):
            who += (", pid %s not running, but a process it started still holds the worktree "
                    "(see `fuser -v %s`)" % (rec.get("pid"), live_path(rec["worktree"])))
        else:
            who += ", pid %s (not running: stale)" % rec.get("pid")
    return who


def acquire(worktree, kind, task_id, lane):
    """Take the lock or raise LockHeld. An RC lane may re-take its own lock (a rerun of the
    same task); a headless lock is only ever taken over from a process that has died."""
    worktree = os.path.realpath(worktree)
    with mutex():
        cur = read(worktree)
        if cur and not stale(cur) and not (kind == "rc" and owned(cur, kind, task_id, lane)):
            raise LockHeld(worktree, cur)
        lp = live_path(worktree)
        if worktree not in _live:
            if kind == "headless":
                os.makedirs(os.path.dirname(lp), exist_ok=True)
                fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)
                    raise LockHeld(worktree, cur or {"kind": "headless", "task_id": "(unknown)", "lane": "?",
                                                     "pid": None, "worktree": worktree})
                os.set_inheritable(fd, True)
                _live[worktree] = fd
            elif held(lp):
                raise LockHeld(worktree, cur or {"kind": "headless", "task_id": "(unknown)", "lane": "?",
                                                 "pid": None, "worktree": worktree})
        rec = {"kind": kind, "task_id": task_id, "lane": lane, "worktree": worktree,
               "pid": os.getpid() if kind == "headless" else None,
               "proc_start": proc_start(os.getpid()) if kind == "headless" else None,
               "created": (cur or {}).get("created") if owned(cur, kind, task_id, lane) and kind == "rc"
               else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        p = lock_path(worktree)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = "%s.%d.tmp" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1)
        os.replace(tmp, p)
        return rec


def release(worktree, kind, task_id, lane):
    """Remove the lock only if this owner holds it. Returns True when it was removed."""
    with mutex():
        cur = read(worktree)
        if not owned(cur, kind, task_id, lane):
            return False
        if kind == "headless" and cur.get("pid") != os.getpid() and not stale(cur):
            return False
        fd = _live.pop(os.path.realpath(worktree), None)
        if fd is not None:
            os.close(fd)
        if kind == "headless" and held(live_path(worktree)):
            return False  # a worker it started still runs; the lock goes stale when it exits
        os.remove(lock_path(worktree))
        try:
            os.remove(live_path(worktree))
        except FileNotFoundError:
            pass
        return True
