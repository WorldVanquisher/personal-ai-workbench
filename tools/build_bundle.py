#!/usr/bin/env python3
"""Build the portable pai release bundle from an explicit allowlist.

    python3 -B tools/build_bundle.py [--out dist] [--root .]

Output (refuses to overwrite):
    OUT/pai-workbench-<version>.tar.gz
    OUT/pai-workbench-<version>.tar.gz.sha256

Each archive has a single top-level directory `pai-workbench-<version>/`.
Alongside the allowlisted files, that directory holds MANIFEST.json (version,
requirements, per-file sha256/size/mode) and SHA256SUMS (`sha256sum -c`
format, also checked by `pai doctor`).

Rules:
- Only files named in release/allowlist.txt are packaged. Each must be a
  regular, non-symlink file inside the root.
- The allowlist must cover every runtime module (pai/**/*.py) and template
  (pai/templates/*).
- Entries naming local configuration, evidence, handoffs, caches, VCS data or
  secrets are refused, even if listed.
- Bundled text is scanned for absolute developer paths, and the build refuses
  if one is found.
- The archive is deterministic: sorted entries, fixed mtime (SOURCE_DATE_EPOCH,
  default 0), uid/gid 0, normalized modes, and a gzip header without a name or
  time. A rebuild of the same files gives the same bytes.

This is release tooling, deliberately outside the runtime package. It uses no
network and runs no commands.
"""

import argparse
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

NAME = "pai-workbench"
FORBIDDEN = ("pai.local.toml", "*.local.toml", "trial-evidence/*", "trial-evidence", ".git", ".git/*", "*/.git/*",
             "*__pycache__*", "*.pyc", ".env", "*.env", ".env.*", "*secret*", "*credential*", "*password*", "*token*",
             "*.key", "*.pem", "dist/*", "release/*", "tools/*", "tests/*", "HANDOFF.md", "*/HANDOFF.md", "Agent Inbox/*",
             "context-*.md", ".DS_Store", "*~")
EXECUTABLE = ("bin/pai",)
DEV_PATH = re.compile(r"/home/[A-Za-z0-9_.-]+/|/Users/[A-Za-z0-9_.-]+/|/srv/data/|[A-Za-z]:\\Users\\")


class BuildError(Exception):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_version(root):
    text = Path(root, "pai", "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"\s*$', text, re.M)
    if not m:
        raise BuildError("pai/__init__.py has no __version__ = \"X.Y.Z\"")
    return m.group(1)


def read_allowlist(root):
    path = os.path.join(root, "release", "allowlist.txt")
    entries = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("/") or "\\" in line or ".." in line.split("/") or any(c in line for c in "*?["):
            raise BuildError(f"allowlist line {n}: must be a plain relative path, got {line!r}")
        entries.append(line)
    if len(set(entries)) != len(entries):
        raise BuildError("allowlist has duplicate entries")
    return sorted(entries)


def check_entries(root, entries):
    real_root = os.path.realpath(root)
    for rel in entries:
        if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p) for p in FORBIDDEN):
            raise BuildError(f"allowlist entry is forbidden by policy: {rel}")
        full = os.path.join(root, rel)
        if os.path.islink(full) or not os.path.isfile(full):
            raise BuildError(f"allowlist entry is missing, not a regular file, or a symlink: {rel}")
        if not os.path.realpath(full).startswith(real_root + os.sep):
            raise BuildError(f"allowlist entry resolves outside the root: {rel}")
    runtime = set()
    for dirpath, dirs, files in os.walk(os.path.join(root, "pai")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
            if rel.endswith(".py") or rel.startswith("pai/templates/"):
                runtime.add(rel)
    missing = sorted(runtime - set(entries))
    if missing:
        raise BuildError(f"runtime files not in the allowlist (add them or remove them): {', '.join(missing)}")


def scan_dev_paths(root, entries):
    for rel in entries:
        text = Path(root, rel).read_text(encoding="utf-8", errors="replace")
        m = DEV_PATH.search(text)
        if m:
            raise BuildError(f"{rel} contains an absolute developer path ({m.group(0)!r}); use placeholders")


def build(root, out, epoch):
    root = os.path.abspath(root)
    version = read_version(root)
    entries = read_allowlist(root)
    check_entries(root, entries)
    scan_dev_paths(root, entries)
    out = os.path.abspath(out)
    if os.path.realpath(out).startswith(os.path.realpath(root) + os.sep) and os.path.relpath(out, root).split(os.sep)[0] != "dist":
        raise BuildError("--out inside the source tree must be dist/ (git-ignored)")
    os.makedirs(out, exist_ok=True)
    base = f"{NAME}-{version}"
    archive = os.path.join(out, base + ".tar.gz")
    if os.path.lexists(archive) or os.path.lexists(archive + ".sha256"):
        raise BuildError(f"{archive} already exists; remove it or choose another --out")

    files = []
    for rel in entries:
        data = Path(root, rel).read_bytes()
        mode = 0o755 if rel in EXECUTABLE else 0o644
        files.append((rel, data, mode))
    manifest = {
        "schema": "pai_bundle_manifest_v1", "name": NAME, "version": version,
        "python_requires": ">=3.11", "runtime_dependencies": [],
        "entry_points": {"launcher": "bin/pai", "module": "python3 -m pai (from the bundle directory)"},
        "external_prerequisites": ["agent CLIs and their logins (e.g. Claude Code)", "a Markdown vault",
                                   "file synchronization such as Syncthing", "the project repositories"],
        "not_included": ["local configuration (pai.local.toml)", "vault contents", "handoffs", "trial evidence",
                         "credentials", "caches", "version-control data", "generated context"],
        "files": [{"path": rel, "sha256": sha256_bytes(d), "size": len(d), "mode": oct(m)} for rel, d, m in files],
    }
    mdata = (json.dumps(manifest, indent=2) + "\n").encode()
    files.append(("MANIFEST.json", mdata, 0o644))
    files.sort()
    sums = "".join(f"{sha256_bytes(d)}  {rel}\n" for rel, d, _ in files).encode()
    files.append(("SHA256SUMS", sums, 0o644))
    files.sort()

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        dirs = sorted({os.path.dirname(f"{base}/{rel}") for rel, _, _ in files} | {base})
        dirs = {"/".join(d.split("/")[:i]) for d in dirs for i in range(1, len(d.split("/")) + 1)}
        for d in sorted(dirs):
            ti = tarfile.TarInfo(d)
            ti.type, ti.mode, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = tarfile.DIRTYPE, 0o755, epoch, 0, 0, "", ""
            tar.addfile(ti)
        for rel, data, mode in files:
            ti = tarfile.TarInfo(f"{base}/{rel}")
            ti.size, ti.mode, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = len(data), mode, epoch, 0, 0, "", ""
            tar.addfile(ti, io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz, mtime=epoch, compresslevel=9) as g:
        g.write(raw.getvalue())
    blob = gz.getvalue()
    with open(archive, "xb") as f:
        f.write(blob)
    digest = sha256_bytes(blob)
    with open(archive + ".sha256", "x", encoding="utf-8") as f:
        f.write(f"{digest}  {os.path.basename(archive)}\n")
    return archive, digest, version, len(files)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p.add_argument("--out", default=None, help="output directory (default: ROOT/dist)")
    a = p.parse_args(argv)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    try:
        archive, digest, version, n = build(a.root, a.out or os.path.join(a.root, "dist"), epoch)
    except BuildError as e:
        print(f"build_bundle: refused: {e}", file=sys.stderr)
        return 2
    print(f"pai {version}: {n} files\n{archive}\nsha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
