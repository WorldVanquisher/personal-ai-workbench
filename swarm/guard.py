#!/usr/bin/env python3
"""Safety guard for pai-swarm workers. Standard library only.

Three entry points, all fail-closed (a bug blocks the action instead of letting it through):

  guard.py bash  --root REPO                  PreToolUse hook for the Bash tool.
                                              Blocks git/gh commands that change anything,
                                              and any command that touches the .git directory.
  guard.py edit  --root REPO [--allow PATH]   PreToolUse hook for Edit/Write/MultiEdit/NotebookEdit.
                                              Blocks writes into .git, outside the repo,
                                              and (when --allow is given) outside the task's paths.
  guard.py shim  git|gh ARGS...               Runtime shim put first on the worker's PATH, so
                                              scripts that call git indirectly are checked too.

Hook protocol: Claude Code sends the tool call as JSON on stdin. Exit code 2 blocks the call
and the stderr text is shown to Claude as the reason.
"""
import json
import os
import shlex
import shutil
import sys
import tempfile

# git subcommands that only read. Everything else is treated as a write and blocked.
READ_ONLY_GIT = {
    "status", "diff", "log", "show", "blame", "ls-files", "ls-tree", "grep",
    "rev-parse", "describe", "shortlog", "cat-file", "diff-tree", "diff-files",
    "diff-index", "merge-base", "name-rev", "whatchanged", "rev-list", "help", "version",
}
# Global git options that are safe and take no value / take one value.
SAFE_GIT_FLAGS = {"--no-pager", "-P", "--paginate", "-p", "--no-optional-locks", "--literal-pathspecs"}
SAFE_GIT_FLAGS_WITH_VALUE = {"-C"}
# Global options that can run arbitrary programs or redirect git elsewhere.
DANGEROUS_GIT_FLAGS = ("-c", "--config-env", "--exec-path", "--git-dir", "--work-tree", "--namespace")

SHELL_OPS = {";", "&", "&&", "|", "||", "|&", "(", ")", "<", ">", ">>", "<<", ";;", "\n"}
GIT_NAMES = {"git", "git.exe"}
GH_NAMES = {"gh", "gh.exe", "hub"}
TEXT_LIMIT_DEPTH = 4


def block(reason):
    sys.stderr.write("pai-swarm guard: " + reason + "\n")
    sys.exit(2)


# ---------------------------------------------------------------- git argument check

def check_git_args(args):
    """Return None if `git ARGS` is read-only, else a reason string."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in SAFE_GIT_FLAGS:
            i += 1
            continue
        if a in SAFE_GIT_FLAGS_WITH_VALUE:
            i += 2
            continue
        if a.startswith(DANGEROUS_GIT_FLAGS):
            return "git option %r is not allowed for workers" % a
        if a in ("--version", "--help", "-h"):
            return None
        if a.startswith("-"):
            return "unknown git option %r (blocked to be safe)" % a
        break
    if i >= len(args):
        return None  # bare `git`, prints help
    sub = args[i]
    if sub in READ_ONLY_GIT:
        rest = args[i + 1:]
        # `git diff --output=f` etc. write plain files, not the repo; fine.
        # `git grep -O<cmd>` / `--open-files-in-pager` runs a program.
        if sub == "grep" and any(r.startswith(("-O", "--open-files-in-pager")) for r in rest):
            return "git grep -O runs a program; not allowed"
        if sub in ("diff", "log", "show") and any(r.startswith("--ext-diff") for r in rest):
            return "--ext-diff runs a program; not allowed"
        return None
    return ("`git %s` changes the repository. Workers must not run git write commands; "
            "the human reviews and commits. Read-only git (status/diff/log/show) is fine." % sub)


# ---------------------------------------------------------------- Bash command text check

def _tokens(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        # Unbalanced quotes etc. Fall back to a rough split so we still look for git.
        return command.replace("`", " ").replace("$(", " ( ").split()


def _clean(tok):
    return tok.strip("`'\"").lstrip("$").strip("({")


def touches_dot_git(tok):
    t = _clean(tok).rstrip("/")
    return (t == ".git" or t.startswith(".git/") or t.endswith("/.git") or "/.git/" in t)


WRITERS = {"rm", "mv", "cp", "chmod", "chown", "ln", "touch", "truncate", "tee", "dd",
           "install", "rsync", "sed", "perl", "unlink", "rmdir", "mkdir", "shred"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "eval"}


def _is_op(tok):
    return tok in SHELL_OPS or tok.startswith(("|", "&", ";", ">", "<"))


def check_command(command, depth=0):
    """Return None if the shell command is allowed, else a reason."""
    if depth > TEXT_LIMIT_DEPTH:
        return "command nesting too deep to check"
    toks = _tokens(command)
    programs = set()
    dot_git = False
    at_start = True
    prev = ""
    for idx, raw in enumerate(toks):
        tok = _clean(raw)
        base = os.path.basename(tok)
        if at_start and not _is_op(raw) and "=" not in raw.split("/")[0]:
            programs.add(base)
            at_start = False
        if _is_op(raw):
            at_start = True
            if raw.startswith(">") and idx + 1 < len(toks) and touches_dot_git(toks[idx + 1]):
                return "commands may not write into the .git directory"
        if base in GIT_NAMES:
            args = []
            for nxt in toks[idx + 1:]:
                if _is_op(nxt):
                    break
                args.append(_clean(nxt) if nxt.startswith("`") else nxt.rstrip("`"))
            reason = check_git_args(args)
            if reason:
                return reason
        elif base in GH_NAMES:
            return "`%s` talks to GitHub on your behalf; workers may not use it" % base
        elif touches_dot_git(raw):
            dot_git = True
        # Strings that are themselves shell code: sh -c "...", python -c "...", eval "...",
        # backticks, $(...). Plain quoted text (e.g. grep "git push" README) is not re-parsed.
        runs_code = (
            (prev.startswith("-") and prev.endswith("c") and len(prev) <= 4)
            or os.path.basename(_clean(prev)) in SHELLS
            or "`" in raw or "$(" in raw or prev == "("
        )
        if runs_code and " " in raw and not _is_op(raw):
            reason = check_command(raw.replace("`", " ").replace("$(", " ( "), depth + 1)
            if reason:
                return reason
        prev = raw
    if dot_git:
        risky = programs & WRITERS or (
            "find" in programs and any(t in ("-delete", "-exec", "-execdir") for t in toks))
        if risky:
            return "commands that modify files may not touch the .git directory"
    return None


# ---------------------------------------------------------------- file path check

def _real(p):
    return os.path.realpath(os.path.abspath(os.path.expanduser(p)))


def _under(path, parent):
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def check_edit_path(file_path, root, allowed, cwd):
    if not file_path:
        return "no file path given"
    if not os.path.isabs(file_path):
        file_path = os.path.join(cwd or root, file_path)
    path = _real(file_path)
    root = _real(root)
    tmp = _real(tempfile.gettempdir())
    if _under(path, os.path.join(root, ".git")):
        return "writing inside .git is not allowed"
    if not _under(path, root):
        if _under(path, tmp) or _under(path, "/tmp"):
            return None  # scratch files can't collide with other workers' repo files
        return "path %s is outside the repository" % path
    if allowed:
        for a in allowed:
            if _under(path, _real(os.path.join(root, a))):
                return None
        rel = os.path.relpath(path, root)
        return ("%s is outside this task's paths (%s). Other workers may own it. "
                "If the task really needs it, stop and report STATUS: BLOCKED with the reason."
                % (rel, ", ".join(allowed)))
    return None


# ---------------------------------------------------------------- entry points

def _parse(argv):
    root, allowed, rest = None, [], []
    i = 0
    while i < len(argv):
        if argv[i] == "--root":
            root = argv[i + 1]
            i += 2
        elif argv[i] == "--allow":
            allowed.append(argv[i + 1])
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return root, allowed, rest


def hook_main(mode, argv):
    root, allowed, _ = _parse(argv)
    try:
        data = json.load(sys.stdin)
    except Exception as e:  # noqa: BLE001
        block("could not read hook input (%s)" % e)
    tool = data.get("tool_name", "")
    tin = data.get("tool_input") or {}
    cwd = data.get("cwd") or root or os.getcwd()
    root = root or os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    if mode == "bash":
        reason = check_command(tin.get("command", ""))
    else:
        path = tin.get("file_path") or tin.get("notebook_path") or tin.get("path")
        reason = check_edit_path(path, root, allowed, cwd)
    if reason:
        block(reason + " [%s]" % tool)
    sys.exit(0)


def _find_real(name):
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    shim_dir = os.environ.get("PAI_SHIM_DIR", "")
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep)
             if p and _real(p) not in (_real(here), _real(shim_dir) if shim_dir else "")]
    return shutil.which(name, path=os.pathsep.join(parts))


def shim_main(argv):
    name, args = argv[0], argv[1:]
    if name in GH_NAMES:
        block("`%s` is disabled for pai-swarm workers" % name)
    reason = check_git_args(args)
    if reason:
        block(reason)
    real = os.environ.get("PAI_REAL_GIT") or _find_real("git")
    if not real:
        block("real git not found")
    os.execv(real, [real] + args)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    mode = sys.argv[1]
    try:
        if mode in ("bash", "edit"):
            hook_main(mode, sys.argv[2:])
        elif mode == "shim":
            shim_main(sys.argv[2:])
        else:
            block("unknown mode %r" % mode)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001  fail closed
        block("guard error: %r" % e)


if __name__ == "__main__":
    main()
