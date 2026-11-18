"""`pai install`: copy this verified release bundle to a user-local folder and put one `pai`
launcher where your shell finds it. No administrator or root rights, no network.

    pai install [--prefix DIR] [--bin-dir DIR] [--dry-run]

Defaults (--prefix DIR puts everything under DIR, the launcher in DIR/bin; --bin-dir moves it):
  Linux, macOS  PREFIX ${XDG_DATA_HOME:-~/.local/share}/pai-workbench, launcher ~/.local/bin/pai (a symlink)
  Windows       PREFIX %LOCALAPPDATA%\\pai-workbench, launcher PREFIX\\bin\\pai.cmd (a two-line batch file)

PREFIX/versions/<version>/ holds one verified copy per version; the launcher points at one of
them. Each step refuses on any problem before the launcher changes:
  1. this folder must be a release bundle whose files all match its SHA256SUMS (check the
     archive's .sha256 before extracting it; this re-checks what was extracted)
  2. copy into a temporary folder in PREFIX/versions and check the copy the same way
  3. move it into place: an identical copy already there is kept as it is; a damaged or
     different one is moved aside first and removed afterwards
  4. point the launcher at it with one atomic rename
A failure at any step leaves the previous launcher, and so the previous version, working.
Earlier versions stay in PREFIX/versions until you delete them. Nothing else is written:
configuration, task notes, credentials and shell profiles are never touched. When the
launcher's folder is not on PATH, the exact command that adds it is printed instead.
"""
import hashlib
import json
import os
import shutil
import sys

from pai import __version__, config

MARK = "pai-workbench launcher"
DIST = "pai-workbench"  # the Python package name (pyproject.toml)
REPO = "https://github.com/WorldVanquisher/personal-ai-workbench"


def uv_command(version=__version__, force=False):
    """The one-line install (or, with force, reinstall/upgrade) of a tagged version with uv."""
    return f"uv tool install{' --force' if force else ''} git+{REPO}@v{version}"


class InstallError(Exception):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sums(root):
    """{relative path: sha256} from the bundle's SHA256SUMS (`sha256sum -c` format)."""
    path = os.path.join(root, "SHA256SUMS")
    if not os.path.isfile(path):
        raise InstallError(f"{root} is not a release bundle (no SHA256SUMS), so there is nothing verified to install. "
                           "In a checkout, build the archives first: python3 -B tools/build_bundle.py --out dist")
    sums = {}
    with open(path, encoding="utf-8") as f:
        for line in f.read().splitlines():
            if not line:
                continue
            digest, sep, rel = line.partition("  ")
            parts = rel.split("/")
            if (not sep or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or not rel
                    or rel.startswith("/") or "\\" in rel or ".." in parts or ":" in rel):
                raise InstallError(f"SHA256SUMS has a line that is not '<sha256>  <path>': {line[:100]!r}")
            sums[rel] = digest
    if "MANIFEST.json" not in sums or "bin/pai.py" not in sums:
        raise InstallError("SHA256SUMS does not list the bundle (MANIFEST.json, bin/pai.py)")
    return sums


def damaged(root, sums):
    """Relative paths that are missing, links, or differ from SHA256SUMS."""
    bad = []
    for rel, digest in sorted(sums.items()):
        p = os.path.join(root, *rel.split("/"))
        if os.path.islink(p) or not os.path.isfile(p) or _sha256(p) != digest:
            bad.append(rel)
    return bad


def defaults(env=None, windows=None):
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    home = os.path.expanduser("~")
    if windows:
        prefix = os.path.join(env.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"), "pai-workbench")
        return prefix, os.path.join(prefix, "bin")
    data = env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(data, "pai-workbench"), os.path.join(home, ".local", "bin")


def _same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def on_path(folder, env=None):
    env = os.environ if env is None else env
    return any(p and _same(p, folder) for p in (env.get("PATH") or "").split(os.pathsep))


def _home_relative(path, posix=True):
    home = os.path.expanduser("~")
    if posix and (path == home or path.startswith(home + os.sep)):
        return "$HOME" + path[len(home):].replace(os.sep, "/")  # a POSIX shell line, whatever this host uses
    return path


def path_fix(bin_dir, env=None, windows=None, platform=None):
    """Lines telling the user exactly how to put bin_dir on PATH for new terminals."""
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    if windows:
        return ["[Environment]::SetEnvironmentVariable(\"Path\", \"%s;\" + [Environment]::GetEnvironmentVariable("
                "\"Path\", \"User\"), \"User\")" % bin_dir,
                "(PowerShell; then open a new terminal. cmd.exe for this window only: set \"PATH=%s;%%PATH%%\")" % bin_dir]
    shown = _home_relative(bin_dir)
    shell = os.path.basename(env.get("SHELL") or "")
    if shell == "fish":
        return ["fish_add_path %s" % shown.replace("$HOME", "~"), "(then open a new terminal)"]
    rc = {"zsh": "~/.zshrc", "bash": "~/.bash_profile" if (platform or sys.platform) == "darwin" else "~/.bashrc"}.get(
        shell, "~/.profile")
    line = 'export PATH="%s:$PATH"' % shown
    return ["echo '%s' >> %s" % (line, rc), "(then open a new terminal; for this one only: %s)" % line]


def _launcher(bin_dir, windows):
    return os.path.join(bin_dir, "pai.cmd" if windows else "pai")


def current(bin_dir, prefix, windows=None):
    """(version, target) the existing launcher runs, or (None, problem text) if it is not ours,
    or (None, None) if there is none."""
    windows = os.name == "nt" if windows is None else windows
    link = _launcher(bin_dir, windows)
    versions = os.path.join(prefix, "versions")
    if windows:
        if not os.path.lexists(link):
            return None, None
        try:
            with open(link, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            return None, f"{link} cannot be read ({e})"
        if MARK not in text:
            return None, f"{link} exists and was not written by pai install"
        target = text.split('"')[1] if text.count('"') >= 2 else ""
    else:
        if not os.path.lexists(link):
            return None, None
        if not os.path.islink(link):
            return None, f"{link} exists and is not a launcher pai install made (a regular file)"
        target = os.path.join(os.path.dirname(link), os.readlink(link))
    rel = os.path.relpath(os.path.normpath(target), versions)
    parts = rel.split(os.sep)
    if rel.startswith(os.pardir) or len(parts) < 3:
        return None, f"{link} points outside {versions} ({target})"
    return parts[0], target


def _copy(src, dest, sums, windows):
    for rel in sorted(sums) + ["SHA256SUMS"]:
        s, d = os.path.join(src, *rel.split("/")), os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(s, d)
        if not windows:
            os.chmod(d, 0o755 if rel == "bin/pai" else 0o644)


def install(src=None, prefix=None, bin_dir=None, dry_run=False, env=None, windows=None, out=print):
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    src = os.path.realpath(src or config.WORKBENCH)
    dp, db = defaults(env, windows)
    explicit = prefix is not None
    prefix = os.path.abspath(os.path.expanduser(prefix or dp))
    # --prefix DIR keeps everything under DIR (launcher in DIR/bin); --bin-dir overrides that
    bin_dir = os.path.abspath(os.path.expanduser(bin_dir or (os.path.join(prefix, "bin") if explicit else db)))
    sums = read_sums(src)
    bad = damaged(src, sums)
    if bad:
        raise InstallError(f"this bundle does not match its SHA256SUMS ({', '.join(bad[:5])}"
                           f"{', ...' if len(bad) > 5 else ''}). Nothing was installed; extract it again from an "
                           "archive whose .sha256 checks out")
    versions = os.path.join(prefix, "versions")
    dest = os.path.join(versions, __version__)
    have, target = current(bin_dir, prefix, windows)
    if have is None and target:
        raise InstallError(f"{target}. Nothing was installed: move it away, or choose another --bin-dir")
    link = _launcher(bin_dir, windows)
    launch = os.path.join(dest, "bin", "pai.cmd" if windows else "pai")
    out(f"pai {__version__} install{' (dry run: nothing is written)' if dry_run else ''}")
    out(f"  bundle    {src}: {len(sums)} files match SHA256SUMS")
    reuse = os.path.isdir(dest) and _same(dest, src) or (
        os.path.isfile(os.path.join(dest, "SHA256SUMS")) and _sha256(os.path.join(dest, "SHA256SUMS"))
        == _sha256(os.path.join(src, "SHA256SUMS")) and not damaged(dest, sums))
    if dry_run:
        out(f"  would     {'keep' if reuse else 'copy it to'} {dest}")
        out(f"  would     point {link} at it{f' (now {have})' if have else ''}")
        return 0
    os.makedirs(versions, exist_ok=True)
    if reuse:
        out(f"  version   {dest} (already installed and intact; kept)")
    else:
        tmp = os.path.join(versions, f".{__version__}.{os.getpid()}.tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            _copy(src, tmp, sums, windows)
            bad = damaged(tmp, sums)
            if bad:
                raise InstallError(f"the copy in {tmp} does not match SHA256SUMS ({', '.join(bad[:5])})")
            aside = None
            if os.path.lexists(dest):
                aside = os.path.join(versions, f".{__version__}.{os.getpid()}.old")
                os.replace(dest, aside)
            try:
                os.replace(tmp, dest)
            except OSError:
                if aside:
                    os.replace(aside, dest)
                raise
            if aside:
                shutil.rmtree(aside, ignore_errors=True)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        out(f"  version   {dest} ({'replaced a damaged or different copy' if aside else 'new'})")
    os.makedirs(bin_dir, exist_ok=True)
    tmp = os.path.join(bin_dir, f".pai.{os.getpid()}.tmp")
    if windows:
        with open(tmp, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f'@echo off\nrem {MARK}: runs the installed version (written by pai install; safe to delete)\n'
                    f'"{launch}" %*\n')
    else:
        if os.path.lexists(tmp):
            os.remove(tmp)
        os.symlink(launch, tmp)
    os.replace(tmp, link)  # the one step that switches versions
    out(f"  launcher  {link} -> {launch}{f' (was {have})' if have and have != __version__ else ''}")
    receipt = os.path.join(prefix, "receipt.json")  # lets `pai doctor` find the launcher folder later
    with open(receipt + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"version": __version__, "prefix": prefix, "bin_dir": bin_dir, "launcher": link}, f, indent=1)
    os.replace(receipt + ".tmp", receipt)
    older = sorted(v for v in os.listdir(versions) if not v.startswith(".") and v != __version__)
    if older:
        out(f"  kept      earlier version(s) {', '.join(older)} in {versions} (delete them when satisfied)")
    found = shutil.which("pai", path=env.get("PATH") or "")
    if not on_path(bin_dir, env):
        out(f"  PATH      {bin_dir} is not on PATH, so a new terminal will say `pai: command not found`. Add it:")
        for line in path_fix(bin_dir, env, windows):
            out(f"              {line}")
    elif found and not _same(found, link):
        out(f"  PATH      another pai comes first on PATH: {found}. Remove it, or put {bin_dir} before its folder")
    else:
        out(f"  PATH      {bin_dir} is on PATH")
    out("\nNext, in a new terminal and inside your Git checkout: pai doctor")
    return 0


def main(a):
    if config.PACKAGED:
        print(f"pai install: this pai {__version__} is an installed Python package ({config.WORKBENCH}), not an "
              "extracted release archive, so there is nothing here for `pai install` to copy. Upgrade or reinstall "
              f"it the way it was installed, for example: {uv_command('X.Y.Z', force=True)}", file=sys.stderr)
        return 2
    try:
        return install(prefix=a.prefix, bin_dir=a.bin_dir, dry_run=a.dry_run)
    except InstallError as e:
        print(f"pai install: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"pai install: {e}. The previous launcher (if any) is unchanged.", file=sys.stderr)
        return 2
