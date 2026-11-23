# 0.5.0 发布说明（草稿）

[English](RELEASE-NOTES-DRAFT.md) | 简体中文

**给 owner 的草稿，还没发布。** 代码里的版本号仍是 0.4.0。依据是 R6 四条线各自的 NEXT-PR.md，以及 core 的 [CHANGELOG-CANDIDATE.md](CHANGELOG-CANDIDATE.md)（每项改动和对应的测试都列在那里）。这一页是写给用户看的摘要。发布前请先核对下面标了 **owner** 的两处，再把这一页抄进 RELEASE_NOTES.md。

## 一句话

0.5.0 用文件来跑一整轮并行的 agent lane：开一轮、告诉你哪条 lane 在等你、通过 Markdown 笔记来回传递工作和决定（任何编辑器或 Obsidian 都能读），最后把结果收拢成一个由你合并的 PR。

## 新功能

### 轮次：`pai round`

一组命令，管理一轮并行的 Claude Code lane。一轮的信息保存在 `ROUND.json`、`BOARD.md` 和每天一页的协调日志里，都是 vault 的 `Agent Inbox/` 下的 Markdown 或 JSON。

- `pai round new PROJECT ROUND --lane L --prompt FILE --write` 从合并后的 main 克隆，每条 lane 建一个 worktree，并填好每条 lane 的 prompt。不加 `--write` 只是预览。
- `pai round status` 每条 lane 一行：会话、窗口、状态（运行中、空闲、卡在对话框、草稿已输入但没发送、排队中、已退出）、worktree、最近的 checkpoint、queue 统计。
- `pai round launch LANE --paste-prompt --write` 启动一条 lane，只在安全的时候粘贴它的 prompt；`pai round next` 和 `pai round inject` 用同样的方式发送续做消息或一条留言。每次发送都有记录，不会盲目重发。
- `pai round collect` 和 `pai round integrate` 收集各条 lane 的交接，并在一个临时副本上预演合并。
- `pai decide answer` 回答一张决策单，批准的正是你读过的那一版文字。

完整参考：[ROUND.md](../ROUND.md)（英文）。一个两条 lane 的完整例子，附终端记录：[examples/two-lane-round/](../../examples/two-lane-round/README.md)。

### prompt 库：`pai prompt`

启动和引导 worker 的 prompt 是带版本号、声明了占位符的文件（`pai-prompts/1`）。`pai prompt list` 列出它们，`pai prompt lint` 检查它们，`pai prompt init --dest DIR --write` 把它们装进你的 vault；`pai prompt diff --dest DIR` 显示升级会改动什么。只有托管标记之内的文字会被改写。这个库用中文写（owner 决策 PRM-D1）。见 [PROMPTS.md](../PROMPTS.md)。

### Handoff v2

统一的交接格式 `pai-handoff/2`：一张 State 表，逐条的条目行，以及和 queue 对得上的合计。`pai handoff seal PROJECT FILE` 检查交接、打印正文的 sha256，并把它归档到这一轮的 inbox。你用 `pai handoff approve PROJECT DIR --expect-sha SHA` 批准的正是这一版，也可以在 Obsidian 里写好、再用 `--from-vault` 批准；文件一改，批准就过期。见 [HANDOFF-V2.md](../HANDOFF-V2.md)。

### vault 笔记，Obsidian 作为可选的控制台

PAI 把这一轮的状态写成带 front matter 的普通 Markdown 笔记：一个首页，每条 lane 一篇，每个决定一篇。生成的笔记带托管标记；你自己写的或改过的笔记，PAI 绝不改写。`pai vault lint` 检查笔记，`pai vault wait FILE --sha SHA --timeout 120` 等到同步过来的是那一版为止。Obsidian 是可选的：每篇笔记在任何编辑器里看起来都一样。见 [OBSIDIAN.zh-CN.md](../OBSIDIAN.zh-CN.md) 和 [FRONTMATTER.md](../FRONTMATTER.md)。

### lane 指令放在 `CLAUDE.local.md`（L2 层）

`pai worktree init PATH --target claude-local --write` 把一条 lane 自己的指令写进它 worktree 里一个被 git 忽略的 `CLAUDE.local.md`，worktree 保持干净。真实的 Claude Code 会话会读它（mastermind 2026-11-22 real run，Claude Code 2.1.330）。`pai context --worktree PATH` 显示一条 lane 会读到什么。为什么要分层，见 [AGENTS-LAYERS.zh-CN.md](../AGENTS-LAYERS.zh-CN.md)。

### JEV 账本

启用 JEV 时，每次请求连同 token 数都会记下来。`pai jev usage` 按天和项目汇总。JEV 仍然是可选的参考意见，永远不能推翻检查结果。

### 0.4.0 以来的其他新功能

在新会话里接着做一条 lane（`pai recover --continue LANE`）、在机器之间传递任务包、带编号的工作队列（`pai queue`）、拒绝任务范围之外 shell 写入的 guard，以及会跑 workbench 测试和文档检查的 CI。详见 [CHANGELOG-CANDIDATE.md](CHANGELOG-CANDIDATE.md)。

## 从 0.4.x 升级

1. **安装**方式和以前一样，换成新版本：`uv tool install` tag `v0.5.0`，或者用新压缩包跑 `bin/pai install`。配置、任务笔记和运行状态都保留。回滚：用同样的方式装回 0.4.x（[REFRESH.zh-CN.md](../REFRESH.zh-CN.md#a-升级-pai)）。
2. **读 `pai status --json` 的脚本**：现在输出的是一个对象（`pai_status_v1`），lane 的行在 `lanes` 下面。把 `.[0]` 换成 `.lanes[0]`。见 [MIGRATION.md](../1.0/MIGRATION.md)。
3. **`pai doctor` 的退出码**：1 现在只表示缺了必需的东西（缺 `gh` 不再算）。
4. **无头模式的权限（owner）：** 无头 worker（`swarm/pai_swarm.py run`）在这个版本里默认用 `manual`。真实运行表明，这样的 worker 写不了任务要求写的文件；`acceptEdits` 加 guard 可以。发布前由决策 D-R6-1 定默认值。单次运行可以在那条命令后面加 `--permission-mode acceptEdits`（[PERMISSION-MODES.md](PERMISSION-MODES.md)）。
5. **仓库里同时有 `CLAUDE.md` 和 `AGENTS.md`**：Claude Code 只读 `CLAUDE.md`。在 `CLAUDE.md` 里写一行 `@AGENTS.md`，两种 agent 就读同一份规则（[AGENTS-LAYERS.zh-CN.md](../AGENTS-LAYERS.zh-CN.md#l0宪法)）。
6. **vault 里的 prompt**：先跑 `pai prompt diff --dest DIR`，再跑 `pai prompt init --dest DIR --write`。你在托管标记之外的改动会保留。
7. **tmux 低于 3.8**（Ubuntu 和 WSL 自带 3.6）：`pai round` 只往运行着 Claude Code 或 Codex 的 pane 里粘贴（[FIRST-USE.zh-CN.md 第 1 节](../FIRST-USE.zh-CN.md#1-需要准备什么)）。

## 测过的，和还没测的

- **测过：** 四条线合并后，在 Arch Linux（各线的 lane tree）和 tmux 3.6 的 WSL Ubuntu（合并后的候选）上：core 和 workbench 测试套件、压缩包、bundle 冒烟、新用户引导、doc_check。真实运行：无头 Claude Code 冒烟（用 `acceptEdits` 7 项全过）、`CLAUDE.local.md` 装载检查（10/10）、`pai jev check`。各平台细节见 [FIRST-USE-PLATFORMS.md](../FIRST-USE-PLATFORMS.md)（英文）。
- **还没有（owner）：** release commit 上的 CI 全绿；WSL 上的打包步骤；round 线修好之后在 tmux 3.6 上重跑 round 测试；macOS 上的 workbench；真实的 Codex lane；用 `pai round` 对真实 agent 跑完一整轮。
