#!/usr/bin/env python3
"""Check the user manuals against this checkout: every `pai` command they show exists, every
--flag it uses is one that command accepts, and every relative link and #anchor resolves.

    python3 -B tools/doc_check.py [FILE.md ...] [--root DIR] [--json]

Without FILE arguments it checks the manuals in MANUALS below (those that exist).

Commands: only text inside code (``` blocks and `inline` spans) is read, so prose such as
"pai never answers" is not a command. A command is `pai` at the start of a line or code
span (after an optional `$ ` prompt), or after `&&`, `||`, `;`, `|`, `(` or `$(`, followed by
words; so a quoted output line such as `… -> pai init` is not read as one. doc_check runs
`bin/pai.py WORDS --help` for the longest prefix of those words whose usage line names it
(for example `pai queue init`, then `pai pr` for `pai pr plan a`) and checks each --flag
against that help text. `--help` and `--version` are always accepted.

Commands that another lane adds and that are not merged yet are declared in the manual
itself, on a line of its own:

    <!-- doc_check: pending pai round, pai prompt -->

A flag that another lane adds to an existing command is declared as `pai CMD --flag` in the same
list. A missing pending command is reported as PENDING (not a failure); a pending command that now
exists is reported as a warning, so the marker gets removed.

Links: [text](target) with a relative target must name an existing file (relative to the
manual), and a #anchor must match a heading of that file, using GitHub's rules (lower case;
punctuation removed; spaces become hyphens; a repeated heading gets -1, -2, ...).
http(s) and mailto links are not fetched.

Translations: for every checked FILE.md that has a FILE.zh-CN.md beside it (both checked), the two
must correspond section by section: the same headings at the same levels, with the same
numbering ("6.", "2a.", "(b)"); and in each section the same number of code blocks and tables
(the commands and the reference tables must not drift; paragraphs may be split differently).

Front matter fields (--schema FILE): every field a manual names in a front-matter block shown in
code (``` … --- key: value … ---); in a manual marked `<!-- doc_check: fields -->`, also every
`key:` code span and every `field` code span in a table's first column; and every key of a template's own
front matter (pai/templates/*.md, pai/prompts/*.md), must be a field of the schema: core's pai-frontmatter-schema/1
(docs/frontmatter.schema.json: every kind's fields, match, required, owner_fields and optional keys)
or {"fields": {"<kind>": ["field", ...]}}; the union of all kinds is allowed. Without --schema
nothing is checked and doc_check says so. The vault schema itself is the core lane's (R6-COR-039).

Developer paths: a line quoting a real home or server folder (/home/NAME/, /Users/NAME/,
/srv/data/, C:\\Users\\) fails, as tools/build_bundle.py would refuse the bundle for it.

Exit: 0 nothing wrong, 1 a broken command, flag, link, anchor or translation mismatch, 2 usage. Standard library
only; it runs only this checkout's own `bin/pai.py --help` and writes nothing.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUALS = (
    "README.md", "README.zh-CN.md", "QUICKSTART.md", "QUICKSTART.zh-CN.md",
    "docs/README.md", "docs/FIRST-USE.md", "docs/FIRST-USE.zh-CN.md", "docs/FIRST-USE-PLATFORMS.md",
    "docs/REFRESH.md", "docs/REFRESH.zh-CN.md", "docs/GLOSSARY.md", "docs/GLOSSARY.zh-CN.md",
    "docs/WHY-PAI.md", "docs/WHY-PAI.zh-CN.md", "docs/FAQ.md", "docs/FAQ.zh-CN.md",
    "docs/OBSIDIAN.md", "docs/OBSIDIAN.zh-CN.md", "docs/AGENTS-LAYERS.md", "docs/AGENTS-LAYERS.zh-CN.md", "docs/research/FLH-AGENTS-ANALYSIS.md", "examples/two-lane-round/README.md",
    "docs/0.5.0/RELEASE-NOTES-DRAFT.md", "docs/0.5.0/RELEASE-NOTES-DRAFT.zh-CN.md",
)
ALWAYS_OK = {"--help", "--version"}
FENCE = re.compile(r"^(```|~~~)")
INLINE = re.compile(r"`([^`\n]+)`")
PAI_CMD = re.compile(r"(?:^|&&|\|\||[;|(]|\$\(|\bthen\b|\bdo\b)\s*(?:\$\s+)?pai((?:[ \t]+[^\s|;&#`)]+)*)")
WORD = re.compile(r"^[a-z][a-z0-9-]*$")
LINK = re.compile(r"(?<!\!)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
PENDING = re.compile(r"<!--\s*doc_check:\s*pending\s+(.+?)\s*-->")
# The same rule as tools/build_bundle.py: a bundled manual must not quote a machine's real paths.
DEV_PATH = re.compile(r"/home/[A-Za-z0-9_.-]+/|/Users/[A-Za-z0-9_.-]+/|/srv/data/|[A-Za-z]:\\Users\\")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def slug(text):
    """GitHub's heading anchor: lower case, inline markup and punctuation removed, spaces to hyphens."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.strip().lower()
    text = "".join(ch for ch in text if ch.isalnum() or ch in " -_")
    return text.replace(" ", "-")


def anchors(path):
    """Every anchor a Markdown file defines through its headings (outside code fences)."""
    seen, out = {}, set()
    in_code = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            if FENCE.match(line.strip()):
                in_code = not in_code
                continue
            m = None if in_code else HEADING.match(line)
            if not m:
                continue
            base = slug(m.group(2))
            n = seen.get(base, 0)
            seen[base] = n + 1
            out.add(base if n == 0 else "%s-%d" % (base, n))
    return out


def code_segments(text):
    """(line number, code text) for every fenced line and inline code span."""
    in_code = False
    for no, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line.strip()):
            in_code = not in_code
            continue
        if in_code:
            yield no, line
        else:
            for m in INLINE.finditer(line):
                yield no, m.group(1)


def commands(text):
    """(line, command text, words, flags) for every `pai …` in code."""
    for no, seg in code_segments(text):
        seg = seg.split(" #", 1)[0]
        for m in PAI_CMD.finditer(seg):
            rest = m.group(1).strip()
            try:
                toks = shlex.split(rest, comments=False)
            except ValueError:
                toks = rest.split()
            words = []
            for t in toks:
                if not WORD.match(t):
                    break
                words.append(t)
            flags = sorted({t.split("=", 1)[0] for t in toks if re.match(r"^--[a-z][a-z0-9-]*(=.*)?$", t)})
            yield no, ("pai " + rest).strip(), words, flags


class Pai:
    """Runs `bin/pai.py WORDS --help` once per prefix."""

    def __init__(self, root):
        self.root, self.cache = root, {}

    def help(self, words):
        key = tuple(words)
        if key not in self.cache:
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", COLUMNS="200")
            try:
                p = subprocess.run([sys.executable, "-B", os.path.join(self.root, "bin", "pai.py")] + list(words)
                                   + ["--help"], capture_output=True, text=True, timeout=60, env=env,
                                   stdin=subprocess.DEVNULL)
                self.cache[key] = (p.returncode, p.stdout + p.stderr)
            except (OSError, subprocess.TimeoutExpired) as e:
                self.cache[key] = (None, str(e))
        return self.cache[key]

    def resolve(self, words):
        """(prefix, help text) of the longest prefix pai knows as a command, or (None, text)."""
        last = ""
        for d in range(min(len(words), 3), 0, -1):
            rc, out = self.help(words[:d])
            last = out
            usage = re.search(r"usage:\s*(?:pai|[\w./-]*pai(?:\.py)?)\s+(.*)", out)
            if rc == 0 and usage and usage.group(1).split()[:d] == list(words[:d]):
                return words[:d], out
        return None, last


def has_flag(help_text, flag):
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(flag), help_text) is not None


def check_file(path, root, pai):
    rel = os.path.relpath(path, root)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pending, pending_flags = set(), set()
    for m in PENDING.finditer(text):
        for c in (c.strip() for c in m.group(1).split(",")):
            if " --" in c:  # `pai worktree init --target`: only that flag of an existing command is pending
                cmd, _, flag = c.partition(" --")
                pending_flags.add((cmd.strip(), "--" + flag.strip()))
            elif c:
                pending.add(c)
    problems, notes = [], []
    for no, line in enumerate(text.splitlines(), 1):
        m = DEV_PATH.search(line)
        if m:
            problems.append("%s:%d: absolute developer path %r (build_bundle refuses it; use a placeholder)"
                            % (rel, no, m.group(0)))

    for no, cmd, words, flags in commands(text):
        if not words:
            bad = [f for f in flags if f not in ALWAYS_OK]
            if bad:
                rc, out = pai.help([])
                for f in bad:
                    if f not in out:
                        problems.append("%s:%d: `pai` has no option %s (in `%s`)" % (rel, no, f, cmd))
            continue
        pend = next((p for p in sorted(pending, key=len, reverse=True)
                     if ("pai " + " ".join(words) + " ").startswith(p + " ")), None)
        prefix, out = pai.resolve(words)
        if pend and (prefix is None or len(prefix) < len(pend.split()) - 1):
            # a pending subcommand of a command that exists (`pai handoff approve` while only `pai handoff` does)
            notes.append("PENDING %s:%d: `%s` (declared pending: %s)" % (rel, no, cmd, pend))
            continue
        if prefix is None:
            if pend:
                notes.append("PENDING %s:%d: `%s` (declared pending: %s)" % (rel, no, cmd, pend))
            else:
                problems.append("%s:%d: unknown command `pai %s` (in `%s`)" % (rel, no, " ".join(words), cmd))
            continue
        if pend:
            notes.append("WARNING %s:%d: `%s` is declared pending but exists now; remove it from the marker"
                         % (rel, no, pend))
        for f in flags:
            if f in ALWAYS_OK:
                continue
            pf = next((c for c, fl in pending_flags if fl == f and ("pai " + " ".join(words) + " ").startswith(c + " ")),
                      None)
            exists = has_flag(out, f)
            if not exists:
                # some flags change routing (`pai doctor --agents` is PAI's own doctor): ask with the flag present
                rc, out2 = pai.help(list(prefix) + [f])
                if not (rc == 0 and has_flag(out2, f)) and len(prefix) == 1:
                    # a positional also changes routing (`pai init PROJECT --vault …` is PAI's own init)
                    rc, out2 = pai.help(list(prefix) + ["PROJECT", f])
                    # argparse may stop with a usage error here (the flag took "--help" as its value); the
                    # usage it prints still lists the real options, and the error part is ignored
                    if rc == 2 and has_flag(out2.split("error:", 1)[0], f):
                        rc = 0
                    if not (rc == 0 and has_flag(out2, f)) and len(flags) > 1:
                        # the other flags route too (`pai init P … --vault V --write`: --write alone goes to the
                        # workbench init, with --vault to PAI's own): ask with the command's flags as written
                        rc, out2 = pai.help(list(prefix) + ["PROJECT"] + [g for g in flags if g != f] + [f])
                        if rc == 2 and has_flag(out2.split("error:", 1)[0], f):
                            rc = 0
                exists = rc == 0 and has_flag(out2, f)
            if pf:
                tail = "%s %s" % (pf, f)
                if exists:
                    kind, msg = "WARNING", ("WARNING %s:%d: `%s` is declared pending but exists now; remove it from "
                                            "the marker" % (rel, no, tail))
                else:
                    kind, msg = "PENDING", "PENDING %s:%d: `%s` (declared pending: %s)" % (rel, no, cmd, tail)
                if not any(n.startswith(kind) and tail in n for n in notes):
                    notes.append(msg)
                continue
            if not exists:
                problems.append("%s:%d: `pai %s` has no option %s (in `%s`)" % (rel, no, " ".join(prefix), f, cmd))

    in_code = False
    for no, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line.strip()):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in LINK.finditer(INLINE.sub("", line)):
            target = m.group(2)
            if re.match(r"^(https?:|mailto:)", target):
                continue
            file_part, _, anchor = target.partition("#")
            dest = os.path.normpath(os.path.join(os.path.dirname(path), file_part)) if file_part else path
            if not os.path.exists(dest):
                problems.append("%s:%d: broken link %s (no file %s)" % (rel, no, target, os.path.relpath(dest, root)))
                continue
            if anchor and os.path.isfile(dest) and dest.endswith(".md") and anchor not in anchors(dest):
                problems.append("%s:%d: broken anchor %s (no such heading in %s)"
                                % (rel, no, target, os.path.relpath(dest, root)))
    return problems, notes


NUMBERING = re.compile(r"^(\(?[0-9a-z]{1,3}[.)])\s")


def outline(path):
    """[(level, numbering or '', code blocks, tables)] per section, the text before the first ## included."""
    out = [[1, "", 0, 0]]
    in_code, in_table = False, False
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if FENCE.match(s):
                if not in_code:
                    out[-1][2] += 1
                in_code = not in_code
                continue
            if in_code:
                continue
            m = HEADING.match(line)
            if m and len(m.group(1)) >= 2:
                num = NUMBERING.match(m.group(2) + " ")
                out.append([len(m.group(1)), num.group(1) if num else "", 0, 0])
                in_table = False
                continue
            if s.startswith("|") and not in_table:
                out[-1][3] += 1
            in_table = s.startswith("|")
    return [tuple(x) for x in out]


def parity(en, zh, root):
    """Problems where FILE.zh-CN.md does not follow FILE.md section by section."""
    a, b = outline(en), outline(zh)
    rel = os.path.relpath(zh, root)
    if [(x[0], x[1]) for x in a] != [(x[0], x[1]) for x in b]:
        return ["%s: headings differ from %s: %s vs %s" % (
            rel, os.path.basename(en), [("#" * x[0]) + " " + x[1] for x in a], [("#" * x[0]) + " " + x[1] for x in b])]
    problems = []
    for i, (x, y) in enumerate(zip(a, b)):
        name = "section %d (%s %s)" % (i, "#" * x[0], x[1] or "-") if i else "the opening"
        if x[2] != y[2]:
            problems.append("%s: %s has %d code block(s), %s has %d" % (rel, name, y[2], os.path.basename(en), x[2]))
        if x[3] != y[3]:
            problems.append("%s: %s has %d table(s), %s has %d" % (rel, name, y[3], os.path.basename(en), x[3]))
    return problems


FIELD_SPAN = re.compile(r"`([a-z][a-z0-9_]*):(?:[^`]*)`")
FIELDS_ON = re.compile(r"<!--\s*doc_check:\s*fields\s*-->")
TABLE_FIRST = re.compile(r"^\|\s*([^|]*?)\s*\|")
FM_KEY = re.compile(r"^([a-z][a-z0-9_]*):(?:\s|$)")
TEMPLATES = ("pai/templates", "pai/prompts")


def named_fields(path):
    """{field: first line} named by a Markdown file: keys of front-matter blocks shown in code, and
    `key:` code spans in prose."""
    found = {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    lines = text.splitlines()
    spans = FIELDS_ON.search(text) is not None
    in_code, in_fm = False, False
    for no, line in enumerate(lines, 1):
        s = line.strip()
        if FENCE.match(s):
            in_code, in_fm = not in_code, False
            continue
        if in_code:
            if s == "---":
                in_fm = not in_fm
            elif in_fm and FM_KEY.match(s):
                found.setdefault(FM_KEY.match(s).group(1), no)
            continue
        if not spans:
            continue
        for m in FIELD_SPAN.finditer(line):
            found.setdefault(m.group(1), no)
        cell = TABLE_FIRST.match(line)
        if cell:
            for name in re.findall(r"`([a-z][a-z0-9_]*)`", cell.group(1)):
                found.setdefault(name, no)
    return found


def template_fields(root):
    """{(file, field): line} for the front matter of every template file."""
    out = {}
    for d in TEMPLATES:
        base = os.path.join(root, d)
        for name in sorted(os.listdir(base)) if os.path.isdir(base) else ():
            path = os.path.join(base, name)
            if not name.endswith(".md") or not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            if not lines or lines[0].strip() != "---":
                continue
            for no, line in enumerate(lines[1:], 2):
                if line.strip() == "---":
                    break
                m = FM_KEY.match(line.strip())
                if m:
                    out[(os.path.relpath(path, root), m.group(1))] = no
    return out


def schema_fields(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("kinds"), dict):  # core's pai-frontmatter-schema/1
        out = set()
        for kind in data["kinds"].values():
            if not isinstance(kind, dict):
                raise ValueError("a kind of pai-frontmatter-schema/1 is not an object")
            out |= set(kind.get("fields") or {}) | set(kind.get("match") or {})
            for key in ("required", "owner_fields", "optional"):
                out |= {x for x in kind.get(key) or [] if isinstance(x, str)}
        return out
    fields = data.get("fields") if isinstance(data, dict) else None
    if not isinstance(fields, dict) or not all(isinstance(v, list) for v in fields.values()):
        raise ValueError('expected {"kinds": {...}} (pai-frontmatter-schema/1) or {"fields": {"<kind>": ["field", ...]}}')
    return {x for v in fields.values() for x in v if isinstance(x, str)}


def check_fields(files, root, schema):
    allowed = schema_fields(schema)
    problems = []
    for f in files:
        for name, no in sorted(named_fields(f).items(), key=lambda x: x[1]):
            if name not in allowed:
                problems.append("%s:%d: front matter field `%s` is not in the schema %s"
                                % (os.path.relpath(f, root), no, name, os.path.basename(schema)))
    for (rel, name), no in sorted(template_fields(root).items()):
        if name not in allowed:
            problems.append("%s:%d: template front matter field `%s` is not in the schema %s"
                            % (rel, no, name, os.path.basename(schema)))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", metavar="FILE.md")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--schema", metavar="FILE", help="front matter schema (JSON) to check field names against")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    files = [os.path.abspath(f) for f in a.files] or [os.path.join(root, f) for f in MANUALS
                                                       if os.path.isfile(os.path.join(root, f))]
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        print("doc_check: no such file: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    pai = Pai(root)
    problems, notes = [], []
    for f in files:
        p, n = check_file(f, root, pai)
        problems += p
        notes += n
    for f in files:
        zh = f[:-3] + ".zh-CN.md"
        if f.endswith(".md") and not f.endswith(".zh-CN.md") and zh in files:
            problems += parity(f, zh, root)
    if a.schema:
        try:
            problems += check_fields(files, root, os.path.abspath(a.schema))
        except (OSError, ValueError) as e:
            print("doc_check: cannot read --schema %s: %s" % (a.schema, e), file=sys.stderr)
            return 2
    else:
        notes.append("front matter fields: not checked (no --schema; the vault schema is the core lane's R6-COR-039)")
    if a.json:
        print(json.dumps({"files": [os.path.relpath(f, root) for f in files], "problems": problems, "notes": notes},
                         indent=2, ensure_ascii=False))
    else:
        for line in notes + problems:
            print(line)
        print("doc_check: %d file(s), %d command help(s) read, %s" % (
            len(files), len(pai.cache), "OK" if not problems else "%d PROBLEM(S)" % len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
