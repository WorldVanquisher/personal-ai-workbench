# 0.5.0 发布说明

[English](RELEASE-NOTES-DRAFT.md) | 简体中文

**这是 release PR 用的定稿；owner 打 `v0.5.0` tag 之后才算发布。** release 分支里的版本号已经是 0.5.0。这一页的每一句话在树里都有出处（文件、测试或有记录的运行），对照表在 fin 线交接里的 `RELEASE-NOTES-SOURCES.md`。完整的改动清单和对应测试见 [CHANGELOG-CANDIDATE.md](CHANGELOG-CANDIDATE.md)。RELEASE_NOTES.md 里放的是同样的内容（英文）。

## 一句话

0.5.0 用文件来跑一整轮并行的 agent lane：开一轮、告诉你哪条 lane 在等你、通过 Markdown 笔记来回传递工作和决定（任何编辑器或 Obsidian 都能读），最后把结果收拢成一个由你合并的 PR。

## 新功能

### 轮次：`pai round`

一组命令，管理一轮并行的 Claude Code lane。一轮的信息保存在 `ROUND.json`、`BOARD.md` 和协调日志里，都是 vault 的 `Agent Inbox/` 下的 Markdown 或 JSON。

- `pai round new PROJECT ROUND --lane L --prompt FILE --write` 找到这个项目的主 clone，从远端读出基线，每条 lane 建一个 worktree，并填好每条 lane 的 prompt。不加 `--write` 只是预览。
- `pai round status` 每条 lane 一行：会话、窗口、状态（运行中、空闲、卡在对话框、草稿已输入但没发送、排队中、已退出）、worktree、最近的 checkpoint、queue 统计。
- `pai round launch LANE --paste-prompt --write` 启动一条 lane，只在安全的时候粘贴它的 prompt；`pai round next` 和 `pai round inject` 用同样的方式发送续做消息或一条留言。每次发送都有记录。
- `pai round collect` 和 `pai round integrate` 收集各条 lane 的交接，并在一个临时副本上预演合并。
- `pai decide answer` 回答一张决策单，批准的正是你读过的那一版文字。

完整参考：[ROUND.md](../ROUND.md)（英文）。一个两条 lane 的完整例子，附终端记录：[examples/two-lane-round/](../../examples/two-lane-round/README.md)。

### prompt 库：`pai prompt`

启动和引导 worker 的 prompt 是带版本号、声明了占位符的文件（`pai-prompts/2`；旧的 `/1` 格式仍然能读）。`pai prompt list` 列出它们，`pai prompt lint` 检查它们，`pai prompt init --dest DIR --write` 把它们装进你的 vault；`pai prompt diff --dest DIR` 显示升级会改动什么。只有托管标记之内的文字会被改写。这个库用中文写（owner 决策 PRM-D1）。见 [PROMPTS.md](../PROMPTS.md)。

### Handoff v2

统一的交接格式 `pai-handoff/2`：一张 State 表，逐条的条目行，以及和 queue 对得上的合计。`pai handoff seal PROJECT FILE` 检查交接、打印正文的 sha256，并把它归档到这一轮的 inbox。你用 `pai handoff approve PROJECT DIR --expect-sha SHA` 批准的正是这一版，也可以在 Obsidian 里写好、再用 `--from-vault` 批准；封存后正文被改过的，会被标成 stale，不能批准。见 [HANDOFF-V2.md](../HANDOFF-V2.md)。

### vault 笔记，Obsidian 作为可选的控制台

PAI 把这一轮的状态写成带 front matter 的普通 Markdown 笔记：一个首页，每条 lane 一篇，每个决定一篇。生成的笔记带托管标记；你自己写的或改过的笔记，PAI 绝不改写。`pai vault lint` 检查笔记，`pai vault wait FILE --sha SHA --timeout 120` 等到同步过来的是那一版为止。Obsidian 是可选的：每篇笔记在任何编辑器里看起来都一样。见 [OBSIDIAN.zh-CN.md](../OBSIDIAN.zh-CN.md) 和 [FRONTMATTER.md](../FRONTMATTER.md)。

### lane 指令放在 `CLAUDE.local.md`（L2 层）

`pai worktree init PATH --target claude-local --write` 把一条 lane 自己的指令写进它 worktree 里一个被 git 忽略的 `CLAUDE.local.md`，worktree 保持干净。真实的 Claude Code 会话会读它（mastermind real run，Claude Code 2.1.330：10 项检查全过）。`pai context --worktree PATH` 显示一条 lane 会读到什么。为什么要分层，见 [AGENTS-LAYERS.zh-CN.md](../AGENTS-LAYERS.zh-CN.md)。

### JEV 账本

启用 JEV 时，每次请求连同 token 数都会记下来。`pai jev usage` 按时间段（`--since`、`--until`）汇总，并按请求的类型分开。JEV 仍然是可选的参考意见，永远不能推翻检查结果。

### 0.4.0 以来的其他新功能

在新会话里接着做一条 lane（`pai recover --continue LANE`）、在机器之间传递任务包、带编号的工作队列（`pai queue`）、拒绝任务范围之外 shell 写入的 guard，以及会跑 workbench 测试和文档检查的 CI。详见 [CHANGELOG-CANDIDATE.md](CHANGELOG-CANDIDATE.md)。

## 从 0.4.x 升级

1. **安装**方式和以前一样，换成新版本：`uv tool install` tag `v0.5.0`，或者用新压缩包跑 `bin/pai install`。配置、任务笔记和运行状态都保留。回滚：用同样的方式装回 0.4.x（[REFRESH.zh-CN.md](../REFRESH.zh-CN.md#a-升级-pai)）。
2. **读 `pai status --json` 的脚本**：现在输出的是一个对象（`pai_status_v1`），lane 的行在 `lanes` 下面。把 `.[0]` 换成 `.lanes[0]`。见 [MIGRATION.md](../1.0/MIGRATION.md)。
3. **`pai doctor` 的退出码**：1 现在只表示缺了必需的东西（缺 `gh` 不再算）。在 `pai init` 选定 agent 之前，缺 claude 或 codex 会列在 `FOR YOUR FIRST RUN` 下面，也不算；选定之后，那个 agent 就是必需的。`pai doctor --json` 里这几行的 `need` 是 `later`。
4. **无头 worker 现在默认用 `acceptEdits`**（owner 决策 D-R6-1 = A）：无头 worker（`swarm/pai_swarm.py run`）能写任务要求写的文件，guard 照样拒绝任务 `paths:` 之外的 shell 写入。`manual` 仍然可以用 `--permission-mode manual` 选，适合有人在场的会话；无头运行时它连任务自己的写入也会拦住（[PERMISSION-MODES.md](PERMISSION-MODES.md)）。
5. **仓库里同时有 `CLAUDE.md` 和 `AGENTS.md`**：Claude Code 只读 `CLAUDE.md`。在 `CLAUDE.md` 里写一行 `@AGENTS.md`，两种 agent 就读同一份规则（[AGENTS-LAYERS.zh-CN.md](../AGENTS-LAYERS.zh-CN.md#l0宪法)）。
6. **vault 里的 prompt**：先跑 `pai prompt diff --dest DIR`，再跑 `pai prompt init --dest DIR --write`。你在托管标记之外的改动会保留。
7. **tmux 低于 3.8**（Ubuntu 和 WSL 自带 3.6）：`pai round` 只往运行着 Claude Code 或 Codex 的 pane 里粘贴（[FIRST-USE.zh-CN.md 第 1 节](../FIRST-USE.zh-CN.md#1-需要准备什么)）。

## 测过的，和还没测的

- **在合并后的候选上测过：** `tools/verify.py` 9 项通过、0 项失败、5 项跳过（跳过的几步需要 uv、zsh、fish、PowerShell 或离线的 setuptools），分别在 tmux 3.6 的 WSL Ubuntu（reported-by-mastermind）和 tmux 3.8 的 Arch Linux（real run）上。之前在 tmux 3.6 上失败的 round 测试，现在在那里也通过了。打包步骤（wheel、从 0.4.0 升级、中断的安装、卸载）在 Arch Linux 上离线 36 项全过；core 测试（`tests/`）在支持的最低版本 Python 3.11 上通过（core 线，在它最后几处文字修改之前）。
- **真实运行**（reported-by-mastermind，WSL）：用默认的 `acceptEdits` 跑无头 Claude Code 冒烟，7 项全过，任务 paths 之外的写入被拒；`CLAUDE.local.md` 装载检查 10 项全过；`pai jev check`。prompt 实验 E3（haiku，每种开场白 n = 16 次；得分 = first-reply 契约检查和四项任务检查的平均）：带填好示例的开场白 0.994（sd 0.023），不带的 0.955（sd 0.081）。这只说明在这各 16 次上是这样，不是测出来的提升幅度。
- **新用户走查：** 一个没有任何背景的会话在空的 home 目录里装了 0.5.0 候选包，照中文 FIRST-USE 走到第 5 节（reported-by-mastermind）；它记下的 19 条问题已经在手册、`pai doctor` 和 `pai --help` 里改好，用改好之后的压缩包、在没装任何 agent 的情况下重走一遍，结果和页面一致（real run）。
- **还没有（owner）：** release commit 上的 CI 全绿；WSL 上的打包步骤；macOS 上的 workbench；真实的 Codex lane；用 `pai round` 对真实 agent 跑完一整轮。

各平台细节见 [FIRST-USE-PLATFORMS.md](../FIRST-USE-PLATFORMS.md)（英文）。
