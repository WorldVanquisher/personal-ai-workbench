#!/usr/bin/env python3
"""unittest discovery that fails on leaked resources, not only on failed tests.

    python3 -B tools/strict_unittest.py DIR [-v] [--jobs N] [--pattern GLOB]

`python -W error::ResourceWarning -m unittest` does not catch the common leak: a file left
open is noticed only when it is garbage-collected, inside a finalizer, where the error turns
into an "Exception ignored ..." message on stderr and the run still exits 0. Here every
ResourceWarning is an error, every exception ignored in a finalizer (sys.unraisablehook) is
recorded, and the run exits 1 when there was any, after printing where each came from.
Leaks in child processes (the CLIs the tests start) are not seen; this covers the test process.

--jobs N runs each test module of DIR in its own process, N at a time (longest first), with the
same strict checks in every process, and prints each module's output whole when it ends. The run
fails if any module fails or leaks. Modules share nothing: every fixture works in its own
temporary folder, tmux socket and pai home. --pattern limits discovery (default test*.py).
"""
import gc
import glob
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import warnings
import weakref

IGNORED = []  # (kind, message, where) of every exception ignored in a finalizer


def record(u):
    where = ""
    if u.exc_traceback is not None:
        frames = traceback.extract_tb(u.exc_traceback)
        if frames:
            where = "%s:%d" % (frames[-1].filename, frames[-1].lineno)
    IGNORED.append((u.exc_type.__name__ if u.exc_type else "?", str(u.exc_value), where))
    sys.__unraisablehook__(u)  # still printed as usual


RAN_RE = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s", re.M)


def parallel(folder, jobs, verbose, pattern):
    """Run every module of FOLDER in its own strict process, JOBS at a time; 0 when all pass."""
    mods = sorted(glob.glob(os.path.join(folder, pattern)), key=lambda p: (-os.path.getsize(p), p))
    if not mods:
        print("strict_unittest: no test modules matching %s in %s" % (pattern, folder), file=sys.stderr)
        return 1
    me = os.path.abspath(__file__)
    pending, running, results = list(mods), [], []
    started = time.time()
    while pending or running:
        while pending and len(running) < jobs:
            mod = pending.pop(0)
            argv = [sys.executable, "-B", me, folder, "--pattern", os.path.basename(mod)] + (["-v"] if verbose > 1 else [])
            log = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")  # not a pipe: no full-buffer stall
            running.append((mod, time.time(), log, subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                                                    stdin=subprocess.DEVNULL)))
        for item in list(running):
            mod, t0, log, proc = item
            if proc.poll() is None:
                continue
            log.seek(0)
            out = log.read()
            log.close()
            running.remove(item)
            name = os.path.basename(mod)
            ran = RAN_RE.search(out)
            results.append((name, proc.returncode, int(ran.group(1)) if ran else 0, time.time() - t0))
            sys.stdout.write("==== %s: %s (%.0fs)\n%s" % (name, "OK" if proc.returncode == 0 else "FAILED",
                                                          time.time() - t0, out if (verbose > 1 or proc.returncode) else
                                                          "".join(out.splitlines(True)[-3:])))
            sys.stdout.flush()
        time.sleep(0.05)
    failed = [r for r in results if r[1] != 0]
    total = sum(r[2] for r in results)
    print("\nRan %d tests in %.3fs" % (total, time.time() - started))
    print("slowest modules: %s" % ", ".join("%s %.0fs" % (r[0], r[3]) for r in sorted(results, key=lambda r: -r[3])[:5]))
    print("strict_unittest: %s" % ("OK (%d modules in %d processes; no failed test, no leaked resource)" % (len(results), jobs)
                                   if not failed else "FAILED (modules: %s)" % ", ".join(r[0] for r in failed)), file=sys.stderr)
    return 1 if failed else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = 2 if any(a in ("-v", "--verbose") for a in argv) else 1
    jobs, pattern, dirs, i = 1, "test*.py", [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--jobs", "-j", "--pattern") and i + 1 < len(argv):
            if a == "--pattern":
                pattern = argv[i + 1]
            else:
                try:
                    jobs = max(1, int(argv[i + 1]))
                except ValueError:
                    print("strict_unittest: --jobs takes a number", file=sys.stderr)
                    return 2
            i += 2
            continue
        if not a.startswith("-"):
            dirs.append(a)
        i += 1
    if len(dirs) != 1:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    if jobs > 1:
        return parallel(dirs[0], jobs, verbose, pattern)
    sys.unraisablehook = record
    warnings.simplefilter("error", ResourceWarning)
    # unittest.main() would install its own warning filter for the run (replacing the one above),
    # so the loader and the runner are used directly; warnings=None leaves the filters as set.
    suite = unittest.defaultTestLoader.discover(dirs[0], pattern=pattern)
    result = unittest.TextTestRunner(verbosity=verbose, warnings=None).run(suite)
    gc.collect()
    # A TemporaryDirectory nobody cleaned up is only reported at interpreter exit ("Implicitly cleaning
    # up"), after this summary: count every one still waiting for its finalizer (R6-COR-047).
    for fin, info in list(getattr(weakref.finalize, "_registry", {}).items()):
        if fin.alive and getattr(info.func, "__qualname__", "").endswith("TemporaryDirectory._cleanup"):
            IGNORED.append(("ResourceWarning", "TemporaryDirectory %s was never cleaned up (it would be removed only at "
                            "exit)" % (info.args[0] if info.args else "?"), ""))
    if IGNORED:
        print("\nstrict_unittest: %d exception(s) ignored in finalizers (leaked files, sockets, processes...):"
              % len(IGNORED), file=sys.stderr)
        for kind, msg, where in IGNORED:
            print("  %s: %s%s" % (kind, msg[:200], "  (%s)" % where if where else ""), file=sys.stderr)
        if not os.environ.get("PYTHONTRACEMALLOC"):
            print("  (PYTHONTRACEMALLOC=5 shows where each leaked object was allocated)", file=sys.stderr)
    ok = result.wasSuccessful() and not IGNORED
    print("strict_unittest: %s" % ("OK (no failed test, no leaked resource)" if ok else
                                   "FAILED (%d failed or errored test(s), %d leaked resource(s))"
                                   % (len(result.failures) + len(result.errors), len(IGNORED))), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
