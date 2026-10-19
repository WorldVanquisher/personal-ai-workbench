"""Explicit, validated project configuration.

A project registers:

- a repository root, with instruction files and optional documents inside it;
- a vault root, with a memory package, an inbox and permitted Markdown roots
  inside it.

Every file the tool reads is named in the configuration, or sits directly
inside the registered memory-package directory (non-recursive). Explicit handoff
discovery additionally reads direct Inbox task directories and HANDOFF.md only.
After symlinks
(and, on Windows, junctions and other reparse points) are resolved, every path
must stay inside its declared root. Containment compares path components, never
string prefixes. Anything else is refused, never followed.
"""

import fnmatch
import os
import tomllib
from dataclasses import dataclass, field

WORKBENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(WORKBENCH, "pai.local.toml")

# Never read, whatever the configuration says.
DENY_PATTERNS = (".*", "*.env", "*secret*", "*credential*", "*password*", "*token*",
                 "*.key", "*.pem", "id_rsa*", "id_ed25519*")

DEFAULT_ALWAYS = ("human decision", "constraint", "freeze", "frozen", "limitation", "unknown",
                  "blocker", "contradiction", "conflict", "inapplicable", "sync",
                  "verified vs unverified")
DEFAULT_PROPOSAL_FILES = ("knowledge-proposals.md",)


class ConfigError(Exception):
    pass


@dataclass
class Source:
    kind: str          # instructions | repo-doc | vault-note | memory
    label: str         # display path, relative to its root
    path: str          # absolute, resolved
    status: str        # ok | missing | refused | excluded
    reason: str = ""


@dataclass
class Project:
    key: str
    name: str
    repo: str
    vault: str
    memory: str             # absolute memory-package directory
    memory_rel: str
    inbox: str              # absolute inbox directory
    inbox_rel: str
    markdown_roots: list
    instructions: list
    repo_docs: list
    project_notes: list
    exclude: list
    always_include: list
    instruction_always: list
    proposal_files: list
    budget_chars: int
    max_section_chars: int
    required_section_chars: int
    sources: list = field(default_factory=list)


def config_path(explicit=None):
    return explicit or os.environ.get("PAI_CONFIG") or DEFAULT_CONFIG


def is_absolute(path):
    """Absolute on this platform. On Windows this needs a drive or UNC share: a rooted path such as
    \\notes is relative to the current drive, so it is not accepted."""
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        return False
    return os.name != "nt" or bool(os.path.splitdrive(path)[0])


def _real_dir(path, what):
    if not is_absolute(path):
        raise ConfigError(f"{what} must be an absolute path (got {path!r})")
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise ConfigError(f"{what} does not exist or is not a directory: {path}")
    return real


def inside(path, root):
    """True when the resolved path is root or below it, compared by components (case-folded on Windows).

    Paths on different drives are never inside each other.
    """
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives, or a mix of absolute and relative paths
        return False


def _rel(rel, what):
    bad = (not isinstance(rel, str) or not rel or os.path.isabs(rel) or rel[0] in "/\\"
           or ".." in rel.replace("\\", "/").split("/"))
    # Windows: no drive-relative paths (C:notes) and no alternate data streams (note.md:hidden).
    if not bad and os.name == "nt" and (os.path.splitdrive(rel)[0] or ":" in rel):
        bad = True
    if bad:
        raise ConfigError(f"{what} must be a relative path without '..' (got {rel!r})")
    return rel


def excluded(name, patterns):
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in tuple(patterns) + DENY_PATTERNS)


def resolve_file(root, rel, kind, exclude, extra_roots=None):
    """A Source for root/rel: refused if it escapes root (or extra_roots), excluded by name, or missing."""
    label = rel.replace(os.sep, "/")
    full = os.path.join(root, rel)
    if any(excluded(part, exclude) for part in rel.replace("\\", "/").split("/")):
        return Source(kind, label, full, "excluded", "matches an exclusion pattern")
    if not os.path.lexists(full):
        return Source(kind, label, full, "missing", "not found")
    real = os.path.realpath(full)
    if not inside(real, root):
        return Source(kind, label, full, "refused", "symlink escapes its root")
    if extra_roots is not None and not any(inside(real, r) for r in extra_roots):
        return Source(kind, label, full, "refused", "outside the permitted Markdown roots")
    if not os.path.isfile(real):
        return Source(kind, label, full, "missing", "not a regular file")
    return Source(kind, label, real, "ok")


def read(path=None):
    """(path, parsed TOML) of the configuration, with actionable errors."""
    path = config_path(path)
    try:
        with open(path, "rb") as f:
            return path, tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"no configuration at {path}; create one with `pai init …` "
                          "(see `pai init --help`) or copy pai.example.toml")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}")


def load(project_key, path=None):
    path, data = read(path)
    projects = data.get("projects", {})
    if project_key not in projects:
        known = ", ".join(sorted(projects)) or "none"
        raise ConfigError(f"project {project_key!r} not registered in {path}; known: {known}")
    return from_dict(project_key, projects[project_key])


def from_dict(project_key, p):
    """Validate one project table and resolve its sources (no file is written)."""
    if not isinstance(p, dict):
        raise ConfigError(f"[projects.{project_key}] must be a table")
    repo = _real_dir(p.get("repo"), "repo")
    vault = _real_dir(p.get("vault"), "vault")
    if inside(repo, vault) or inside(vault, repo):
        raise ConfigError("repo and vault must not contain each other")
    roots = []
    for r in p.get("markdown_roots", []):
        real = os.path.realpath(os.path.join(vault, _rel(r, "markdown_roots entry")))
        if not inside(real, vault):
            raise ConfigError(f"markdown root escapes the vault: {r}")
        roots.append(real)
    memory_rel = _rel(p.get("memory", ""), "memory")
    memory = os.path.realpath(os.path.join(vault, memory_rel))
    inbox_rel = _rel(p.get("inbox", ""), "inbox")
    inbox = os.path.realpath(os.path.join(vault, inbox_rel))
    for name, real in (("memory", memory), ("inbox", inbox)):
        if not inside(real, vault) or real == vault:
            raise ConfigError(f"{name} must resolve to a directory inside the vault")
    if not os.path.isdir(inbox):
        raise ConfigError(f"inbox does not exist: {inbox_rel} (create that folder in the vault yourself; pai does not create it)")
    budget = int(p.get("budget_chars", 16000))
    if not 2000 <= budget <= 200000:
        raise ConfigError("budget_chars must be between 2000 and 200000")
    proj = Project(
        key=project_key, name=p.get("name", project_key), repo=repo, vault=vault,
        memory=memory, memory_rel=memory_rel, inbox=inbox, inbox_rel=inbox_rel, markdown_roots=roots,
        instructions=[_rel(x, "instructions entry") for x in p.get("instructions", ["AGENTS.md"])],
        repo_docs=[_rel(x, "repo_docs entry") for x in p.get("repo_docs", [])],
        project_notes=[_rel(x, "project_notes entry") for x in p.get("project_notes", [])],
        exclude=list(p.get("exclude", [])),
        always_include=[s.lower() for s in p.get("always_include", DEFAULT_ALWAYS)],
        instruction_always=[s.lower() for s in p.get("instruction_always", [])],
        proposal_files=list(p.get("proposal_files", DEFAULT_PROPOSAL_FILES)),
        budget_chars=budget, max_section_chars=int(p.get("max_section_chars", 2500)),
        required_section_chars=int(p.get("required_section_chars", 1500)),
    )
    proj.sources = gather_sources(proj)
    return proj


def gather_sources(proj):
    """Resolve every registered source. The memory package is listed non-recursively, *.md only."""
    out = []
    for rel in proj.instructions:
        out.append(resolve_file(proj.repo, rel, "instructions", proj.exclude))
    for rel in proj.repo_docs:
        out.append(resolve_file(proj.repo, rel, "repo-doc", proj.exclude))
    for rel in proj.project_notes:
        out.append(resolve_file(proj.vault, rel, "vault-note", proj.exclude, extra_roots=proj.markdown_roots or None))
    if not os.path.isdir(proj.memory):
        out.append(Source("memory", proj.memory_rel, proj.memory, "missing", "memory package directory not found"))
    else:
        for name in sorted(os.listdir(proj.memory)):
            rel = os.path.join(proj.memory_rel, name)
            if not name.lower().endswith(".md"):
                continue
            out.append(resolve_file(proj.vault, rel, "memory", proj.exclude,
                                    extra_roots=[proj.memory]))
    return out
