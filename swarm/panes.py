"""Text and keys into a tmux pane, as a person at the keyboard would send them, but in one piece.

Pasting long or multi-line text into an agent through a terminal is fragile: over SSH or a
phone the paste arrives in chunks, a newline can submit half a prompt, and tmux's own paste
buffer is not your clipboard. Here the text goes into a named tmux buffer through stdin (no file
on disk, no shell) and out with `paste-buffer -p`: a bracketed paste whenever the program in the
pane asked for one, so the agent receives it as a single paste and nothing is submitted.

  paste()      the whole text at once. Enter only when asked, and checked again first.
  send_keys()  what a person types: literal text and a fixed set of named keys (pai web's
               live typing). Every item is checked; nothing is passed to a shell.
  screen()     the visible screen with its colours (or the scrollback), the cursor and size.

Safety, shared by `pai paste` and `pai web`:
  - A paste is refused while the agent's folder-trust prompt is on the screen: the text could
    answer it. pai never answers that prompt; you do (`pai trust`, or typing in `pai web`).
  - Escape and other control characters are removed from pasted text (tabs and newlines stay),
    so a paste cannot end the bracketed paste early and turn the rest into keystrokes.
  - Trailing newlines are dropped: a file's last newline must not act as Enter. Enter is pressed
    only when the caller asks for it (pai paste --enter, "Paste and Enter").
  - Multi-line text goes only to a program that tmux says asked for bracketed paste
    (#{bracket_paste_flag}: tmux 3.8 reports it, 3.5a cannot). Otherwise each line could be
    entered on its own, so it is refused unless the caller accepts that (allow_unbracketed=True:
    pai paste --allow-unbracketed, or the confirmation in pai web). Even then tmux still sends
    the bracketed-paste markers whenever the program did ask for them.
"""
import re
import subprocess
import time

MAX_PASTE = 1024 * 1024  # bytes of UTF-8; a larger paste is refused, nothing is sent
MAX_TYPED = 4096         # characters of literal text in one send_keys call
# Control characters other than tab and newline (C0, DEL and C1): removed from pasted text.
CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Named keys pai web may send (tmux key names). Ctrl and Alt combinations: C-a .. C-z, M-<char>.
NAMED = {"Enter", "Tab", "BTab", "BSpace", "Escape", "Space", "Up", "Down", "Left", "Right", "Home", "End",
         "PageUp", "PageDown", "Insert", "Delete"} | {"F%d" % i for i in range(1, 13)}
CTRL = re.compile(r"C-[a-z\[\]\\@^_]")
META = re.compile(r"M-[\x21-\x3a\x3c-\x7e]")  # not M-; (tmux would read the ; as a separator)
PASTE_ENTER_GAP = 0.4  # seconds between a paste and the look that precedes Enter


class Refused(Exception):
    """Nothing was sent. `code` is a short reason a caller can act on."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def clean(text):
    """(text to paste, number of control characters removed). CRLF and CR become LF; trailing
    newlines are dropped (Enter is a separate, explicit choice)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out, n = CONTROL.subn("", text)
    return out.rstrip("\n"), n


def _tmux(tmux, *args, data=None):
    return subprocess.run(tmux.base + list(args), input=data, capture_output=True, env=tmux.env,
                          stdin=None if data is not None else subprocess.DEVNULL, timeout=30)


def info(tmux, pane_id):
    """{"bracketed": True|False|None, "cursor": [x, y], "size": [w, h], "in_mode": bool} or None
    when the pane does not exist. bracketed is None when this tmux cannot tell (3.5a cannot;
    3.8 can)."""
    p = _tmux(tmux, "display-message", "-p", "-t", pane_id,
              "#{pane_id}\t#{bracket_paste_flag}\t#{cursor_x}\t#{cursor_y}\t#{pane_width}\t#{pane_height}\t#{pane_in_mode}")
    parts = p.stdout.decode("utf-8", "replace").rstrip("\n").split("\t")
    if p.returncode != 0 or len(parts) != 7 or parts[0] != pane_id:
        return None
    num = lambda v: int(v) if v.isdigit() else 0  # noqa: E731
    return {"bracketed": {"1": True, "0": False}.get(parts[1]), "cursor": [num(parts[2]), num(parts[3])],
            "size": [num(parts[4]), num(parts[5])], "in_mode": parts[6] == "1"}


def screen(tmux, pane_id, history=False):
    """{"text", "cursor", "size", "bracketed"}: the visible screen with its colours (escape
    sequences, as tmux renders them), or with history=True the last 2000 lines, wrapped lines
    joined. None when the pane does not exist."""
    meta = info(tmux, pane_id)
    if meta is None:
        return None
    args = ["capture-pane", "-p", "-e", "-t", pane_id] + (["-J", "-S", "-2000"] if history else [])
    p = _tmux(tmux, *args)
    if p.returncode != 0:
        return None
    return dict(meta, text=p.stdout.decode("utf-8", "replace"))


def preview(tmux, pane_id, text, trust=None, allow_unbracketed=False):
    """What paste() would do with `text` in this pane, without sending anything: {'ok', 'code',
    'why', 'chars', 'lines', 'bytes', 'limit', 'bracketed', 'removed'}. The same checks as
    paste(), in the same order."""
    cleaned, removed = clean(text)
    data = cleaned.encode("utf-8")
    res = {"chars": len(cleaned), "lines": cleaned.count("\n") + 1 if cleaned else 0, "bytes": len(data),
           "limit": MAX_PASTE, "removed": removed, "bracketed": None, "ok": True, "code": None, "why": None}
    try:
        res["bracketed"] = _checks(tmux, pane_id, cleaned, data, trust, allow_unbracketed)[0]["bracketed"]
    except Refused as e:
        res.update(ok=False, code=e.code, why=str(e))
    return res


def _checks(tmux, pane_id, text, data, trust, allow_unbracketed):
    """(pane info, visible-screen function, line count, notes); raises Refused."""
    if not text:
        raise Refused("empty", "nothing to paste")
    if len(data) > MAX_PASTE:
        raise Refused("too-large", "the text is %d bytes; at most %d are pasted at once" % (len(data), MAX_PASTE))
    meta = info(tmux, pane_id)
    if meta is None:
        raise Refused("no-pane", "the pane %s is gone" % pane_id)

    def visible():
        p = _tmux(tmux, "capture-pane", "-p", "-J", "-t", pane_id)
        return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""

    if trust and trust(visible()):
        raise Refused("trust-prompt", "the agent's folder-trust prompt is on the screen: a paste could answer it. "
                                      "Answer it yourself first (pai trust, or type in pai web)")
    if meta["in_mode"]:
        raise Refused("copy-mode", "the pane is in tmux copy mode (press q in it first)")
    lines = text.count("\n") + 1
    notes = []
    if lines > 1 and meta["bracketed"] is False and not allow_unbracketed:
        raise Refused("not-bracketed", "the program in this pane did not ask for bracketed paste, so each of the %d "
                                       "lines would be entered on its own. Paste one line, or accept that "
                                       "(pai paste --allow-unbracketed)" % lines)
    if lines > 1 and meta["bracketed"] is None and not allow_unbracketed:
        raise Refused("bracketed-unknown", "this tmux cannot tell whether the program in this pane asked for "
                                           "bracketed paste (tmux 3.8 can). Claude Code and Codex normally do; a "
                                           "program that did not would get each of the %d lines entered on its own. "
                                           "Paste anyway with pai paste --allow-unbracketed" % lines)
    if meta["bracketed"] is None and lines > 1:
        notes.append("this tmux cannot tell whether the program asked for bracketed paste; pasted on your word")
    return meta, visible, lines, notes


def paste(tmux, pane_id, text, trust=None, enter=False, allow_unbracketed=False, buffer="pai-paste"):
    """Paste `text` into the pane in one piece. `trust` is a function(screen text) -> bool that
    says the folder-trust prompt is visible (the lane's agent adapter), or None for a plain shell.
    Returns a dict describing what was sent; raises Refused (nothing sent) otherwise."""
    text, removed = clean(text)
    data = text.encode("utf-8")
    meta, visible, lines, notes = _checks(tmux, pane_id, text, data, trust, allow_unbracketed)
    if removed:
        notes.append("%d control character(s) removed" % removed)
    buf = "%s-%s" % (buffer, pane_id.lstrip("%"))
    p = _tmux(tmux, "load-buffer", "-b", buf, "-", data=data)
    if p.returncode != 0:
        raise Refused("tmux", "tmux could not take the text: %s" % (p.stderr.decode("utf-8", "replace").strip() or "error"))
    p = _tmux(tmux, "paste-buffer", "-d", "-p", "-b", buf, "-t", pane_id)
    if p.returncode != 0:
        _tmux(tmux, "delete-buffer", "-b", buf)
        raise Refused("tmux", "tmux could not paste: %s" % (p.stderr.decode("utf-8", "replace").strip() or "error"))
    res = {"pasted": len(text), "lines": lines, "bracketed": meta["bracketed"], "enter": False, "notes": notes}
    if enter:
        time.sleep(PASTE_ENTER_GAP)
        if trust and trust(visible()):
            res["notes"].append("a folder-trust prompt appeared after the paste, so Enter was NOT pressed")
            return res
        p = _tmux(tmux, "send-keys", "-t", pane_id, "Enter")
        res["enter"] = p.returncode == 0
        if p.returncode != 0:
            res["notes"].append("Enter failed: %s" % p.stderr.decode("utf-8", "replace").strip())
    return res


def key_name(k):
    """The tmux key name for a named key, or None when it is not one pai sends."""
    if not isinstance(k, str):
        return None
    tmux_names = {"PageUp": "PPage", "PageDown": "NPage", "Insert": "IC", "Delete": "DC"}
    if k in NAMED:
        return tmux_names.get(k, k)
    if CTRL.fullmatch(k) or META.fullmatch(k):
        return k
    return None


def send_keys(tmux, pane_id, items):
    """Type a list of items in order: {"text": "..."} (literal; control characters are dropped)
    or {"key": NAME} (see NAMED, C-x, M-x). Raises Refused before sending anything when an item
    is not allowed.

    Literal text is typed through a tmux buffer pasted WITHOUT bracketed-paste markers (exactly
    the bytes, as if typed): tmux reads a command-line argument that ends in ";" as a command
    separator, so `send-keys -l` would drop a typed ";". Named keys go through send-keys."""
    if not isinstance(items, list) or not items or len(items) > 200:
        raise Refused("bad-keys", "send 1 to 200 keys at a time")
    groups, typed = [], 0  # [("text", str) | ("keys", [tmux key names])], in order
    for it in items:
        if not isinstance(it, dict):
            raise Refused("bad-keys", "each key is {\"text\": ...} or {\"key\": ...}")
        if "text" in it:
            t = CONTROL.sub("", str(it["text"]).replace("\r", "").replace("\n", ""))
            typed += len(t)
            if not t:
                continue
            if groups and groups[-1][0] == "text":
                groups[-1] = ("text", groups[-1][1] + t)
            else:
                groups.append(("text", t))
        else:
            name = key_name(it.get("key"))
            if not name:
                raise Refused("bad-keys", "pai does not send the key %r" % it.get("key"))
            if groups and groups[-1][0] == "keys":
                groups[-1][1].append(name)
            else:
                groups.append(("keys", [name]))
    if typed > MAX_TYPED:
        raise Refused("too-large", "at most %d characters are typed at once; paste longer text" % MAX_TYPED)
    if not groups:
        return {"sent": 0}
    if info(tmux, pane_id) is None:
        raise Refused("no-pane", "the pane %s is gone" % pane_id)
    buf = "pai-type-%s" % pane_id.lstrip("%")
    for kind, v in groups:
        if kind == "text":
            p = _tmux(tmux, "load-buffer", "-b", buf, "-", data=v.encode("utf-8"))
            if p.returncode == 0:
                p = _tmux(tmux, "paste-buffer", "-d", "-b", buf, "-t", pane_id)
        else:
            p = _tmux(tmux, "send-keys", "-t", pane_id, *v)
        if p.returncode != 0:
            raise Refused("tmux", "tmux could not send the keys: %s"
                          % (p.stderr.decode("utf-8", "replace").strip() or "error"))
    return {"sent": len(items)}
