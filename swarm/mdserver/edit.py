"""pai edit FILE.md: open one Markdown file in a browser editor with a live preview.

    pai edit notes/plan.md                 opens the browser on this machine
    pai edit notes/plan.md --no-browser    only prints the link (for an SSH tunnel, see --port)

EXPERIMENTAL and optional. It runs in the foreground until Ctrl-C and stores nothing. The
server listens on 127.0.0.1 only and serves exactly this one file: no file list, task runner,
dashboard, chat or Git. The printed link signs in once (a one-time token); after that it is
useless. Typing updates the preview without writing; Save shows the diff and writes only
after you confirm that exact revision, and never over a newer version of the file (your draft
stays in the browser). Standard library only; Linux, macOS and native Windows, without tmux.
"""
import argparse
import os
import secrets
import signal
import stat
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import server  # noqa: E402


def gui_available():
    """Only try a browser where one can show up on this screen (never a terminal browser over
    SSH, which would take over the terminal)."""
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_browser(url, opener=None):
    """True when a browser was started. Any failure is reported, never fatal."""
    if not gui_available():
        return False
    try:
        return bool((opener or webbrowser.open)(url))
    except Exception:  # noqa: BLE001  a missing or broken browser must not stop the editor
        return False


def check_file(path):
    """(absolute path, problem). Refuses anything but an existing, regular, UTF-8 .md file."""
    full = os.path.abspath(os.path.expanduser(path))
    if not full.endswith(".md"):
        return full, "only .md files can be edited"
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        return full, "%s does not exist; create it first (an empty file is fine)" % full
    if stat.S_ISLNK(st.st_mode):
        return full, "%s is a symbolic link; open the file it points to instead" % full
    if not stat.S_ISREG(st.st_mode):
        return full, "%s is not a regular file" % full
    if st.st_size > server.MAX_BYTES:
        return full, "%s is larger than 2 MB" % full
    try:
        with open(full, "rb") as f:
            f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return full, "%s is not UTF-8 text" % full
    except OSError as e:
        return full, "cannot read %s: %s" % (full, e.strerror or e)
    return full, None


def utf8_output():
    # Windows: piped or redirected output would use the legacy code page and fail on a non-Latin
    # file name (as in pai/__main__.py).
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")


def foreground(cfg, what, intro, no_browser=False, opener=None, out=None, ready=None):
    """Serve `cfg` on loopback with a one-time sign-in link until Ctrl-C (or SIGTERM, or
    Ctrl-Break on Windows). `intro` lines follow the link. Returns the exit status."""
    out = out or sys.stdout
    token = secrets.token_urlsafe(24)
    cfg = dict(cfg, bind="127.0.0.1", one_time_token=True)
    try:
        httpd = server.serve(cfg, token, ready=ready)
    except OSError as e:
        print("%s: cannot listen on 127.0.0.1:%s (%s); try another --port" % (what, cfg.get("port") or "any",
                                                                              e.strerror or e), file=sys.stderr)
        return 1
    port = httpd.server_address[1]
    url = "http://127.0.0.1:%d/open?t=%s" % (port, token)
    print("  open:  %s" % url, file=out)
    for line in intro:
        print("  " + line, file=out)
    print("  From another computer: ssh -N -L %d:127.0.0.1:%d YOU@THIS-HOST, then open the link there." % (port, port),
          file=out)
    print("  Ctrl-C stops it; unsaved changes stay only in the browser tab.", file=out)
    out.flush()
    if not no_browser:
        print("  %s" % ("browser opened" if open_browser(url, opener) else
                        "could not open a browser here: open the link above yourself"), file=out)
        out.flush()

    def stop(*_):  # Ctrl-C, a polite kill, or Ctrl-Break; also when started with SIGINT ignored
        raise KeyboardInterrupt

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    print("\n%s: stopped. Nothing else was changed." % what, file=out)
    return 0


def main(argv=None, opener=None, out=None):
    utf8_output()
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="pai edit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="the Markdown file to edit")
    ap.add_argument("--port", type=int, default=0, help="loopback port (default: a free one)")
    ap.add_argument("--no-browser", action="store_true", help="print the link only")
    a = ap.parse_args(argv)
    full, problem = check_file(a.file)
    if problem:
        print("pai edit: %s" % problem, file=sys.stderr)
        return 2
    if a.port and not 1024 <= a.port <= 65535:
        print("pai edit: --port must be between 1024 and 65535", file=sys.stderr)
        return 2
    print("pai edit (EXPERIMENTAL): %s" % full, file=out)
    cfg = {"roots": [os.path.dirname(full)], "single": os.path.basename(full), "port": a.port}
    return foreground(cfg, "pai edit", ["The link signs in once. Only this file is served, on this machine only (127.0.0.1)."],
                      a.no_browser, opener, out)


if __name__ == "__main__":
    sys.exit(main())
