#!/bin/sh
# Runs the pai-swarm fixture suites in a throwaway container with that image's own Python, as
# the unprivileged uid 65534. Used to check the stated minimum (Python 3.9) on a host that has
# only newer interpreters; run it from the repository root:
#   docker run --rm -v "$PWD:/src:ro" python:3.9-slim-bookworm sh /src/swarm/tests/container_check.sh
# It installs git, tmux, procps (`rc status` reads `ps`) and the distribution's python3 with
# apt inside the container, copies the read-only checkout, puts tripwires in front of `claude`
# and `gh` (any call fails the check), then byte-compiles swarm/, runs swarm/tests with
# unittest and runs swarm/tests/run_tests.sh.
# swarm/ runs on the image's Python; the tests that call PAI run it with the distribution's
# python3 (PAI_PYTHON), because PAI itself needs 3.11 or newer. The test user gets SHELL=/bin/sh:
# tmux starts windows through $SHELL, and uid 65534's login shell is nologin.
# No secrets, nothing published.
set -eu
apt-get update -qq > /dev/null
apt-get install -y -qq --no-install-recommends git tmux procps python3 > /dev/null
mkdir -p /work/tripwire
cp -R /src /work/src
# A linked worktree's .git is a file naming a host path that does not exist in here; drop it
# from the copy so the tests' own working directory is not a broken repository.
if [ -f /work/src/.git ]; then rm /work/src/.git; fi
for tool in claude gh; do
  printf '#!/bin/sh\necho "tripwire: the real %s was called" >> /work/tripwire.log\nexit 97\n' "$tool" > "/work/tripwire/$tool"
  chmod +x "/work/tripwire/$tool"
done
chown -R 65534:65534 /work
cd /work/src
exec python3 -B - <<'PY'
import os, platform, subprocess, sys
os.setgroups([])
os.setgid(65534)
os.setuid(65534)
os.environ.update(HOME="/work", PATH="/work/tripwire:" + os.environ["PATH"], PAI_REPO="/work/src", SHELL="/bin/sh",
                  PAI_SWARM_HOME="/work/pai-swarm-home", CLAUDE_CONFIG_DIR="/work/claude-config",
                  PAI_PYTHON="/usr/bin/python3")
for k in ("TMUX", "TMUX_PANE", "TYPESAFE_API_KEY"):
    os.environ.pop(k, None)
tools = [subprocess.run(c, capture_output=True, text=True).stdout.strip()
         for c in (["git", "--version"], ["tmux", "-V"], ["/usr/bin/python3", "--version"])]
print("container: swarm on Python %s (%s); %s; %s; PAI on %s (/usr/bin/python3); uid %d"
      % (platform.python_version(), sys.executable, tools[0], tools[1], tools[2], os.getuid()), flush=True)
results = []
for name, cmd in (("byte-compile swarm/", [sys.executable, "-m", "compileall", "-q", "swarm"]),
                  ("swarm/tests (unittest)", [sys.executable, "-B", "-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", "swarm/tests", "-v"]),
                  ("swarm/tests/run_tests.sh", ["bash", "swarm/tests/run_tests.sh"])):
    print("\n+ " + " ".join(cmd), flush=True)
    results.append((name, subprocess.run(cmd, timeout=1200).returncode))
tripped = os.path.exists("/work/tripwire.log") and open("/work/tripwire.log").read()
results.append(("tripwires for claude and gh untouched", 1 if tripped else 0))
print("\n" + (tripped or ""), flush=True)
for name, code in results:
    print("%-40s %s" % (name, "ok" if code == 0 else "FAILED (exit %s)" % code))
sys.exit(1 if any(code for _, code in results) else 0)
PY
