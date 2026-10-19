import sys

if sys.version_info < (3, 11):  # checked before importing anything that needs 3.11 (tomllib)
    sys.stderr.write("pai: Python 3.11 or newer is required; this is %d.%d (%s).\n"
                     "     Install Python 3.11+ and select it with PAI_PYTHON=/path/to/python, "
                     "or run it directly (for example python3.11 -m pai, or py -3.12 -m pai on Windows).\n"
                     % (sys.version_info[0], sys.version_info[1], sys.executable or "unknown interpreter"))
    sys.exit(2)

if sys.platform == "win32":
    # Piped or redirected output would otherwise use the legacy ANSI code page and fail on non-Latin
    # text. Console output is unaffected: it is written as UTF-16 either way.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

from pai.cli import main  # noqa: E402

sys.exit(main())
