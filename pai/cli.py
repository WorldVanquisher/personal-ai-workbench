"""Command line: `python3 -m pai COMMAND …` (`pai --help` lists the commands)."""

import argparse
import os
import sys

from pai import __version__, config, context, doctor, handoff, initcfg, install, privacy, workqueue
from pai import commands, promptlib, vault  # registry: one `commands.use` line per feature (R6-COR-049)

commands.use(handoff.CLI)  # pai-handoff/2: handoff new v2 flags, seal, approve, reject (R6-COR-019..040)
commands.use(vault.CLI)  # pai vault lint|wait|schema (R6-COR-039..044)
commands.use(commands.CONTEXT_WORKTREE)  # pai context --worktree PATH (R6-COR-061)
commands.use(commands.SECRETS_WHERE)  # pai secrets where (R6-COR-069)
commands.use(promptlib.CLI)  # pai prompt ...; pai init installs the library (R6-PRM-011, R6-PRM-082)

INIT_EPILOG = """examples:
  preview (writes nothing):
    pai init flh --repo /abs/path/repo --vault /abs/path/vault \\
      --memory "Memory/PAI-001-X" --inbox "Agent Inbox" \\
      --markdown-root "30 Projects" --project-note "30 Projects/Project.md"
  (Windows: --repo "C:\\path\\to\\repo" --vault "D:\\path\\to\\vault"; on PowerShell continue lines with `)
  then write it, exactly as previewed:
    pai init flh … --write
paths after --vault are relative to the vault; --instructions/--repo-doc are relative to the repo.
pai init only looks at the paths you give it; it never overwrites an existing configuration."""


def _parser():
    p = argparse.ArgumentParser(prog="pai", description="Project context and draft handoffs for agent sessions "
                                "(standard library; no network, model or command execution).")
    p.add_argument("--config", help="configuration file (default: $PAI_CONFIG, else %s)" % (
        config.DEFAULT_CONFIG.replace("%", "%%") if config.PACKAGED else "pai.local.toml in the pai directory"))
    p.add_argument("--version", action="version", version=f"pai {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)  # the usage line lists every command (R6-COR-080)

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

    ins = sub.add_parser("install", help="copy this verified bundle to a user-local folder and put `pai` on PATH "
                         "(no sudo; see `pai install --help`)", description=install.__doc__,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    ins.add_argument("--prefix", help="install folder (default: ~/.local/share/pai-workbench; Windows: "
                     "%%LOCALAPPDATA%%\\pai-workbench)")
    ins.add_argument("--bin-dir", help="folder for the `pai` launcher (default: ~/.local/bin, or PREFIX/bin with --prefix; "
                     "Windows: PREFIX\\bin)")
    ins.add_argument("--dry-run", action="store_true", help="show what would happen; write nothing")
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
    n.add_argument("--decision", action="append", default=[], metavar="TEXT",
                   help="a decision made within your authority: what, why, how to reverse it (repeatable)")
    n.add_argument("--needs-owner", action="append", default=[], metavar="TEXT",
                   help="a decision above your authority, not acted on: options, trade-offs, recommendation (repeatable)")
    n.add_argument("--preview", action="store_true", help="print the draft and what the privacy filter removes; "
                   "write nothing")
    n.add_argument("--keep-private", action="store_true", help="keep agent session links and credential-like text "
                   "(removed by default, so a handoff can be shared)")
    commands.add_all(sub, {"init": i, "handoff": hs, "handoff new": n, "context": c})
    workqueue.add_parser(sub)
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
    print("".join(f"# WARNING: {w}\n" for w in handoff.layout_warnings(project)), end="")  # R6-COR-016
    commands.hook("init before-write", a)  # registered features (R6-COR-081)
    if not a.write:
        print(f"\n# Re-run with --write to create {path}. Existing files are never overwritten.")
        return 0
    initcfg.write(path, text)
    commands.hook("init after-write", a)
    print(f"\n# Wrote {path} ({initcfg.permission_note()}). Next: pai doctor {a.project}")
    return 0


def main(argv=None):
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "init":
            return _init(a)
        if a.cmd == "install":
            return install.main(a)
        if a.cmd == "queue":
            return workqueue.main(a)
        if (code := commands.dispatch(a, lambda: config.load(a.project, a.config))) is not None:
            return code
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
            summary, lists = a.summary, [a.evidence, a.blocker, a.next, a.proposal, a.decision, a.needs_owner]
            if not a.keep_private:  # session links and credentials stay out of a shared handoff by default
                removed = []

                def clean(value, field):
                    text, rem = privacy.scrub(value or "")
                    removed.extend((field, r) for r in rem)
                    return text if value is not None else None
                states = {k: clean(v, k) for k, v in states.items()}
                summary = clean(summary, "summary")
                lists = [[clean(x, "%s %d" % (name, i)) for i, x in enumerate(items, 1)]
                         for name, items in zip(("evidence", "blocker", "next", "proposal", "decision", "needs-owner"),
                                                 lists)]
                for field, r in removed:
                    print(f"removed  {field}: {r['kind']} ({r['sample']})", file=sys.stderr)
            if a.preview:
                print("PREVIEW (nothing written; %s)" % ("privacy filter off: --keep-private" if a.keep_private else
                                                        "the privacy filter's removals are listed above"))
                print(handoff.render(project, a.task_id, "(set when written)",
                                     {k: v for k, v in states.items() if v}, summary, *lists), end="")
                return 0
            path = handoff.create(project, a.task_id, states, summary, *lists[:4], decisions=lists[4],
                                  needs_owner=lists[5], **handoff.v2_args(a))
            print(path)
            return 0
    except (config.ConfigError, handoff.HandoffError) as e:
        print(f"pai: {e}", file=sys.stderr)
        return 2
    return 2
