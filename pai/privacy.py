"""Text that must not leave this machine by default: agent session links and credentials.

`scrub(text)` returns the text with each finding replaced by a short marker, and the list of
what it removed (line, kind, a masked sample), so a caller can show the removals before it
writes anything. Ordinary prose about tools, people's names and public URLs is left alone.
"""
import re

REMOVED = "<removed: %s>"
SESSION = [
    ("Claude session link", re.compile(
        r"(?:https?://)?(?:www\.)?claude\.ai/(?:code/)?(?:session[_/-]|chat/|share/)[\w-]+[^\s)>\]]*", re.I)),
    ("Claude session id", re.compile(r"\bsession_01[A-Za-z0-9]{16,}\b")),
    ("ChatGPT or Codex share link", re.compile(r"(?:https?://)?chatgpt\.com/(?:share|c|codex/tasks?)/[\w-]+", re.I)),
]
CREDENTIALS = [
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("API key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("password in a URL", re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("secret assignment", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
                                     r"\s*[:=]\s*['\"]?[^\s'\"]{6,}")),
]
RULES = SESSION + CREDENTIALS


def masked(s):
    return s[:4] + "..." + "(%d chars)" % len(s)


def findings(text, rules=RULES):
    """[(line number, kind, matched text)] in reading order."""
    found = []
    for n, line in enumerate((text or "").splitlines(), 1):
        for kind, rx in rules:
            for m in rx.finditer(line):
                found.append((n, kind, m.group(0)))
    return found


def scrub(text, rules=RULES):
    """(clean text, removals): every finding replaced by <removed: KIND>; removals are
    [{'line', 'kind', 'sample'}] with a masked sample, never the secret itself."""
    removals, out = [], []
    for n, line in enumerate((text or "").split("\n"), 1):
        for kind, rx in rules:
            def sub(m, kind=kind, n=n):
                removals.append({"line": n, "kind": kind, "sample": masked(m.group(0))})
                return REMOVED % kind
            line = rx.sub(sub, line)
        out.append(line)
    return "\n".join(out), removals
