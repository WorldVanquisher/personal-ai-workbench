"""A small, inert Markdown renderer for the experimental PAI Markdown server.

GitHub-style subset: front matter (shown as text), ATX headings, paragraphs, emphasis,
strong, strikethrough, code spans, fenced code, block quotes, ordered and bullet lists,
task lists (disabled checkboxes), tables, rules and links.

Safety by construction: every piece of source text is HTML-escaped, so raw HTML is shown,
never interpreted; links keep only http, https, mailto, in-page and relative targets;
images are never loaded (they become a labelled link); code is shown, never run.
Standard library only.
"""
import html
import re

SAFE_SCHEME = re.compile(r"^(?:https?:|mailto:)", re.I)
HAS_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def esc(s):
    return html.escape(s, quote=True)


def decode(raw):
    """Undo HTML entities completely (ours and any in the source), as a browser would."""
    prev, s = None, raw
    while s != prev:
        prev, s = s, html.unescape(s)
    return s


def safe_url(raw):
    """The decoded URL if it is safe to put in an href, else None. Checked after decoding
    every entity and removing control characters and whitespace, as a browser would."""
    url = decode(raw).strip()
    probe = re.sub(r"[\x00-\x20\x7f]+", "", url)
    if not probe or probe.startswith("//"):
        return None if probe.startswith("//") else url
    if HAS_SCHEME.match(probe) and not SAFE_SCHEME.match(probe):
        return None
    return url


def inline(text):
    """Escape, then apply inline Markdown to the escaped text."""
    codes = []

    def keep(m):
        codes.append("<code>%s</code>" % esc(m.group(2)))
        return "\x00%d\x00" % (len(codes) - 1)

    text = re.sub(r"(`+)(.+?)\1", keep, text)
    out = esc(text)

    def image(m):
        alt, url = m.group(1), m.group(2)
        u = safe_url(url)
        label = "image: %s" % (alt or "untitled")
        if u and SAFE_SCHEME.match(u):
            return '<a class="img-link" href="%s" rel="noopener noreferrer nofollow">[%s]</a>' % (esc(u), label)
        return '<span class="img-link">[%s, not loaded]</span>' % label

    def link(m):
        label, url = m.group(1), m.group(2)
        u = safe_url(url)
        if u is None:
            return '%s <span class="removed">(link removed: unsafe target)</span>' % label
        return '<a href="%s" rel="noopener noreferrer nofollow">%s</a>' % (esc(u), label)

    out = re.sub(r"!\[([^\]]*)\]\(([^)\s]*)(?:\s+&quot;[^&]*&quot;)?\)", image, out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]*)(?:\s+&quot;[^&]*&quot;)?\)", link, out)
    out = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: "<strong>%s</strong>" % (m.group(1) or m.group(2)), out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])",
                 lambda m: "<em>%s</em>" % (m.group(1) or m.group(2)), out)
    out = re.sub(r"~~(.+?)~~", r"<del>\1</del>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], out)


def _table(lines):
    def cells(line):
        line = line.strip()
        line = line[1:] if line.startswith("|") else line
        line = line[:-1] if line.endswith("|") else line
        return [c.strip() for c in re.split(r"(?<!\\)\|", line)]
    head, rows = cells(lines[0]), [cells(l) for l in lines[2:]]
    out = ["<table><thead><tr>"] + ["<th>%s</th>" % inline(c) for c in head] + ["</tr></thead><tbody>"]
    for r in rows:
        out.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in r) + "</tr>")
    return "".join(out + ["</tbody></table>"])


TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")


def render(text):
    """Markdown text -> inert HTML."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i = [], 0
    if lines and lines[0].strip() == "---":
        end = next((j for j in range(1, len(lines)) if lines[j].strip() in ("---", "...")), None)
        if end:
            out.append('<pre class="front-matter">%s</pre>' % esc("\n".join(lines[1:end])))
            i = end + 1
    para = []

    def flush():
        if para:
            out.append("<p>%s</p>" % "<br>".join(inline(p) for p in para))
            para.clear()

    while i < len(lines):
        line = lines[i]
        fence = re.match(r"^\s*(```+|~~~+)\s*([\w+-]*)", line)
        if fence:
            flush()
            mark, lang, body = fence.group(1), fence.group(2), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(mark):
                body.append(lines[i])
                i += 1
            out.append('<pre class="code"%s><code>%s</code></pre>' % (
                ' data-lang="%s"' % esc(lang) if lang else "", esc("\n".join(body))))
            i += 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if h:
            flush()
            out.append("<h%d>%s</h%d>" % (len(h.group(1)), inline(h.group(2)), len(h.group(1))))
            i += 1
            continue
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            flush()
            block = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue
        if line.lstrip().startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>" % render("\n".join(quote)))
            continue
        lm = LIST_RE.match(line)
        if lm:
            flush()
            ordered = lm.group(2)[0].isdigit()
            items = []
            while i < len(lines) and LIST_RE.match(lines[i]):
                body = LIST_RE.match(lines[i]).group(3)
                task = re.match(r"^\[([ xX])\]\s+(.*)$", body)
                if task:
                    items.append('<li class="task"><input type="checkbox" disabled%s> %s</li>' % (
                        " checked" if task.group(1) in "xX" else "", inline(task.group(2))))
                else:
                    items.append("<li>%s</li>" % inline(body))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join(items), tag))
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        para.append(line.strip())
        i += 1
    flush()
    return "\n".join(out)
