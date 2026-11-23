"""pai round integrate: put every lane's work together on a scratch copy of the base, and say what
breaks, before anything is merged.

    pai round integrate [ROUND.json] --plan [--order L1,L2,...] [--proposals all|none] [--test] [--write]

--plan
  For each lane, in --order (default: ROUND.json's lane order):
    patch       the newest checkpoint folder's cumulative.patch. A checkpoint without one (some
                lanes seal only HANDOFF.md) falls back to the lane's worktree: `git diff BASE` plus
                its untracked files, read-only, labelled `worktree` (not sealed)
    proposals   then every proposals/*.patch of every lane, lane by lane (--proposals none skips them)
  The base is exported (`git archive BASE`, no repository, nothing to commit) into
  <work_root>/integrate/<UTC time>/tree, and each patch is applied there with `git apply`. A patch
  that does not apply cleanly is applied with --reject: what applies stays, each rejected hunk is
  a CONFLICT (kept under .../rejects/), and integration continues so every conflict is seen at once.
  Exception: release/allowlist.txt is a set of lines (build_bundle refuses duplicates and missing
  files). A rejected change to it is merged as a union: each added line that is not there yet and
  names a file present in the merged copy is added; lines naming files that are not in the copy are
  skipped and listed; a change that removes lines stays a CONFLICT. Reported as `ok (line union)`.
  A rejected patch whose result for every rejected file equals the merged copy (a proposal written
  "base -> both lanes' changes") is `ok (already in the merged copy)`, not a conflict.
  Hot files: every path two or more patches touch, the known hot spots (pai/launch.py, pai/cli.py,
  swarm/workbench.py, release/allowlist.txt, pyproject.toml) first.
  Open dependencies: the Blockers section of each lane's newest HANDOFF.md and the lines of its
  NEXT-PR.md that name a precondition (前提 / 依赖 / depends / requires / after).
--test
  On the merged copy: `tools/verify.py` (which runs tests/ and swarm/tests strictly, and the rest
  this machine can run), with a test-only git identity (example.invalid). The summary and exit
  code are kept; the full logs stay in .../verify/.
--write
  INTEGRATION.md (a managed note, kind integration/1) and INTEGRATION.json in the round's state
  folder: patch sha256s, order, conflicts, hot files, test results, open dependencies, and a merge
  plan DRAFT built from each lane's NEXT-PR.md (lanes in order, their commits and checks), for the
  owner to approve. Nothing is pushed, committed or merged by this command.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import time

import gitprep
import round as rnd
import round_status as rs

LINE_SETS = ("release/allowlist.txt",)  # files that are a set of lines: a conflict is merged as a union
HOT = ("pai/launch.py", "pai/cli.py", "swarm/workbench.py", "release/allowlist.txt", "pyproject.toml")
DEP_RE = re.compile(r"前提|依赖|等 ?core|等 ?docs|等 ?prompts|depends|requires|\bafter\b|blocked", re.I)
IDENT = {"GIT_AUTHOR_NAME": "PAI Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
         "GIT_COMMITTER_NAME": "PAI Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}


def checkpoints(handoff_dir):
    """Checkpoint folders, newest first (cp-* and final)."""
    try:
        cps = [e for e in os.scandir(handoff_dir) if e.is_dir() and (e.name.startswith("cp-") or e.name == "final")]
    except OSError:
        return []
    return [e.path for e in sorted(cps, key=lambda e: (e.stat().st_mtime, e.name), reverse=True)]


def worktree_patch(ln, base, dest):
    """`git diff BASE` of the lane's worktree plus its untracked files; read-only."""
    wt = ln["worktree"]
    if not os.path.isdir(wt):
        return None
    parts = []
    p = gitprep.git(wt, "diff", "--binary", base, "--", ".")
    if p.returncode != 0:
        return None
    parts.append(p.stdout)
    others = gitprep.out(wt, "ls-files", "--others", "--exclude-standard", "-z")
    for f in sorted(x for x in (others or "").split("\0") if x):
        if "__pycache__" in f.split("/"):
            continue
        q = gitprep.git(wt, "diff", "--no-index", "--binary", "/dev/null", f)
        parts.append(q.stdout)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))
    return dest


def lane_patch(r, ln, scratch):
    """{"lane", "source", "path", "sha256", "checkpoint"} or None when there is nothing to apply."""
    cps = checkpoints(ln.get("handoff_dir") or "")
    newest = cps[0] if cps else None
    if newest and os.path.isfile(os.path.join(newest, "cumulative.patch")):
        path = os.path.join(newest, "cumulative.patch")
        return {"lane": ln["name"], "source": "checkpoint", "checkpoint": os.path.basename(newest), "path": path,
                "sha256": rnd.sha256_file(path)}
    dest = os.path.join(scratch, "patches", "%s-worktree.patch" % ln["name"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if worktree_patch(ln, r["base_sha"], dest) and os.path.getsize(dest) > 0:  # an unchanged worktree has nothing to apply
        return {"lane": ln["name"], "source": "worktree (the newest checkpoint %s has no cumulative.patch)" % (
            os.path.basename(newest) if newest else "-"), "checkpoint": os.path.basename(newest) if newest else None,
            "path": dest, "sha256": rnd.sha256_file(dest)}
    return None


def touched(patch_path):
    """Paths a patch touches (git or plain unified diff headers)."""
    out = set()
    with open(patch_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"^diff --git a/(\S+) b/(\S+)", line)
            if m:
                out.add(m.group(2) if m.group(2) != "/dev/null" else m.group(1))
                continue
            m = re.match(r"^\+\+\+ (?:b/)?(\S+)", line)
            if m and m.group(1) != "/dev/null":
                out.add(m.group(1))
    return out


def _git_env(tree):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(tree)  # never find a repository above the copy
    return env


def same_as_proposed(tree, base_tree, rel, patch_path):
    """True when the merged copy's `rel` is exactly what the patch makes of the base's `rel`
    (a proposal written against the base that the lanes already applied, e.g. "base -> both")."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(base_tree, rel)
        dst = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isfile(src):
            shutil.copy(src, dst)
        p = subprocess.run([gitprep.git_bin(), "apply", "--whitespace=nowarn", "--include=" + rel, patch_path], cwd=tmp,
                           env=_git_env(tmp), capture_output=True, text=True)
        if p.returncode != 0 or not os.path.isfile(dst) or not os.path.isfile(os.path.join(tree, rel)):
            return False
        with open(dst, "rb") as a, open(os.path.join(tree, rel), "rb") as b:
            return a.read() == b.read()


def apply_patch(tree, item, rejects_dir, base_tree=None):
    """Apply one patch to the copy: ok | CONFLICT (with rejected files) | FAILED (nothing applied)."""
    env = _git_env(tree)
    base = [gitprep.git_bin(), "apply", "--whitespace=nowarn"]
    chk = subprocess.run(base + ["--check", item["path"]], cwd=tree, env=env, capture_output=True, text=True)
    if chk.returncode == 0:
        p = subprocess.run(base + [item["path"]], cwd=tree, env=env, capture_output=True, text=True)
        return {"result": "ok" if p.returncode == 0 else "FAILED", "detail": p.stderr.strip()[-500:]}
    p = subprocess.run(base + ["--reject", item["path"]], cwd=tree, env=env, capture_output=True, text=True)
    rej = sorted(glob.glob(os.path.join(glob.escape(tree), "**", "*.rej"), recursive=True))
    kept = []
    for f in rej:
        rel = os.path.relpath(f, tree)
        dest = os.path.join(rejects_dir, "%s__%s" % (item["name"], rel.replace(os.sep, "__")))
        os.makedirs(rejects_dir, exist_ok=True)
        shutil.move(f, dest)
        kept.append(rel[:-4])
    msgs = [l for l in (chk.stderr or "").splitlines() if l.startswith("error:")]
    if base_tree and kept and all(same_as_proposed(tree, base_tree, rel, item["path"]) for rel in kept) and \
            not [m for m in msgs if "already exists" in m]:
        return {"result": "ok (already in the merged copy)", "files": kept,
                "detail": "the patch's result for %s equals the merged copy" % ", ".join(kept)}
    unions = {}
    for rel in list(kept) + [m.split(": ")[1] for m in msgs if "already exists in working directory" in m]:
        if rel in LINE_SETS and rel not in unions:
            unions[rel] = union_merge(tree, rel, item["path"])
    if unions and set(kept) <= set(unions) and not any(u["removals"] for u in unions.values()) and \
            all(m.split(": ")[1] in unions for m in msgs if "already exists" in m):
        return {"result": "ok (line union)", "files": sorted(unions), "union": unions,
                "detail": "; ".join("%s: +%d, already there %d, skipped (file not in the copy) %d" % (
                    k, len(u["added"]), len(u["present"]), len(u["missing_file"])) for k, u in unions.items())}
    return {"result": "CONFLICT" if kept or p.returncode != 0 else "ok", "files": kept, "detail": "; ".join(msgs[:6])[-800:],
            "union": unions or None}


def patch_lines(patch_path, rel):
    """(added, removed) lines of one file's hunks in a patch."""
    added, removed, on = [], [], False
    with open(patch_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("diff --git ") or line.startswith("--- "):
                on = False if line.startswith("diff --git ") else on
                continue
            if line.startswith("+++ "):
                on = line[4:].split("\t")[0] in ("b/" + rel, rel)
                continue
            if on and line.startswith("+"):
                added.append(line[1:])
            elif on and line.startswith("-"):
                removed.append(line[1:])
    return added, removed


def union_merge(tree, rel, patch_path):
    """Merge a rejected change to a line-set file: add each added line that is missing (and whose
    path exists in the merged copy). Returns {"added", "present", "missing_file", "removals"}."""
    path = os.path.join(tree, rel)
    have = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            have = f.read().splitlines()
    added, removed = patch_lines(patch_path, rel)
    res = {"added": [], "present": [], "missing_file": [], "removals": [l for l in removed if l not in added]}
    for l in added:
        t = l.strip()
        if not t or t.startswith("#"):
            continue
        if t in have:
            res["present"].append(t)
        elif not os.path.exists(os.path.join(tree, t)):
            res["missing_file"].append(t)
        else:
            have.append(t)
            res["added"].append(t)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(have) + "\n")
    return res


def section(text, title_re):
    m = re.search(r"^##+\s*(?:%s).*?$(.*?)(?=^##\s|\Z)" % title_re, text, re.M | re.S)
    return m.group(1).strip() if m else ""


def dependencies(ln):
    out = []
    cps = checkpoints(ln.get("handoff_dir") or "")
    if not cps:
        return out
    for name, rx in (("HANDOFF.md", r"Blockers"), ("NEXT-PR.md", None)):
        p = os.path.join(cps[0], name)
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        lines = section(text, rx).splitlines() if rx else [l for l in text.splitlines() if DEP_RE.search(l)]
        for l in lines:
            l = l.strip(" -*")
            if l and l not in ("无", "无。", "none", "None") and not l.startswith("|---"):
                out.append({"lane": ln["name"], "from": "%s/%s" % (os.path.basename(cps[0]), name), "text": l[:240]})
    return out


def pr_plan(r, order):
    plan = []
    for name in order:
        ln = rnd.lane(r, name)
        cps = checkpoints(ln.get("handoff_dir") or "")
        np = os.path.join(cps[0], "NEXT-PR.md") if cps else None
        if not np or not os.path.isfile(np):
            plan.append({"lane": name, "branch": ln["branch"], "next_pr": None, "commits": [], "checks": []})
            continue
        with open(np, encoding="utf-8", errors="replace") as f:
            text = f.read()
        commits = re.findall(r"^\s*\d+\.\s+`([^`]+)`", text, re.M) or \
            re.findall(r"^\s*(?:[-*]|\d+\.)\s*`?((?:feat|fix|docs|test|chore|refactor)(?:\([^)]*\))?!?:[^`\n]+)", text, re.M)
        checks = [l.strip(" -*") for l in section(text, r"合并前|Before merg|Checks").splitlines() if l.strip(" -*")]
        plan.append({"lane": name, "branch": ln["branch"], "next_pr": np, "next_pr_sha256": rnd.sha256_file(np),
                     "commits": commits[:20], "checks": checks[:8]})
    return plan


def run_tests(tree, out_dir, timeout=3600):
    env = dict(_git_env(tree), **IDENT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    work = os.path.join(out_dir, "verify")
    t0 = time.time()
    try:
        p = subprocess.run(["python3", "-B", "tools/verify.py", "--work", work], cwd=tree, env=env, capture_output=True,
                           text=True, timeout=timeout)
        code, text = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        code, text = None, "timed out after %ds" % timeout
    with open(os.path.join(out_dir, "verify.out"), "w", encoding="utf-8") as f:
        f.write(text)
    steps = {}
    try:
        with open(os.path.join(work, "verify-report.json"), encoding="utf-8") as f:
            rep = json.load(f)
        for s in rep.get("steps") or []:  # verify-report.json: [{"step", "result", "detail", "seconds", "ci"}]
            name = isinstance(s, dict) and (s.get("step") or s.get("name"))
            if name:
                steps[name] = {"status": s.get("result") or s.get("status"), "seconds": s.get("seconds"),
                               "summary": s.get("detail") or s.get("summary") or s.get("reason")}
    except (OSError, ValueError):
        pass
    tail = [l for l in text.splitlines() if re.search(r"\b(PASS|FAIL|SKIPPED|passed|failed|skipped)\b", l)][-25:]
    return {"exit": code, "seconds": round(time.time() - t0), "steps": steps, "summary": tail}


def integrate(r, order=None, proposals="all", test=False, log=print):
    names = [l["name"] for l in r["lanes"]]
    order = order or names
    for n in order:
        rnd.lane(r, n)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    import tempfile
    os.makedirs(os.path.join(r["work_root"], "integrate"), exist_ok=True)
    out_dir = tempfile.mkdtemp(prefix=stamp + "-", dir=os.path.join(r["work_root"], "integrate"))  # two runs never share one
    tree = os.path.join(out_dir, "tree")
    os.makedirs(tree)
    arch = subprocess.run([gitprep.git_bin(), "-C", r["repo"], "archive", "--format=tar", r["base_sha"]], capture_output=True)
    if arch.returncode != 0:
        raise rnd.RoundError("git archive %s failed: %s" % (r["base_sha"][:12], arch.stderr.decode()[-300:]))
    subprocess.run(["tar", "-x", "-C", tree], input=arch.stdout, check=True)
    base_tree = os.path.join(out_dir, "base")  # pristine, to tell "already applied" from a real conflict
    os.makedirs(base_tree)
    subprocess.run(["tar", "-x", "-C", base_tree], input=arch.stdout, check=True)
    log("pai round integrate %s: base %s exported to %s" % (r["round"], r["base_sha"][:12], tree))
    items = []
    for n in order:
        it = lane_patch(r, rnd.lane(r, n), out_dir)
        if it:
            items.append(dict(it, kind="lane", name="%s-lane" % n))
        else:
            log("  %-10s nothing to apply (no checkpoint patch, no worktree changes)" % n)
    if proposals != "none":
        for n in order:
            pdir = os.path.join(rnd.lane(r, n).get("handoff_dir") or "", "proposals")
            for p in sorted(glob.glob(os.path.join(glob.escape(pdir), "*.patch"))):
                items.append({"lane": n, "kind": "proposal", "name": "%s-%s" % (n, os.path.basename(p)[:-6]),
                              "source": "proposals/" + os.path.basename(p), "path": p, "sha256": rnd.sha256_file(p)})
    seen = {}
    for it in items:
        it["paths"] = sorted(touched(it["path"]))
        for pth in it["paths"]:
            seen.setdefault(pth, []).append(it["name"])
        res = apply_patch(tree, it, os.path.join(out_dir, "rejects"), base_tree)
        it.update(res)
        log("  %-9s %-40s %s%s" % (it["result"], it["name"], it["sha256"][:12],
                                   "  (%s)" % ", ".join(res.get("files") or []) if res.get("files") else ""))
    hot = sorted(((p, ns) for p, ns in seen.items() if len(ns) > 1 or p in HOT), key=lambda x: (x[0] not in HOT, x[0]))
    deps = [d for n in order for d in dependencies(rnd.lane(r, n))]
    report = {"format": "pai-integration", "version": 1, "round": r["round"], "base_sha": r["base_sha"], "at": rnd.now(),
              "order": order, "proposals": proposals, "tree": tree,
              "patches": [{k: it.get(k) for k in ("name", "lane", "kind", "source", "checkpoint", "sha256", "result", "files", "union",
                                                  "detail", "paths")} for it in items],
              "conflicts": [{"patch": it["name"], "files": it.get("files") or [], "detail": it.get("detail")}
                            for it in items if not it["result"].startswith("ok")],
              "unions": [{"patch": it["name"], "detail": it.get("detail"), "union": it.get("union")}
                         for it in items if it["result"] == "ok (line union)"],
              "contained": [{"patch": it["name"], "files": it.get("files")} for it in items
                            if it["result"] == "ok (already in the merged copy)"],
              "hot_files": [{"path": p, "patches": ns} for p, ns in hot], "dependencies": deps, "pr_plan": pr_plan(r, order),
              "test": None}
    leftover = [p for p in glob.glob(os.path.join(glob.escape(tree), "**", "__pycache__"), recursive=True)]
    if test:
        log("  running tools/verify.py on the merged copy (this takes a while)")
        report["test"] = run_tests(tree, out_dir)
        log("  verify exit %s in %ss%s" % (report["test"]["exit"], report["test"]["seconds"], "  (WARNING: %d conflict(s) were "
            "applied partially; failures may come from that)" % len(report["conflicts"]) if report["conflicts"] else ""))
    report["pycache_before_tests"] = len(leftover)
    with open(os.path.join(out_dir, "INTEGRATION.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    return report


def render(r, rep):
    """The INTEGRATION.md body (between managed markers)."""
    import round_vault as rv
    c = rv.cell
    L = ["", rv.BEGIN, "# %s 集成预演（%s）" % (r["round"], rep["at"]), "",
         "PAI 生成（`pai round integrate`）。在 base `%s` 的导出副本上按顺序打补丁，只是预演：没有 commit，没有 push，没有合并。" % rep["base_sha"][:12],
         "副本：`%s`" % rep["tree"], "", "## 补丁（按打的顺序）", "", "| # | 补丁 | 来源 | sha256 | 结果 |", "|---|---|---|---|---|"]
    for i, p in enumerate(rep["patches"], 1):
        L.append("| %d | %s | %s | `%s` | %s |" % (i, c(p["name"]), c(p["source"]), p["sha256"], c(p["result"] + (
            "：" + "、".join(p.get("files") or []) if p.get("files") else ""))))
    L += ["", "## 冲突（%d）" % len(rep["conflicts"]), ""]
    L += ["- **%s**：%s%s" % (x["patch"], "、".join(x["files"]) or "（整份没打上）", "；" + c(x["detail"]) if x.get("detail") else "")
          for x in rep["conflicts"]] or ["（没有）"]
    L += ["", "## 自动合并（行集合文件，取并集）", ""]
    L += ["- **%s**：%s%s" % (x["patch"], c(x["detail"]), "；跳过：" + "、".join(
        v for u in (x["union"] or {}).values() for v in u["missing_file"]) if any(
        u["missing_file"] for u in (x["union"] or {}).values()) else "") for x in rep.get("unions", [])] or ["（没有）"]
    L += ["", "## 已经包含（补丁的结果和合并后的副本一致）", ""]
    L += ["- **%s**：%s" % (x["patch"], "、".join(x["files"] or [])) for x in rep.get("contained", [])] or ["（没有）"]
    L += ["", "## 热点文件", "", "| 文件 | 改它的补丁 |", "|---|---|"]
    L += ["| %s%s | %s |" % (c(h["path"]), "（热点）" if h["path"] in HOT else "", c("、".join(h["patches"]))) for h in rep["hot_files"]] \
        or ["| （没有） | |"]
    t = rep.get("test")
    L += ["", "## 测试（合并后的副本）", ""]
    if t:
        L += ["`tools/verify.py` 退出码 **%s**，%s 秒。" % (t["exit"], t["seconds"])]
        if rep["conflicts"]:
            L += ["", "> [!warning] 副本里还有 %d 个冲突：有冲突的补丁只打上了一部分（--reject），测试失败可能只是部分打上造成的，"
                  "不代表合并后的真实结果。先解决冲突，或者换一个不含冲突补丁的 --order 再测。" % len(rep["conflicts"])]
        L += ["", "| 步骤 | 结果 | 说明 |", "|---|---|---|"]
        L += ["| %s | %s | %s |" % (c(k), c(v.get("status")), c(v.get("summary") or v.get("reason") or "")) for k, v in t["steps"].items()] \
            or ["| （verify-report.json 读不到） | | |"]
        if t["summary"]:
            L += ["", "```"] + t["summary"][-12:] + ["```"]
    else:
        L.append("没跑（加 `--test`）。")
    L += ["", "## 还没解决的依赖", ""]
    L += ["- %s（%s）：%s" % (d["lane"], d["from"], c(d["text"])) for d in rep["dependencies"]] or ["（没有）"]
    L += ["", "## 合并计划（草稿，等 owner 批准；PAI 不会 push）", ""]
    for i, p in enumerate(rep["pr_plan"], 1):
        L.append("%d. **%s**：分支 `%s`，%s" % (i, p["lane"], p["branch"], "NEXT-PR sha `%s`" % p["next_pr_sha256"][:12]
                                                if p.get("next_pr_sha256") else "没有 NEXT-PR.md"))
        L += ["   - commit：`%s`" % x for x in p["commits"]]
        L += ["   - 合并前：%s" % x for x in p["checks"][:4]]
    L += ["", "批准后的执行方式：每条 lane 用 `pai round prplan LANE --commit N --out PLAN.json` 预览，再由 owner 运行 "
          "`pai worktree publish PLAN.json --expect-plan SHA --yes --draft`。", rv.END, ""]
    return "\n".join(L)


def write(r, rep, log=print):
    import round_vault as rv
    rnd.write_atomic(os.path.join(r["state_dir"], "INTEGRATION.json"), json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    fm = {"round": r["round"], "base": r["base_sha"], "order": rep["order"], "patches": len(rep["patches"]),
          "conflicts": len(rep["conflicts"]), "hot_files": len(rep["hot_files"]),
          "test_exit": (rep.get("test") or {}).get("exit"), "integrated_at": rep["at"]}
    return rv.write_note(os.path.join(r["state_dir"], "INTEGRATION.md"), "integration/1", fm, render(r, rep), log)


def main(a, r, out):
    log = lambda s: print(s, file=out, flush=True)  # noqa: E731
    if not (a.plan or a.test):
        raise rnd.RoundError("give --plan (and --test to run the checks on the merged copy)")
    order = [x.strip() for x in a.order.split(",")] if a.order else None
    rep = integrate(r, order, a.proposals, a.test, log)
    log("  %d patch(es), %d conflict(s), %d hot file(s), %d open dependenc(ies)" % (
        len(rep["patches"]), len(rep["conflicts"]), len(rep["hot_files"]), len(rep["dependencies"])))
    if a.write:
        log("  INTEGRATION.md: %s" % write(r, rep, log))
    else:
        log("  (add --write for INTEGRATION.md / INTEGRATION.json in %s)" % r["state_dir"])
    return 1 if rep["conflicts"] or (rep.get("test") and rep["test"]["exit"] != 0) else 0
