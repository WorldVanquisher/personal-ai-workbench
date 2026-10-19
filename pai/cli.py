"""Command line: `python3 -m pai {context,handoff,check} …`."""

import argparse
import sys

from pai import __version__, config, context, handoff


def _parser():
    p = argparse.ArgumentParser(prog="pai", description="Project context and handoff drafts (stdlib, read-only context).")
    p.add_argument("--config", help="configuration file (default: $PAI_CONFIG or pai.local.toml next to the package)")
    p.add_argument("--version", action="version", version=f"pai {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("context", help="print task context as Markdown")
    c.add_argument("project")
    c.add_argument("--task", required=True, help="the task, in a sentence")
    c.add_argument("--budget", type=int, help="maximum output characters (default: project budget_chars)")

    k = sub.add_parser("check", help="validate configuration and list source status")
    k.add_argument("project")

    h = sub.add_parser("handoff", help="handoff drafts")
    hs = h.add_subparsers(dest="hcmd", required=True)
    n = hs.add_parser("new", help="create a new draft handoff in the project inbox (never overwrites)")
    n.add_argument("project")
    n.add_argument("--task-id", required=True)
    n.add_argument("--summary", default="")
    for state in handoff.STATES:
        n.add_argument(f"--{state}", metavar="TEXT", help=f"{state} state as you observed it (default UNKNOWN)")
    n.add_argument("--evidence", action="append", default=[], metavar="TEXT")
    n.add_argument("--blocker", action="append", default=[], metavar="TEXT")
    n.add_argument("--next", action="append", default=[], metavar="TEXT")
    n.add_argument("--proposal", action="append", default=[], metavar="TEXT")
    return p


def main(argv=None):
    a = _parser().parse_args(argv)
    try:
        project = config.load(a.project, a.config)
        if a.cmd == "check":
            print(f"project {project.key}: repo {project.repo}; vault {project.vault}")
            print(f"memory {project.memory_rel}; inbox {project.inbox_rel}; budget {project.budget_chars}")
            bad = 0
            for s in project.sources:
                print(f"{s.status:8} {s.kind:12} {s.label}{'  (' + s.reason + ')' if s.reason else ''}")
                bad += s.status != "ok"
            return 1 if bad else 0
        if a.cmd == "context":
            if a.budget is not None and not 2000 <= a.budget <= 200000:
                raise config.ConfigError("--budget must be between 2000 and 200000")
            sys.stdout.write(context.build(project, a.task, a.budget))
            return 0
        if a.cmd == "handoff" and a.hcmd == "new":
            states = {s: getattr(a, s) for s in handoff.STATES}
            path = handoff.create(project, a.task_id, states, a.summary, a.evidence, a.blocker, a.next, a.proposal)
            print(path)
            return 0
    except (config.ConfigError, handoff.HandoffError) as e:
        print(f"pai: {e}", file=sys.stderr)
        return 2
    return 2
