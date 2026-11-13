#!/usr/bin/env python3
"""Stand-in for `claude` used by the tests. It behaves like a worker that:
  1. runs the hooks from --settings against forbidden and allowed actions (as Claude Code would),
  2. tries `git commit` through PATH (should hit the shim),
  3. writes a file inside its scope, sleeps, and prints a claude -p style JSON result.
Every observation is appended to $FAKE_LOG as one JSON line."""
import json
import os
import random
import re
import subprocess
import sys
import time

if "--version" in sys.argv:
    print("2.1.322 (Claude Code)")
    sys.exit(0)
if "--help" in sys.argv:
    print("  --permission-prompts <target>\n" + ("" if os.environ.get("FAKE_NO_SCHEMA") else "  --json-schema <schema>\n"))
    sys.exit(0)

argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
settings = json.load(open(argv[argv.index("--settings") + 1]))
task = os.environ["PAI_TASK"]
repo = os.getcwd()
log = {"task": task, "start": time.time(), "argv_flags": [a for a in argv if a.startswith("--")]}


def run_hook(matcher, tool_input, tool_name):
    for entry in settings["hooks"]["PreToolUse"]:
        if re.fullmatch(entry["matcher"], tool_name):
            cmd = entry["hooks"][0]["command"]
            p = subprocess.run(cmd, shell=True, input=json.dumps(
                {"tool_name": tool_name, "tool_input": tool_input, "cwd": repo}),
                capture_output=True, text=True)
            return p.returncode
    return None


log["hook_git_commit"] = run_hook("Bash", {"command": "git commit -am sneaky"}, "Bash")
log["hook_git_status"] = run_hook("Bash", {"command": "git status --short"}, "Bash")
log["hook_sh_c_push"] = run_hook("Bash", {"command": "sh -c 'git push origin main'"}, "Bash")
log["hook_edit_dotgit"] = run_hook("Edit", {"file_path": ".git/config"}, "Edit")
log["hook_edit_outside"] = run_hook("Write", {"file_path": "OTHER_TEAM_FILE.txt"}, "Write")
m = re.search(r"Edit only: (.*)", prompt)
scope = m.group(1).split(", ") if m and "anything" not in m.group(1) else ["."]
target = os.path.join(scope[0], "%s.txt" % task) if scope[0] != "." else "%s.txt" % task
log["hook_edit_inscope"] = run_hook("Write", {"file_path": target}, "Write")

# indirect git through PATH -> shim
p = subprocess.run("git commit --allow-empty -m via-shim", shell=True, capture_output=True, text=True)
log["shim_commit_rc"] = p.returncode
p = subprocess.run("git status --short", shell=True, capture_output=True, text=True)
log["shim_status_rc"] = p.returncode

time.sleep(float(os.environ.get("FAKE_SLEEP") or random.uniform(0.5, 1.5)))
os.makedirs(os.path.dirname(os.path.join(repo, target)) or ".", exist_ok=True)
with open(os.path.join(repo, target), "w") as f:
    f.write("written by %s\n" % task)
log["end"] = time.time()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(log) + "\n")

log["has_pai_context"] = "<<<pai-context" in prompt
log["deny_agent"] = "Agent" in settings["permissions"]["deny"]
log["schema"] = "--json-schema" in argv
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"task": task, "extra": True, "has_pai_context": log["has_pai_context"],
                        "deny_agent": log["deny_agent"], "schema": log["schema"],
                        "prompt_head": prompt[:30000]}) + "\n")

fail = task.endswith("fail")
status = "BLOCKED" if "blocked" in task else "DONE"
out = {
    "type": "result", "is_error": fail, "session_id": "sess-" + task,
    "total_cost_usd": 0.12,
    "result": "Did it.\nSTATUS: %s\nFILES: %s\nCHECKS: pytest -q passed\nNOTES: none" % (status, target),
}
if "--json-schema" in argv and not os.environ.get("FAKE_NO_SCHEMA"):
    out["structured_output"] = {
        "status": status, "summary": "Wrote %s for %s" % (target, task), "files_changed": [target],
        "implementation": "created %s" % target, "validation": "pytest -q passed (3 tests)",
        "evidence": ["pytest -q: 3 passed"], "blockers": [], "next_steps": ["human review"],
        "proposals": ["Document %s in the project note" % task],
    }
print(json.dumps(out))
sys.exit(1 if fail else 0)
