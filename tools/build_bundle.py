#!/usr/bin/env python3
"""Build the portable pai release archives from an explicit allowlist.

    python3 -B tools/build_bundle.py [--out dist] [--root .]

Output (refuses to overwrite; nothing is written if any of these exists):
    OUT/pai-workbench-<version>.tar.gz          (Linux, macOS)
    OUT/pai-workbench-<version>.tar.gz.sha256
    OUT/pai-workbench-<version>.zip             (Windows)
    OUT/pai-workbench-<version>.zip.sha256

Both archives hold the same files with the same bytes, under a single
top-level directory `pai-workbench-<version>/`.
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
- Line endings are checked: *.cmd files must use CRLF (cmd.exe needs it), every
  other file LF only.
- The archives are deterministic: sorted entries, fixed timestamps
  (SOURCE_DATE_EPOCH, default 0; ZIP clamps to its 1980 minimum), uid/gid 0,
  normalized modes, a gzip header without a name or time, and fixed ZIP
  metadata. A rebuild of the same files with the same Python (zlib) gives the
  same bytes.

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
import stat
import sys
import tarfile
import time
import zipfile
from pathlib import Path

NAME = "pai-workbench"
FORBIDDEN = ("pai.local.toml", "*.local.toml", "trial-evidence/*", "trial-evidence", ".git", ".git/*", "*/.git/*",
             "*__pycache__*", "*.pyc", ".env", "*.env", ".env.*", "*secret*", "*credential*", "*password*", "*token*",
             "*.key", "*.pem", "dist/*", "release/*", "tools/*", "tests/*", "Agent Inbox/*",
             "context-*.md", ".DS_Store", "*~")
EXECUTABLE = ("bin/pai",)
ZIP_MIN_EPOCH = 315532800  # 1980-01-01T00:00:00Z, the earliest time a ZIP entry can record
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
        # Policy paths use '/' and case-insensitive matching on every host. Only this
        # exact shipped template is exempt from the private HANDOFF.md filename rule.
        name = rel.rsplit("/", 1)[-1]
        private_handoff = name.casefold() == "handoff.md" and rel != "pai/templates/handoff.md"
        if private_handoff or any(fnmatch.fnmatchcase(rel.casefold(), p.casefold())
                                  or fnmatch.fnmatchcase(name.casefold(), p.casefold()) for p in FORBIDDEN):
            raise BuildError(f"allowlist entry is forbidden by policy: {rel}")
        full = os.path.join(root, rel)
        if os.path.islink(full) or not os.path.isfile(full):
            raise BuildError(f"allowlist entry is missing, not a regular file, or a symlink: {rel}")
        if not Path(os.path.realpath(full)).is_relative_to(real_root) or os.path.realpath(full) == real_root:
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


def check_line_endings(root, entries):
    for rel in entries:
        data = Path(root, rel).read_bytes()
        if rel.endswith(".cmd"):
            if data.count(b"\r\n") != data.count(b"\n") or data.count(b"\r") != data.count(b"\n"):
                raise BuildError(f"{rel} must use CRLF line endings only (cmd.exe misparses LF batch files)")
        elif b"\r" in data:
            raise BuildError(f"{rel} must use LF line endings only")


def archive_names(out, version):
    base = os.path.join(out, f"{NAME}-{version}")
    return {"tar.gz": base + ".tar.gz", "zip": base + ".zip"}


def make_tar(base, files, epoch):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for d in directories(base, files):
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
    return gz.getvalue()


def make_zip(base, files, epoch):
    stamp = time.gmtime(max(epoch, ZIP_MIN_EPOCH))[:6]
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in directories(base, files):
            zi = zipfile.ZipInfo(d + "/", stamp)
            zi.create_system, zi.external_attr = 3, (stat.S_IFDIR | 0o755) << 16 | 0x10
            z.writestr(zi, b"")
        for rel, data, mode in files:
            zi = zipfile.ZipInfo(f"{base}/{rel}", stamp)
            # Unix mode bits let unzip restore bin/pai as executable; Windows ignores them.
            zi.create_system, zi.external_attr = 3, (stat.S_IFREG | mode) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    return blob.getvalue()


def directories(base, files):
    dirs = {os.path.dirname(f"{base}/{rel}") for rel, _, _ in files} | {base}
    return sorted({"/".join(d.split("/")[:i]) for d in dirs for i in range(1, len(d.split("/")) + 1)})


def build(root, out, epoch):
    """Build both archives. Returns ({format: (path, sha256)}, version, file count)."""
    root = os.path.abspath(root)
    version = read_version(root)
    entries = read_allowlist(root)
    check_entries(root, entries)
    scan_dev_paths(root, entries)
    check_line_endings(root, entries)
    out = os.path.abspath(out)
    real_out, real_root = Path(os.path.realpath(out)), Path(os.path.realpath(root))
    if real_out.is_relative_to(real_root) and real_out.relative_to(real_root).parts[:1] != ("dist",):
        raise BuildError("--out inside the source tree must be dist/ (git-ignored)")
    base = f"{NAME}-{version}"
    targets = archive_names(out, version)
    for path in targets.values():
        for p in (path, path + ".sha256"):
            if os.path.lexists(p):
                raise BuildError(f"{p} already exists; release artifacts are never overwritten. "
                                 "Choose another --out (published assets stay untouched)")
    os.makedirs(out, exist_ok=True)

    files = []
    for rel in entries:
        data = Path(root, rel).read_bytes()
        mode = 0o755 if rel in EXECUTABLE else 0o644
        files.append((rel, data, mode))
    manifest = {
        "schema": "pai_bundle_manifest_v1", "name": NAME, "version": version,
        "license": "MIT", "python_requires": ">=3.11", "runtime_dependencies": [],
        "entry_points": {"posix_launcher": "bin/pai", "windows_launcher": "bin/pai.cmd",
                         "script": "bin/pai.py (python3 bin/pai.py, or py -3 bin\\pai.py on Windows)"},
        "archives": {"tar.gz": "Linux and macOS", "zip": "Windows"},
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

    blobs = {"tar.gz": make_tar(base, files, epoch), "zip": make_zip(base, files, epoch)}
    result = {}
    for fmt, path in targets.items():
        digest = sha256_bytes(blobs[fmt])
        with open(path, "xb") as f:
            f.write(blobs[fmt])
        with open(path + ".sha256", "x", encoding="utf-8", newline="\n") as f:
            f.write(f"{digest}  {os.path.basename(path)}\n")
        result[fmt] = (path, digest)
    return result, version, len(files)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p.add_argument("--out", default=None, help="output directory (default: ROOT/dist)")
    a = p.parse_args(argv)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    try:
        archives, version, n = build(a.root, a.out or os.path.join(a.root, "dist"), epoch)
    except BuildError as e:
        print(f"build_bundle: refused: {e}", file=sys.stderr)
        return 2
    print(f"pai {version}: {n} files")
    for fmt in ("tar.gz", "zip"):
        path, digest = archives[fmt]
        print(f"{path}\nsha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
