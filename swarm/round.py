"""pai round: open a round of parallel lanes, see their state, hand them work, and close it.

    pai round new PROJECT ROUND [--lane L ...] [--prompt FILE] [--write]   preview, or create
    pai round status [ROUND.json] [--json]       one line per lane: session, window, state, worktree, checkpoint, queue
    pai round launch LANE [--paste-prompt] [--write]   start a lane's Claude Code session in tmux
    pai round next LANE [--send]                 a short resume message from the lane's RESTART.md
    pai round inject LANE --file F [--dry-run]   one message into a lane, only when it is safe
    pai round brief                              a 1-2k token handover package for a fresh mastermind
    pai round log "DECISION" --why "REASON"      one line in COORDINATOR-LOG.md
    pai round close [--only-re RE] [--write]     acceptance checklist, then reclaim clean, idle worktrees
    pai round budget [--remaining USD]           usage per lane and how many items to hand out next
    pai round collect [--jev]                    verify each lane's handoff (SHA256SUMS), update the board
    pai round prplan LANE [--out PLAN.json]      a lane's NEXT-PR.md as a pai worktree publish preview
    pai round integrate --plan [--test] [--write] every lane's patch on a scratch copy: conflicts, hot files, tests
    pai round web                                the read-only board in the browser (loopback only)

A round is described by one file, ROUND.json (format pai-round/1, see docs/ROUND.md), kept with
the round's other state in the vault: <vault>/Agent Inbox/<ROUND>/. Every command reads that file;
only `new --write`, `log`, `collect` (the board) and `close --write` write anything, and none of
them commits, pushes, deletes a branch or answers a folder-trust prompt.

Standard library only (tomllib: Python 3.11+, as the workbench itself).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import gitprep  # noqa: E402

if __name__ == "__main__":  # one module object, also when run as a script
    sys.modules.setdefault("round", sys.modules[__name__])

try:
    import tomllib
except ImportError:  # pragma: no cover - the workbench needs 3.11
    tomllib = None

FORMAT, VERSION = "pai-round", 1
INBOX = "Agent Inbox"
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")
LANE_LINE_RE = re.compile(r"^线名：([A-Za-z0-9_-]+)。")
LANE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
ROUND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".cache", ".npm", ".local", ".cargo", ".rustup"}


class RoundError(Exception):
    """A refusal; nothing was changed. The message says what to do."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def expand(path):
    return os.path.abspath(os.path.expanduser(path)) if path else path


def write_atomic(path, data, mode=0o644):
    """Write bytes or text through a temporary file and fsync, then rename into place."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# -- configuration ---------------------------------------------------------------------------
def config_path(explicit=None):
    return expand(explicit or os.environ.get("PAI_ROUND_CONFIG") or os.path.join("~", ".config", "pai", "round.toml"))


def load_config(explicit=None):
    """(config dict, path or None). A missing default file is an empty configuration; a missing
    explicit file, or one that cannot be parsed, is a RoundError."""
    path = config_path(explicit)
    if not os.path.isfile(path):
        if explicit or os.environ.get("PAI_ROUND_CONFIG"):
            raise RoundError("round configuration %s does not exist" % path)
        return {}, None
    if tomllib is None:
        raise RoundError("reading %s needs Python 3.11 or later (tomllib)" % path)
    try:
        with open(path, "rb") as f:
            return tomllib.load(f), path
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise RoundError("cannot read %s: %s" % (path, e))


def project_config(cfg, project):
    return dict((cfg.get("project") or {}).get(project) or {})


# -- the main clone --------------------------------------------------------------------------
def _walk_git_dirs(root, depth):
    """Directories under root (at most `depth` levels) that hold a `.git` directory: main clones
    only (a linked worktree's .git is a file)."""
    root = expand(root)
    if not os.path.isdir(root):
        return
    stack = [(root, 0)]
    while stack:
        d, level = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        if any(e.name == ".git" and e.is_dir(follow_symlinks=False) for e in entries):
            yield d
            continue  # a repository's own subfolders are not searched
        if level >= depth:
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS and not e.name.startswith("."):
                stack.append((e.path, level + 1))


def find_clones(github, roots, depth=5):
    """Every main clone under `roots` whose `origin` points at GitHub repository `github`
    (OWNER/NAME, case-insensitive), sorted."""
    want = github.lower().removesuffix(".git")
    found = set()
    for r in roots:
        for d in _walk_git_dirs(r, depth):
            url = gitprep.out(d, "config", "--get", "remote.origin.url")
            if url and gitprep.github_slug(url) == want:
                found.add(gitprep.canon(d))
    return sorted(found)


def pick_clone(github, roots, explicit=None, depth=5):
    """(path, how). An explicit --repo must be a main clone whose origin is `github`."""
    if explicit:
        path = gitprep.canon(explicit)
        if not os.path.isdir(os.path.join(path, ".git")):
            raise RoundError("--repo %s is not a main clone (no .git directory; a linked worktree is not accepted)" % path)
        url = gitprep.out(path, "config", "--get", "remote.origin.url")
        if gitprep.github_slug(url or "") != github.lower():
            raise RoundError("--repo %s: its origin is %s, not github.com/%s" % (
                path, gitprep.redact_url(url) if url else "(none)", github))
        return path, "--repo"
    clones = find_clones(github, roots, depth)
    if not clones:
        raise RoundError("no main clone of github.com/%s under %s; clone it, or give --repo PATH" % (
            github, ", ".join(roots)))
    if len(clones) > 1:
        raise RoundError("%d main clones of github.com/%s found; choose one with --repo:\n  %s" % (
            len(clones), github, "\n  ".join(clones)))
    return clones[0], "found under %s" % ", ".join(roots)


def read_base(repo, branch="main", explicit=None, timeout=30):
    """(sha, how). An explicit --base must be a full SHA; otherwise `git ls-remote origin`."""
    if explicit:
        if not SHA_RE.match(explicit):
            raise RoundError("--base must be a full commit SHA (40 hex characters), not %r" % explicit)
        return explicit, "--base"
    sha, why = gitprep.ls_remote(repo, "origin", branch, timeout=timeout)
    if not sha:
        raise RoundError("cannot read origin/%s with git ls-remote: %s" % (branch, why))
    return sha, "git ls-remote origin refs/heads/%s" % branch


# -- the vault -------------------------------------------------------------------------------
def is_vault(path):
    return bool(path) and os.path.isdir(os.path.join(path, INBOX))


def vault_from(path):
    """The nearest folder at or above `path` that holds an Agent Inbox, or None."""
    d = os.path.dirname(expand(path)) if os.path.isfile(expand(path)) else expand(path)
    while d and d != os.path.dirname(d):
        if is_vault(d):
            return d
        d = os.path.dirname(d)
    return None


def resolve_vault(explicit=None, cfg=None, prompt=None, env=None):
    """(vault, source). Order: --vault, the configuration's `vault`, the folder holding the
    prompt file (walking up to the folder with an Agent Inbox), then $VAULT. A candidate without
    an Agent Inbox is refused, not skipped, so a wrong setting is never silently replaced."""
    env = os.environ if env is None else env
    tried = []
    for source, value in (("--vault", explicit), ("config", (cfg or {}).get("vault")),
                          ("prompt location", vault_from(prompt) if prompt else None), ("VAULT", env.get("VAULT"))):
        if not value:
            continue
        v = expand(value)
        if is_vault(v):
            return v, source
        if source != "prompt location":
            raise RoundError("%s %s has no %r folder: this is not the vault (on the NAS host the vault is the "
                             "host path, not a Syncthing container path)" % (source, v, INBOX))
        tried.append(source)
    raise RoundError("no vault: give --vault, set `vault` in %s, keep the prompt inside the vault, or set VAULT"
                     % config_path())


# -- prompts ---------------------------------------------------------------------------------
def render(text, values):
    """(rendered, missing placeholders). Uses the prompt library's renderer when the prompts
    lane provides one (pai/promptlib.py: render(text, values) -> (text, missing)); else the
    r6-prepare.sh rule: replace {{NAME}} and report any {{UPPER_CASE}} left."""
    try:
        from pai import promptlib  # noqa: WPS433 (optional, from the prompts lane)
        fn = getattr(promptlib, "render", None)
    except ImportError:
        fn = None
    if callable(fn):
        out = fn(text, values)
        if isinstance(out, tuple) and len(out) == 2:  # the library lists bare names; report them as {{NAME}}
            return out[0], sorted({m if m.startswith("{{") else "{{%s}}" % m for m in out[1]})
    for k, v in values.items():
        text = text.replace("{{%s}}" % k, v)
    return text, sorted(set(PLACEHOLDER_RE.findall(text)))


def split_parts(text):
    """The ```text blocks of a prompt note, in order (r6-prepare.sh's awk)."""
    parts, cur = [], None
    for line in text.splitlines():
        if cur is None and re.match(r"^```text\s*$", line):
            cur = []
        elif cur is not None and re.match(r"^```\s*$", line):
            parts.append("\n".join(cur) + "\n")
            cur = None
        elif cur is not None:
            cur.append(line)
    return parts


def lane_prompts(text, project):
    """{lane: paste-ready text}: Part 0 plus each lane's part (its first line is 线名：LANE。). A
    note with a single block is one lane named after the project."""
    parts = split_parts(text)
    if not parts:
        raise RoundError("the prompt has no ```text block")
    if len(parts) == 1:
        return {project: parts[0]}
    out = {}
    for i, p in enumerate(parts[1:], 2):
        m = LANE_LINE_RE.match(p.splitlines()[0] if p.strip() else "")
        if not m:
            raise RoundError("text block %d does not start with 线名：<lane>。" % i)
        if m.group(1) in out:
            raise RoundError("lane %s has two parts" % m.group(1))
        out[m.group(1)] = parts[0] + "\n" + p
    return out


# -- ROUND.json ------------------------------------------------------------------------------
def state_dir(vault, round_id):
    return os.path.join(vault, INBOX, round_id)


def round_path(vault, round_id):
    return os.path.join(state_dir(vault, round_id), "ROUND.json")


def load_round(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        raise RoundError("%s does not exist; open a round with pai round new" % path)
    except (OSError, ValueError) as e:
        raise RoundError("cannot read %s: %s" % (path, e))
    if not isinstance(d, dict) or d.get("format") != FORMAT or d.get("version") != VERSION:
        raise RoundError("%s is not a %s/%d file" % (path, FORMAT, VERSION))
    if not isinstance(d.get("lanes"), list):
        raise RoundError("%s has no lanes list" % path)
    d["_path"] = os.path.abspath(path)
    return d


def dump_round(r):
    return json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, indent=1, sort_keys=True,
                      ensure_ascii=False) + "\n"


def find_round(arg=None, vault=None):
    """The ROUND.json to use: an explicit path or round state folder, $PAI_ROUND, or the newest
    open round under <vault>/Agent Inbox."""
    cand = arg or os.environ.get("PAI_ROUND")
    if cand:
        cand = expand(cand)
        return cand if cand.endswith(".json") else os.path.join(cand, "ROUND.json")
    v = vault or os.environ.get("VAULT")
    if v and is_vault(expand(v)):
        inbox = os.path.join(expand(v), INBOX)
        found = []
        for name in os.listdir(inbox):
            p = os.path.join(inbox, name, "ROUND.json")
            if os.path.isfile(p):
                try:
                    if load_round(p).get("status", "open") == "open":
                        found.append((os.path.getmtime(p), p))
                except RoundError:
                    continue
        if found:
            return max(found)[1]
    raise RoundError("which round? give the path of its ROUND.json, set PAI_ROUND, or set VAULT")


def lane(r, name):
    for ln in r["lanes"]:
        if ln["name"] == name:
            return ln
    raise RoundError("round %s has no lane %r (lanes: %s)" % (r["round"], name, ", ".join(l["name"] for l in r["lanes"])))


def fmt(template, **kw):
    try:
        return template.format(**kw)
    except (KeyError, IndexError) as e:
        raise RoundError("template %r uses an unknown field %s" % (template, e))


# -- pai round new ---------------------------------------------------------------------------
def plan_new(a, cfg):
    """Everything `new` would do, as data: nothing is written here."""
    pc = project_config(cfg, a.project)
    if not ROUND_RE.match(a.round):
        raise RoundError("round id %r: letters, digits, . _ - only" % a.round)
    github = a.github or pc.get("github")
    if not github or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", github):
        raise RoundError("which GitHub repository? give --github OWNER/NAME or set [project.%s] github" % a.project)
    roots = a.search or cfg.get("search") or ["~", "/srv"]
    repo, repo_how = pick_clone(github, roots, a.repo, depth=int(cfg.get("search_depth") or 5))
    branch = a.base_branch or pc.get("base_branch") or "main"
    base, base_how = read_base(repo, branch, a.base)
    prompt = a.prompt or pc.get("prompt")
    # The prompt may be vault-relative, so the vault is resolved from the absolute prompt first.
    prompt_abs = expand(prompt) if prompt and os.path.isabs(os.path.expanduser(prompt)) else None
    vault, vault_src = resolve_vault(a.vault, cfg, prompt_abs)
    if prompt and not prompt_abs:
        prompt_abs = os.path.join(vault, prompt)
    trusted = expand(fmt(a.trusted_root or pc.get("trusted_root") or "~/workspace/{project}-workers/{round}",
                         project=a.project, round=a.round))
    if "{round}" not in (a.trusted_root or pc.get("trusted_root") or "{round}"):
        trusted = os.path.join(trusted, a.round)
    work = expand(fmt(a.work_root or pc.get("work_root") or "~/.cache/{project}-{round}", project=a.project, round=a.round))
    sdir = state_dir(vault, a.round)
    session = a.tmux_session or pc.get("tmux_session") or "%s-%s" % (a.project, a.round)
    prefix = a.window_prefix if a.window_prefix is not None else pc.get("window_prefix", "")
    name_t = pc.get("session_name") or "{PROJECT} {round} {lane}"

    prompts, prompt_sha, missing = {}, None, []
    if prompt_abs:
        if not os.path.isfile(prompt_abs) or not os.path.getsize(prompt_abs):
            raise RoundError("prompt file %s is missing or empty" % prompt_abs)
        with open(prompt_abs, encoding="utf-8") as f:
            raw = f.read()
        prompt_sha = sha256_bytes(raw.encode("utf-8"))
        values = {"RUN_ID": a.round, "BASE_SHA": base, "VAULT": vault, "WORKTREE_ROOT": trusted,
                  "WORK": work, "HANDOFF_ROOT": os.path.join(vault, INBOX), "PROJECT": a.project}
        for kv in a.set or []:
            k, _, v = kv.partition("=")
            if not re.fullmatch(r"[A-Z_]+", k):
                raise RoundError("--set %r: NAME=VALUE with NAME in upper case" % kv)
            values[k] = v
        filled, missing = render(raw, values)
        if not missing:
            prompts = lane_prompts(filled, a.project)
    lanes = a.lane or list(prompts) or pc.get("lanes") or []
    if not lanes:
        raise RoundError("no lanes: give --lane NAME (repeatable) or a prompt whose parts start with 线名：<lane>。")
    for ln in lanes:
        if not LANE_RE.match(ln):
            raise RoundError("lane name %r: letters, digits, _ and - only" % ln)
    if prompts and a.lane:
        unknown = [ln for ln in a.lane if ln not in prompts]
        if unknown:
            raise RoundError("the prompt has no part for lane(s) %s (it has: %s)" % (", ".join(unknown), ", ".join(prompts)))
    r = {"format": FORMAT, "version": VERSION, "round": a.round, "project": a.project, "github": github.lower(),
         "repo": repo, "repo_found": repo_how, "base_branch": branch, "base_sha": base, "base_source": base_how,
         "vault": vault, "vault_source": vault_src, "trusted_root": trusted, "work_root": work, "state_dir": sdir,
         "tmux_session": session, "prompt": {"file": prompt_abs, "sha256": prompt_sha} if prompt_abs else None,
         "created": now(), "status": "open", "lanes": []}
    for ln in lanes:
        text = prompts.get(ln)
        pfile = os.path.join(work, "prompts", ln + ".txt") if text is not None else None
        r["lanes"].append({
            "name": ln, "worktree": os.path.join(trusted, ln), "branch": "worker/%s/%s" % (a.round, ln),
            "session_name": fmt(name_t, project=a.project, PROJECT=a.project.upper(), round=a.round, lane=ln),
            "tmux_target": "%s:%s%s" % (session, prefix, ln),
            "prompt_file": pfile, "prompt_sha256": sha256_bytes(text.encode("utf-8")) if text is not None else None,
            "work_dir": os.path.join(work, ln), "handoff_dir": os.path.join(sdir, ln)})
    actions = worktree_actions(r)
    return {"round": r, "prompts": prompts, "missing": missing, "actions": actions}


def worktree_actions(r):
    """One action per lane: exists | create-branch | add-existing-branch | CONFLICT, with why.
    Re-running is safe: a lane whose worktree is already there on its branch is `exists`."""
    repo = r["repo"]
    have = {w["path"]: w for w in gitprep.worktree_list(repo)}
    out = []
    root = gitprep.canon(r["trusted_root"])
    for ln in r["lanes"]:
        path = gitprep.canon(ln["worktree"])
        if not gitprep.under(path, root):
            out.append({"lane": ln["name"], "action": "CONFLICT", "why": "%s is outside the trusted root %s" % (path, root)})
            continue
        w = have.get(path)
        if w:
            if w["branch"] == ln["branch"]:
                out.append({"lane": ln["name"], "action": "exists", "why": "already a worktree on %s" % ln["branch"]})
            else:
                out.append({"lane": ln["name"], "action": "CONFLICT",
                            "why": "%s is a worktree on %s, not %s" % (path, w["branch"] or "a detached HEAD", ln["branch"])})
            continue
        if os.path.exists(path):
            out.append({"lane": ln["name"], "action": "CONFLICT", "why": "%s exists and is not a worktree of %s" % (path, repo)})
            continue
        used = [p for p, w in have.items() if w["branch"] == ln["branch"]]
        if used:
            out.append({"lane": ln["name"], "action": "CONFLICT", "why": "branch %s is checked out at %s" % (ln["branch"], used[0])})
        elif gitprep.branch_sha(repo, ln["branch"]):
            out.append({"lane": ln["name"], "action": "add-existing-branch", "why": "branch %s exists; its commits are kept"
                        % ln["branch"]})
        else:
            out.append({"lane": ln["name"], "action": "create-branch", "why": "new branch %s at %s" % (ln["branch"], r["base_sha"][:12])})
    return out


def apply_new(p, log):
    r = p["round"]
    if p["missing"]:
        raise RoundError("placeholders left in the prompt: %s; nothing was written" % ", ".join(p["missing"]))
    bad = [x for x in p["actions"] if x["action"] == "CONFLICT"]
    if bad:
        raise RoundError("CONFLICT: %s; nothing was written" % "; ".join("%s: %s" % (x["lane"], x["why"]) for x in bad))
    path = round_path(r["vault"], r["round"])
    if os.path.exists(path):
        old = load_round(path)
        if old.get("base_sha") != r["base_sha"] or old.get("repo") != r["repo"]:
            raise RoundError("MISMATCH: %s already describes round %s at base %s in %s; a round keeps its base. Use a "
                             "new round id, or pai round close the old one first" % (path, old["round"], old.get("base_sha", "?")[:12], old.get("repo")))
        r["created"] = old.get("created", r["created"])
        names = {l["name"] for l in r["lanes"]}
        r["lanes"] = r["lanes"] + [l for l in old["lanes"] if l["name"] not in names]
    if any(x["action"] in ("create-branch", "add-existing-branch") for x in p["actions"]) and \
            not gitprep.rev(r["repo"], r["base_sha"]):
        raise RoundError("the base %s is not in %s yet: run `git -C %s fetch origin %s` first; nothing was written"
                         % (r["base_sha"][:12], r["repo"], r["repo"], r["base_branch"]))
    by = {l["name"]: l for l in r["lanes"]}
    for x in p["actions"]:
        ln = by[x["lane"]]
        if x["action"] == "exists":
            log("  exists      %s  [%s]" % (ln["worktree"], ln["branch"]))
            continue
        os.makedirs(os.path.dirname(ln["worktree"]), exist_ok=True)
        args = ["worktree", "add", "-q", ln["worktree"], ln["branch"]] if x["action"] == "add-existing-branch" else \
            ["worktree", "add", "-q", "-b", ln["branch"], ln["worktree"], r["base_sha"]]
        proc = gitprep.git(r["repo"], *args)
        if proc.returncode != 0:
            raise RoundError("git %s failed: %s (lanes before it are in place; re-run is safe)" % (
                " ".join(args[:3]), proc.stderr.strip()[-300:]))
        log("  created     %s  [%s @ %s]" % (ln["worktree"], ln["branch"], r["base_sha"][:12]))
    for name, text in p["prompts"].items():
        if name in by and by[name]["prompt_file"]:
            write_atomic(by[name]["prompt_file"], text, 0o600)
    if p["prompts"]:
        env = "RUN=%s\nBASE=%s\nVAULT=%s\n" % (r["round"], r["base_sha"], r["vault"])
        write_atomic(os.path.join(r["work_root"], "prompts", "run.env"), env, 0o600)
    for ln in r["lanes"]:
        os.makedirs(ln["work_dir"], exist_ok=True)
        if not os.path.isdir(ln["handoff_dir"]):
            os.makedirs(ln["handoff_dir"])  # an existing handoff folder is never touched
    write_atomic(path, dump_round(r))
    r["_path"] = path
    import round_board
    round_board.write_board(r, None, log)
    log("  wrote       %s" % path)
    return path


def render_new(p, write):
    r = p["round"]
    lines = ["pai round new %s %s%s" % (r["project"], r["round"], "" if write else "  (preview: nothing is written; add --write)"),
             "  repository  %s  (%s)" % (r["repo"], r["repo_found"]),
             "  base        %s  (%s)" % (r["base_sha"], r["base_source"]),
             "  vault       %s  (%s)" % (r["vault"], r["vault_source"]),
             "  state       %s" % r["state_dir"],
             "  worktrees   %s" % r["trusted_root"],
             "  work        %s" % r["work_root"],
             "  tmux        session %s" % r["tmux_session"]]
    if r["prompt"]:
        lines.append("  prompt      %s  sha256 %s" % (r["prompt"]["file"], r["prompt"]["sha256"][:16]))
    if p["missing"]:
        lines.append("  REFUSED: placeholders left in the prompt: %s" % ", ".join(p["missing"]))
    by = {x["lane"]: x for x in p["actions"]}
    for ln in r["lanes"]:
        x = by.get(ln["name"], {"action": "?", "why": ""})
        lines.append("  lane %-10s %-20s %s  [%s]  window %s%s" % (
            ln["name"], x["action"], ln["worktree"], ln["branch"], ln["tmux_target"],
            "  prompt %s" % ln["prompt_sha256"][:12] if ln["prompt_sha256"] else ""))
        if x["action"] == "CONFLICT":
            lines.append("       CONFLICT: %s" % x["why"])
    return "\n".join(lines)


# -- command line ----------------------------------------------------------------------------
def parser():
    ap = argparse.ArgumentParser(prog="pai round", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="round configuration (default $PAI_ROUND_CONFIG or ~/.config/pai/round.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="open a round: main clone, base, vault, worktrees, prompts, board (preview unless --write)")
    n.add_argument("project")
    n.add_argument("round")
    n.add_argument("--lane", action="append", help="lane name (repeatable; default: the prompt's parts)")
    n.add_argument("--prompt", help="the round prompt note (absolute, or relative to the vault)")
    n.add_argument("--github", help="OWNER/NAME of the GitHub repository")
    n.add_argument("--repo", help="the main clone (default: found by its GitHub remote under --search)")
    n.add_argument("--search", action="append", help="where to look for the main clone (repeatable; default ~ and /srv)")
    n.add_argument("--base", help="the owner-confirmed base SHA (default: git ls-remote origin BASE_BRANCH)")
    n.add_argument("--base-branch", help="default main")
    n.add_argument("--vault", help="the vault folder (default: config, the prompt's location, then $VAULT)")
    n.add_argument("--trusted-root", help="where the worktrees go (default ~/workspace/{project}-workers/{round})")
    n.add_argument("--work-root", help="logs, queues, prompts (default ~/.cache/{project}-{round})")
    n.add_argument("--tmux-session", help="tmux session (default {project}-{round})")
    n.add_argument("--window-prefix", help="tmux window name prefix (default none)")
    n.add_argument("--set", action="append", metavar="NAME=VALUE", help="an extra prompt placeholder value")
    n.add_argument("--json", action="store_true")
    n.add_argument("--write", action="store_true", help="create what the preview shows")
    s = sub.add_parser("status", help="one line per lane (reads tmux, /proc and files; no model)")
    s.add_argument("round_file", nargs="?")
    s.add_argument("--json", action="store_true")
    s.add_argument("--board", action="store_true", help="also rewrite the board's generated block")
    s.add_argument("--write-vault", action="store_true", help="also write PAI/rounds/<ROUND>/, PAI/decisions/ and PAI/Home.md")
    la = sub.add_parser("launch", help="start a lane's Claude Code session in tmux (preview unless --write)")
    la.add_argument("lane")
    la.add_argument("--round-file")
    la.add_argument("--paste-prompt", action="store_true", help="paste the lane's prompt once the input box is ready")
    la.add_argument("--wait", type=float, default=60.0, help="seconds to wait for the input box (with --paste-prompt)")
    la.add_argument("--write", action="store_true")
    nx = sub.add_parser("next", help="a short resume message from the lane's latest RESTART.md")
    nx.add_argument("lane")
    nx.add_argument("--round-file")
    nx.add_argument("--send", action="store_true", help="send it with the same checks as pai round inject")
    nx.add_argument("--no-enter", action="store_true", help="with --send: paste only, do not press Enter")
    nx.add_argument("--queue-ok", action="store_true", help="with --send: let a busy Claude queue it")
    ij = sub.add_parser("inject", help="one message into a lane, only when safe (no dialog, no draft, window there)")
    ij.add_argument("lane")
    ij.add_argument("--file", required=True, help="the message (UTF-8 text)")
    ij.add_argument("--round-file")
    ij.add_argument("--queue-ok", action="store_true", help="allow it while Claude is working (Claude queues it); "
                    "an owner draft in the input box still refuses")
    ij.add_argument("--dry-run", action="store_true", help="show what would be sent and to whom; send nothing")
    ij.add_argument("--again", action="store_true", help="send a text that was already sent once more")
    ij.add_argument("--no-enter", action="store_true", help="paste only; Enter is yours")
    ij.add_argument("--wait", type=float, default=10.0, help="seconds to wait for the lane to become ready")
    b = sub.add_parser("brief", help="a 1-2k token package for a fresh mastermind")
    b.add_argument("round_file", nargs="?")
    b.add_argument("--out", help="also write it to this file")
    b.add_argument("--max-tokens", type=int, default=2000)
    lg = sub.add_parser("log", help="append one decision to COORDINATOR-LOG.md")
    lg.add_argument("decision")
    lg.add_argument("--why", required=True)
    lg.add_argument("--round-file")
    c = sub.add_parser("close", help="acceptance checklist, then reclaim clean idle worktrees (preview unless --write)")
    c.add_argument("round_file", nargs="?")
    c.add_argument("--only-re", help="only worktree paths matching this regular expression are candidates")
    c.add_argument("--all-worktrees", action="store_true", help="also old worktrees of the repository, not only this round's")
    c.add_argument("--write", action="store_true")
    bu = sub.add_parser("budget", help="usage per lane and a suggested next batch")
    bu.add_argument("round_file", nargs="?")
    bu.add_argument("--remaining", type=float, help="remaining budget in USD")
    bu.add_argument("--per-item", type=float, help="assumed USD per item (default: measured, else 0.5)")
    bu.add_argument("--add", nargs=2, metavar=("LANE", "USD"), help="record a Remote Control session's usage by hand")
    bu.add_argument("--note", help="with --add: where the number comes from")
    bu.add_argument("--json", action="store_true")
    co = sub.add_parser("collect", help="verify each lane's handoff and update the board")
    co.add_argument("round_file", nargs="?")
    co.add_argument("--jev", action="store_true", help="ask JEV whether each handoff's claims match its evidence")
    co.add_argument("--json", action="store_true")
    co.add_argument("--write", action="store_true", help="write COLLECT.json and the board")
    co.add_argument("--write-vault", action="store_true", help="also write the vault notes (as status --write-vault)")
    rt = sub.add_parser("route", help="the model and effort for a kind of work ([routing] in round.toml)")
    rt.add_argument("kind")
    rt.add_argument("--text", help="the item text, for a JEV tier suggestion (--jev)")
    rt.add_argument("--jev", action="store_true")
    pp = sub.add_parser("prplan", help="a lane's NEXT-PR.md as a pai worktree publish preview plan (reads only)")
    pp.add_argument("lane")
    pp.add_argument("--round-file")
    pp.add_argument("--commit", type=int, default=1)
    pp.add_argument("--next-pr", help="default: NEXT-PR.md of the lane's newest checkpoint")
    pp.add_argument("--offline", action="store_true", help="do not ask the remote or GitHub")
    pp.add_argument("--out", help="write the plan (a new file)")
    ig = sub.add_parser("integrate", help="every lane's patch on a scratch copy of the base: conflicts, hot files, tests")
    ig.add_argument("round_file", nargs="?")
    ig.add_argument("--plan", action="store_true", help="apply the lanes' patches (and proposals) in order and report")
    ig.add_argument("--test", action="store_true", help="also run tools/verify.py on the merged copy")
    ig.add_argument("--order", help="lane order, comma-separated (default: ROUND.json's)")
    ig.add_argument("--proposals", choices=("all", "none"), default="all")
    ig.add_argument("--write", action="store_true", help="write INTEGRATION.md / INTEGRATION.json to the state folder")
    w = sub.add_parser("web", help="the read-only round board in the browser (127.0.0.1 only)")
    w.add_argument("round_file", nargs="?")
    w.add_argument("--port", type=int, default=0)
    w.add_argument("--no-browser", action="store_true")
    return ap


def _round(a, attr="round_file"):
    return load_round(find_round(getattr(a, attr, None)))


def main(argv=None):
    a = parser().parse_args(argv)
    out = sys.stdout
    try:
        cfg, _ = load_config(a.config)
        if a.cmd == "new":
            p = plan_new(a, cfg)
            if a.json and not a.write:
                print(json.dumps({"round": p["round"], "actions": p["actions"], "missing": p["missing"]}, indent=1,
                                 ensure_ascii=False))
                return 1 if p["missing"] or any(x["action"] == "CONFLICT" for x in p["actions"]) else 0
            print(render_new(p, a.write))
            if not a.write:
                return 1 if p["missing"] or any(x["action"] == "CONFLICT" for x in p["actions"]) else 0
            apply_new(p, print)
            return 0
        if a.cmd == "status":
            import round_status
            r = _round(a)
            rows = round_status.status(r)
            if a.board:
                import round_board
                round_board.write_board(r, rows, lambda s: None)
            if a.write_vault:
                import round_vault
                round_vault.write_vault(r, rows, round_vault.last_collect(r), lambda s: print(s, file=sys.stderr))
            print(json.dumps(round_status.as_json(r, rows), indent=1, ensure_ascii=False) if a.json
                  else round_status.render(r, rows))
            return 0
        if a.cmd in ("launch", "next", "inject"):
            import round_launch
            r = load_round(find_round(a.round_file))
            return round_launch.main(a, r, cfg, out)
        if a.cmd == "brief":
            import round_board
            r = _round(a)
            text, tokens = round_board.brief(r, a.max_tokens)
            if a.out:
                write_atomic(expand(a.out), text)
            print(text)
            print("(about %d tokens%s)" % (tokens, "" if tokens <= a.max_tokens else "; OVER the %d limit" % a.max_tokens),
                  file=sys.stderr)
            return 0
        if a.cmd == "log":
            import round_board
            r = load_round(find_round(a.round_file))
            print(round_board.log_decision(r, a.decision, a.why))
            return 0
        if a.cmd == "close":
            import round_close
            return round_close.main(a, _round(a), out)
        if a.cmd == "budget":
            import round_collect
            return round_collect.budget_main(a, _round(a), out, cfg)
        if a.cmd == "collect":
            import round_collect
            return round_collect.collect_main(a, _round(a), out)
        if a.cmd == "route":
            import round_collect
            return round_collect.route_main(a, cfg, out)
        if a.cmd == "prplan":
            import round_prplan
            return round_prplan.main(a, load_round(find_round(a.round_file)), out)
        if a.cmd == "integrate":
            import round_integrate
            return round_integrate.main(a, _round(a), out)
        if a.cmd == "web":
            import round_web
            return round_web.main(find_round(a.round_file), a.port, a.no_browser, out=out)
    except RoundError as e:
        msg = str(e)
        print("pai round: %s" % msg, file=sys.stderr)
        return 1 if msg.startswith(("CONFLICT", "MISMATCH", "REFUSED")) else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
