"""Portable pai entry point, for any platform and any working directory:

    python3 /path/to/pai-workbench/bin/pai.py ARGS        (Linux, macOS)
    py -3 C:\\path\\to\\pai-workbench\\bin\\pai.py ARGS        (Windows)

bin/pai and bin/pai.cmd run this file. Running a script (rather than -m pai)
keeps the caller's working directory off the import path, so a pai/ folder or a
module in that directory can never replace the bundled code.
"""
import os
import sys

sys.dont_write_bytecode = True  # like -B: never write __pycache__ into the bundle
HERE = os.path.dirname(os.path.abspath(__file__))
if sys.path and os.path.abspath(sys.path[0] or os.curdir) == HERE:
    del sys.path[0]  # this directory holds pai.py, which must not be imported as the pai package
sys.path.insert(0, os.path.dirname(HERE))

import runpy  # noqa: E402

runpy.run_module("pai", run_name="__main__", alter_sys=True)
