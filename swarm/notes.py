"""Obsidian task notes: Markdown files with YAML frontmatter. Standard library only.

Only the simple YAML that people type by hand is understood:
    key: value
    key: [a, b]
    key:
      - a
      - b
Edits change only the keys that pai-swarm owns, so your other frontmatter stays as you wrote it.
"""
import os
import re

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
KEY_RE = re.compile(r"^([A-Za-z0-9_\-]+):\s*(.*)$")


def _scalar(v):
    v = v.strip()
    if " #" in v and not v.startswith(("'", '"')):
        v = v.split(" #", 1)[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def parse(text):
    """Return (meta dict, body str)."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    meta, key = {}, None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and line.strip().startswith("- ") and key:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(_scalar(line.strip()[2:]))
            continue
        km = KEY_RE.match(line)
        if not km:
            continue
        key, val = km.group(1), km.group(2).strip()
        if " #" in val and not val.startswith(("'", '"')):
            val = val.split(" #", 1)[0].strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            meta[key] = [_scalar(x) for x in inner.split(",")] if inner else []
        elif val == "":
            meta[key] = None
        else:
            meta[key] = _scalar(val)
    return meta, text[m.end():]


def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{},]|^\s|\s$", s):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def set_meta(text, updates):
    """Return text with frontmatter keys set (None deletes). Other lines untouched."""
    m = FM_RE.match(text)
    lines = m.group(1).splitlines() if m else []
    body = text[m.end():] if m else text
    out, i, done = [], 0, set()
    while i < len(lines):
        line = lines[i]
        km = KEY_RE.match(line)
        if km and km.group(1) in updates:
            k = km.group(1)
            i += 1
            while i < len(lines) and lines[i].startswith((" ", "\t")):
                i += 1  # drop old block-list items
            if updates[k] is not None:
                out.append("%s: %s" % (k, _fmt(updates[k])))
            done.add(k)
            continue
        out.append(line)
        i += 1
    for k, v in updates.items():
        if k not in done and v is not None:
            out.append("%s: %s" % (k, _fmt(v)))
    return "---\n" + "\n".join(out) + "\n---\n" + body


class Task:
    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.reload()

    def reload(self):
        with open(self.path, encoding="utf-8") as f:
            self.text = f.read()
        self.meta, self.body = parse(self.text)

    @property
    def status(self):
        return str(self.meta.get("status") or "todo").lower()

    @property
    def paths(self):
        p = self.meta.get("paths")
        if p is None:
            return []
        if isinstance(p, str):
            p = [p]
        return [str(x).strip().strip("/") for x in p if str(x).strip()]

    @property
    def priority(self):
        try:
            return int(self.meta.get("priority") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def title(self):
        for line in self.body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return self.name

    def update(self, meta=None, append=None):
        """Re-read from disk (you may have edited it in Obsidian meanwhile), then write."""
        self.reload()
        text = self.text
        if meta:
            text = set_meta(text, meta)
        if append:
            text = text.rstrip("\n") + "\n\n" + append.strip("\n") + "\n"
        tmp = self.path + ".pai-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, self.path)
        self.reload()


def load_tasks(folder):
    tasks = []
    for fn in sorted(os.listdir(folder)):
        if fn.endswith(".md") and not fn.startswith(("_", ".")):
            tasks.append(Task(os.path.join(folder, fn)))
    return tasks
