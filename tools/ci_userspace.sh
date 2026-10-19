#!/bin/sh
# CI helper (not in the bundle): runs inside a throwaway distribution container.
#   docker run --rm -v "$PWD:/src:ro" -e SETUP="<install python3 as root>" IMAGE sh /src/tools/ci_userspace.sh
# Installs python3 with the image's package manager, copies the read-only checkout, then runs the
# tests, a release build and the bundle smoke as the unprivileged uid 65534. No secrets, no publish.
set -eu
sh -c "$SETUP"
. /etc/os-release
echo "userspace: $PRETTY_NAME; $(python3 --version)"
mkdir -p /work
cp -R /src /work/src
chown -R 65534:65534 /work
cd /work/src
exec python3 -B - <<'EOF'
import os, subprocess, sys
os.setgroups([])
os.setgid(65534)
os.setuid(65534)
os.environ["HOME"] = "/work"
def run(*cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, timeout=600, **kw)
run(sys.executable, "-B", "-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", "tests")
out = run(sys.executable, "-B", "tools/build_bundle.py", "--out", "/work/dist", capture_output=True, text=True).stdout
print(out, flush=True)
lines = out.splitlines()
run(sys.executable, "-B", "tools/bundle_smoke.py", lines[1], lines[3], "--work", "/work/smoke work")
EOF
