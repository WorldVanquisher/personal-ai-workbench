"""Minimal Markdown handling: front matter and heading-delimited sections."""

import re

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def front_matter(text):
    """(dict, body) for a leading '---' block of simple 'key: value' lines."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = value.strip()
    rest = text[end + 4:]
    return meta, rest[1:] if rest.startswith("\n") else rest


def sections(text):
    """[(heading_path, level, body)] in document order. Text before the first heading is '(preamble)'.

    Headings inside fenced code blocks are not headings. Each section body excludes
    its subsections, which are separate entries with the full heading path.
    """
    out, stack, body, in_fence = [], [], [], False
    current = ("(preamble)", 0)

    def flush():
        content = "\n".join(body).strip()
        if content:
            out.append((current[0], current[1], content))

    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(line)
        if m:
            flush()
            body = []
            level, title = len(m.group(1)), m.group(2)
            stack[:] = [s for s in stack if s[0] < level] + [(level, title)]
            current = (" > ".join(t for _, t in stack), level)
        else:
            body.append(line)
    flush()
    return out
