#!/usr/bin/env bash
# A whole round with two lanes, offline, with FAKE agents, in a throw-away folder.
#
#   bash examples/two-lane-round/run.sh [WORK_DIR]
#
# WORK_DIR (default: a new mktemp folder) gets its own HOME, a tiny repository with a local
# "remote", two task notes, fake `claude`/`gh` on PATH (this checkout's test fakes) and its own
# tmux server, so nothing of yours is read or changed. Every pai command is the normal one; the
# script prints each command before running it, so its output reads like a terminal session.
# The fake agent never edits files: the script makes each lane's commit itself, marked
# "(the agent's work, done by the script)". Needs bash, git, tmux and Python 3.11+.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
WORK=${1:-$(mktemp -d "${TMPDIR:-/tmp}/pai-two-lanes.XXXXXX")}
mkdir -p "$WORK"
WORK=$(cd "$WORK" && pwd)
if [ -e "$WORK/home" ]; then echo "two-lane-round: $WORK/home exists; give a new WORK_DIR" >&2; exit 2; fi
for t in git tmux python3; do command -v "$t" >/dev/null || { echo "two-lane-round: needs $t" >&2; exit 2; }; done

# --- a brand-new user's environment (nothing inherited but a minimal PATH)
mkdir -p "$WORK/home" "$WORK/bin" "$WORK/ctl" "$WORK/claude-config" "$WORK/tmux"
PY=$(command -v python3)
for pair in claude:fake_claude_rc.py gh:fake_gh.py ssh:fake_ssh.py; do
  name=${pair%%:*}; fake=${pair#*:}
  printf '#!%s\nimport runpy, sys\nsys.argv[0] = __file__\nrunpy.run_path(%s, run_name="__main__")\n' \
    "$PY" "'$ROOT/swarm/tests/$fake'" > "$WORK/bin/$name"
  chmod +x "$WORK/bin/$name"
done
printf '#!/bin/sh\nexec "%s" -B "%s/bin/pai.py" "$@"\n' "$PY" "$ROOT" > "$WORK/bin/pai"   # this checkout's pai
chmod +x "$WORK/bin/pai"
export HOME="$WORK/home" PATH="$WORK/bin:/usr/local/bin:/usr/bin:/bin" LANG=C.UTF-8 TERM=xterm SHELL=/bin/bash
export TMUX_TMPDIR="$WORK/tmux" FAKE_RC_DIR="$WORK/ctl" CLAUDE_CONFIG_DIR="$WORK/claude-config"
export FAKE_GH_STATE="$WORK/gh.json" FAKE_GH_REMOTE="$WORK/origin.git" PAI_SWARM_GH="$WORK/bin/gh"
export PAI_JEV_CREDENTIAL_STORE=none GIT_CONFIG_NOSYSTEM=1 PYTHONDONTWRITEBYTECODE=1
export GIT_AUTHOR_NAME="Example User" GIT_AUTHOR_EMAIL=user@example.invalid
export GIT_COMMITTER_NAME="Example User" GIT_COMMITTER_EMAIL=user@example.invalid
unset TMUX TYPESAFE_API_KEY

show() { printf '\n$ %s\n' "$*"; set +e; eval "$@"; rc=$?; set -e; [ "$rc" = 0 ] || printf '[exit %s]\n' "$rc"; }
note() { printf '\n# %s\n' "$*"; }

note "1. A tiny project with a local remote (a stand-in for a GitHub clone)"
git init -q --bare -b main "$WORK/origin.git"
git clone -q "$WORK/origin.git" "$HOME/code/shop" 2>/dev/null
cd "$HOME/code/shop"
printf 'def total(prices):\n    return sum(prices)\n' > shop.py
printf 'import shop\nassert shop.total([1, 2]) == 3\nprint("ok")\n' > test_shop.py
git add . && git commit -qm "A tiny shop" && git push -q origin main
show 'ls'

note "2. Save this checkout's defaults (one review check: the tests)"
show 'pai init --default-agent claude --base main --check "test=python3 test_shop.py" --workers 2 --no-input | tail -4'

note "3. Two task notes: one per lane"
show 'pai task new "Add a discount" --body "Add discount(prices, pct) to shop.py and a test for it in test_shop.py." --write | tail -1'
show 'pai task new "Write a README" --body "Create README.md: what shop.py does, and how to run the test." --write | tail -1'
show 'pai task list'

note "4. The plan, then the run: two lanes, two worktrees, two fake agents"
show 'pai run --dry-run | grep -E "^lane|DRY RUN"'
show 'pai run --yes --wait 20 | grep -E "^started|^summary|task: "'
show 'pai status --brief'

note "5. (the agent's work, done by the script: the fake agent does not edit files)"
LANES="$HOME/code/shop-pai-lanes"
cat >> "$LANES/a/shop.py" <<'PYEOF'


def discount(prices, pct):
    return [p * (100 - pct) / 100 for p in prices]
PYEOF
printf 'assert shop.discount([100], 10) == [90.0]\n' >> "$LANES/a/test_shop.py"
git -C "$LANES/a" commit -qam "Add discount()"
printf '# shop\n\n`shop.total(prices)` adds prices up. Run the test: `python3 test_shop.py`.\n' > "$LANES/b/README.md"
git -C "$LANES/b" add README.md && git -C "$LANES/b" commit -qm "Add a README"
show 'git -C "$LANES/a" log --oneline -2'
show 'git -C "$LANES/b" log --oneline -2'

note "6. What would be published, and the review check on each lane"
show 'pai pr plan a | grep -E "^commits|^ +[0-9a-f]{7}|^check|^BLOCKED|^PLAN"'
show 'pai pr check a | grep -E "^check|^review"'
show 'pai pr check b | grep -E "^check|^review"'

note "7. Close the round: stop both workers (worktrees, branches and notes stay)"
show 'pai cleanup --apply --stop-running | grep -E "stopped|closed"'
show 'git -C "$HOME/code/shop" branch --list "pai/*"'

note "Done. Everything is under $WORK (delete it when you have looked: rm -rf \"$WORK\")."
