#!/usr/bin/env bash
# End-to-end tests with a fake `claude`. No Claude usage, no network.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
T="$(mktemp -d)"
FAKE="$HERE/fake_claude.py"
chmod +x "$FAKE"
pass=0; fail=0
ok() { echo "PASS $1"; pass=$((pass+1)); }
bad() { echo "FAIL $1"; fail=$((fail+1)); }

# Every run is a git task now: the repository needs a remote to fetch, and task records and
# worktree locks go to an isolated home instead of ~/.cache/pai-swarm.
export PAI_SWARM_HOME="$T/home"
setup() {
  rm -rf "$T/repo" "$T/origin.git" "$T/vault" "$T/fake.log" "$T/home"
  mkdir -p "$T/repo/src/api" "$T/repo/src/web" "$T/repo/docs" "$T/vault/Tasks"
  git init -q --bare -b main "$T/origin.git"
  (cd "$T/repo" && git init -q -b main && git config user.email t@t && git config user.name t \
     && echo hi > README.md && git add -A && git commit -qm init \
     && git remote add origin "$T/origin.git" && git push -q origin main)
}
mk() { printf -- "---\nstatus: %s\npriority: %s\n%b---\n# Task %s\n\nDo the %s thing.\n" "$2" "$3" "$4" "$1" "$1" > "$T/vault/Tasks/$1.md"; }

# ---- 1. scheduling, hooks, shim, notes
setup
mk api-a todo 5 "paths: [src/api]  # backend\n"
mk api-b todo 4 "paths:\n  - src/api/sub\n"
mk web-a todo 3 "paths: [src/web]\n"
mk docs-a todo 3 "paths: [docs]\nmodel: haiku\n"
mk exclusive-x todo 2 ""
mk web-blocked todo 1 "paths: [src/web/x]\n"
mk docs-fail todo 1 "paths: [docs/f]\n"
mk later inbox 0 "paths: [src/z]\n"
HEAD0=$(git -C "$T/repo" rev-parse HEAD)
FAKE_LOG="$T/fake.log" python3 "$ROOT/pai_swarm.py" run --repo "$T/repo" --tasks "$T/vault/Tasks" \
  --task-id t1 --workers 3 --claude-bin "$FAKE" > "$T/out.txt" 2>&1 || true

[ "$(git -C "$T/repo" rev-parse HEAD)" = "$HEAD0" ] && ok "no commits made" || bad "HEAD moved"
[ "$(git -C "$T/repo" symbolic-ref --short HEAD)" = "swarm/t1" ] && ok "workers ran on the new task branch" || bad "branch"
[ ! -e "$T/home/locks" ] || [ -z "$(ls "$T/home/locks")" ] && ok "worktree lock released after the run" || bad "lock left behind"
python3 - "$T/fake.log" <<'EOF' && ok "concurrency limit, path locks, hooks, shim" || bad "see above"
import json, sys
rows = [r for r in (json.loads(l) for l in open(sys.argv[1])) if not r.get("extra")]
paths = {"api-a": ["src/api"], "api-b": ["src/api/sub"], "web-a": ["src/web"], "docs-a": ["docs"],
         "exclusive-x": [], "web-blocked": ["src/web/x"], "docs-fail": ["docs/f"]}
assert len(rows) == 7, "expected 7 workers to run, got %d" % len(rows)
assert "later" not in [r["task"] for r in rows], "inbox task ran"
ev = sorted([(r["start"], 1) for r in rows] + [(r["end"], -1) for r in rows])
cur = peak = 0
for _, d in ev:
    cur += d; peak = max(peak, cur)
assert peak <= 3, "peak concurrency %d > 3" % peak
assert peak >= 2, "never ran in parallel"
def overlap(a, b): return a["start"] < b["end"] and b["start"] < a["end"]
def conflict(x, y):
    if not x or not y: return True
    return any(p == q or p.startswith(q + "/") or q.startswith(p + "/") for p in x for q in y)
for i, a in enumerate(rows):
    for b in rows[i+1:]:
        if overlap(a, b):
            assert not conflict(paths[a["task"]], paths[b["task"]]), "%s and %s ran together" % (a["task"], b["task"])
for r in rows:
    assert r["hook_git_commit"] == 2 and r["hook_sh_c_push"] == 2 and r["hook_edit_dotgit"] == 2, r
    assert r["hook_git_status"] == 0 and r["hook_edit_inscope"] == 0, r
    assert r["shim_commit_rc"] == 2 and r["shim_status_rc"] == 0, r
    expect_outside = 0 if r["task"] == "exclusive-x" else 2
    assert r["hook_edit_outside"] == expect_outside, r
print("peak concurrency", peak)
EOF
grep -q "^status: done" "$T/vault/Tasks/api-a.md" && ok "done task marked done" || bad "api-a status"
grep -q "^status: review" "$T/vault/Tasks/web-blocked.md" && ok "BLOCKED report -> review" || bad "web-blocked status"
grep -q "^status: failed" "$T/vault/Tasks/docs-fail.md" && ok "error -> failed" || bad "docs-fail status"
grep -q "^status: inbox" "$T/vault/Tasks/later.md" && ok "inbox untouched" || bad "later status"
grep -q "paths: \[src/api\]  # backend" "$T/vault/Tasks/api-a.md" && ok "user frontmatter preserved" || bad "frontmatter rewritten"
grep -q "claude --resume sess-api-a" "$T/vault/Tasks/api-a.md" && ok "resume hint written" || bad "resume hint"
ls "$T/vault/Tasks/_runs/"*.md >/dev/null && ok "run note written" || bad "run note"

# ---- 2. Ctrl-C puts running tasks back to todo
setup
mk slow-a todo 1 "paths: [src/api]\n"
mk slow-b todo 1 "paths: [src/web]\n"
FAKE_SLEEP=6 FAKE_LOG="$T/fake.log" python3 "$ROOT/pai_swarm.py" run --repo "$T/repo" \
  --tasks "$T/vault/Tasks" --task-id t2 --workers 2 --claude-bin "$FAKE" > "$T/out2.txt" 2>&1 &
PID=$!
sleep 2.5
kill -TERM $PID; wait $PID || true
grep -q "^status: todo" "$T/vault/Tasks/slow-a.md" && grep -q "^status: todo" "$T/vault/Tasks/slow-b.md" \
  && ok "Ctrl-C returns tasks to todo" || { bad "Ctrl-C"; cat "$T/out2.txt"; }

# ---- 3. --lock-git restores permissions afterwards
setup
mk one todo 1 "paths: [src/api]\n"
before=$(stat -c %a "$T/repo/.git/HEAD")
FAKE_LOG="$T/fake.log" python3 "$ROOT/pai_swarm.py" run --repo "$T/repo" --tasks "$T/vault/Tasks" \
  --task-id t3 --workers 1 --lock-git --claude-bin "$FAKE" > /dev/null 2>&1 || true
after=$(stat -c %a "$T/repo/.git/HEAD")
[ "$before" = "$after" ] && ok "--lock-git restores modes ($after)" || bad "modes $before -> $after"

# ---- 4. Jev triage + verify against a local mock server (checks request shape, not the real API)
setup
mk inbox-good inbox 1 "paths: [src/api]\n"
mk inbox-vague inbox 1 ""
mk verify-me todo 1 "paths: [docs]\n"
python3 "$HERE/mock_jev.py" 8765 "$T/jev_requests.jsonl" & MOCK=$!
sleep 0.5
export TYPESAFE_API_KEY=test TYPESAFE_BASE_URL=http://127.0.0.1:8765
python3 "$ROOT/pai_swarm.py" triage --tasks "$T/vault/Tasks" > "$T/out4.txt"
FAKE_LOG="$T/fake.log" python3 "$ROOT/pai_swarm.py" run --repo "$T/repo" --tasks "$T/vault/Tasks" \
  --task-id t4 --workers 2 --only verify-me --jev-verify --claude-bin "$FAKE" > /dev/null 2>&1 || true
kill $MOCK
unset TYPESAFE_API_KEY TYPESAFE_BASE_URL
grep -q "^status: todo" "$T/vault/Tasks/inbox-good.md" && grep -q "^model: opus" "$T/vault/Tasks/inbox-good.md" \
  && ok "triage: actionable inbox -> todo with model" || { bad "triage good"; cat "$T/vault/Tasks/inbox-good.md"; }
grep -q "^status: needs-detail" "$T/vault/Tasks/inbox-vague.md" && ok "triage: vague -> needs-detail" || bad "triage vague"
grep -q "^jev_done: 0.91" "$T/vault/Tasks/verify-me.md" && ok "verify scores recorded" || { bad "verify"; cat "$T/vault/Tasks/verify-me.md"; }

# ---- 5. older claude without --json-schema: STATUS block in text still works
setup
mk text-only todo 1 "paths: [src/api]\n"
FAKE_NO_SCHEMA=1 FAKE_LOG="$T/fake.log" python3 "$ROOT/pai_swarm.py" run --repo "$T/repo" \
  --tasks "$T/vault/Tasks" --task-id t5 --workers 1 --claude-bin "$FAKE" > /dev/null 2>&1 || true
grep -q "^status: done" "$T/vault/Tasks/text-only.md" && ok "text STATUS fallback" || bad "text fallback"

# ---- 6. pai integration (needs a personal-ai-workbench checkout in $PAI_REPO)
PAI_REPO="${PAI_REPO:-}"
if [ -n "$PAI_REPO" ] && [ -f "$PAI_REPO/bin/pai.py" ]; then
  setup
  mkdir -p "$T/vault/Agent Inbox/PAI-001-MEM" "$T/vault/30 Projects" "$T/vault/Swarm"
  printf -- "# Memory\n\n## Human decisions\n\n- Use tabs not spaces in generated files.\n" > "$T/vault/Agent Inbox/PAI-001-MEM/decisions.md"
  printf -- "---\nproject: demo\n---\n# Demo\n\n## Constraints\n\n- Never touch the payments module.\n" > "$T/vault/30 Projects/Demo.md"
  printf -- "# Agent rules\n\n## Working rules\n\n- Keep functions small.\n" > "$T/repo/AGENTS.md"
  (cd "$T/repo" && git add AGENTS.md && git commit -qm "agent rules" && git push -q origin main)  # a new task needs a clean tree
  rm -f "$T/pai.toml"
  "${PAI_PYTHON:-python3}" "$PAI_REPO/bin/pai.py" --config "$T/pai.toml" init demo --repo "$T/repo" --vault "$T/vault" \
    --memory "Agent Inbox/PAI-001-MEM" --inbox "Agent Inbox" --markdown-root "30 Projects" \
    --project-note "30 Projects/Demo.md" --write > /dev/null
  printf -- "---\nstatus: todo\npaths: [src/api]\ntask_id: SWARM-API\n---\n# Add api helper\n\nAdd a helper to the api module.\n" > "$T/vault/Swarm/api.md"
  printf -- "---\nstatus: todo\npaths: [src/web]\n---\n# Add web helper\n\nAdd a helper to the web module.\n" > "$T/vault/Swarm/web helper.md"
  rm -f "$T/fake.log"
  FAKE_LOG="$T/fake.log" python3 "$ROOT/pai_swarm.py" run --pai demo --pai-bin "$PAI_REPO/bin/pai.py" \
    --pai-config "$T/pai.toml" --tasks "$T/vault/Swarm" --task-id t6 --workers 2 --claude-bin "$FAKE" > "$T/out6.txt" 2>&1 || true
  python3 - "$T/fake.log" <<'EOF' && ok "pai: workers got pai context, subagents denied, schema used" || { bad "pai context"; cat "$T/out6.txt"; }
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
extra = [r for r in rows if r.get("extra")]
assert len(extra) == 2, len(extra)
for r in extra:
    assert r["has_pai_context"] and r["deny_agent"] and r["schema"], r
    p = r["prompt_head"]
    assert "Use tabs not spaces" in p and "Never touch the payments module" in p, p[-2500:]
EOF
  H1=$(ls "$T/vault/Agent Inbox/" | grep -c '^SWARM-API-' || true)
  H2=$(ls "$T/vault/Agent Inbox/" | grep -c '^web-helper-' || true)
  [ "$H1" = 1 ] && [ "$H2" = 1 ] && ok "pai: one draft handoff per task" || { bad "handoff dirs"; ls "$T/vault/Agent Inbox/"; cat "$T/out6.txt"; }
  HF=$(ls -d "$T/vault/Agent Inbox/SWARM-API-"*)/HANDOFF.md
  grep -q "worker-reported: created" "$HF" && grep -q "No commit: HEAD" "$HF" && grep -q "approved: false" "$HF" \
    && ok "pai: handoff states filled, commit observed, still unapproved" || { bad "handoff content"; cat "$HF"; }
  grep -q "Git task t6: remote origin" "$HF" && grep -q "base main @ $(git -C "$T/repo" rev-parse origin/main)" "$HF" \
    && grep -q "task branch swarm/t6, worktree $T/repo" "$HF" \
    && ok "pai: handoff shows remote, base SHA, task branch and worktree" || { bad "handoff git task"; cat "$HF"; }
  "${PAI_PYTHON:-python3}" "$PAI_REPO/bin/pai.py" --config "$T/pai.toml" handoff list demo > "$T/list.txt" 2>&1 || true
  grep -q "SWARM-API" "$T/list.txt" && grep -q "web-helper" "$T/list.txt" && ok "pai: handoff list sees them" || { bad "handoff list"; cat "$T/list.txt"; }
  grep -q "^handoff: " "$T/vault/Swarm/api.md" && ok "pai: note links its handoff" || bad "note handoff link"
else
  echo "SKIP pai integration (set PAI_REPO to a personal-ai-workbench checkout)"
fi

echo
echo "$pass passed, $fail failed"
rm -rf "$T"
[ "$fail" = 0 ]
