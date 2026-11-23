"""The vault as PAI's control surface (owner decision D9 (a), R6-COR-039..044; docs/FRONTMATTER.md).

State is Markdown with front matter that agents and scripts read and write directly. Dataview or
Bases queries only display it; nothing here reads them. PAI never writes an owner's note: this
module only reads, except `wait`, which writes nothing either.

    resolve(...)      one answer to "where is the vault" for every command (R6-COR-043)
    lint(...)         notes under Agent Inbox/ and PAI/ against SCHEMA, plus Syncthing conflicts
    wait(...)         until a file is there with the expected sha256 (sync delay; R6-COR-044)

Standard library only; no network, no subprocess.
"""

import fnmatch
import hashlib
import json
import os
import re
import stat
import time

# ---------------------------------------------------------------- schema (R6-COR-039)

HEX = {"sha40": r"[0-9a-f]{40}", "sha256": r"[0-9a-f]{64}", "sha16": r"[0-9a-f]{12,64}"}
SCHEMA = {
    "format": "pai-frontmatter-schema/1",
    "kinds": {
        "handoff": {
            "match": {"format": "pai-handoff/2"},
            "required": ["format", "status", "approved", "project", "round", "lane", "checkpoint", "base_sha",
                         "head_sha", "queue", "queue_sha256"],
            "fields": {
                "status": {"enum": ["draft", "sealed", "approved", "rejected", "superseded", "continued", "published"]},
                "approved": {"enum": ["true", "false"]},
                "project": {"pattern": r"[a-z0-9][a-z0-9_-]{0,31}"},
                "round": {"pattern": r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}|UNKNOWN"},
                "lane": {"pattern": r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}|UNKNOWN"},
                "checkpoint": {"pattern": r"cp-(?:[A-Za-z0-9]{1,16}|final)|UNKNOWN"},
                "base_sha": {"pattern": HEX["sha40"] + "|UNKNOWN"},
                "head_sha": {"pattern": HEX["sha40"] + "|UNKNOWN"},
                "queue_sha256": {"pattern": HEX["sha256"] + "|none|UNKNOWN"},
                "sealed_sha256": {"pattern": HEX["sha256"]},
                "approval": {"enum": ["pending", "approved", "rejected", "stale"]},
            },
            "owner_fields": ["approved"],
            "optional": ["task", "created", "generator", "sealed_at", "sealed_sha256", "approval", "approved_by",
                         "approved_at", "rejected_reason", "jev_done", "jev_checked", "jev_verify"],
            "written_by": "the worker (pai handoff new); seal adds status, sealed_sha256, approval",
        },
        "decision": {
            "match": {"pai_managed": "decision/1"},
            "required": ["pai_managed", "status", "options", "source", "source_sha", "pai_sha256"],
            "fields": {
                "status": {"enum": ["open", "answered"]},
                "options": {"type": "list"},  # non-empty while the decision is open
                "source_sha": {"pattern": HEX["sha16"]},
                "pai_sha256": {"pattern": HEX["sha256"]},
            },
            "owner_fields": ["choice", "choice_note"],
            "optional": ["round", "id", "question", "recommendation", "default", "deadline", "blocks", "answer",
                         "updated"],
            "written_by": "pai round (round lane, swarm/round_vault.py)",
        },
        "lane": {
            "match": {"pai_managed": "lane/1"},
            "required": ["pai_managed", "round", "status", "pai_sha256"],
            "fields": {"needs_owner": {"enum": ["true", "false"]}, "pai_sha256": {"pattern": HEX["sha256"]},
                       "queued": {"enum": ["true", "false"]}},
            "owner_fields": [],
            "optional": ["project", "lane", "dialog", "queued", "current_id", "last_cp", "totals", "blockers",
                         "needs_owner", "needs_owner_why", "branch", "worktree_state", "updated"],
            "written_by": "pai round (round lane, swarm/round_vault.py: PAI/rounds/<ROUND>/<lane>.md)",
        },
        # R6-COR-078: the other notes the round and prompts lanes write (F3 of the 2026-11-22 acceptance)
        "round": {
            "match": {"pai_managed": "round/1"},
            "required": ["pai_managed", "round", "status", "pai_sha256"],
            "fields": {"status": {"enum": ["open", "closed"]}, "pai_sha256": {"pattern": HEX["sha256"]}},
            "owner_fields": [],
            "optional": ["project", "base", "lanes", "needs_owner", "round_json", "updated"],
            "written_by": "pai round (round lane, swarm/round_vault.py: PAI/rounds/<ROUND>/ROUND.md)",
        },
        "home": {
            "match": {"pai_managed": "home/1"},
            "required": ["pai_managed", "pai_sha256"],
            "fields": {"pai_sha256": {"pattern": HEX["sha256"]}},
            "owner_fields": [],
            "optional": ["title", "rounds_open", "lanes_need_owner", "decisions_open", "handoffs_unapproved", "updated"],
            "written_by": "pai round (round lane, swarm/round_vault.py: PAI/Home.md)",
        },
        "integration": {
            "match": {"pai_managed": "integration/1"},
            "required": ["pai_managed", "round", "pai_sha256"],
            "fields": {"pai_sha256": {"pattern": HEX["sha256"]}},
            "owner_fields": [],
            "optional": ["base", "order", "patches", "conflicts", "hot_files", "test_exit", "integrated_at", "updated"],
            "written_by": "pai round integrate (round lane, swarm/round_integrate.py: INTEGRATION.md)",
        },
        "log": {
            "match": {"pai_managed": "log/1"},
            "required": ["pai_managed", "date", "pai_sha256"],
            "fields": {"date": {"pattern": r"\d{4}-\d\d-\d\d"}, "pai_sha256": {"pattern": HEX["sha256"]}},
            "owner_fields": [],
            "optional": ["entries", "updated"],
            "written_by": "pai round (round lane, swarm/round_board.py: the daily log)",
        },
        "eval": {
            "match": {"template": "pai-eval/1"},
            "required": ["type", "template", "round", "contract"],
            "fields": {"type": {"enum": ["pai-eval"]}},
            "owner_fields": [],
            "optional": ["contract_version", "artifacts", "mean_pass", "all_pass", "behaviour_warnings", "generated"],
            "written_by": "pai prompt eval --note (prompts lane, pai/prompteval.py)",
        },
        "request": {
            "match": {"pai_managed": "request/1"},
            "required": ["pai_managed", "id", "status", "from", "to", "created"],
            "fields": {"status": {"enum": ["open", "accepted", "done", "declined"]},
                       "created": {"pattern": r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"}},
            "owner_fields": ["status", "answer"],
            "written_by": "a lane or the mastermind asking another lane or the owner for something",
        },
    },
}
SYNC_CONFLICT = "*.sync-conflict-*"
LINT_ROOTS = ("Agent Inbox", "PAI")
MAX_NOTE = 262144


class VaultError(Exception):
    pass


# ---------------------------------------------------------------- one vault path (R6-COR-043)

def _looks_like_vault(path, inbox="Agent Inbox"):
    return os.path.isdir(path) and os.path.isdir(os.path.join(path, inbox))


def container_hint(path):
    """The hint for a vault path copied from inside a container (/vaults/MainVault). Judged on the path as
    given: on Windows such a path is not absolute, and resolving it puts a drive in front of it."""
    if isinstance(path, str) and path.replace(os.sep, "/").startswith("/vaults/"):
        return " (a container path such as /vaults/... is not the host path; on the NAS host the vault is under /srv)"
    return ""


def resolve(argument=None, config_vault=None, script=None, env=None, inbox="Agent Inbox"):
    """(vault, source, notes): the one vault path every command uses. Sources, in this order:
    the configuration, the command-line argument, the folder above the running script (a script kept
    in the vault, such as 99 Templates/r6-prepare.sh), then $VAULT. The first one given is used; a
    later one that names a different folder is a note, and a configuration that disagrees with an
    explicit argument is refused, never silently overridden. A path that does not exist on this
    machine is refused with a hint (a container path such as /vaults/MainVault is not the host's)."""
    env = os.environ if env is None else env
    cands = [("configuration", config_vault), ("argument", argument),
             ("script location", os.path.dirname(os.path.dirname(os.path.abspath(script))) if script else None),
             ("VAULT", env.get("VAULT"))]
    given = [(src, os.path.realpath(os.path.expanduser(p)), p) for src, p in cands if p]
    if not given:
        raise VaultError("no vault given: set `vault` in the pai configuration, pass --vault PATH, or set VAULT")
    src, path, as_given = given[0]
    notes = []
    for other, p, _ in given[1:]:
        if p == path:
            continue
        if src == "configuration" and other == "argument":
            raise VaultError(f"the configuration says the vault is {path} but --vault says {p}; pai uses one vault: "
                             "fix the configuration or drop --vault")
        if other == "script location" and not _looks_like_vault(p, inbox):
            continue  # a script outside any vault says nothing about where the vault is
        notes.append(f"{other} names another folder ({p}); using the {src}'s")
    if not os.path.isdir(path):
        hint = container_hint(as_given) or container_hint(path)
        raise VaultError(f"vault does not exist on this machine: {path} (from the {src}){hint}")
    if not _looks_like_vault(path, inbox):
        notes.append(f"{path} has no {inbox!r} folder; is it the vault?")
    return path, src, notes


# ---------------------------------------------------------------- front matter

def front_matter(text):
    """{key: str | [str]} of a note's front matter, or None when it has none. Simple YAML only:
    `key: value`, `key: [a, b]` and `key:` followed by `- item` lines; quotes are removed."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    out, last = {}, None
    for line in text[4:end].split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and line.strip().startswith("- ") and last is not None:
            if not isinstance(out[last], list):
                out[last] = []
            out[last].append(_unquote(line.strip()[2:]))
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or key != key.strip():
            continue
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            out[key] = [_unquote(v) for v in value[1:-1].split(",") if v.strip()]
        else:
            out[key] = _unquote(value) if value else ""
        last = key
    return out


def _unquote(v):
    v = v.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v


def known_fields(kind):
    """Every key a note of KIND may carry: match, required, typed, owner and optional keys."""
    spec = SCHEMA["kinds"][kind]
    return (set(spec["match"]) | set(spec["required"]) | set(spec["fields"]) | set(spec["owner_fields"])
            | set(spec.get("optional", [])))


def kind_of(fm):
    for kind, spec in SCHEMA["kinds"].items():
        if all(str(fm.get(k)) == v for k, v in spec["match"].items()):
            return kind
    return None


def check_note(fm, text=None, decision_check=None):
    """(kind, problems) of one note's front matter against SCHEMA; kind None means not PAI's."""
    kind = kind_of(fm)
    if kind is None:
        return None, []
    spec, probs = SCHEMA["kinds"][kind], []
    for k in spec["required"]:
        if fm.get(k) in (None, ""):
            probs.append(f"{kind}: `{k}` is missing")
    for k, rule in spec["fields"].items():
        v = fm.get(k)
        if v in (None, ""):
            continue
        if rule.get("type") == "list":
            if not isinstance(v, list):
                probs.append(f"{kind}: `{k}` must be a list")
            elif not v and fm.get("status") == "open":
                probs.append(f"{kind}: `{k}` is empty, but the decision is open: there is nothing to choose from")
        elif isinstance(v, list):
            probs.append(f"{kind}: `{k}` must be a single value")
        elif "enum" in rule and v not in rule["enum"]:
            probs.append(f"{kind}: `{k}: {v}` is not one of {', '.join(rule['enum'])}")
        elif "pattern" in rule and not re.fullmatch(rule["pattern"], v):
            probs.append(f"{kind}: `{k}: {v}` does not have the expected form")
    if kind == "handoff" and text is not None and fm.get("sealed_sha256"):
        from pai import handoff
        if handoff.body_sha(text) != fm["sealed_sha256"]:
            probs.append("handoff: the body changed after sealing (sealed_sha256 differs): approval is stale")
        if fm.get("approved") == "true" and fm.get("approval") not in ("approved",):
            probs.append("handoff: `approved: true` is set in the note but not recorded yet: run "
                         "`pai handoff approve PROJECT DIR --from-vault`")
    if kind == "decision":  # R6-COR-041
        choice = fm.get("choice")
        if choice and isinstance(fm.get("options"), list) and not choice_matches(choice, fm["options"]):
            probs.append(f"decision: choice {choice!r} is not one of the options ({'; '.join(fm['options'])})")
        if decision_check is not None:
            probs += ["decision: %s" % p for p in decision_check(fm, text)]
    return kind, probs


def choice_matches(choice, options):
    """The rule `pai decide answer --from-note` applies (swarm/round_vault.py answer_from_note): the whole
    option, or its leading label (`A` for "A $5", "A：…"). R6-FIN-010: lint refused `choice: A`, which the
    round lane accepts."""
    choice = str(choice).strip()
    return not options or any(choice == o or o.startswith(choice + " ") or o.startswith(choice + "：")
                              or (o.split() or [""])[0] == choice for o in options)


def _round_decision_check(vault):
    """The round lane's sha checks for a decision note (its pai_sha256 and the source block's sha),
    when swarm/round_vault.py and swarm/decide.py are in this tree; else None (reported once)."""
    import importlib
    import sys
    from pai import config
    swarm = os.path.join(config.WORKBENCH, "swarm")
    if not os.path.exists(os.path.join(swarm, "round_vault.py")):
        return None
    if swarm not in sys.path:
        sys.path.insert(0, swarm)
    try:
        rv = importlib.import_module("round_vault")
        dc = importlib.import_module("decide")
    except ImportError:
        return None

    def check(fm, text):
        out = []
        try:
            # the round lane's own reader (JSON values: escaped quotes, commas inside an option), so the sha is
            # computed on exactly what it wrote (R6-FIN-010: this module's simple reader gave false "edited")
            rfm, _, rbody = rv.split((text or "").replace("\r\n", "\n"))
            if rfm and rfm.get("pai_sha256") and \
                    rv.content_sha(rfm, rbody, "decision/1") != rfm["pai_sha256"]:
                out.append("the PAI-written part was edited (pai_sha256 differs)")
            src = os.path.join(vault, str(fm.get("source") or ""))
            if fm.get("source") and os.path.isfile(src):
                # the round lane's reader too: its sha covers the bytes, \r\n included (a sheet saved on Windows)
                blocks = dc.parse(dc.read(src)[1])
                if not any(b["sha"].startswith(str(fm.get("source_sha"))) for b in blocks):
                    out.append("the question changed in %s (source_sha %s matches no decision there)" % (
                        fm["source"], fm.get("source_sha")))
            elif fm.get("source"):
                out.append("source %s is missing" % fm["source"])
        except Exception as e:  # noqa: BLE001  the round lane's format may move; report, never crash lint
            out.append("could not check with the round lane's code (%s)" % type(e).__name__)
        return out
    return check


def lint(vault, roots=LINT_ROOTS, decision_check="auto"):
    """[{path, level, message}] for every note under ROOTS (relative to the vault): PROBLEM for a
    PAI note that does not match SCHEMA, CONFLICT for a Syncthing conflict copy (reported, never
    removed: R6-COR-042), INFO for counts. Owner notes (no PAI kind) are not judged. Links are not
    followed. Reads only."""
    if decision_check == "auto":
        decision_check = _round_decision_check(vault)
    out, counts = [], {}
    for r in roots:
        top = os.path.join(vault, r)
        if not os.path.isdir(top) or os.path.islink(top):
            continue
        for d, dirs, files in os.walk(top):
            dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not os.path.islink(os.path.join(d, x)))
            for name in sorted(files):
                p = os.path.join(d, name)
                rel = os.path.relpath(p, vault)
                if fnmatch.fnmatch(name, SYNC_CONFLICT):
                    out.append({"path": rel, "level": "CONFLICT", "message": "Syncthing conflict copy: compare it with "
                                "%s by hand and delete the one you do not keep (pai never deletes it)"
                                % re.sub(r"\.sync-conflict-[^.]*", "", name)})
                    continue
                if not name.endswith(".md"):
                    continue
                try:
                    st = os.lstat(p)
                    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_NOTE:
                        continue
                    with open(p, encoding="utf-8") as f:
                        text = f.read()
                except (OSError, UnicodeDecodeError) as e:
                    out.append({"path": rel, "level": "PROBLEM", "message": "unreadable (%s)" % (
                        getattr(e, "strerror", None) or type(e).__name__)})
                    continue
                fm = front_matter(text)
                if not fm:
                    continue
                kind, probs = check_note(fm, text, decision_check)
                if kind:
                    counts[kind] = counts.get(kind, 0) + 1
                out += [{"path": rel, "level": "PROBLEM", "message": m} for m in probs]
    if decision_check is None and counts.get("decision"):
        out.append({"path": "", "level": "INFO", "message": "decision sha checks need the round lane's "
                    "swarm/round_vault.py in this tree; only the form and the choice were checked"})
    out.append({"path": "", "level": "INFO", "message": "PAI notes checked: " + (
        ", ".join("%s %d" % kv for kv in sorted(counts.items())) or "none yet (notes are recognised by "
        "their front matter, see `pai vault schema`; the prompt library and templates are not notes. `pai handoff "
        "seal` and `pai round status --write-vault` write the first ones)")})
    return out


# ---------------------------------------------------------------- wait for sync (R6-COR-044)

def file_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def wait(path, sha, timeout, interval=0.5, clock=time.monotonic, sleep=time.sleep):
    """(ok, last sha256 seen or None): poll until PATH exists and its sha256 starts with SHA, or
    TIMEOUT seconds pass. Reads only."""
    if not re.fullmatch(r"[0-9a-f]{12,64}", sha or ""):
        raise VaultError("--sha needs at least 12 hex digits")
    end, last = clock() + max(0.0, timeout), None
    while True:
        try:
            last = file_sha(path)
            if last.startswith(sha):
                return True, last
        except OSError:
            pass
        if clock() >= end:
            return False, last
        sleep(interval)


def schema_json():
    return json.dumps(SCHEMA, indent=1, sort_keys=True) + "\n"


# ---------------------------------------------------------------- command line (registered in pai/cli.py)

def _cli_add(sub, parsers):
    v = sub.add_parser("vault", help="the vault: lint PAI notes, wait for a synced file, print the schema")
    vs = v.add_subparsers(dest="vcmd", required=True)
    vl = vs.add_parser("lint", help="check notes under Agent Inbox/ and PAI/ against the front matter schema; report "
                       "Syncthing conflict copies (never deletes)")
    vl.add_argument("--project", help="take the vault from this project's configuration")
    vl.add_argument("--vault", help="the vault folder (else the configuration, then $VAULT)")
    vl.add_argument("--json", action="store_true")
    vw = vs.add_parser("wait", help="wait until FILE exists with the expected sha256 (sync delay)")
    vw.add_argument("file")
    vw.add_argument("--sha", required=True, metavar="SHA256", help="sha256 or a prefix of 12+ hex digits")
    vw.add_argument("--timeout", type=float, default=60, help="seconds (default 60)")
    vs.add_parser("schema", help="print the front matter schema (JSON)")


def _cli_run(a, load_project):
    import sys
    if a.cmd != "vault":
        return None
    from pai import config
    try:
        if a.vcmd == "schema":
            sys.stdout.write(schema_json())
            return 0
        if a.vcmd == "wait":
            ok, last = wait(a.file, a.sha.lower(), a.timeout)
            print("%s %s (sha256 %s)" % ("ready  " if ok else "TIMEOUT", a.file, last or "none: not there"))
            return 0 if ok else 1
        conf = config.load(a.project, a.config).vault if a.project else None
        path, src, notes = resolve(argument=a.vault, config_vault=conf)
        for n in notes:
            print(f"note     {n}", file=sys.stderr)
        rows = lint(path)
        if a.json:
            print(json.dumps({"vault": path, "source": src, "findings": rows}, indent=1, ensure_ascii=False))
        else:
            print(f"vault    {path} (from the {src})")
            for r in rows:
                print(f"{r['level']:<8} {r['path']}{': ' if r['path'] else ''}{r['message']}")
        return 1 if any(r["level"] in ("PROBLEM", "CONFLICT") for r in rows) else 0
    except VaultError as e:
        print(f"pai vault: {e}", file=sys.stderr)
        return 2


def _feature():
    from pai import commands
    return commands.Feature("vault", _cli_add, _cli_run)


CLI = _feature()
