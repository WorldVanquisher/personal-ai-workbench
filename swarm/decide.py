"""pai decide: read and answer a decision sheet (Markdown), approving exactly what was read.

    pai decide list FILE.md [--open] [--json]
    pai decide answer FILE.md ID --sha SHA12 --choice TEXT [--note TEXT] [--write]
    pai decide answer --from-note PAI/decisions/<ROUND>-<ID>.md [--write]   (the owner filled `choice:` in Obsidian)

A decision sheet is any Markdown file whose decisions are checkbox items with a bold ID:

    - [ ] **D4 本轮范围**：用精简版替换 60 条的 R6。
      - 选项：A 精简版；B 60 条
      - 推荐：A
      - 默认：没有回答时按推荐执行
      - 阻塞：R6-RND-001

`list` prints every decision with the sha256 (first 12 hex digits) of its block: the checkbox
line and its indented lines. `answer` needs that sha: if the block changed since it was listed
(a mastermind rewrote the options, someone else answered), nothing is written and the new sha
is shown, so an approval always refers to the text the person saw. Answering ticks the box and
adds one indented line `- 回答：CHOICE（by pai decide, UTC time）`; nothing else in the file
changes. Preview unless --write; the write is atomic and refused if the file changed while
answering.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

ITEM_RE = re.compile(r"^(?P<indent>\s*)[-*]\s+\[(?P<mark>[ xX])\]\s+\*\*(?P<id>[A-Za-z][A-Za-z0-9-]*\d+)\b\s*(?P<title>[^*]*)\*\*\s*[:：]?\s*(?P<text>.*)$")
FIELD_RE = re.compile(r"^\s+[-*]\s+(?P<k>[^：:]{1,12})[：:]\s*(?P<v>.*)$")
FIELDS = {"选项": "options", "options": "options", "推荐": "recommendation", "recommendation": "recommendation",
          "默认": "default", "默认做法": "default", "default": "default", "阻塞": "blocks", "blocks": "blocks",
          "取舍": "tradeoffs", "tradeoffs": "tradeoffs", "回答": "answer", "answer": "answer", "问题": "question"}


class DecideError(Exception):
    pass


def block_sha(lines):
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def parse(text):
    """[{id, title, text, answered, line, end, sha, fields}] in file order."""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        m = ITEM_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group("indent"))
        j = i + 1
        while j < len(lines) and lines[j].strip() and (len(lines[j]) - len(lines[j].lstrip())) > indent:
            j += 1
        block = lines[i:j]
        fields = {}
        for l in block[1:]:
            f = FIELD_RE.match(l)
            if f and f.group("k").strip().lower() in FIELDS:
                fields[FIELDS[f.group("k").strip().lower()]] = f.group("v").strip()
        out.append({"id": m.group("id"), "title": m.group("title").strip(), "text": m.group("text").strip(),
                    "answered": m.group("mark") != " ", "line": i + 1, "end": j, "sha": block_sha(block),
                    "fields": fields})
        i = j
    return out


def read(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise DecideError("cannot read %s: %s" % (path, e))
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError:
        raise DecideError("%s is not UTF-8 text" % path)


def open_items(path):
    """Unanswered decisions of a sheet; [] when the file does not exist."""
    if not os.path.isfile(path):
        return []
    return [d for d in parse(read(path)[1]) if not d["answered"]]


def answer(path, did, sha, choice, note=None, write=False, at=None):
    """(new text, decision). Raises DecideError (nothing written) on any mismatch."""
    if not choice or "\n" in choice or (note and "\n" in note):
        raise DecideError("--choice (and --note) must be one non-empty line")
    if not re.fullmatch(r"[0-9a-f]{12,64}", sha or ""):
        raise DecideError("--sha needs the 12+ hex digits `pai decide list` showed for %s" % did)
    data, text = read(path)
    items = [d for d in parse(text) if d["id"] == did]
    if not items:
        raise DecideError("%s has no decision %s" % (path, did))
    if len(items) > 1:
        raise DecideError("%s has %d decisions with id %s; make the ids unique first" % (path, len(items), did))
    d = items[0]
    if not d["sha"].startswith(sha):
        raise DecideError("MISMATCH: %s changed since you read it (now %s, you approved %s). Read it again with "
                          "pai decide list; nothing was written" % (did, d["sha"][:12], sha[:12]))
    if d["answered"]:
        raise DecideError("REFUSED: %s is already answered%s" % (did, " (%s)" % d["fields"]["answer"]
                                                                  if d["fields"].get("answer") else ""))
    lines = text.split("\n")
    first = lines[d["line"] - 1]
    lines[d["line"] - 1] = re.sub(r"\[ \]", "[x]", first, count=1)
    indent = re.match(r"^\s*", first).group(0) + "  "
    stamp = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines.insert(d["end"], "%s- 回答：%s（by pai decide, %s%s）" % (indent, choice, stamp, "；" + note if note else ""))
    new = "\n".join(lines)
    if write:
        _, now = read(path)
        if now != text:
            raise DecideError("MISMATCH: %s changed while answering; nothing was written" % path)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    return new, d


def render_list(items):
    out = []
    for d in items:
        out.append("%s %-6s sha %s  %s%s" % ("[x]" if d["answered"] else "[ ]", d["id"], d["sha"][:12], d["title"],
                                             "：" + d["text"] if d["text"] else ""))
        for k in ("options", "recommendation", "default", "blocks", "answer"):
            if d["fields"].get(k):
                out.append("             %s: %s" % (k, d["fields"][k]))
    return "\n".join(out) or "(no decisions)"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pai decide", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("file")
    ls.add_argument("--open", action="store_true", help="only unanswered decisions")
    ls.add_argument("--json", action="store_true")
    an = sub.add_parser("answer", help="FILE ID --sha SHA --choice TEXT, or --note PATH (a PAI decision note in the "
                        "vault whose `choice:` the owner filled)")
    an.add_argument("file", nargs="?")
    an.add_argument("id", nargs="?")
    an.add_argument("--sha", help="the sha `pai decide list` showed (12+ hex digits)")
    an.add_argument("--choice")
    an.add_argument("--from-note", "--decision-note", dest="from_note", metavar="PATH",
                    help="answer from PAI/decisions/<ROUND>-<ID>.md: its choice, checked against the sha it was made from")
    an.add_argument("--note")
    an.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            items = parse(read(a.file)[1])
            if a.open:
                items = [d for d in items if not d["answered"]]
            print(json.dumps(items, indent=1, ensure_ascii=False) if a.json else render_list(items))
            return 0
        if a.from_note:
            if a.file or a.id or a.sha or a.choice:
                raise DecideError("--from-note takes the choice and sha from the note; give no FILE, ID, --sha or --choice")
            import round_vault
            round_vault.answer_from_note(a.from_note, a.write)
            return 0
        if not (a.file and a.id and a.sha and a.choice):
            raise DecideError("answer needs FILE ID --sha SHA --choice TEXT, or --from-note PATH")
        new, d = answer(a.file, a.id, a.sha, a.choice, a.note, a.write)
        added = new.split("\n")[d["end"]]
        print("%s %s: [x] and\n%s" % ("answered" if a.write else "preview (add --write):", d["id"], added))
        return 0
    except DecideError as e:
        print("pai decide: %s" % e, file=sys.stderr)
        return 1 if str(e).startswith(("MISMATCH", "REFUSED")) else 2


if __name__ == "__main__":
    sys.exit(main())
