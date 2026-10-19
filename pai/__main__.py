import sys

if sys.version_info < (3, 11):  # checked before importing anything that needs 3.11 (tomllib)
    sys.stderr.write(f"pai: Python 3.11 or newer is required; this is {sys.version.split()[0]}. "
                     "Run it with a newer python3 (for example python3.11 -m pai).\n")
    sys.exit(2)

from pai.cli import main  # noqa: E402

sys.exit(main())
