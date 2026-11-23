#!/usr/bin/env python3
"""Real check of Claude Code's instruction files for the L2 = CLAUDE.local.md design (R6-PRM-064).

For the mastermind / owner, not for workers. Each case builds a throwaway git repo with a linked worktree,
writes instruction files that carry a random code word, asks headless `claude -p` "what is the code word in
the project instructions?", and records whether the answer contains it:

  local-only        CLAUDE.local.md has the word, no CLAUDE.md          -> expect FOUND (L2 works)
  agents-shadowed   CLAUDE.md exists (no import), AGENTS.md has the word -> expect NOT FOUND (docs: AGENTS.md skipped)
  agents-imported   CLAUDE.md is "@AGENTS.md", AGENTS.md has the word    -> expect FOUND (L0 via import)
  local-plus-claude CLAUDE.md exists, CLAUDE.local.md has the word       -> expect FOUND (L2 next to L0)

    python3 -B verify_claude_local.py --dry-run          # fake claude modelled on the docs; no network
    python3 -B verify_claude_local.py --out DIR          # real: claude -p, model haiku, stop at $0.20 total

Real runs read the key from the environment as claude does (ANTHROPIC_API_KEY, and ANTHROPIC_CUSTOM_HEADERS
for an org key that needs anthropic-workspace-id); nothing is printed or stored but RESULT.json. A case is
skipped once the recorded cost reaches --budget. Output: DIR/RESULT.json (format pai-verify-claude-local/1).
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

QUESTION = "What is the code word written in this project's instruction files? Answer with the code word only."
CASES = [
    ("local-only", {"CLAUDE.local.md": "# Lane\n\nThe code word is {w}.\n"}, True),
    ("agents-shadowed", {"CLAUDE.md": "# Project\n\nNo code word here.\n", "AGENTS.md": "# Agents\n\nThe code word is {w}.\n"}, False),
    ("agents-imported", {"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "# Agents\n\nThe code word is {w}.\n"}, True),
    ("local-plus-claude", {"CLAUDE.md": "# Project\n\nNo code word here.\n", "CLAUDE.local.md": "The code word is {w}.\n"}, True),
]
FAKE = r'''#!/usr/bin/env python3
# fake claude -p: models the documented loading rules (code.claude.com/docs/en/memory, 2026-11-22)
import json, os, re
def read(n):
    return open(n, encoding="utf-8").read() if os.path.exists(n) else ""
text = read("CLAUDE.md") + read("CLAUDE.local.md")
if not (os.path.exists("CLAUDE.md") or os.path.exists("CLAUDE.local.md")):
    text += read("AGENTS.md")
for m in re.findall(r"^@(\S+)", text, re.M):
    text += read(m)
w = re.search(r"code word is (\S+?)\.", text)
print(json.dumps({"type": "result", "result": w.group(1) if w else "I don't see a code word.", "total_cost_usd": 0.0}))
'''


def sh(args, cwd, env=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env, timeout=300)


def make_case(root, files, word):
    repo = os.path.join(root, "repo")
    os.makedirs(repo)
    env = dict(os.environ, GIT_AUTHOR_NAME="pai-verify", GIT_AUTHOR_EMAIL="pai-verify@example.invalid",
               GIT_COMMITTER_NAME="pai-verify", GIT_COMMITTER_EMAIL="pai-verify@example.invalid")
    sh(["git", "init", "-q", "-b", "main"], repo, env)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("# scratch\n")
    with open(os.path.join(repo, ".gitignore"), "w") as f:
        f.write("CLAUDE.local.md\n")
    for n, body in files.items():
        if n != "CLAUDE.local.md":
            with open(os.path.join(repo, n), "w") as f:
                f.write(body.format(w=word))
    sh(["git", "add", "-A"], repo, env)
    sh(["git", "commit", "-qm", "scratch"], repo, env)
    wt = os.path.join(root, "lane")
    sh(["git", "worktree", "add", "-q", "-b", "lane", wt], repo, env)
    if "CLAUDE.local.md" in files:  # git-ignored: exists only in this worktree, as the docs say
        with open(os.path.join(wt, "CLAUDE.local.md"), "w") as f:
            f.write(files["CLAUDE.local.md"].format(w=word))
    return wt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="use the fake claude (no network, no cost)")
    ap.add_argument("--claude", default=os.environ.get("CLAUDE_BIN", "claude"))
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--budget", type=float, default=0.20, help="stop starting cases at this total cost (USD)")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="pai-verify-local-")
    claude = a.claude
    if a.dry_run:
        claude = os.path.join(tmp, "fake-claude")
        with open(claude, "w") as f:
            f.write(FAKE)
        os.chmod(claude, 0o755)
    version = sh([claude, "--version"], tmp).stdout.strip() if not a.dry_run else "fake (dry run)"
    results, spent = [], 0.0
    try:
        for name, files, expect in CASES:
            if spent >= a.budget:
                results.append({"case": name, "skipped": "budget %.2f reached" % a.budget})
                continue
            word = "PAI-" + secrets.token_hex(4).upper()
            wt = make_case(os.path.join(tmp, name), files, word)
            cmd = [claude, "-p", QUESTION, "--output-format", "json", "--max-turns", "2"]
            if not a.dry_run:
                cmd += ["--model", a.model]
            p = sh(cmd, wt)
            try:
                out = json.loads(p.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                out = {}
            cost = float(out.get("total_cost_usd") or out.get("cost_usd") or 0.0)
            spent += cost
            found = word in str(out.get("result", ""))
            results.append({"case": name, "files": sorted(files), "expect_found": expect, "found": found,
                            "pass": found == expect, "exit": p.returncode, "cost_usd": round(cost, 5),
                            "stderr_tail": p.stderr.strip()[-200:]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res = {"format": "pai-verify-claude-local/1", "mode": "dry-run" if a.dry_run else "real", "claude": version,
           "model": None if a.dry_run else a.model, "total_cost_usd": round(spent, 5), "cases": results,
           "all_pass": all(r.get("pass") for r in results if "skipped" not in r)}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "RESULT.json"), "w") as f:
        json.dump(res, f, indent=2)
    for r in results:
        print("%-18s %s" % (r["case"], r.get("skipped") or ("PASS" if r["pass"] else "FAIL") + " (found=%s, expected=%s)" % (r["found"], r["expect_found"])))
    print("mode %s, total cost $%.4f -> %s" % (res["mode"], spent, os.path.join(a.out, "RESULT.json")))
    return 0 if res["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
