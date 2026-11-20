#!/usr/bin/env python3
"""Run pai's PATH advice in a real shell and check it works (1.0 readiness 059).

    python3 -B tools/path_hint_check.py bash|zsh|fish|pwsh

For several awkward folder names (spaces, quotes, $, a backtick, non-ASCII; inside and outside
HOME) it prints what `pai install` would advise for that shell, executes it exactly as printed in
that shell with a throwaway HOME, opens a new shell and checks that the folder now comes first on
PATH. PowerShell's advice sets the real user-level Path, so for pwsh only its quoted folder string
is evaluated (it must give back the folder exactly); nothing about your environment is changed.
Exit 0 when every case passes; 2 when the shell is not installed (nothing checked).
bash runs on the worker's host; zsh, fish and PowerShell are owner-run where they are installed.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pai import install  # noqa: E402

NAMES = (".local/bin", "My Tools/bin", 'odd "quoted" $dir/`tick`/bin', "café/bin", "it's/bin")


def new_shell_path(shell, home, env):
    if shell == "fish":
        argv = ["fish", "-l", "-c", "string join : $PATH"]
    else:
        rc = {"bash": "~/.bashrc", "zsh": "~/.zshrc"}[shell]
        argv = [shell, "-c", 'source %s; printf "%%s" "$PATH"' % rc]
    p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
    return p.stdout.strip()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    shell = args[0] if args else "bash"
    exe = {"bash": "bash", "zsh": "zsh", "fish": "fish", "pwsh": "pwsh"}.get(shell)
    if not exe:
        print("usage: path_hint_check.py bash|zsh|fish|pwsh")
        return 2
    if not shutil.which(exe):
        print("path_hint_check: %s is not installed here; nothing was checked" % exe)
        return 2
    ok = True
    tmp = tempfile.mkdtemp(prefix="pai-path-hint-")
    try:
        home = os.path.join(tmp, "Ada's home")
        os.makedirs(home)
        for rel in NAMES:
            for base in (home, os.path.join(tmp, "opt pai")):
                d = os.path.join(base, rel)
                env = {"HOME": home, "PATH": "/usr/bin:/bin", "SHELL": "/bin/" + shell,
                       "XDG_CONFIG_HOME": os.path.join(home, ".config")}
                for f in (".bashrc", ".zshrc"):
                    if os.path.exists(os.path.join(home, f)):
                        os.remove(os.path.join(home, f))
                old = os.environ.get("HOME")
                os.environ["HOME"] = home
                try:
                    first = install.path_fix(d, env, windows=shell == "pwsh", platform="linux")[0]
                finally:
                    if old is not None:
                        os.environ["HOME"] = old
                if shell == "pwsh":
                    # Never set the user's real PATH: evaluate only the quoted folder string of the advice
                    # (`[Environment]::SetEnvironmentVariable("Path", "<folder>;" + ...)`) in PowerShell.
                    literal = first.split('"Path", ', 1)[1].split(" + [Environment]", 1)[0]
                    p = subprocess.run(["pwsh", "-NoProfile", "-Command", "Write-Output (%s)" % literal], env=env,
                                       capture_output=True, text=True, timeout=60)
                    good = p.returncode == 0 and p.stdout.rstrip("\r\n") == d + ";"
                else:
                    p = subprocess.run([exe, "-c", first], env=env, capture_output=True, text=True, timeout=60)
                    got = new_shell_path(shell, home, env)
                    good = p.returncode == 0 and got.split(":")[0] == d
                ok = ok and good
                print("%s  %-46s %s" % ("PASS" if good else "FAIL", d.replace(tmp, "<tmp>"), first if not good else ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("path_hint_check %s: %s" % (shell, "OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
