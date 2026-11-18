"""The `pai` command: workbench commands (pai run, status, task, ...) go to the workbench, every
other command to PAI's own command line (pai/cli.py).

Two ways in, one routing:
  - bin/pai.py, used by bin/pai and bin/pai.cmd in a release archive or a checkout; the
    workbench is swarm/ beside pai/;
  - the `pai` console script of an installed package (`uv tool install ...`, pip); the
    workbench modules ship inside the package as pai/_workbench/.

Kept importable on old Pythons: on anything older than 3.11, every command ends in the version
message of pai/__main__.py.
"""
import os
import runpy
import sys

# PAI's own commands (init PROJECT --vault ..., doctor PROJECT, doctor --agents, check, context,
# handoff, install) always stay with the pai package. The workbench needs tmux, so on native
# Windows only PAI's own commands run.
WORKBENCH = {"run", "status", "attach", "logs", "trust", "resume", "pr", "next", "cleanup", "setup", "config", "jev",
             "server", "task"}
PACKAGE = os.path.dirname(os.path.abspath(__file__))
NATIVE_WINDOWS = os.name == "nt"

QUICKSTART = """pai: run Claude Code and Codex on your Git repository, one task note per lane.

Start here, inside your Git checkout:
  pai doctor               check tools, agent logins and remote access (changes nothing)
  pai init                 save this repository's defaults (Enter keeps each suggested value)
  pai task new "TITLE"     preview a task note; add --write to save it
  pai run --dry-run        show what a run would do (changes nothing)
  pai run                  start the agents; then pai status, pai trust, pai attach LANE

All commands: pai --help.  Guide: %s"""
WINDOWS_NOTE = """pai on native Windows: project context and handoffs (pai init PROJECT --vault ..., pai doctor --agents,
pai context, pai handoff) run here. The workbench (pai run, status, task, ...) needs tmux: install
pai inside WSL (Ubuntu) and run it there, in a checkout under the Linux file system."""


def workbench_dir():
    """The folder holding workbench.py: pai/_workbench in an installed package, else swarm/ beside
    pai/ (release archive, checkout); None when neither is there."""
    for d in (os.path.join(PACKAGE, "_workbench"), os.path.join(os.path.dirname(PACKAGE), "swarm")):
        if os.path.isfile(os.path.join(d, "workbench.py")):
            return d
    return None


def guide():
    d = workbench_dir()
    if d and os.path.basename(d) == "_workbench":
        return os.path.join(d, "WORKBENCH.md")
    return "swarm/WORKBENCH.md in this folder."


def workbench_command(args):
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


def _core():
    # pai/__main__.py: the Python version check, UTF-8 output on Windows, then pai.cli.main()
    runpy.run_module("pai", run_name="__main__", alter_sys=True)
    return 0


def main(argv=None):
    if argv is not None:
        sys.argv[1:] = list(argv)
    args = sys.argv[1:]
    if sys.version_info < (3, 11):
        return _core()
    swarm = workbench_dir()
    cmd = workbench_command(args)
    if cmd:
        if not swarm or NATIVE_WINDOWS:
            if cmd in ("init", "doctor"):
                cmd = None  # PAI's own init/doctor
            elif NATIVE_WINDOWS:
                sys.stderr.write("pai %s: %s\n" % (cmd, WINDOWS_NOTE))
                return 2
            else:
                sys.stderr.write("pai %s is part of the pai workbench (swarm/), which is not in this folder; "
                                 "install a pai bundle of version 0.3.0 or later, or run it from a checkout.\n" % cmd)
                return 2
        if cmd:
            sys.path.insert(0, swarm)
            import workbench

            return workbench.main(args)
    if not args and (swarm or NATIVE_WINDOWS):
        print(WINDOWS_NOTE if NATIVE_WINDOWS else QUICKSTART % guide())  # bare `pai`: the shortest useful path
        return 0
    if args in (["-h"], ["--help"]) and swarm and not NATIVE_WINDOWS:
        print(QUICKSTART % guide() + "\n")
        try:
            return _core()
        finally:
            print("\nworkbench commands (`pai COMMAND --help`):\n"
                  "  doctor, init, task, run, status, attach, logs, trust, resume, pr, next, cleanup, setup, config, jev\n"
                  "  (`pai init PROJECT --vault ...`, `pai doctor PROJECT` and `pai doctor --agents` are PAI's own\n"
                  "  context and handoff commands, listed above)")
    if args in (["-h"], ["--help"]) and NATIVE_WINDOWS:
        print(WINDOWS_NOTE + "\n")
    return _core()


if __name__ == "__main__":
    sys.exit(main())
