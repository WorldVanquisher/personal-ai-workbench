#!/usr/bin/env python3
"""unittest discovery that fails on leaked resources, not only on failed tests.

    python3 -B tools/strict_unittest.py DIR [-v]

`python -W error::ResourceWarning -m unittest` does not catch the common leak: a file left
open is noticed only when it is garbage-collected, inside a finalizer, where the error turns
into an "Exception ignored ..." message on stderr and the run still exits 0. Here every
ResourceWarning is an error, every exception ignored in a finalizer (sys.unraisablehook) is
recorded, and the run exits 1 when there was any, after printing where each came from.
Leaks in child processes (the CLIs the tests start) are not seen; this covers the test process.
"""
import gc
import os
import sys
import traceback
import unittest
import warnings

IGNORED = []  # (kind, message, where) of every exception ignored in a finalizer


def record(u):
    where = ""
    if u.exc_traceback is not None:
        frames = traceback.extract_tb(u.exc_traceback)
        if frames:
            where = "%s:%d" % (frames[-1].filename, frames[-1].lineno)
    IGNORED.append((u.exc_type.__name__ if u.exc_type else "?", str(u.exc_value), where))
    sys.__unraisablehook__(u)  # still printed as usual


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = 2 if any(a in ("-v", "--verbose") for a in argv) else 1
    dirs = [a for a in argv if not a.startswith("-")]
    if len(dirs) != 1:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    sys.unraisablehook = record
    warnings.simplefilter("error", ResourceWarning)
    # unittest.main() would install its own warning filter for the run (replacing the one above),
    # so the loader and the runner are used directly; warnings=None leaves the filters as set.
    suite = unittest.defaultTestLoader.discover(dirs[0])
    result = unittest.TextTestRunner(verbosity=verbose, warnings=None).run(suite)
    gc.collect()
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
