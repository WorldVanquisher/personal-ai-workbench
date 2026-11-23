"""`pai doctor`: local, read-only checks that report problems honestly.

Checks:
- the Python version;
- bundle integrity (SHA256SUMS and MANIFEST.json, when this is a release
  bundle; the package RECORD, when this is an installed package);
- the configuration file, and each project's registered roots and sources;
- with --agents, which agent executables are on PATH.

It never launches, authenticates, installs or probes anything remote: agent
detection is a PATH lookup only.
"""

import hashlib
import json
import os
import shutil
import sys

from pai import __version__, config

MIN_PYTHON = (3, 11)
AGENTS = ("claude", "codex")
EXTERNAL = ("syncthing",)
# Files that may exist in a bundle directory without being listed in its manifest.
LOCAL_ALLOWED = ("pai.local.toml",)


class Report:
    def __init__(self):
        self.lines, self.problems = [], 0

    def ok(self, msg):
        self.lines.append(f"ok       {msg}")

    def info(self, msg):
        self.lines.append(f"info     {msg}")

    def warn(self, msg):
        self.lines.append(f"WARNING  {msg}")

    def problem(self, msg, hint=""):
        self.problems += 1
        self.lines.append(f"PROBLEM  {msg}" + (f"\n         → {hint}" if hint else ""))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def check_python(r):
    v = sys.version_info
    if v >= MIN_PYTHON:
        r.ok(f"Python {v.major}.{v.minor}.{v.micro} on {sys.platform} (requires ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}): "
             f"{sys.executable or 'interpreter path unknown'}")
    else:  # unreachable through the launchers and `-m pai`, which refuse earlier; kept for direct calls
        r.problem(f"Python {v.major}.{v.minor} is too old", "install Python 3.11 or newer")


def check_package(r, pkg=os.path.dirname(os.path.abspath(__file__))):
    """An installed package (uv tool install, pip): every file its RECORD lists with a hash still
    matches it. RECORD is written by the installer; it proves the files are unchanged since then."""
    import base64
    from importlib import metadata

    from pai import install
    try:
        dist = metadata.distribution(install.DIST)
    except metadata.PackageNotFoundError:
        r.warn(f"pai {__version__} runs from {pkg}, but no {install.DIST} package metadata is installed beside it, "
               "so its files cannot be checked")
        return
    files = [f for f in (dist.files or []) if f.hash]
    mine = os.path.realpath(os.path.join(pkg, "__init__.py"))
    if not any(os.path.realpath(f.locate()) == mine for f in files):
        r.warn(f"the {install.DIST} metadata found does not describe this pai ({pkg}), so its files cannot be checked")
        return
    bad = []
    for f in files:
        try:
            with open(f.locate(), "rb") as fh:
                digest = hashlib.new(f.hash.mode, fh.read()).digest()
        except (OSError, ValueError):
            bad.append(str(f))
            continue
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != f.hash.value:
            bad.append(str(f))
    fix = "reinstall it the way you installed it, for example: " + install.uv_command(force=True)
    if dist.version != __version__:
        r.problem(f"package metadata says {install.DIST} {dist.version}, the code is {__version__}", fix)
    if bad:
        r.problem(f"installed files modified or missing: {', '.join(bad[:5])}{', ...' if len(bad) > 5 else ''}", fix)
    if not bad and dist.version == __version__:
        r.ok(f"package {install.DIST} {__version__} intact: {len(files)} files match its RECORD ({pkg})")


def check_bundle(r, root=config.WORKBENCH):
    if config.PACKAGED and root == config.WORKBENCH:
        check_package(r)
        return
    sums = os.path.join(root, "SHA256SUMS")
    manifest = os.path.join(root, "MANIFEST.json")
    if not os.path.exists(sums):
        r.info(f"pai {__version__} running from {root}: no SHA256SUMS, so this is a development checkout, not a release bundle")
        return
    listed = {}
    try:
        with open(sums, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for line in lines:
            if line.strip():
                digest, _, rel = line.partition("  ")
                listed[rel] = digest
    except (OSError, ValueError) as e:
        r.problem(f"cannot read SHA256SUMS: {e}", "re-extract the bundle")
        return
    bad = [rel for rel, d in sorted(listed.items())
           if not os.path.isfile(os.path.join(root, rel)) or _sha256(os.path.join(root, rel)) != d]
    try:
        with open(manifest, encoding="utf-8") as f:
            m = json.load(f)
        files = {f["path"] for f in m["files"]} | {"MANIFEST.json"}
        bversion = m.get("version")
    except (OSError, ValueError, KeyError, TypeError) as e:
        r.problem(f"cannot read MANIFEST.json: {e}", "re-extract the bundle")
        return
    unlisted = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for n in names:
            rel = os.path.relpath(os.path.join(dirpath, n), root).replace(os.sep, "/")
            if rel in files or rel == "SHA256SUMS" or rel in LOCAL_ALLOWED or n.endswith(".pyc"):
                continue
            unlisted.append(rel)
    if set(listed) != files:
        r.problem("SHA256SUMS and MANIFEST.json list different files", "re-extract the bundle")
    if bversion != __version__:
        r.problem(f"bundle manifest version {bversion} differs from the code version {__version__}", "re-extract the bundle")
    if bad:
        r.problem(f"bundle files modified or missing: {', '.join(bad)}", "re-extract the bundle from a verified archive")
    if unlisted:
        r.problem(f"files not in the bundle manifest: {', '.join(sorted(unlisted))}",
                  "remove them, or re-extract into a new directory")
    if not bad and not unlisted and set(listed) == files and bversion == __version__:
        r.ok(f"bundle pai {__version__} intact: {len(listed)} files match SHA256SUMS ({root})")


def check_config(r, path, only=None):
    path = config.config_path(path)
    inside_bundle = os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(config.WORKBENCH)
    try:
        path, data = config.read(path)
    except config.ConfigError as e:
        r.problem(str(e))
        return
    r.ok(f"configuration {path}")
    if inside_bundle and os.path.exists(os.path.join(config.WORKBENCH, "SHA256SUMS")):
        r.warn("the configuration lives inside the bundle directory; keep a copy, or set PAI_CONFIG to a path "
               "outside it, before replacing the bundle on update")
    projects = data.get("projects", {})
    if not projects:
        r.problem("no [projects.…] table in the configuration", "run `pai init …`")
        return
    keys = [only] if only else sorted(projects)
    for key in keys:
        if key not in projects:
            r.problem(f"project {key!r} is not registered", f"registered: {', '.join(sorted(projects))}")
            continue
        p = projects[key]
        before = r.problems
        for name in ("repo", "vault"):
            value = p.get(name)
            if not config.is_absolute(value):
                r.problem(f"{key}: {name} must be an absolute path (got {value!r})", "fix it in the configuration")
            elif not os.path.isdir(value):
                r.problem(f"{key}: {name} not found or not a directory: {value}",
                          "mount or create it, or correct the path (moved machines need new paths)")
            elif not os.access(value, os.R_OK | os.X_OK):
                r.problem(f"{key}: {name} is not readable: {value}")
        if r.problems > before:
            continue
        try:
            proj = config.from_dict(key, p)
        except config.ConfigError as e:
            r.problem(f"{key}: {e}")
            continue
        if not os.access(proj.inbox, os.W_OK):
            r.problem(f"{key}: inbox is not writable: {proj.inbox_rel}", "handoff new will fail until it is")
        __import__("pai.commands", fromlist=["x"]).doctor_checks(r, key, proj)  # core-lane checks (R6-COR-051)
        counts = {}
        for s in proj.sources:
            counts[s.status] = counts.get(s.status, 0) + 1
            if s.status == "missing" and s.kind == "instructions":  # R6-FIN-004 (core NOTES C10): not a failure
                r.warn(f"{key}: instructions source MISSING: {s.label} (not found; optional: pai context uses it when present)")
            elif s.status != "ok":
                r.warn(f"{key}: {s.kind} source {s.status.upper()}: {s.label} ({s.reason})")
        r.ok(f"{key}: roots valid; sources " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))


def check_agents(r):
    for name in AGENTS:
        p = shutil.which(name)
        r.info(f"agent {name}: {'found at ' + p if p else 'not found on PATH'} (not launched; login is external)")
    for name in EXTERNAL:
        p = shutil.which(name)
        r.info(f"{name}: {'found at ' + p if p else 'not found on PATH'} (external; pai never configures it)")


def run(config_path=None, project=None, agents=False):
    r = Report()
    check_python(r)
    check_bundle(r)
    check_config(r, config_path, project)
    if agents:
        check_agents(r)
    r.lines.append(f"\n{'NO PROBLEMS FOUND' if not r.problems else str(r.problems) + ' PROBLEM(S) FOUND'}")
    r.lines.append(next_step(r.problems))
    return r


REPO_URL = "https://github.com/WorldVanquisher/personal-ai-workbench"


def manual(name):
    """Where to read a manual: the copy in this bundle or checkout if there is one (an installed
    package has none), else the file on GitHub for this version."""
    from pai import __version__
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", name)
    return local if os.path.isfile(local) else f"{REPO_URL}/blob/v{__version__}/docs/{name}"


def next_step(problems):
    """The last line of doctor: which manual section to read next."""
    if problems:
        return ("Next: fix each PROBLEM above (the → line says how), then run this doctor again. "
                f"Stuck? {manual('FIRST-USE.md')}, section 8 \"When something goes wrong\".")
    return (f"Next: {manual('FIRST-USE.md')}, section 6 \"Your first round\"; "
            f"for a later round, {manual('REFRESH.md')}, part (b) \"Every round\".")
