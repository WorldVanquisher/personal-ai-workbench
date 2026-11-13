# pai-swarm

让 N 个 Claude Code worker 同时改同一个 repo，任务来自 Obsidian 里的笔记，**worker 一律不能动 git**，最后由你自己 review 和 commit。可选接 Jev（TypeSafe 的判断模型）做任务分诊和结果验收。

只用 Python 标准库，Python 3.9+（它调用的 PAI CLI 需要 3.11+；python3 更旧时用 PAI_PYTHON 指定解释器）。worker 跑的是普通的 `claude -p`，用你自己的 Claude 登录，所以吃的是订阅额度。

## 快速开始

```bash
# 1. 在 Obsidian vault 里放任务（会写一个示例笔记）
python3 pai_swarm.py init --tasks ~/Vault/PAI/Tasks

# 2. 先看计划，不真跑
python3 pai_swarm.py run --repo ~/code/myapp --tasks ~/Vault/PAI/Tasks --task-id T1 --workers 4 --dry-run

# 3. 4 个 worker 并行，这次最多处理 10 个任务
python3 pai_swarm.py run --repo ~/code/myapp --tasks ~/Vault/PAI/Tasks --task-id T1 --workers 4 --limit 10

# 4. 看状态 / review / 自己 commit
python3 pai_swarm.py status --tasks ~/Vault/PAI/Tasks
git -C ~/code/myapp diff
```

`--workers` 是同时开几个 Claude，`--limit` 是这次处理几份任务。

`--task-id` 必填：新的 id 会先 `git fetch origin main`，从抓到的提交开一个新分支（默认 `swarm/<id>`）再启动 worker；工作区不干净、fetch 失败、分支已存在都会直接拒绝，一个 worker 也不启动。已经跑过的 id 是 resume：不 fetch，继续用记录下来的分支和基线，未提交的改动原样保留。

Remote Control worker（tmux 里每个 worker 一个窗口、一个 worktree、一个分支）、`doctor` / `setup`、review 检查、`pr plan` / `pr open`、发布前的 AI 署名检查，见 [RC.md](RC.md)。

## 接入 pai（personal-ai-workbench）

加 `--pai 项目名`，每个 worker 就按 pai 的规矩干活：

```bash
export PAI_BIN=~/code/personal-ai-workbench/bin/pai.py
python3 pai_swarm.py run --pai flh --tasks ~/Vault/PAI/Swarm --task-id FLH-131 --workers 4 --limit 10
```

- **开工前**：对每个任务跑 `pai context 项目 --task "<标题+第一段>" --handoffs 3`，把输出（human decisions、constraints、之前的 handoff……）放进 worker 的提示里。`--repo` 不用写，直接取 pai 配置里的 repo。context 失败（比如 budget 太小）的任务不会启动，直接标 `failed` 并写明原因。
- **干活时**：worker 必须用结构化格式交报告（`--json-schema`），不能自己跑 `pai handoff new`，也不能开 subagent（按 SESSION_START 的规矩，deny 了 `Agent` 工具）。
- **收工后**：编排器用报告调用 `pai handoff new`，每个任务恰好生成一份 draft，然后把路径写回任务笔记的 `handoff:`。
  - implementation / validation 标成 `worker-reported:`，表示这是 worker 自己说的，不算核实过。
  - commit 这一栏是编排器自己用只读的 `git rev-parse HEAD` 前后对比，看到什么写什么。
  - merge 和 deployment 保持 UNKNOWN。
  - draft 仍然是 `approved: false`。

任务文件夹别放在 pai 的 Inbox 里面，否则 `handoff list` 会把它当成缺少 `HANDOFF.md` 的任务目录报出来。可以放在 `Vault/PAI/Swarm/` 这样的位置。笔记里可以写 `task_id: FLH-120`；不写的话就用文件名。

**放进 pai 仓库的位置**：放在仓库根目录的 `swarm/` 文件夹，不要放进 `pai/`。原因有两个：
- `pai/` 有一个测试，规定运行时不能 import `subprocess` 或网络库。pai-swarm 两样都用，而且是把 pai 当外部程序调用的。
- `release/allowlist.txt` 里没有 `swarm/`，所以它不会进发布包。

已验证：加上 `swarm/` 之后，pai 自己的 91 个测试全部通过，build_bundle 生成的包里也没有 swarm。

**要你决定的一点**：pai 的 SESSION_START.md 现在写着 "One session, one agent: do not start … other coding agents"。用 swarm 就是改变这条规矩，建议在 SESSION_START 或 README 里补一句 swarm 模式的例外和边界，比如：每个 worker 仍然是单会话、不开 subagent、不碰 git。

## 任务笔记格式

每个任务是一个 `.md`，放在任务文件夹里（`_` 开头的文件和子文件夹会被跳过）：

```markdown
---
status: todo                  # inbox | todo | running | done | review | failed | needs-detail
priority: 2                   # 大的先跑
paths: [src/auth, tests/auth] # 这个任务只能改这些路径
model: sonnet                 # haiku / sonnet / opus，不写就用 --model
---
# 给登录加限流

具体要求写在这里，worker 会原样读到。
```

- **`paths` 很重要**：两个任务的 paths 有重叠（比如 `src` 和 `src/api`）就不会同时跑；worker 改 paths 以外的文件会被 hook 拦下。不写 `paths` = 独占整个 repo，它会等其他 worker 都结束才跑。
- 跑完后笔记会自动更新：`status`、`session`、`cost_usd`，并在末尾追加 worker 的报告和一行 `claude --resume <id>`，你可以接着那个会话继续聊。
- 每次运行在 `任务文件夹/_runs/` 生成一份汇总笔记（Obsidian 里能直接点 `[[任务名]]` 跳转）。
- 结果判定：worker 报告 `STATUS: DONE` → `done`；`BLOCKED/PARTIAL` → `review`；出错或超时 → `failed`；Ctrl-C → 回到 `todo`。

## worker 怎么被挡住不碰 git

一共四层，前两层默认开：

1. **PreToolUse hook（`guard.py bash`）**：解析 worker 要跑的每条 shell 命令，包括 `sh -c "..."`、`$(...)`、反引号、`/usr/bin/git`、`git -C . push`、`FOO=1 git commit`。只放行只读的 `status/diff/log/show/blame/grep` 等；`-c` 这类能执行任意程序的 git 参数一律拦；`gh` 全拦；改动 `.git` 目录的命令也拦。
2. **PATH 里的 git 假身（shim）**：worker 的 PATH 最前面放了一个 `git`，脚本、`npm run xxx` 间接调用 git 也会被同一套规则检查。
3. **权限 deny 规则**：`Bash(git commit *)` 等，给 Claude 一个早一点、清楚一点的拒绝。
4. **`--lock-git`（可选）**：运行期间把 `.git` 设成只读，结束后恢复原权限。如果程序崩了，跑 `python3 pai_swarm.py unlock-git --repo ...`。

另外 `guard.py edit` 会拦下：写进 `.git`、写到 repo 外面（`/tmp` 除外）、写到任务 `paths` 以外。

已知漏洞，诚实说明：脚本里用绝对路径 `/usr/bin/git` 调 git 时，第 1 层看不到、第 2 层也绕过了，只有第 4 层能挡；而第 4 层对 root 用户无效。macOS 上如果你的 shell 配置把 Homebrew 路径插到 PATH 最前面，第 2 层也可能失效。默认只放行常见的测试/构建命令（`npm test`、`pytest`、`go test` 等），用 `--allow-bash "uv run *,make e2e *"` 加更多；`--bash-all` 放开全部 shell（git 仍然拦）。

## 开多少个 worker

- 所有 worker 共用你订阅的用量上限，开得越多，额度烧得越快——这正好是“别浪费会员”的用法。
- 官方对 agent teams 的建议是从 3–5 个开始；超过之后协调成本上升、收益递减。这里的 worker 互不通信，只要任务的 `paths` 分得开，可以再多开一些，遇到限流报错就往回调。
- 先用 `--model haiku` 跑简单任务、`opus` 跑难的，同样额度能多干活。

## Jev（可选）

需要 `TYPESAFE_API_KEY`（在 console.typesafe.ai 申请）。Jev 不写文字，只回答“是/否的概率”和“选哪个”，很便宜，所以用来做 Claude 不值得花 token 的判断：

- `python3 pai_swarm.py triage --tasks ...`：把 `status: inbox` 的随手笔记分成能直接开工的（→ `todo`，并按难度填 `model: haiku/sonnet/opus`）和写得太含糊的（→ `needs-detail`）。
- `run --jev-verify`：worker 说 DONE 之后，让 Jev 再判断一次报告是不是真的做完、有没有跑测试；不像做完就降级为 `review`。

注意：Jev 的部分只用本地模拟服务器测过请求格式，还没对真实 API 跑过。拿到 key 后先在一两条笔记上试 `triage`。

## 测试

```bash
bash tests/run_tests.sh   # 用假的 claude，不花额度、不联网
python3 -B -m unittest discover -s tests   # RC 启动器、git 任务准备、发布检查、review/PR、doctor
```

覆盖：并发上限、路径冲突不同时跑、git/越界写入被拦、Ctrl-C 恢复 todo、`--lock-git` 恢复权限、Jev 请求格式。
