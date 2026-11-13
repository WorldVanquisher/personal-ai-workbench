#!/usr/bin/env python3
"""Publication check: refuse AI attribution in what is about to become public history.

    publish_check.py --repo PATH --range BASE..HEAD [--range ...] [--commit SHA ...]
                     [--pr-title TEXT ...] [--pr-body-file FILE ...]
                     [--squash-message-file FILE|-] [--allow-ai-attribution] [--json]

It reads the commits themselves (`git cat-file commit`, so no log format, mailmap, notes or
replace ref stands between you and the bytes that would be pushed), the exact PR body
file, the PR title and, when given, the squash message. It writes nothing and never talks
to a remote.

Rejected by default, case-insensitively:
  * `Co-authored-by:` trailers that name Claude or Anthropic (an anthropic.com address, a
    Claude bot address, or a name such as "Claude", "Claude Code", "Claude Opus 5.5");
  * commits whose author or committer is such an identity;
  * Claude session links (claude.ai/code/session_..., chat and share links) and
    `Claude-Session:` trailers;
  * the "Generated with Claude Code" footer line.
Left alone: human co-authors (including people called Claude) and ordinary sentences
about having used AI.

Findings name the commit or field, the line and the rule. Session links are never
printed. `--allow-ai-attribution` is the only opt-in: it applies to this invocation, the
findings are still listed, and no environment variable, setting or manifest field can
stand in for it.

Exit: 0 nothing found (or allowed by the opt-in); 1 rejected; 2 usage;
3 could not inspect (bad range, unreadable file, nothing to check): treat as rejected.
"""
import argparse
import json
import os
import re
import subprocess
import sys

SESSION_LINK = re.compile(
    r"(?:https?://)?(?:www\.)?claude\.ai/(?:code/)?(?:session[_/-]|chat/|share/)[\w-]+[^\s)>\]]*", re.I)
# A bare session id, e.g. a link that was wrapped across two lines.
SESSION_ID = re.compile(r"\bsession_01[A-Za-z0-9]{16,}\b")
SESSION_KEY = re.compile(r"^\s*(?:x-)?claude[-_ ]?session[\w-]*\s*:", re.I)
COAUTHOR = re.compile(r"^[\s>*-]*co-authored-by\s*:\s*(.*?)\s*$", re.I)
FOOTER = re.compile(r"^\W*generated\s+(?:with|by|using)\s+\[?claude[ -]?code\]?"
                    r"(?:\s*\(\s*https?://[^)\s]*\s*\)|\s+https?://\S+)?\W*$", re.I)
IDENT = re.compile(r"^(.*?)\s*<([^<>]*)>\s*$")
# "Claude" alone, or followed by a product or model word, a version, "[bot]" or "(...)";
# anything starting with "Anthropic". Not "Claude Dupont", "Claude Monet", "Claudette".
AI_NAME = re.compile(
    r"^(?:anthropic\b.*|claude(?:[\s_-]*(?:(?:code|opus|sonnet|haiku|fable|instant|ai|bot|agent)\b|\d|\[bot\]|\().*)?)$",
    re.I)
AI_EMAIL = re.compile(
    r"^(?:.*@(?:[\w-]+\.)*anthropic\.com|(?:\d+\+)?claude(?:-code)?(?:\[bot\])?@users\.noreply\.github\.com)$", re.I)
REDACTED = "<Claude session link redacted>"


def redact(text):
    return SESSION_ID.sub(REDACTED, SESSION_LINK.sub(REDACTED, text))


def ai_identity(value):
    """True when `Name <email>` (or a bare name) is Claude or Anthropic, not a person."""
    m = IDENT.match(value.strip())
    name, email = (m.group(1), m.group(2)) if m else (value, "")
    name = re.sub(r"\s+", " ", name.strip().strip("\"'"))
    return bool(AI_EMAIL.match(email.strip()) or AI_NAME.match(name))


def check_text(field, text):
    """Findings for one piece of publication text."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        m = COAUTHOR.match(line)
        if m and ai_identity(m.group(1)):
            found.append({"field": field, "line": n, "rule": "ai-co-author",
                          "detail": "Co-authored-by trailer names Claude/Anthropic: %s" % redact(m.group(1))[:120]})
        if SESSION_KEY.match(line):
            found.append({"field": field, "line": n, "rule": "claude-session", "detail": "Claude-Session trailer"})
        elif SESSION_LINK.search(line) or SESSION_ID.search(line):
            found.append({"field": field, "line": n, "rule": "claude-session", "detail": "Claude session link"})
        if FOOTER.match(line):
            found.append({"field": field, "line": n, "rule": "ai-footer", "detail": "\"Generated with Claude Code\" footer"})
    return found


class CannotInspect(Exception):
    pass


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    p = subprocess.run([os.environ.get("PAI_SWARM_GIT") or "git", "--no-replace-objects", "-C", repo] + list(args),
                       capture_output=True, env=env, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise CannotInspect("git %s failed: %s" % (" ".join(args[:2]), p.stderr.decode("utf-8", "replace").strip()[-200:]))
    return p.stdout


def commits_in(repo, ranges=(), commits=()):
    shas = []
    for r in ranges:
        if ".." not in r or r.startswith("-"):
            raise CannotInspect("range %r must look like BASE..HEAD" % r)
        got = _git(repo, "rev-list", "--reverse", r, "--").decode().split()
        if not got:
            raise CannotInspect("range %s contains no commit: nothing would be published from it" % r)
        shas += got
    for c in commits:
        if c.startswith("-"):
            raise CannotInspect("bad commit %r" % c)
        shas.append(_git(repo, "rev-parse", "--verify", c + "^{commit}").decode().strip())
    return list(dict.fromkeys(shas))


def check_commit(repo, sha):
    raw = _git(repo, "cat-file", "commit", sha).decode("utf-8", "replace")
    head, _, message = raw.partition("\n\n")
    field = "commit %s" % sha[:12]
    found = []
    for line in head.splitlines():
        role, _, rest = line.partition(" ")
        if role in ("author", "committer"):
            ident = re.sub(r"\s+\d+\s+[+-]\d{4}$", "", rest)
            if ai_identity(ident):
                found.append({"field": "%s %s" % (field, role), "line": 0, "rule": "ai-identity",
                              "detail": "%s is Claude/Anthropic: %s" % (role, redact(ident)[:120])})
    return found + check_text(field + " message", message)


def check(repo=None, ranges=(), commits=(), pr_title=None, pr_bodies=(), squash_messages=()):
    """All findings plus the list of fields inspected. Raises CannotInspect."""
    found, inspected = [], []
    if (ranges or commits) and not repo:
        raise CannotInspect("--repo is required to inspect commits")
    for sha in commits_in(repo, ranges, commits) if (ranges or commits) else []:
        inspected.append("commit %s" % sha[:12])
        found += check_commit(repo, sha)
    for title in ([pr_title] if isinstance(pr_title, str) else pr_title or []):
        inspected.append("PR title")
        found += check_text("PR title", title)
    for label, paths in (("PR body", pr_bodies), ("squash message", squash_messages)):
        for path in paths:
            try:
                data = sys.stdin.buffer.read() if path == "-" else open(path, "rb").read()
            except OSError as e:
                raise CannotInspect("cannot read %s %s: %s" % (label, path, e.strerror))
            field = "%s (%s)" % (label, "stdin" if path == "-" else path)
            inspected.append(field)
            found += check_text(field, data.decode("utf-8", "replace"))
    if not inspected:
        raise CannotInspect("nothing to inspect: give --range, --commit, --pr-body-file or --squash-message-file")
    return found, inspected


def report(found, inspected, allowed, stream=sys.stdout):
    for f in found:
        where = f["field"] + (", line %d" % f["line"] if f["line"] else "")
        print("%s  %s: %s [%s]" % ("ALLOWED " if allowed else "REJECTED", where, f["detail"], f["rule"]), file=stream)
    if not found:
        print("publication check: OK, no AI attribution in %d item(s): %s"
              % (len(inspected), ", ".join(inspected[:8]) + (" …" if len(inspected) > 8 else "")), file=stream)
    elif allowed:
        print("publication check: %d AI attribution finding(s) ALLOWED for this run by --allow-ai-attribution"
              % len(found), file=stream)
    else:
        print("publication check: REJECTED, %d finding(s). Nothing may be pushed or opened. Reword the commit "
              "or text (you own the history), or pass --allow-ai-attribution for this run only." % len(found),
              file=stream)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="publish_check.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="repository or worktree that holds the commits")
    ap.add_argument("--range", action="append", default=[], metavar="BASE..HEAD",
                    help="commits that would be published (repeatable)")
    ap.add_argument("--commit", action="append", default=[], metavar="SHA", help="one commit, e.g. a squash merge")
    ap.add_argument("--pr-title", action="append", default=[], metavar="TEXT")
    ap.add_argument("--pr-body-file", action="append", default=[], metavar="FILE")
    ap.add_argument("--squash-message-file", action="append", default=[], metavar="FILE",
                    help="the squash commit message you are about to use ('-' reads stdin)")
    ap.add_argument("--allow-ai-attribution", action="store_true",
                    help="deliberate opt-in for this run: list the findings but exit 0")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        found, inspected = check(a.repo, a.range, a.commit, a.pr_title, a.pr_body_file, a.squash_message_file)
    except CannotInspect as e:
        print("publication check: CANNOT INSPECT (treated as rejected): %s" % e, file=sys.stderr)
        return 3
    ok = not found or a.allow_ai_attribution
    if a.json:
        json.dump({"ok": ok, "allowed_by_opt_in": bool(found and a.allow_ai_attribution),
                   "findings": found, "inspected": inspected}, sys.stdout, indent=1)
        print()
    else:
        report(found, inspected, a.allow_ai_attribution, sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
