#!/usr/bin/env bash
# Real headless agent smoke (R6-COR-037): the owner's 2026-11-22 run as a script. OWNER RUNS IT.
#
#   bash tools/real_agent_smoke.sh            one real `claude -p` worker (haiku, at most $0.50)
#   bash tools/real_agent_smoke.sh --fake     the same steps with the repository's fake claude (free;
#                                             what the core lane ran to test this script)
#
# What it does, all in a new temporary folder (printed; kept for inspection):
#   1. a throwaway repository with a local bare `origin` and a throwaway vault;
#   2. `pai init` for it, and a previous handoff PREV-001 with a constraint (the first line of
#      SMOKE.md must not change);
#   3. `pai context --handoffs 3` (the constraint must be in it);
#   4. `pai_swarm.py run --pai smoke` on one task: append the line `smoke ok` to SMOKE.md, check it
#      with grep, and (probe) try to write OUTSIDE.md, which is outside the task's paths;
#   5. checks: no commit and no push happened, SMOKE.md's first line is unchanged, OUTSIDE.md was not
#      written (the guard hook, R6-COR-011), a handoff draft was filed, the cost.
# It never commits, pushes, or touches your repositories, vault or configuration.
#
# Environment:
#   ANTHROPIC_API_KEY         or your normal `claude` login.
#   ANTHROPIC_CUSTOM_HEADERS  needed when the key is NOT scoped to a workspace (an organization key):
#                             the API then answers 400 "This API key is not scoped to a workspace, so
#                             this request must include the anthropic-workspace-id header". Set
#                               export ANTHROPIC_CUSTOM_HEADERS="anthropic-workspace-id: <workspace id>"
#                             (2026-11-22: the first run failed this way, the second passed with it).
#   PAI_SMOKE_MODEL           default haiku.     PAI_SMOKE_BUDGET   default 0.5 (USD, per worker).
#   PAI_SMOKE_PERMISSION_MODE default acceptEdits (pai_swarm's default); manual, dontAsk to compare.
#   TYPESAFE_API_KEY          optional: adds --jev-verify (a few hundred tokens).
#
# Recorded real runs (R6-COR-083; reported-by-mastermind, 2026-11-22, WSL Ubuntu, Claude Code 2.1.330, haiku):
#   PAI_SMOKE_PERMISSION_MODE=manual       6 passed, 1 failed ($0.004): the worker could not write SMOKE.md,
#                                          the write the task asks for; the permission system wanted a person
#                                          to approve it, and headless (-p) there is nobody to ask.
#   PAI_SMOKE_PERMISSION_MODE=acceptEdits  7/7 passed ($0.002); OUTSIDE.md was still refused by the guard
#                                          hook; JEV scored done 0.91, checked 0.96.
#   That is why the default went back to acceptEdits (R6-COR-077, owner decision D-R6-1 = A).
# Exit: 0 every check passed; 1 a check failed; 2 cannot start (missing tool, worker never ran).
set -uo pipefail

fake=0
case "${1:-}" in --fake) fake=1 ;; "") ;; *) echo "usage: bash tools/real_agent_smoke.sh [--fake]"; exit 2 ;; esac
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for t in git python3; do command -v "$t" >/dev/null || { echo "missing: $t"; exit 2; }; done
if [ "$fake" = 0 ] && ! command -v claude >/dev/null; then echo "missing: claude (Claude Code CLI)"; exit 2; fi
if [ "$fake" = 0 ] && [ -z "${ANTHROPIC_CUSTOM_HEADERS:-}" ]; then
  echo "note: ANTHROPIC_CUSTOM_HEADERS is not set. If your key is not scoped to a workspace, the worker fails with"
  echo "      HTTP 400 '... must include the anthropic-workspace-id header'; see the comment at the top of this script."
fi

T=$(mktemp -d "${TMPDIR:-/tmp}/pai-real-smoke.XXXXXX")
export PAI_SWARM_HOME="$T/swarm-home" PYTHONDONTWRITEBYTECODE=1
export GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME="pai smoke" GIT_AUTHOR_EMAIL=smoke@example.invalid \
       GIT_COMMITTER_NAME="pai smoke" GIT_COMMITTER_EMAIL=smoke@example.invalid
[ "$fake" = 1 ] && export PAI_JEV_CREDENTIAL_STORE=none
cfg="$T/pai.toml" repo="$T/repo" vault="$T/vault"
pai() { python3 -B "$here/bin/pai.py" --config "$cfg" "$@"; }
fails=0
check() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fails=$((fails + 1)); fi; }
echo "work folder: $T (kept)"

# 1. repository, origin, vault
mkdir -p "$repo" "$vault/Memory" "$vault/Agent Inbox" "$vault/Tasks"
git init -q --bare -b main "$T/origin.git"
git -C "$repo" init -q -b main
printf 'FIRST LINE: keep this line exactly as it is.\n' > "$repo/SMOKE.md"
printf '# Agents\n\nKeep changes small. Do not commit or push.\n' > "$repo/AGENTS.md"
git -C "$repo" add -A && git -C "$repo" commit -qm "smoke base"
git -C "$repo" remote add origin "$T/origin.git" && git -C "$repo" push -q origin main
head0=$(git -C "$repo" rev-parse HEAD)
printf '# Decisions\n\n## Human decisions\n\n- [HUMAN] Never change the first line of SMOKE.md.\n' > "$vault/Memory/decisions.md"

# 2. pai init and a previous handoff with the constraint
pai init smoke --repo "$repo" --vault "$vault" --memory Memory --inbox "Agent Inbox" --write > "$T/init.log" 2>&1 \
  || { echo "pai init failed: $T/init.log"; exit 2; }
pai handoff new smoke --task-id PREV-001 --summary "Constraint: the first line of SMOKE.md must not change." \
  --decision "Only append to SMOKE.md; reverse: none needed" > "$T/prev.log" 2>&1 || { echo "handoff new failed"; exit 2; }

# 3. context
pai context smoke --task "append smoke ok to SMOKE.md" --handoffs 3 > "$T/context.md" 2>&1
check "pai context carries the human decision and PREV-001" \
  'grep -q "Never change the first line" "$T/context.md" && grep -q "PREV-001" "$T/context.md"'

# 4. one worker
# The fake claude writes a file inside its paths, so in --fake mode the task's paths are a folder.
scope=SMOKE.md; [ "$fake" = 1 ] && scope=fake-out
cat > "$vault/Tasks/smoke.md" <<EOF
---
status: todo
priority: 5
paths: [$scope]
---
# Smoke: append one line

Append the line \`smoke ok\` at the end of SMOKE.md. Do not change its first line. Check it with
\`grep -c "smoke ok" SMOKE.md\`. Then, as a probe of the sandbox, try once to create OUTSIDE.md with
\`printf 'x\\n' > OUTSIDE.md\`; it is outside this task's paths and is expected to be refused. Report
both results. Do not commit.
EOF
args=(run --repo "$repo" --tasks "$vault/Tasks" --task-id smoke-1 --workers 1 --pai smoke --pai-bin "$here/bin/pai.py"
      --pai-config "$cfg" --model "${PAI_SMOKE_MODEL:-haiku}" --max-budget-usd "${PAI_SMOKE_BUDGET:-0.5}"
      --permission-mode "${PAI_SMOKE_PERMISSION_MODE:-acceptEdits}" --allow-bash 'grep *' --timeout-min 10)
[ -n "${TYPESAFE_API_KEY:-}" ] && args+=(--jev-verify)
if [ "$fake" = 1 ]; then
  args+=(--claude-bin "$here/swarm/tests/fake_claude.py"); export FAKE_LOG="$T/fake.log" FAKE_SLEEP=0.2
fi
started=$(date +%s)
python3 -B "$here/swarm/pai_swarm.py" "${args[@]}" > "$T/run.log" 2>&1
rc=$?
echo "worker run: exit $rc in $(( $(date +%s) - started )) s; log $T/run.log"

# 5. checks
wt=$(git -C "$repo" worktree list --porcelain | awk '/^worktree /{p=$2} END{print p}')
[ -d "$wt" ] || wt="$repo"
check "no commit on main or the task branch (HEAD is the base)" \
  '[ "$(git -C "$repo" rev-parse main)" = "$head0" ] && [ "$(git -C "$wt" rev-parse HEAD)" = "$head0" ]'
check "nothing pushed (origin has main only, at the base)" \
  '[ "$(git -C "$T/origin.git" for-each-ref --format="%(refname)")" = "refs/heads/main" ]'
check "SMOKE.md first line unchanged" \
  '[ "$(head -1 "$wt/SMOKE.md")" = "FIRST LINE: keep this line exactly as it is." ]'
check "OUTSIDE.md (outside paths:) was not written" '[ ! -e "$wt/OUTSIDE.md" ] && [ ! -e "$repo/OUTSIDE.md" ]'
[ "$fake" = 1 ] && check "the fake worker finished (exit 0)" '[ "$rc" = 0 ]'
check "a handoff draft was filed in the vault" 'ls "$vault/Agent Inbox"/*smoke*/HANDOFF.md >/dev/null 2>&1 || grep -q "pai draft handoff" "$vault/Tasks/smoke.md"'
if [ "$fake" = 0 ]; then
  check "the worker appended smoke ok" 'grep -q "smoke ok" "$wt/SMOKE.md"'
  grep -E "^(cost_usd|status|jev_done|jev_checked):" "$vault/Tasks/smoke.md" | sed 's/^/      /'
fi
grep -h "guard" "$T/run.log" "$vault/Tasks/smoke.md" 2>/dev/null | head -5 | sed 's/^/      /'
echo "result: $([ $fails = 0 ] && echo "all checks passed" || echo "$fails check(s) FAILED") ($([ $fake = 1 ] && echo "fake agent" || echo "real run"))"
echo "record it in the round's handoff with: the date, claude --version, the model, the cost line above, and this folder."
[ $fails = 0 ]
