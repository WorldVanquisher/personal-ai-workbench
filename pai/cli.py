"""Command line: `python3 -m pai {init,doctor,check,context,handoff} …`."""

import argparse
import os
import sys

from pai import __version__, config, context, doctor, handoff, initcfg

INIT_EPILOG = """examples:
  preview (writes nothing):
    python3 -m pai init flh --repo /abs/path/repo --vault /abs/path/vault \\
      --memory "Agent Inbox/PAI-001-X" --inbox "Agent Inbox" \\
      --markdown-root "30 Projects" --project-note "30 Projects/Project.md"
  then write it, exactly as previewed:
    python3 -m pai init flh … --write
paths after --vault are relative to the vault; --instructions/--repo-doc are relative to the repo.
pai init only looks at the paths you give it; it never overwrites an existing configuration."""


def _parser():
    p = argparse.ArgumentParser(prog="pai", description="Project context and draft handoffs for agent sessions "
                                "(standard library; no network, model or command execution).")
    p.add_argument("--config", help="configuration file (default: $PAI_CONFIG, else pai.local.toml in the pai directory)")
    p.add_argument("--version", action="version", version=f"pai {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{init,doctor,check,context,handoff}")

    i = sub.add_parser("init", help="create a local configuration from explicit paths (preview unless --write)",
                       epilog=INIT_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    i.add_argument("project", help="short project key, e.g. flh")
    i.add_argument("--repo", required=True, help="absolute path of the project repository")
    i.add_argument("--vault", required=True, help="absolute path of the Markdown vault")
    i.add_argument("--memory", required=True, help="memory-package directory, relative to the vault")
    i.add_argument("--inbox", required=True, help="existing inbox folder for draft handoffs, relative to the vault")
    i.add_argument("--name", default="", help="display name (default: the key)")
    i.add_argument("--markdown-root", action="append", default=[], help="permitted vault folder for project notes (repeatable)")
    i.add_argument("--project-note", action="append", default=[], help="project note, relative to the vault (repeatable)")
    i.add_argument("--instructions", action="append", default=None, help="instruction file in the repo (repeatable; default AGENTS.md)")
    i.add_argument("--repo-doc", action="append", default=[], help="repository document to consider (repeatable)")
    i.add_argument("--instruction-always", action="append", default=[], help="instruction heading always included (repeatable)")
    i.add_argument("--proposal-file", action="append", default=None, help="memory file holding proposals (default knowledge-proposals.md)")
    i.add_argument("--budget", type=int, default=24000, help="default context budget in characters (default 24000)")
    i.add_argument("--write", action="store_true", help="write the previewed configuration (refuses to overwrite)")

    d = sub.add_parser("doctor", help="check Python, bundle integrity, configuration and roots (read-only)")
    d.add_argument("project", nargs="?", help="check only this project")
    d.add_argument("--agents", action="store_true", help="also report agent executables on PATH (never launched)")

    k = sub.add_parser("check", help="validate one project and list source status")
    k.add_argument("project")

    c = sub.add_parser("context", help="print task context as Markdown")
    c.add_argument("project")
    c.add_argument("--task", required=True, help="the task, in a sentence")
    c.add_argument("--budget", type=int, help="maximum output characters (default: project budget_chars)")

    c.add_argument("--handoffs", type=int, default=0, help="include up to N subsequent reports (0..20; default 0)")

    h = sub.add_parser("handoff", help="draft handoffs")
    hs = h.add_subparsers(dest="hcmd", required=True)
    listing = hs.add_parser("list", help="list validated project handoffs and discovery problems")
    listing.add_argument("project")
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


def _init(a):
    values = {
        "name": a.name, "repo": a.repo, "vault": a.vault, "memory": a.memory, "inbox": a.inbox,
        "markdown_roots": a.markdown_root, "project_notes": a.project_note,
        "instructions": a.instructions or ["AGENTS.md"], "repo_docs": a.repo_doc,
        "instruction_always": a.instruction_always,
        "proposal_files": a.proposal_file or list(config.DEFAULT_PROPOSAL_FILES), "budget": a.budget,
    }
    if not 2000 <= a.budget <= 200000:
        raise config.ConfigError("--budget must be between 2000 and 200000")
    path = os.path.abspath(config.config_path(a.config))
    text, project = initcfg.prepare(a.project, values)
    print(f"# {'Writing' if a.write else 'DRY RUN (nothing written); would write'}: {path}\n")
    print(text)
    print("# Validation: roots valid. Sources:")
    for s in project.sources:
        print(f"#   {s.status:8} {s.kind:12} {s.label}{'  (' + s.reason + ')' if s.reason else ''}")
    if not a.write:
        print(f"\n# Re-run with --write to create {path}. Existing files are never overwritten.")
        return 0
    initcfg.write(path, text)
    print(f"\n# Wrote {path} (mode 0600). Next: python3 -m pai doctor {a.project}")
    return 0


def main(argv=None):
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "init":
            return _init(a)
        if a.cmd == "doctor":
            r = doctor.run(a.config, a.project, a.agents)
            print("\n".join(r.lines))
            return 1 if r.problems else 0
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
            sys.stdout.write(context.build(project, a.task, a.budget, handoffs=a.handoffs))
            return 0
        if a.cmd == "handoff" and a.hcmd == "list":
            text, problems = handoff.listing(project)
            sys.stdout.write(text)
            return 1 if problems else 0
        if a.cmd == "handoff" and a.hcmd == "new":
            states = {s: getattr(a, s) for s in handoff.STATES}
            path = handoff.create(project, a.task_id, states, a.summary, a.evidence, a.blocker, a.next, a.proposal)
            print(path)
            return 0
    except (config.ConfigError, handoff.HandoffError) as e:
        print(f"pai: {e}", file=sys.stderr)
        return 2
    return 2
