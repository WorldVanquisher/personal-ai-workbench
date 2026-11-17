"""Portable pai entry point, for any platform and any working directory:

    python3 /path/to/pai-workbench/bin/pai.py ARGS        (Linux, macOS)
    py -3 C:\\path\\to\\pai-workbench\\bin\\pai.py ARGS        (Windows)

bin/pai and bin/pai.cmd run this file. Running a script (rather than -m pai)
keeps the caller's working directory off the import path, so a pai/ folder or a
module in that directory can never replace the bundled code.
"""
import os
import sys

sys.dont_write_bytecode = True  # like -B: never write __pycache__ into the bundle
HERE = os.path.dirname(os.path.abspath(__file__))
if sys.path and os.path.abspath(sys.path[0] or os.curdir) == HERE:
    del sys.path[0]  # this directory holds pai.py, which must not be imported as the pai package
sys.path.insert(0, os.path.dirname(HERE))

# The operator workbench (pai run, status, attach, ... in swarm/) is development tooling that
# lives next to bin/ in a checkout and is not in the release bundle. Its commands are routed
# there; PAI's own commands (init PROJECT --vault ..., doctor PROJECT, check, context,
# handoff) always stay with the pai package.
WORKBENCH = {"run", "status", "attach", "logs", "trust", "resume", "pr", "next", "cleanup", "setup", "config", "jev", "server"}


def _workbench_command(args):
    if not args or args[0].startswith("-"):
        return None
    cmd, rest = args[0], args[1:]
    if cmd in WORKBENCH:
        return cmd
    if cmd == "init" and not {"--vault", "--memory", "--inbox"} & {a.split("=")[0] for a in rest}:
        return cmd
    if cmd == "doctor" and "--agents" not in rest and not [a for a in rest if not a.startswith("-")]:
        return cmd
    return None


_cmd = _workbench_command(sys.argv[1:])
if _cmd:
    _swarm = os.path.join(os.path.dirname(HERE), "swarm")
    if not os.path.isfile(os.path.join(_swarm, "workbench.py")):
        if _cmd in ("init", "doctor"):
            _cmd = None  # the bundle's own init/doctor
        else:
            sys.stderr.write("pai %s is part of the pai workbench (swarm/), which is not in this release bundle; "
                             "run it from a checkout of personal-ai-workbench.\n" % _cmd)
            sys.exit(2)
    if _cmd:
        sys.path.insert(0, _swarm)
        import workbench  # noqa: E402

        sys.exit(workbench.main(sys.argv[1:]))

import runpy  # noqa: E402

if sys.argv[1:] in (["-h"], ["--help"]) and os.path.isfile(os.path.join(os.path.dirname(HERE), "swarm", "workbench.py")):
    try:
        runpy.run_module("pai", run_name="__main__", alter_sys=True)
    finally:
        print("\nworkbench commands (this checkout's swarm/; `pai COMMAND --help`):\n"
              "  init, doctor, setup, config, run, status, attach, logs, trust, resume, pr, next, cleanup, jev, server\n"
              "  (`pai init PROJECT --vault ...` and `pai doctor PROJECT` are PAI's own commands, as above)")
runpy.run_module("pai", run_name="__main__", alter_sys=True)
