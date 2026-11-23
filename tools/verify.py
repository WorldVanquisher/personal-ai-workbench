#!/usr/bin/env python3
"""Local verification before a review or a release: one command for the existing checks.

    python3 -B tools/verify.py                  everything this machine can run
    python3 -B tools/verify.py --quick          core suite, archives and bundle smoke only
    python3 -B tools/verify.py --work DIR       keep logs and archives in DIR (a new folder)

Steps, in order (each one's full output goes to WORK/logs/<step>.log):
  core        tests/ through tools/strict_unittest.py (fails on a leaked file too)
  workbench   swarm/tests through tools/strict_unittest.py              needs git, tmux
  archives    tools/build_bundle.py: both archives and their .sha256
  bundle      tools/bundle_smoke.py with the two archives just built
  headless    swarm/tests/run_tests.sh (fake claude, PAI_REPO=this checkout) needs bash, git
  onboarding  tools/onboarding_acceptance.py --archive <the .tar.gz built>  needs git, tmux, bash
  quickstart  tools/quickstart_walkthrough.py (QUICKSTART.md as written)   needs uv, git, tmux
  console     tools/console_walkthrough.py (pai web: inspect, edit, paste) needs git, tmux
  path-SHELL  tools/path_hint_check.py SHELL: pai install's PATH advice run in bash, zsh, fish and
              PowerShell (each needs that shell)
  package     tools/package_check.py (wheel, upgrade, interrupted install, uninstall, one build)
              needs git, tmux, pip, venv with ensurepip and setuptools (importable, or --setuptools
              DIR for an offline build); a missing one is SKIPPED with its name, never FAIL
  doc-check   tools/doc_check.py: manuals against the real --help, links and anchors (when present)
  tests:DIR   any other folder of test_*.py files is found and run like core (strict)

Test groups are discovered, not listed: every folder holding test_*.py files becomes a step
(tests/ is core, swarm/tests is workbench), so a new test module or folder is never left out.
`--list` shows the steps, what each needs and the modules found, and runs nothing.

A step whose tools are missing is reported as SKIPPED with the missing tool, never as passed;
a step that depends on one that failed is SKIPPED with that reason. Exit 0 only when no step
failed; skipped steps are listed in the summary (with --require-all they also fail the run).
The summary also gives each step's wall time and every test the suites skipped, with its reason
(platform-specific skips, such as Windows-only tests); WORK/verify-report.json holds all of it,
with `"ci": "yes"|"no"` per step: whether a workflow in .github/workflows runs that step's command too.
Nothing here pushes, publishes or touches your configuration: every step works in WORK.
Not covered: native Windows and macOS (CI), real agents (docs/1.0/REAL-AGENT-SMOKE.md), WSL.
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

SKIP_RE = re.compile(r"""^(test\S+) \((\S+)\)(?: \S+)? \.\.\. skipped (?:'(.*)'|"(.*)")$""")


def groups():
    """{folder relative to the checkout: [test module, ...]} for every folder with test_*.py."""
    found = {}
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__", "node_modules", "dist", "build") and
                         not d.startswith("."))
        mods = sorted(f[:-3] for f in files if f.startswith("test_") and f.endswith(".py"))
        if mods:
            found[os.path.relpath(root, ROOT).replace(os.sep, "/")] = mods
    return found


def tool_version(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return (p.stdout or p.stderr).strip().splitlines()[0] if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None

def package_missing(python=None, setuptools_dir=None):
    """What the package step needs that this interpreter lacks: pip (it builds the wheel with
    `python -m pip wheel` and installs it into venvs), ensurepip (a venv without it has no pip,
    as on Ubuntu without python3-venv) and setuptools. An empty list means the step can run."""
    python = python or sys.executable
    lacking = []
    probe = subprocess.run([python, "-B", "-m", "pip", "--version"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL) if os.path.exists(python) else None
    if probe is None or probe.returncode != 0:
        lacking.append("pip (`%s -m pip --version` failed; install python3-pip)" % os.path.basename(python))
    probe = subprocess.run([python, "-B", "-c", "import ensurepip, venv"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL) if os.path.exists(python) else None
    if probe is None or probe.returncode != 0:
        lacking.append("venv with ensurepip (install python3-venv)")
    if setuptools_dir:
        if not os.path.isdir(os.path.join(setuptools_dir, "setuptools")):
            lacking.append("setuptools in %s" % setuptools_dir)
    else:
        probe = subprocess.run([python, "-B", "-c", "import setuptools"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL) if os.path.exists(python) else None
        if probe is None or probe.returncode != 0:
            lacking.append("setuptools (offline build; pass --setuptools DIR)")
    return lacking


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What each step runs, as it would appear in a CI workflow. A step is "covered by CI" when one of
# its signatures appears in a `run:` line of .github/workflows/*.yml, so the answer follows the
# workflow files instead of a hand-kept list (R6-COR-006). Read as text: no YAML parser needed.
CI_SIGNATURES = {
    "core": ("strict_unittest.py tests", "unittest discover -s tests"),
    "workbench": ("strict_unittest.py swarm/tests",),
    "archives": ("build_bundle.py",),
    "bundle": ("bundle_smoke.py",),
    "headless": ("swarm/tests/run_tests.sh",),
    "onboarding": ("onboarding_acceptance.py",),
    "quickstart": ("quickstart_walkthrough.py",),
    "console": ("console_walkthrough.py",),
    "package": ("package_check.py",),
    "doc-check": ("doc_check.py",),
}


def ci_runs(root=None):
    """The `run:` text of every workflow under ROOT/.github/workflows (comments dropped)."""
    d = os.path.join(root or ROOT, ".github", "workflows")
    text = []
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            if name.endswith((".yml", ".yaml")):
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    text.extend(line.split(" #", 1)[0] for line in f if not line.lstrip().startswith("#"))
    return "".join(text)


def ci_covers(step, runs):
    """'yes' when CI runs this step's command too, else 'no' (path-SHELL: that shell's check)."""
    sigs = CI_SIGNATURES.get(step)
    if sigs is None and step.startswith("path-"):
        sigs = ("path_hint_check.py " + step[5:],)
    if sigs is None and step.startswith("tests:"):
        sigs = ("strict_unittest.py " + step[6:],)
    return "yes" if sigs and any(sg in runs for sg in sigs) else "no"
PY = [sys.executable, "-B"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("--work", help="a new folder for logs and archives (default: a new temporary folder, kept)")
    ap.add_argument("--quick", action="store_true", help="core suite, archives and bundle smoke only")
    ap.add_argument("--require-all", action="store_true", help="a skipped step (missing tool) fails the run too")
    ap.add_argument("--list", action="store_true", help="show the steps and the test modules found; run nothing")
    ap.add_argument("--setuptools", help="package step: a folder holding setuptools, for an offline wheel build")
    a = ap.parse_args(argv)
    found = groups()
    extra = [g for g in found if g not in ("tests", "swarm/tests")]
    if a.list:
        print("test groups found (%d modules):" % sum(len(v) for v in found.values()))
        for g, mods in found.items():
            print("  %-12s %-14s %s" % ({"tests": "core", "swarm/tests": "workbench"}.get(g, "tests:" + g), g,
                                       ", ".join(mods)))
        print("other steps: archives, bundle, headless (bash, git), onboarding (git, tmux, bash), quickstart (uv, git, "
              "tmux, bash), console (git, tmux), path-bash/zsh/fish/pwsh (that shell), package (git, tmux, setuptools)")
        return 0
    started = time.time()
    work = os.path.abspath(a.work) if a.work else tempfile.mkdtemp(prefix="pai-verify-")
    if a.work:
        if os.path.exists(work):
            ap.error("--work %s exists; give a new folder" % work)
        os.makedirs(work)
    logs = os.path.join(work, "logs")
    os.makedirs(logs)
    # PYTHONDONTWRITEBYTECODE: no step leaves __pycache__ in the checkout (run_tests.sh calls python without -B)
    env = dict(os.environ, TMPDIR=os.path.join(work, "tmp"), PYTHONDONTWRITEBYTECODE="1")
    os.makedirs(env["TMPDIR"])
    for k in ("TMUX", "TMUX_PANE"):  # never the operator's own tmux server
        env.pop(k, None)
    rows, state, timings, skips = [], {}, {}, []

    def missing(*tools):
        return [t for t in tools if not shutil.which(t)]

    def step(name, argv, needs=(), after=(), cwd=ROOT, extra_env=None, timeout=1800):
        lacking = [x for x in missing(*needs)] + [x for x in needs_extra(name)]
        failed_before = [d for d in after if state.get(d) != "PASS"]
        t0 = time.time()
        log = os.path.join(logs, name + ".log")
        if lacking:
            res, why = "SKIPPED", "not installed: %s" % ", ".join(lacking)
        elif failed_before:
            res, why = "SKIPPED", "needs %s, which did not pass" % ", ".join(failed_before)
        else:
            shown = [os.path.relpath(x, ROOT) if os.path.isabs(x) and x.startswith(ROOT) else x
                     for x in argv if x not in PY]
            print("... %-10s %s" % (name, " ".join(shown)), flush=True)
            t0 = time.time()
            with open(log, "w") as f:
                try:
                    p = subprocess.run(argv, cwd=cwd, env=dict(env, **(extra_env or {})), stdout=f, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, timeout=timeout)
                    rc = p.returncode
                except subprocess.TimeoutExpired:
                    rc = "timeout after %ds" % timeout
            res = "PASS" if rc == 0 else "FAIL"
            why = "%.0fs" % (time.time() - t0) + ("" if rc == 0 else "; exit %s; see %s" % (rc, log))
            if res == "FAIL":
                with open(log, errors="replace") as f:
                    tail = f.read()[-1500:]
                print("    last lines of %s:\n      %s" % (log, "\n      ".join(tail.rstrip().splitlines()[-12:])),
                      flush=True)
        state[name] = res
        rows.append((name, res, why))
        timings[name] = round(time.time() - t0, 1) if res != "SKIPPED" else None
        if res != "SKIPPED" and os.path.exists(log):
            with open(log, errors="replace") as f:
                for line in f:
                    mt = SKIP_RE.match(line.strip())
                    if mt:
                        skips.append({"step": name, "test": mt.group(2), "reason": mt.group(3) or mt.group(4)})
        print("%-7s %-10s %s" % (res, name, why), flush=True)
        return res

    def needs_extra(name):
        return package_missing(setuptools_dir=a.setuptools) if name == "package" else []

    step("core", PY + [os.path.join(ROOT, "tools", "strict_unittest.py"), "tests", "-v"])
    if not a.quick:
        step("workbench", PY + [os.path.join(ROOT, "tools", "strict_unittest.py"), "swarm/tests", "--jobs",
                                str(min(4, os.cpu_count() or 1)), "-v"],
             needs=("git", "tmux"), timeout=2400)
        for g in extra:  # a test folder nobody listed is still run
            step("tests:" + g, PY + [os.path.join(ROOT, "tools", "strict_unittest.py"), g, "-v"])
    doc_check = os.path.join(ROOT, "tools", "doc_check.py")
    if os.path.exists(doc_check):  # docs lane, R6-COR-008: manuals against the real --help, links, anchors
        step("doc-check", PY + [doc_check])
    else:
        rows.append(("doc-check", "SKIPPED", "tools/doc_check.py is not in this tree"))
        state["doc-check"], timings["doc-check"] = "SKIPPED", None
    dist = os.path.join(work, "dist")
    step("archives", PY + [os.path.join(ROOT, "tools", "build_bundle.py"), "--out", dist])
    tgz = zips = None
    if state["archives"] == "PASS":
        names = sorted(os.listdir(dist))
        tgz = next((os.path.join(dist, n) for n in names if n.endswith(".tar.gz")), None)
        zips = next((os.path.join(dist, n) for n in names if n.endswith(".zip")), None)
    if tgz and zips:
        step("bundle", PY + [os.path.join(ROOT, "tools", "bundle_smoke.py"), tgz, zips, "--work",
                             os.path.join(work, "bundle-smoke")], after=("archives",))
    else:
        step("bundle", [], after=("archives",))
    if not a.quick:
        step("headless", ["bash", os.path.join(ROOT, "swarm", "tests", "run_tests.sh")], needs=("bash", "git"),
             extra_env={"PAI_REPO": ROOT})
        step("onboarding", PY + [os.path.join(ROOT, "tools", "onboarding_acceptance.py"), "--work",
                                 os.path.join(work, "onboarding"), "--archive", tgz or ""],
             needs=("git", "tmux", "bash"), after=("archives",))
        step("quickstart", PY + [os.path.join(ROOT, "tools", "quickstart_walkthrough.py"), "--work",
                                 os.path.join(work, "quickstart")], needs=("uv", "git", "tmux", "bash"))
        step("console", PY + [os.path.join(ROOT, "tools", "console_walkthrough.py")], needs=("git", "tmux"))
        for sh in ("bash", "zsh", "fish", "pwsh"):  # pai install's PATH advice, executed in that shell
            step("path-" + sh, PY + [os.path.join(ROOT, "tools", "path_hint_check.py"), sh], needs=(sh,))
        step("package", PY + [os.path.join(ROOT, "tools", "package_check.py"), "--work", os.path.join(work, "package")]
             + (["--setuptools", a.setuptools] if a.setuptools else []), needs=("git", "tmux"), timeout=1200)

    failed = [r for r in rows if r[1] == "FAIL"]
    skipped = [r for r in rows if r[1] == "SKIPPED"]
    print("\nverify: %d passed, %d failed, %d skipped   (logs: %s)" % (
        sum(r[1] == "PASS" for r in rows), len(failed), len(skipped), logs))
    for name, res, why in failed + skipped:
        print("  %-7s %-10s %s" % (res, name, why))
    if a.quick:
        print("  (--quick: workbench, headless, onboarding, quickstart, console, path-* and package were not run)")
    total = round(time.time() - started, 1)
    plat = {"system": platform.system(), "release": platform.release(), "python": platform.python_version(),
            "tmux": tool_version(["tmux", "-V"]), "git": tool_version(["git", "--version"]),
            "bash": tool_version(["bash", "--version"]), "uv": tool_version(["uv", "--version"])}
    print("wall time %.1fs: %s" % (total, ", ".join("%s %ss" % (k, v) for k, v in timings.items() if v is not None)))
    by_reason = {}
    for sk in skips:
        by_reason.setdefault((sk["step"], sk["reason"]), []).append(sk["test"])
    if by_reason:
        print("tests skipped inside the suites (by reason):")
        for (stp, reason), tests in sorted(by_reason.items()):
            print("  %-10s %3d  %s" % (stp, len(tests), reason))
    print("platform  %s" % ", ".join("%s %s" % kv for kv in plat.items() if kv[1]))
    runs = ci_runs()
    local_only = [n for n, _, _ in rows if ci_covers(n, runs) == "no"]
    if local_only:
        print("not run by CI (this machine is their only evidence): %s" % ", ".join(local_only))
    with open(os.path.join(work, "verify-report.json"), "w") as f:
        json.dump({"platform": plat, "wall_seconds": total, "steps": [{"step": n, "result": r, "detail": w,
                                                                        "seconds": timings.get(n),
                                                                        "ci": ci_covers(n, runs)} for n, r, w in rows],
                   "skipped_tests": skips, "groups": found}, f, indent=1)
    return 1 if failed or (a.require_all and skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
