"""The prompt library (format `pai-prompts/1`): versioned prompt files with declared placeholders
and a managed block, and the commands that render, check, compare and install them.

    pai prompt list   [--library DIR]                     the library's files, versions and kinds
    pai prompt render NAME [NAME ...] --round R --lane L [--var K=V ...] [--out FILE] [--force]
                                                         fill the declared placeholders; refuse when one
                                                         is missing; write the file and print its sha256
    pai prompt lint   [NAME|PATH ...] [--rendered] [--json] [--jev [--jev-below 0.45]]
                                                         required sections, placeholders, length, and
                                                         text that must not be in a prompt
    pai prompt diff   [NAME ...] --dest DIR               the packaged library against a project's copy
    pai prompt init   --dest DIR [--write]                install or upgrade the library into a project:
                                                         only managed blocks change; text outside them,
                                                         and blocks edited by hand, are never overwritten
    pai prompt eval   ARTIFACT --contract NAME [--jev] [--json] [--out DIR]     (pai/prompteval.py)
    pai prompt optimize --dry-run ...                    offline rewrite proposals  (pai/promptopt.py)

A library file:

    <!-- pai-prompts/1 begin name=worker-base version=3 kind=worker vars=RUN_ID,LANE -->
    ...the managed text, with placeholders like {{RUN_ID}}...
    <!-- pai-prompts/1 end -->
    ...anything here (or above the begin line) is the project's own text...

Placeholders are `{{NAME}}` with NAME in capitals, digits and underscores, and they must be listed
in `vars=`. Prose that needs to show one writes `\\{{NAME}}`: it renders as `{{NAME}}` and is never
treated as a placeholder (the R6 check counted such prose as an unfilled placeholder). An installed
copy also carries `sha256=` of its managed text, so pai can tell a hand edit inside the block from
an older version. Standard library only; no network except `lint --jev` / `eval --jev`.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys

FORMAT = "pai-prompts"
VERSION = 2
READABLE = (1, 2)  # pai-prompts/2 = /1 syntax + the D3-D7 content rules checked by lint (REQUIRED_TEXT)
PACKAGED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
DEFAULT_DEST = "PAI/prompts"  # relative to the vault

BEGIN_RE = re.compile(r"^<!-- pai-prompts/(\d+) begin (.*?)\s*-->\s*$")
END_RE = re.compile(r"^<!-- pai-prompts/(\d+) end\s*-->\s*$")
MARKERISH_RE = re.compile(r"pai-prompts/", re.I)
ATTR_RE = re.compile(r"([a-z][a-z0-9_-]*)=(\S*)")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# a real placeholder: two braces, a capital, capitals/digits/underscores, two braces, not escaped
PLACEHOLDER_RE = re.compile(r"(?<!\\)\{\{([A-Z][A-Z0-9_]*)\}\}")
ESCAPED_RE = re.compile(r"\\(\{\{[A-Za-z0-9_ ]*\}\})")
# looks like a placeholder but is not one: lower case, spaces, or a leading digit
MALFORMED_RE = re.compile(r"(?<!\\)\{\{(?![A-Z][A-Z0-9_]*\}\})\s*[A-Za-z_][A-Za-z0-9_ ]*\s*\}\}")
KINDS = ("worker", "coordinator", "intake", "lane", "checkpoint", "restart", "decision-sheet", "research", "next-pr", "other")
BUILTIN_VARS = {"RUN_ID": "round", "LANE": "lane"}


class PromptError(Exception):
    """A refusal: the message says what is wrong and what to do; nothing was written."""


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Prompt:
    """One parsed library file. `body` is the managed text (between the markers, without them);
    `before` / `after` are the project's own text outside the markers."""

    def __init__(self, path, text):
        self.path, self.text = path, text
        self.nl = "\r\n" if "\r\n" in text else "\n"
        lines = text.replace("\r\n", "\n").split("\n")
        begins = [i for i, line in enumerate(lines) if BEGIN_RE.match(line)]
        ends = [i for i, line in enumerate(lines) if END_RE.match(line)]
        self.problems = []
        stray = [i + 1 for i, line in enumerate(lines) if MARKERISH_RE.search(line) and i not in begins + ends]
        if stray:
            self.problems.append("line(s) %s mention pai-prompts/ but are not a whole marker line" %
                                 ", ".join(map(str, stray)))
        if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
            self.problems.append("needs exactly one `<!-- pai-prompts/1 begin ... -->` line followed by one "
                                 "`<!-- pai-prompts/1 end -->` line (found %d begin, %d end)" % (len(begins), len(ends)))
            self.attrs, self.body, self.before, self.after, self.version = {}, text, "", "", None
            self.begin_line = None
            return
        b, e = begins[0], ends[0]
        m = BEGIN_RE.match(lines[b])
        self.version = int(m.group(1))
        self.attrs = dict(ATTR_RE.findall(m.group(2)))
        self.begin_line = b + 1
        self.before = "\n".join(lines[:b])
        self.body = "\n".join(lines[b + 1:e])
        self.after = "\n".join(lines[e + 1:])
        if self.version not in READABLE:
            self.problems.append("format pai-prompts/%d is not supported (this pai reads pai-prompts/%s)" %
                                 (self.version, " and /".join(map(str, READABLE))))

    @property
    def name(self):
        return self.attrs.get("name") or os.path.splitext(os.path.basename(self.path))[0]

    @property
    def rev(self):
        try:
            return int(self.attrs.get("version", ""))
        except ValueError:
            return None

    @property
    def kind(self):
        return self.attrs.get("kind", "other")

    @property
    def declared(self):
        v = self.attrs.get("vars", "")
        return [x for x in v.split(",") if x]

    def used(self):
        """Placeholders in the managed text and in the project's own text, in first-use order."""
        seen = []
        for part in (self.before, self.body, self.after):
            for n in PLACEHOLDER_RE.findall(part):
                if n not in seen:
                    seen.append(n)
        return seen

    def drifted(self):
        """True when an installed copy's managed text no longer matches its recorded sha256 (a hand
        edit inside the block). Library sources carry no sha256 and never drift."""
        rec = self.attrs.get("sha256")
        return bool(rec) and rec != sha256(self.body)

    def full(self):
        """The prompt as it is sent: the project's own text and the managed text, markers removed."""
        parts = [p for p in (self.before.strip("\n"), self.body.strip("\n"), self.after.strip("\n")) if p]
        return "\n\n".join(parts) + "\n"


def read(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise PromptError("cannot read %s: %s" % (path, e.strerror or e))
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return Prompt(path, data.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise PromptError("%s is not UTF-8 (byte %d)" % (path, e.start))


def library_files(directory):
    """{name: path} of every library file (*.md with a begin marker) in `directory`, sorted."""
    out = {}
    if not os.path.isdir(directory):
        raise PromptError("no prompt library at %s" % directory)
    for fn in sorted(os.listdir(directory)):
        p = os.path.join(directory, fn)
        if fn.endswith(".md") and os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                head = f.read(4096)
            if any(BEGIN_RE.match(line) for line in head.splitlines()):
                out[os.path.splitext(fn)[0]] = p
    return out


def resolve(name, library):
    """A library name (worker-base) or a path to a file."""
    if os.sep in name or "/" in name or name.endswith(".md"):
        if not os.path.isfile(name):
            raise PromptError("no such prompt file: %s" % name)
        return name
    files = library_files(library)
    if name not in files:
        raise PromptError("no prompt named %r in %s (have: %s)" % (name, library, ", ".join(files) or "none"))
    return files[name]


# ---------------------------------------------------------------- render

def parse_vars(pairs):
    out = {}
    for p in pairs or ():
        if "=" not in p:
            raise PromptError("--var needs KEY=VALUE, got %r" % p)
        k, v = p.split("=", 1)
        if not VAR_RE.match(k):
            raise PromptError("--var key %r is not a placeholder name (capitals, digits, _; starts with a capital)" % k)
        out[k] = v
    return out


def substitute(text, values):
    """Fill placeholders in one pass (a value that contains `{{X}}` stays as it is), then turn
    escapes back into literal braces."""
    text = PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text)
    return ESCAPED_RE.sub(r"\1", text)


def render(prompts, values):
    """Two callers, two contracts:

    - render(TEXT: str, values) -> (rendered, leftover): any text, markers optional (the round lane's
      `pai round new`, NOTES-FOR-prompts.md). Fills every placeholder that has a value, turns escapes
      into literal braces, never raises; `leftover` lists the real placeholders still unfilled
      (escaped prose such as \\{{NAME}} is not one), so the caller decides whether to refuse.
    - render([Prompt, ...], values) -> (rendered, used): library files (`pai prompt render`). Refuses
      with PromptError on a format problem, an undeclared placeholder or a missing value."""
    if isinstance(prompts, str):
        return render_text(prompts, values)
    return render_prompts(prompts, values)


def render_text(text, values):
    p = Prompt("<text>", text)
    body = p.full() if not p.problems else text
    left = []
    for n in PLACEHOLDER_RE.findall(body):
        if n not in values and n not in left:
            left.append(n)
    filled = PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), body)
    return ESCAPED_RE.sub(r"\1", filled), left


def render_prompts(prompts, values):
    """(text, used) for one or more parsed prompts joined by a blank line (see render)."""
    errors, used = [], []
    for p in prompts:
        errors += ["%s: %s" % (p.path, x) for x in p.problems]
        undeclared = [n for n in p.used() if n not in p.declared]
        if undeclared:
            errors.append("%s: placeholder(s) %s are not declared in vars= (declare them, or write \\{{NAME}} if the "
                          "text only talks about a placeholder)" % (p.path, ", ".join(undeclared)))
        for n in p.declared:
            if n not in used:
                used.append(n)
    missing = [n for n in used if n not in values]
    if missing:
        errors.append("no value for %s: pass %s" % (", ".join(missing), " ".join(
            "--%s" % BUILTIN_VARS[n] if n in BUILTIN_VARS else "--var %s=..." % n for n in missing)))
    empty = [n for n in used if n in values and not values[n].strip()]
    if empty:
        errors.append("empty value for %s" % ", ".join(empty))
    if errors:
        raise PromptError("not rendered:\n  " + "\n  ".join(errors))
    text = "\n".join(substitute(p.full(), values) for p in prompts)
    return text, used


# ---------------------------------------------------------------- lint

# Required parts by kind: (id, description, pattern). A part is present when a heading-like line
# (Markdown heading, `==== ... ====`, `—— ... ——`, or a bold line) matches the pattern.
SECTIONS = {
    "worker": [
        ("start-check", "start-of-work check", r"开工|start[- ]of[- ]work|before (you )?start|first reply"),
        ("rules", "rules", r"规则|rules?\b|不能做|must not"),
        ("stop", "stop rule", r"停止|停下|stop|restart|checkpoint"),
        ("evidence", "evidence labels", r"证据|evidence"),
        ("handoff", "handoff format", r"交接|handoff|最终回复|final reply"),
    ],
    "coordinator": [
        ("routing", "routing table", r"路由|routing"),
        ("clear", "/clear rule", r"/clear|清空"),
        ("reports", "where reports go", r"报告|reports?\b"),
        ("decisions", "decision sheets", r"决策单|decision"),
        ("budget", "budget rule", r"预算|budget"),
        ("human", "what only the human does", r"交给人|人来做|owner[- ]only|human"),
    ],
    "lane": [
        ("items", "items", r"条目|items?\b"),
        ("done", "done criteria", r"完成标准|done when|definition of done"),
        ("evidence", "evidence", r"证据|evidence"),
        ("deps", "dependencies", r"依赖|depend"),
    ],
    "checkpoint": [
        ("handoff", "HANDOFF.md", r"HANDOFF"),
        ("next-pr", "NEXT-PR.md", r"NEXT-PR"),
        ("items", "items and totals", r"Items|Totals|条目"),
    ],
    "intake": [
        ("input", "input", r"输入|input"),
        ("split", "split rules", r"拆分|split"),
        ("items", "item format", r"条目|item"),
        ("decisions", "decision sheet", r"决策|decision"),
        ("output", "output structure", r"输出|output"),
    ],
    "restart": [],
    "decision-sheet": [("format", "decision format", r"决策|decision")],
    "research": [("format", "report format", r"结论|conclusion|证据|evidence")],
    "next-pr": [("plan", "PR plan", r"PR|commit")],
    "other": [],
}
# pai-prompts/2: rules a library file of this kind must state (owner decisions PRM-D3..D7, 2026-11-22)
REQUIRED_TEXT = {
    "worker": [
        ("D6 not-done wording", r"not merged \(owner-only\)"),
        ("D6 no UNKNOWN for not done", r"UNKNOWN"),
        ("D6 blockers end with a command", r"反引号|backtick"),
        ("D7 full item IDs", r"全称|full ID"),
    ],
    "checkpoint": [
        ("D3 Items from pai queue table", r"pai queue table"),
        ("D4 evidence copied into the cp folder", r"复制进|copied into"),
        ("D4 5 MB rule", r"5 ?MB"),
        ("D5 State | Value | Source", r"State \| Value \| Source"),
    ],
}
# an abbreviated ID list such as "R6-PRM-001、002" (D7)
ABBREV_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]*-[A-Z]{2,5}-\d{3}\s*[、,，/]\s*\d{3}\b")
HEADING_RE = re.compile(r"^\s*(?:[-*]\s+)?(#{1,6}\s+.+|={3,}.*={3,}\s*|—{2}.*—{2}\s*|\*\*[^*]+\*\*.*)$")
# (a template that lists the headings it requires, as `- ## Name: ...`, counts as having them)
# length limits in characters of the managed text: (warn, error)
LIMITS = {"worker": (9000, 16000), "coordinator": (9000, 16000), "lane": (6000, 12000), "restart": (800, 1600)}
DEFAULT_LIMIT = (6000, 12000)
LONG_LINE = 400
FORBIDDEN = [
    ("session-link", "an agent session link", re.compile(r"claude\.ai/(?:code/)?(?:session|chat)[_/]\S+|chatgpt\.com/c/\S+", re.I)),
    ("ai-attribution", "an AI attribution line", re.compile(
        r"co-authored-by:\s*claude|generated with \[?claude|🤖\s*generated|noreply@anthropic\.com", re.I)),
    ("private-path", "a private local path (use a placeholder)", re.compile(
        r"(?<![\w{])(?:/home/[a-z_][\w.-]*|/Users/[\w.-]+|/root/|/srv/(?:data)/|[A-Za-z]:\\Users\\[\w.-]+)")),
]


def headings(text):
    return [line.strip() for line in text.split("\n") if HEADING_RE.match(line)]


def lint(p, rendered=False, known_vars=None):
    """[(level, code, message)] for one prompt; level is error | warn. `rendered=True` checks a
    filled prompt (paths are expected there, and every placeholder must be gone)."""
    out = [("error", "format", x) for x in p.problems]
    text = p.text if rendered else p.body
    if not rendered:
        if p.problems:
            return out
        if not NAME_RE.match(p.attrs.get("name", "")):
            out.append(("error", "header", "begin marker needs name=<file name without .md>"))
        elif p.attrs["name"] != os.path.splitext(os.path.basename(p.path))[0]:
            out.append(("error", "header", "name=%s does not match the file name" % p.attrs["name"]))
        if p.rev is None or p.rev < 1:
            out.append(("error", "header", "begin marker needs version=<positive integer>"))
        if p.kind not in KINDS:
            out.append(("error", "header", "kind=%s is not one of %s" % (p.kind, ", ".join(KINDS))))
        bad = [v for v in p.declared if not VAR_RE.match(v)]
        if bad:
            out.append(("error", "vars", "vars= has names that are not placeholders: %s" % ", ".join(bad)))
        undeclared = [n for n in p.used() if n not in p.declared]
        if undeclared:
            out.append(("error", "placeholder", "used but not declared in vars=: %s" % ", ".join(undeclared)))
        unused = [n for n in p.declared if n not in p.used()]
        if unused:
            out.append(("warn", "placeholder", "declared but never used: %s" % ", ".join(unused)))
        if p.drifted():
            out.append(("warn", "drift", "the managed text was edited by hand (sha256 does not match); "
                        "`pai prompt init` will not overwrite it"))
        clash = sorted({n for n in re.findall(r"\\\{\{([A-Z][A-Z0-9_]*)\}\}", p.body) if n in p.declared})
        if clash:
            out.append(("warn", "placeholder", "escaped \\{{%s}} names a declared variable: after rendering it cannot be "
                        "told from an unfilled one; show a neutral name such as \\{{NAME}}" % "}}, \\{{".join(clash)))
    else:
        # A rendered file has no markers, so it cannot say which braces were prose. Only names that
        # some library file declares count as unfilled: prose like {{NAME}} (from \{{NAME}}) does not.
        known = set(known_vars) if known_vars is not None else library_vars()
        left = sorted({n for n in PLACEHOLDER_RE.findall(text) if n in known})
        if left:
            out.append(("error", "placeholder", "unfilled placeholder(s): %s" % ", ".join(left)))
    for n, line in enumerate(text.split("\n"), 1):
        for m in MALFORMED_RE.finditer(line):
            out.append(("warn", "placeholder", "line %d: %r looks like a placeholder but is not one (NAME must be "
                        "capitals; to show braces in prose write \\{{NAME}})" % (n, m.group(0))))
    kind = p.kind if p.kind in SECTIONS else "other"
    hs = "\n".join(headings(text))
    for sid, desc, pat in SECTIONS[kind]:
        if not re.search(pat, hs, re.I):
            out.append(("error", "section", "missing the %s part (a heading matching /%s/)" % (desc, pat)))
    if not rendered and p.version is not None and p.version >= 2:
        for desc, pat in REQUIRED_TEXT.get(kind, []):
            if not re.search(pat, text):
                out.append(("error", "rule", "pai-prompts/2 needs the rule: %s (text matching /%s/)" % (desc, pat)))
    for n, line in enumerate(text.split("\n"), 1):
        if ABBREV_ID_RE.search(line):
            out.append(("warn", "ids", "line %d abbreviates item IDs (%s): write every ID in full" % (
                n, ABBREV_ID_RE.search(line).group(0))))
    warn, err = LIMITS.get(kind, DEFAULT_LIMIT)
    size = len(text)
    if size > err:
        out.append(("error", "length", "%d characters (limit %d): split it, or move detail into another file" % (size, err)))
    elif size > warn:
        out.append(("warn", "length", "%d characters (aim for at most %d)" % (size, warn)))
    for n, line in enumerate(text.split("\n"), 1):
        if len(line) > LONG_LINE:
            out.append(("warn", "length", "line %d has %d characters (at most %d)" % (n, len(line), LONG_LINE)))
    for code, desc, rx in FORBIDDEN:
        if rendered and code == "private-path":
            continue
        for n, line in enumerate(text.split("\n"), 1):
            if rx.search(line):
                out.append(("error", code, "line %d contains %s" % (n, desc)))
    return out


def library_vars(directory=PACKAGED):
    """Every placeholder name declared by a library file (plus the built-ins)."""
    names = set(BUILTIN_VARS)
    try:
        for path in library_files(directory).values():
            names.update(read(path).declared)
    except PromptError:
        pass
    return names


def rules(p):
    """The prompt's individual instructions, for JEV scoring: [(id, context, text)]. A rule is a list
    item or a numbered line; continuation lines (deeper indentation) are joined to it. `context` is
    the section heading and, for a nested item, its parent item, so a sub-bullet like "the 5 IDs to
    do first" is judged with the rule it belongs to."""
    out, heading, stack = [], "", []
    item = re.compile(r"^(\s*)(?:[-*+]|\d+[.)]|[a-z][.)]|R\d+-[A-Z]+-\d+)\s+(.+)$")
    cur = None
    for line in p.body.split("\n"):
        m = item.match(line)
        if HEADING_RE.match(line) and not m:
            heading, cur, stack = line.strip(" #=—*"), None, []
            continue
        if m:
            ind = len(m.group(1))
            while stack and stack[-1][0] >= ind:
                stack.pop()
            cur = [ind, heading, m.group(2).strip(), stack[-1][2] if stack else ""]
            out.append(cur)
            stack.append(cur)
        elif cur and line.strip() and len(line) - len(line.lstrip()) > cur[0]:
            cur[2] += " " + line.strip()
        else:
            cur = None
            if not line.strip():
                stack = []
    return [("%s#%d" % (p.name, i + 1), h + (" > " + par if par else ""), t) for i, (_, h, t, par) in enumerate(out)]


# ---------------------------------------------------------------- install / diff

def installed_text(src, dest_text=None):
    """The text to write for packaged prompt `src` (a Prompt): the begin marker gains sha256= of
    the managed text; with `dest_text`, only the managed block of the existing file is replaced."""
    attrs = dict(src.attrs)
    attrs["sha256"] = sha256(src.body)
    order = ["name", "version", "kind", "vars", "sha256"]
    keys = order + sorted(k for k in attrs if k not in order)
    fv = src.version or VERSION  # the source's format version: a /1 library installs as /1, a /2 one as /2
    begin = "<!-- %s/%d begin %s -->" % (FORMAT, fv, " ".join("%s=%s" % (k, attrs[k]) for k in keys if k in attrs))
    block = "\n".join([begin, src.body, "<!-- %s/%d end -->" % (FORMAT, fv)])
    if dest_text is None:
        return block + "\n"
    d = Prompt("", dest_text)
    parts = [x for x in (d.before, block, d.after) if x != ""]
    text = "\n".join(parts) if d.after else "\n".join(parts) + "\n"
    if d.after == "" and d.before == "":
        text = block + "\n"
    return text.replace("\n", d.nl) if d.nl != "\n" else text


def plan_install(library, dest):
    """[(name, action, detail, new_text_or_None)]: action is create | update | same | conflict."""
    out = []
    for name, src_path in library_files(library).items():
        src = read(src_path)
        if src.problems:
            raise PromptError("library file %s is broken: %s" % (src_path, "; ".join(src.problems)))
        target = os.path.join(dest, name + ".md")
        if not os.path.exists(target):
            out.append((name, "create", "v%s" % src.rev, installed_text(src)))
            continue
        cur = read(target)
        if cur.problems:
            out.append((name, "conflict", "the markers need fixing by hand: " + "; ".join(cur.problems), None))
        elif cur.drifted():
            out.append((name, "conflict", "the managed text was edited by hand; nothing overwritten. Move your edits "
                        "below the end marker (they are kept), then run again; `pai prompt diff` shows the change", None))
        elif cur.body == src.body and cur.rev == src.rev and cur.version == src.version:
            out.append((name, "same", "v%s" % src.rev, None))
        else:
            new = installed_text(src, cur.text)
            out.append((name, "update", "v%s -> v%s%s; text outside the markers kept" % (
                cur.rev, src.rev, " (format pai-prompts/%s -> /%s)" % (cur.version, src.version) if cur.version != src.version else ""), new))
    return out


def write_atomic(path, text):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".%s.tmp-%d" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def install(library, dest, write=False, out=print):
    """Preview (or with write=True apply) the library into `dest`. Returns the number of conflicts."""
    rows = plan_install(library, dest)
    conflicts = 0
    for name, action, detail, new in rows:
        out("%-8s %-16s %s" % (action.upper(), name + ".md", detail))
        if action == "conflict":
            conflicts += 1
        if write and new is not None:
            write_atomic(os.path.join(dest, name + ".md"), new)
    changes = sum(1 for r in rows if r[3] is not None)
    if write:
        out("wrote %d file(s) to %s; %d conflict(s)" % (changes, dest, conflicts))
    else:
        out("preview: %d file(s) would change in %s; %d conflict(s). Add --write to apply." % (changes, dest, conflicts))
    return conflicts


def diff(library, dest, names=None):
    """Lines of a report: per file, its status and a unified diff of the managed text."""
    lines = []
    lib = library_files(library)
    for name in names or list(lib):
        if name not in lib:
            raise PromptError("no prompt named %r in the library" % name)
        src = read(lib[name])
        target = os.path.join(dest, name + ".md")
        if not os.path.exists(target):
            lines.append("== %s: not installed (library v%s)" % (name, src.rev))
            continue
        cur = read(target)
        if cur.problems:
            lines.append("== %s: installed copy has broken markers: %s" % (name, "; ".join(cur.problems)))
            continue
        local = bool(cur.before.strip() or cur.after.strip())
        state = "same" if cur.body == src.body and cur.rev == src.rev else "differs"
        lines.append("== %s: project v%s, library v%s, %s%s%s%s" % (
            name, cur.rev, src.rev, state,
            "; format pai-prompts/%s -> /%s" % (cur.version, src.version) if cur.version != src.version else "", "; HAND-EDITED inside the managed block" if cur.drifted() else "",
            "; has project text outside the block (kept on upgrade)" if local else ""))
        if state == "differs":
            lines += list(difflib.unified_diff(cur.body.split("\n"), src.body.split("\n"),
                                               "project/%s.md" % name, "library/%s.md" % name, lineterm="", n=2))
    extra = sorted(os.path.splitext(f)[0] for f in os.listdir(dest) if f.endswith(".md")) if os.path.isdir(dest) else []
    for name in extra:
        if name not in lib and not names:
            lines.append("== %s: only in the project (not part of the library; left alone)" % name)
    return lines


def init_hook(a, stage):
    """Called twice by `pai init` (pai/cli.py): at "before-write" it previews the library install
    (only when the run is a preview), at "after-write" (the configuration was just written) it
    installs. Default <vault>/PAI/prompts; --prompts DIR relative to the vault; --prompts "" skips.
    Conflicts are reported, never fatal: the configuration is pai init's job."""
    rel = getattr(a, "prompts", DEFAULT_DEST)
    if not rel or (stage == "before-write") == bool(a.write):
        return 0
    dest = rel if os.path.isabs(rel) else os.path.join(a.vault, rel)
    print("\n# Prompt library (%s/%d) -> %s" % (FORMAT, VERSION, dest))  # the format this pai writes
    try:
        install(PACKAGED, dest, write=a.write, out=lambda s: print("#   " + s))
    except PromptError as e:
        print("#   SKIPPED: %s" % e)
    tdest = os.path.join(a.vault, DEFAULT_TEMPLATES_DEST)
    print("# Obsidian templates (core Templates plugin) -> %s" % tdest)
    try:
        install_templates(TEMPLATES, tdest, write=a.write, out=lambda s: print("#   " + s))
    except PromptError as e:
        print("#   SKIPPED: %s" % e)
    return 0


# ---------------------------------------------------------------- Obsidian templates (Block E)

TEMPLATES = os.path.join(PACKAGED, "obsidian")
DEFAULT_TEMPLATES_DEST = "99 Templates/PAI"  # relative to the vault
TEMPLATE_MANIFEST = ".pai-templates.json"
# Obsidian's core Templates plugin variables: lower case, optional :FORMAT. PAI placeholders are
# capitals only, so the two never match each other's pattern (tests/test_prompt_lib.py Templates).
OBSIDIAN_VAR_RE = re.compile(r"\{\{(date|time|title)(?::[^{}]*)?\}\}")
ANY_BRACES_RE = re.compile(r"\{\{[^{}]*\}\}")


def template_frontmatter(text):
    m = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        k = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if k:
            out[k.group(1)] = k.group(2).strip()
    return out


def load_schema(path):
    """A `pai-frontmatter-schema/1` file: {"format", "types": {TYPE: {"fields": {NAME: {"values": [...]}}}}}."""
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError) as e:
        raise PromptError("cannot read schema %s: %s" % (path, e))
    if s.get("format") != "pai-frontmatter-schema/1" or not isinstance(s.get("types"), dict):
        raise PromptError("schema %s is not pai-frontmatter-schema/1 with a types object" % path)
    return s


def lint_template(path, text, schema=None):
    """[(level, code, message)] for one Obsidian template."""
    out = []
    fm = template_frontmatter(text)
    if fm is None:
        return [("error", "frontmatter", "a template starts with YAML front matter")]
    if not re.match(r"^pai-[a-z-]+/\d+$", fm.get("template", "")):
        out.append(("error", "frontmatter", "needs `template: pai-<kind>/<version>` (marks notes made from it)"))
    if PLACEHOLDER_RE.search(text):
        out.append(("error", "placeholder", "PAI placeholders (%s) do not belong in an Obsidian template: the "
                    "Templates plugin would leave them unfilled" % ", ".join(sorted(set(PLACEHOLDER_RE.findall(text))))))
    for m in ANY_BRACES_RE.finditer(text):
        if not OBSIDIAN_VAR_RE.fullmatch(m.group(0)) and not PLACEHOLDER_RE.fullmatch(m.group(0)):
            out.append(("warn", "variable", "%s is not a core Templates variable (date, time, title)" % m.group(0)))
    if "<%" in text:
        out.append(("error", "templater", "Templater syntax (<% ... %>): templates must work with the core plugin"))
    if schema is not None:
        typ = fm.get("type", "")
        spec = schema["types"].get(typ)
        if spec is None:
            out.append(("error", "schema", "type %r is not in the schema" % typ))
        else:
            for k, v in fm.items():
                f = spec.get("fields", {}).get(k)
                if f is None:
                    out.append(("error", "schema", "front matter field %r is not in the schema for %s" % (k, typ)))
                elif f.get("values") and v and not ANY_BRACES_RE.search(v) and v not in f["values"]:
                    out.append(("error", "schema", "%s: %r is not one of %s" % (k, v, ", ".join(f["values"]))))
            for k, f in spec.get("fields", {}).items():
                if f.get("required") and k not in fm:
                    out.append(("error", "schema", "required field %r is missing" % k))
    else:
        out.append(("warn", "schema", "no schema given (--schema FILE): front matter fields not checked"))
    return out


def plan_templates(src, dest):
    """[(name, action, detail, text_or_None)] and the manifest to write. A file whose sha256 differs
    from the manifest's was changed by the owner (or is the owner's own file): CONFLICT, never written."""
    man_path = os.path.join(dest, TEMPLATE_MANIFEST)
    manifest = {"format": "pai-templates/1", "files": {}}
    if os.path.exists(man_path):
        try:
            with open(man_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except ValueError:
            raise PromptError("%s is not JSON: fix or delete it by hand (nothing was written)" % man_path)
    rows = []
    new_files = dict(manifest.get("files", {}))
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(src, fn), encoding="utf-8") as f:
            text = f.read()
        target = os.path.join(dest, fn)
        rec = manifest.get("files", {}).get(fn)
        if not os.path.exists(target):
            rows.append((fn, "create", "", text))
        else:
            with open(target, encoding="utf-8", errors="replace") as f:
                cur = f.read()
            if rec is None:
                rows.append((fn, "conflict", "a file of this name exists and PAI did not write it; left alone", None))
                continue
            if sha256(cur) != rec.get("sha256"):
                rows.append((fn, "conflict", "changed since PAI wrote it (yours now); left alone", None))
                continue
            if cur == text:
                rows.append((fn, "same", "", None))
                continue
            rows.append((fn, "update", "unchanged since PAI wrote it", text))
        new_files[fn] = {"sha256": sha256(text), "template": (template_frontmatter(text) or {}).get("template", "")}
    return rows, {"format": "pai-templates/1", "files": new_files}


def install_templates(src, dest, write=False, out=print):
    rows, manifest = plan_templates(src, dest)
    for fn, action, detail, _ in rows:
        out("%-8s %-18s %s" % (action.upper(), fn, detail))
    changes = [r for r in rows if r[3] is not None]
    if write and changes:
        for fn, _, _, text in changes:
            write_atomic(os.path.join(dest, fn), text)
        write_atomic(os.path.join(dest, TEMPLATE_MANIFEST), json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    out("%s: %d template(s) %s in %s; %d conflict(s)" % ("wrote" if write else "preview", len(changes),
        "written" if write else "would change", dest, sum(r[1] == "conflict" for r in rows)))
    return sum(r[1] == "conflict" for r in rows)


# ---------------------------------------------------------------- command line

def add_parser(sub):
    p = sub.add_parser("prompt", help="the prompt library: render, lint, diff, init, eval",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ps = p.add_subparsers(dest="pcmd", required=True)
    lib = "library folder (default: the project's copy given by --dest, else the packaged library)"
    ls = ps.add_parser("list", help="the library's files")
    ls.add_argument("--library", help=lib)
    r = ps.add_parser("render", help="fill placeholders; refuse when one is missing; print the sha256")
    r.add_argument("names", nargs="+", metavar="NAME", help="library names or paths, joined in this order")
    r.add_argument("--round", required=True, help="the round id ({{RUN_ID}})")
    r.add_argument("--lane", help="the lane name ({{LANE}}); not used with --split-lanes")
    r.add_argument("--split-lanes", metavar="DIR", help="treat NAME as an R6-style round prompt (```text blocks: Part 0, "
                   "then one block per lane starting \"线名：<lane>。\"): fill it, refuse leftovers, and write "
                   "DIR/<lane>.txt = Part 0 + that Part (what r6-prepare.sh did)")
    r.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    r.add_argument("--vars-file", help="JSON object of KEY: VALUE (--var wins)")
    r.add_argument("--library", help=lib)
    r.add_argument("--out", help="output file (default: <first name>-<round>-<lane>.md here)")
    r.add_argument("--force", action="store_true", help="replace an existing output file")
    li = ps.add_parser("lint", help="check prompts (default: every library file)")
    li.add_argument("names", nargs="*", metavar="NAME|PATH")
    li.add_argument("--library", help=lib)
    li.add_argument("--rendered", action="store_true", help="the files are rendered prompts (no markers needed)")
    li.add_argument("--kind", default="worker", choices=KINDS, help="with --rendered: the lint profile (default worker)")
    li.add_argument("--vars", default="", help="with --rendered: more placeholder names to treat as unfilled (comma list)")
    li.add_argument("--json", action="store_true")
    li.add_argument("--jev", action="store_true", help="also ask JEV whether each rule is unambiguous (network; "
                    "TYPESAFE_API_KEY; usage appended to $PAI_JEV_USAGE_LOG)")
    li.add_argument("--jev-below", type=float, default=0.45, help="list rules JEV scores below this (default 0.45: "
                    "Chinese rules score 0.07-0.15 lower than the same rule in English; docs/research/JEV-QUESTIONS.md)")
    d = ps.add_parser("diff", help="the packaged library against a project's copy")
    d.add_argument("names", nargs="*", metavar="NAME")
    d.add_argument("--dest", required=True, help="the project's prompt folder, e.g. <vault>/PAI/prompts")
    d.add_argument("--library", help="library folder (default: the packaged library)")
    i = ps.add_parser("init", help="install or upgrade the library into a project folder (preview unless --write)")
    i.add_argument("--dest", required=True, help="e.g. <vault>/PAI/prompts")
    i.add_argument("--library", help="library folder (default: the packaged library)")
    i.add_argument("--write", action="store_true")
    tp = ps.add_parser("templates", help="install or upgrade the Obsidian templates (preview unless --write)")
    tp.add_argument("--dest", required=True, help="e.g. <vault>/99 Templates/PAI")
    tp.add_argument("--write", action="store_true")
    li.add_argument("--templates", action="store_true", help="lint the Obsidian templates instead of the library")
    li.add_argument("--schema", help="pai-frontmatter-schema/1 file: template front matter must match it (core R6-COR-039)")
    from pai import prompteval, promptopt
    prompteval.add_parsers(ps)
    promptopt.add_parsers(ps)
    return p


def _library(a):
    return getattr(a, "library", None) or PACKAGED


LANE_LINE_RE = re.compile(r"^线名：([A-Za-z0-9_-]+)。")


def split_lanes(text, values):
    """{lane: text} from an R6-style round prompt, after filling `values`. Refuses (PromptError) on a leftover
    placeholder, no text block, or a lane block whose first line is not 线名：<lane>。 (r6-prepare.sh rules)."""
    filled, left = render_text(text, values)
    if left:
        raise PromptError("not rendered: no value for %s" % ", ".join(left))
    blocks, cur = [], None
    for line in filled.split("\n"):
        if re.match(r"^```text\s*$", line):
            cur = []
            continue
        if re.match(r"^```\s*$", line) and cur is not None:
            blocks.append("\n".join(cur) + "\n")
            cur = None
            continue
        if cur is not None:
            cur.append(line)
    if not blocks:
        raise PromptError("no ```text blocks found")
    if len(blocks) == 1:
        return {"all": blocks[0]}
    out = {}
    for n, b in enumerate(blocks[1:], 2):
        m = LANE_LINE_RE.match(b)
        if not m:
            raise PromptError("text block %d does not start with 线名：<lane>。" % n)
        out[m.group(1)] = blocks[0] + "\n" + b
    return out


def _render(a):
    lib = _library(a)
    prompts = [read(resolve(n, lib)) for n in a.names]
    values = {}
    if a.vars_file:
        try:
            with open(a.vars_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise PromptError("cannot read --vars-file %s: %s" % (a.vars_file, e))
        if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
            raise PromptError("--vars-file must be a JSON object of strings")
        values.update(data)
    values.update({"RUN_ID": a.round})
    if a.lane:
        values["LANE"] = a.lane
    values.update(parse_vars(a.var))
    if a.split_lanes:
        with open(resolve(a.names[0], lib), encoding="utf-8") as f:
            lanes = split_lanes(f.read(), values)
        os.makedirs(a.split_lanes, exist_ok=True)
        for lane, text in lanes.items():
            path = os.path.join(a.split_lanes, lane + ".txt")
            write_atomic(os.path.abspath(path), text)
            print("%s  %s  (%d lines)" % (sha256(text), path, text.count("\n")))
        return 0
    if not a.lane:
        raise PromptError("--lane is required (or use --split-lanes DIR)")
    text, used = render_prompts(prompts, values)
    out = a.out or "%s-%s-%s.md" % (prompts[0].name, a.round, a.lane)
    if os.path.exists(out) and not a.force:
        with open(out, encoding="utf-8", errors="replace") as f:
            if f.read() != text:
                raise PromptError("%s exists with other content: choose another --out or add --force" % out)
    write_atomic(os.path.abspath(out), text)
    print("%s  %s" % (sha256(text), out))
    print("rendered %s with %d placeholder(s): %s" % (" + ".join(p.name for p in prompts), len(used), ", ".join(used)))
    return 0


def _lint_templates(a):
    schema = load_schema(a.schema) if a.schema else None
    paths = a.names or [os.path.join(TEMPLATES, f) for f in sorted(os.listdir(TEMPLATES)) if f.endswith(".md")]
    errors = 0
    for path in paths:
        with open(path, encoding="utf-8") as f:
            found = lint_template(path, f.read(), schema)
        errors += sum(1 for x in found if x[0] == "error")
        print("%s  %s" % ("FAIL" if any(x[0] == "error" for x in found) else "ok  ", path))
        for lvl, code, msg in found:
            print("  %-5s %-14s %s" % (lvl, code, msg))
    print("%d template(s), %d error(s)" % (len(paths), errors))
    return 1 if errors else 0


def _lint(a):
    if a.templates:
        return _lint_templates(a)
    lib = _library(a)
    paths = [resolve(n, lib) for n in a.names] if a.names else list(library_files(lib).values())
    report, errors = {}, 0
    for path in paths:
        if a.rendered:
            with open(path, encoding="utf-8", errors="replace") as f:
                p = Prompt(path, f.read())
            p.problems = []
            p.attrs["kind"] = a.kind
        else:
            p = read(path)
        known = (library_vars(lib) | {v for v in a.vars.split(",") if v}) if a.rendered else None
        found = lint(p, rendered=a.rendered, known_vars=known)
        errors += sum(1 for f in found if f[0] == "error")
        report[path] = {"findings": [{"level": l, "code": c, "message": m} for l, c, m in found]}
        if a.jev:
            from pai import promptjev
            report[path]["jev"] = promptjev.score_rules(rules(p), below=a.jev_below)
    if a.json:
        print(json.dumps({"format": "pai-prompt-lint/1", "files": report, "errors": errors}, ensure_ascii=False, indent=2))
    else:
        for path, r in report.items():
            f = r["findings"]
            print("%s  %s" % ("FAIL" if any(x["level"] == "error" for x in f) else "ok  ", path))
            for x in f:
                print("  %-5s %-14s %s" % (x["level"], x["code"], x["message"]))
            j = r.get("jev")
            if j:
                print("  jev   %s" % j["summary"])
                for low in j["low"]:
                    print("    %.2f  %s  %s" % (low["score"], low["id"], low["text"][:120]))
        print("%d file(s), %d error(s)" % (len(report), errors))
    return 1 if errors else 0


def main(a):
    try:
        if a.pcmd == "list":
            for name, path in library_files(_library(a)).items():
                p = read(path)
                print("%-16s v%-3s %-15s vars=%s" % (name, p.rev, p.kind, ",".join(p.declared) or "-"))
            return 0
        if a.pcmd == "render":
            return _render(a)
        if a.pcmd == "lint":
            return _lint(a)
        if a.pcmd == "diff":
            print("\n".join(diff(_library(a), a.dest, a.names)))
            return 0
        if a.pcmd == "init":
            return 1 if install(_library(a), a.dest, write=a.write) else 0
        if a.pcmd == "templates":
            return 1 if install_templates(TEMPLATES, a.dest, write=a.write) else 0
        from pai import prompteval, promptopt
        if a.pcmd in prompteval.COMMANDS:
            return prompteval.main(a)
        if a.pcmd in promptopt.COMMANDS:
            return promptopt.main(a)
    except PromptError as e:
        sys.stderr.write("pai prompt %s: %s\n" % (a.pcmd, e))
        return 1 if "not rendered" in str(e) or "exists" in str(e) else 2
    return 2
