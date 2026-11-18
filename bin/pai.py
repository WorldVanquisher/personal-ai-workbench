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

# The workbench (pai run, status, attach, ... in swarm/) ships next to bin/ in the bundle and in
# a checkout. Its commands are routed there; PAI's own commands (init PROJECT --vault ...,
# doctor PROJECT, doctor --agents, check, context, handoff, install) always stay with the pai
# package. The workbench needs tmux, so on native Windows only PAI's own commands run.
WORKBENCH = {"run", "status", "attach", "logs", "trust", "resume", "pr", "next", "cleanup", "setup", "config", "jev",
             "server", "task"}
SWARM = os.path.join(os.path.dirname(HERE), "swarm")
HAVE_WORKBENCH = os.path.isfile(os.path.join(SWARM, "workbench.py"))
NATIVE_WINDOWS = os.name == "nt"

QUICKSTART = """pai: run Claude Code and Codex on your Git repository, one task note per lane.

Start here, inside your Git checkout:
  pai doctor               check tools, agent logins and remote access (changes nothing)
  pai init                 save this repository's defaults (Enter keeps each suggested value)
  pai task new "TITLE"     preview a task note; add --write to save it
  pai run --dry-run        show what a run would do (changes nothing)
  pai run                  start the agents; then pai status, pai trust, pai attach LANE

All commands: pai --help.  Guide: swarm/WORKBENCH.md in this folder."""
WINDOWS_NOTE = """pai on native Windows: project context and handoffs (pai init PROJECT --vault ..., pai doctor --agents,
pai context, pai handoff) run here. The workbench (pai run, status, task, ...) needs tmux: install
pai inside WSL (Ubuntu) and run it there, in a checkout under the Linux file system."""


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


def _quickstart():
    print(WINDOWS_NOTE if NATIVE_WINDOWS else QUICKSTART)


_cmd = _workbench_command(sys.argv[1:])
if _cmd:
    if not HAVE_WORKBENCH or NATIVE_WINDOWS:
        if _cmd in ("init", "doctor"):
            _cmd = None  # PAI's own init/doctor
        elif NATIVE_WINDOWS:
            sys.stderr.write("pai %s: %s\n" % (_cmd, WINDOWS_NOTE))
            sys.exit(2)
        else:
            sys.stderr.write("pai %s is part of the pai workbench (swarm/), which is not in this folder; "
                             "install a pai bundle of version 0.3.0 or later, or run it from a checkout.\n" % _cmd)
            sys.exit(2)
    if _cmd:
        sys.path.insert(0, SWARM)
        import workbench  # noqa: E402

        sys.exit(workbench.main(sys.argv[1:]))

import runpy  # noqa: E402

if not sys.argv[1:] and (HAVE_WORKBENCH or NATIVE_WINDOWS):
    _quickstart()  # bare `pai`: the shortest useful path, not a usage error
    sys.exit(0)
if sys.argv[1:] in (["-h"], ["--help"]) and HAVE_WORKBENCH and not NATIVE_WINDOWS:
    print(QUICKSTART + "\n")
    try:
        runpy.run_module("pai", run_name="__main__", alter_sys=True)
    finally:
        print("\nworkbench commands (`pai COMMAND --help`):\n"
              "  doctor, init, task, run, status, attach, logs, trust, resume, pr, next, cleanup, setup, config, jev\n"
              "  (`pai init PROJECT --vault ...`, `pai doctor PROJECT` and `pai doctor --agents` are PAI's own\n"
              "  context and handoff commands, listed above)")
if sys.argv[1:] in (["-h"], ["--help"]) and NATIVE_WINDOWS:
    print(WINDOWS_NOTE + "\n")
runpy.run_module("pai", run_name="__main__", alter_sys=True)
