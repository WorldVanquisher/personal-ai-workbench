#!/usr/bin/env python3
"""Safety guard for pai-swarm workers. Standard library only.

Three entry points, all fail-closed (a bug blocks the action instead of letting it through):

  guard.py bash  --root REPO [--allow PATH]   PreToolUse hook for the Bash tool.
                                              Blocks git/gh commands that change anything,
                                              any command that touches the .git directory,
                                              and writes (>, >>, tee, sed -i, cp, mv, rm, dd of=,
                                              touch, truncate, ...) outside the repository or,
                                              when --allow is given, outside the task's paths.
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
import re
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


# ---------------------------------------------------------------- Bash write targets
#
# Claude Code may run a Bash command without asking (acceptEdits, a read-only-looking command, an
# allow rule), so the hook, not the permission prompt, is where `paths:` is enforced for shell
# writes too. The parser is deliberately simple and fails closed: a write target it cannot
# resolve (a variable, a command substitution, an unknown `cd`) is refused when the task has
# paths. It finds the targets of redirections and of the common file writers below; a program
# that writes files on its own (a build tool, a script) is not inspected.

REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&", "<>"}
DEVICE_OK = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/fd/")
WRAPPERS = {"sudo", "env", "nohup", "time", "command", "exec", "nice", "stdbuf", "timeout", "builtin"}
ALL_OPERANDS = {"rm", "unlink", "rmdir", "shred", "touch", "mkdir", "mv"}
LAST_OPERAND = {"cp", "install", "ln"}
OPT_VALUE = {"truncate": {"-s", "--size", "-r", "--reference"}, "touch": {"-d", "-r", "-t", "--date", "--reference"},
             "mkdir": {"-m", "--mode"}, "install": {"-m", "-o", "-g", "--mode", "--owner", "--group", "-S"},
             "cp": {"-S", "--suffix"}, "mv": {"-S", "--suffix"}, "timeout": {"-s", "-k"}, "nice": {"-n"}}
HEREDOC_RE = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def _strip_heredocs(command):
    """The command without here-document bodies (their text is data, not shell)."""
    out, ends = [], []
    for line in command.split("\n"):
        if ends:
            dash, word = ends[0]
            if (line.lstrip("\t") if dash else line) == word:
                ends.pop(0)
            continue
        out.append(line)
        ends.extend((m.group(1), m.group(3)) for m in HEREDOC_RE.finditer(line))
    return "\n".join(out)


def _simple_commands(command):
    """[[token, ...], ...]: the command split at ; & && | || ( ) and newlines."""
    lex = shlex.shlex(_strip_heredocs(command).replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        toks = list(lex)
    except ValueError:
        return None  # unbalanced quotes: cannot tell
    cmds, cur = [], []
    for t in toks:
        if t in (";", "&", "&&", "|", "||", "|&", "(", ")", ";;", "$"):
            if cur:
                cmds.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        cmds.append(cur)
    return cmds


def _operands(words, prog):
    """Non-option words after the program name, honouring `--` and options that take a value."""
    takes = OPT_VALUE.get(prog, set())
    out, i, opts_done = [], 1, False
    while i < len(words):
        w = words[i]
        if not opts_done and w == "--":
            opts_done = True
        elif not opts_done and w.startswith("-") and w != "-":
            if w in takes:
                i += 1
        else:
            out.append(w)
        i += 1
    return out


def _sed_targets(words):
    in_place = any(w == "--in-place" or w.startswith("--in-place=") or
                   (w.startswith("-") and not w.startswith("--") and "i" in w[1:].split(".", 1)[0]
                    and not w.startswith(("-e", "-f", "-l")))
                   for w in words[1:])
    if not in_place:
        return []
    rest, script_given, i = [], False, 1
    while i < len(words):
        w = words[i]
        if w in ("-e", "-f", "--expression", "--file", "-l", "--line-length"):
            script_given = script_given or w in ("-e", "-f", "--expression", "--file")
            i += 2
            continue
        if w.startswith(("--expression=", "--file=")) or (w.startswith(("-e", "-f")) and len(w) > 2):
            script_given = True
        elif not w.startswith("-") or w == "-":
            rest.append(w)
        i += 1
    return rest if script_given else rest[1:]


def write_targets(command, _depth=0):
    """Every path a shell command writes to, as written (relative paths unresolved), plus the
    `cd` steps in between as ("cd", DIR) tuples. None when the command cannot be parsed."""
    out = []
    cmds = _simple_commands(command)
    if cmds is None:
        return None
    for toks in cmds:
        words, i = [], 0
        while i < len(toks):
            t = toks[i]
            if t in REDIRECTS or (t.startswith(">") and set(t) <= set(">|&")):
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                if not (t.endswith("&") or t == ">&") or not (nxt.isdigit() or nxt == "-"):
                    out.append(nxt)
                i += 2
                continue
            if t in ("<", "<<", "<<<", "<<-"):
                i += 2
                continue
            words.append(t)
            i += 1
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        while words and os.path.basename(words[0]) in WRAPPERS:
            prog = os.path.basename(words[0])
            words = words[1:]
            while words and (words[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])
                             or (prog == "timeout" and re.match(r"^[0-9.]+[smhd]?$", words[0]))):
                words = words[1:]
        if not words:
            continue
        prog = os.path.basename(words[0])
        if prog == "xargs":  # R6-COR-067: the operands arrive on stdin, so the targets cannot be known
            rest = [w for w in words[1:] if not w.startswith("-")]
            if rest and (os.path.basename(rest[0]) in ALL_OPERANDS | LAST_OPERAND | {"tee", "dd", "truncate", "sed"}):
                out.append("$(xargs %s)" % os.path.basename(rest[0]))
            continue
        if prog == "find" and any(w in ("-delete", "-exec", "-execdir", "-ok", "-fprint", "-fls") for w in words):
            out.append("$(find %s)" % " ".join(words[1:3]))  # matched files are only known at run time
            continue
        if prog == "cd":
            out.append(("cd", words[1] if len(words) > 1 else "~"))
        elif prog in SHELLS and _depth < TEXT_LIMIT_DEPTH:
            code = words[1:] if prog == "eval" else (
                [words[words.index("-c") + 1]] if "-c" in words[:-1] else [])
            for c in code:
                inner = write_targets(c, _depth + 1)
                if inner is None:
                    return None
                out.extend(inner)
        elif prog == "tee":
            out.extend(_operands(words, prog))
        elif prog == "sed":
            out.extend(_sed_targets(words))
        elif prog == "dd":
            out.extend(w[3:] for w in words[1:] if w.startswith("of="))
        elif prog in ("cp", "mv"):
            target = [w.split("=", 1)[1] for w in words if w.startswith("--target-directory=")]
            ti = [k for k, w in enumerate(words) if w == "-t" and k + 1 < len(words)]
            target += [words[k + 1] for k in ti] + [w[2:] for w in words[1:] if w.startswith("-t") and len(w) > 2]
            ops = [w for k, w in enumerate(words) if w in _operands(words, prog) and (k - 1) not in ti]
            if target:
                out.extend(target + (ops if prog == "mv" else []))
            elif prog == "mv":
                out.extend(ops)
            else:
                out.extend(ops[-1:])
        elif prog in LAST_OPERAND:
            out.extend(_operands(words, prog)[-1:])
        elif prog in ALL_OPERANDS or prog == "truncate":
            out.extend(_operands(words, prog))
    return out


def check_write_targets(command, root, allowed, cwd):
    """None if every write the command makes stays inside the repository (and the task's paths
    when given; temporary folders are always fine), else a reason."""
    targets = write_targets(command)
    if targets is None:
        return "cannot parse this command to check where it writes" if allowed else None
    here = cwd or root
    for t in targets:
        if isinstance(t, tuple):  # cd DIR
            d = t[1]
            if d == "-" or "$" in d or "`" in d:
                here = None
            elif here is not None or os.path.isabs(os.path.expanduser(d)):
                here = os.path.join(here or "/", os.path.expanduser(d))
            continue
        if t.startswith(DEVICE_OK):
            continue
        if not t or "$" in t or "`" in t or t.startswith(("<(", ">(")) or (here is None and not os.path.isabs(
                os.path.expanduser(t))):
            if allowed:
                return ("cannot tell where this command writes (%r); with task paths set, write to a "
                        "literal path inside them" % (t or "?"))
            continue
        reason = check_edit_path(t, root, allowed, here)
        if reason:
            return "this command writes to %s: %s" % (t, reason)
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
        command = tin.get("command", "")
        reason = check_command(command) or check_write_targets(command, root, allowed, cwd)
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
