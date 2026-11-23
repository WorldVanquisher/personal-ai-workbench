# AGENTS.md 分层：宪法、契约、本线指令、提议

[English](AGENTS-LAYERS.md) | 简体中文

每个 agent 会话开始时，都要先读仓库的指令文件：`AGENTS.md`（Codex 读它；没有 `CLAUDE.md` 时 Claude Code 也读它）或者 `CLAUDE.md`。文件里的每一行，都会在每个会话、每个任务里装进上下文。所以它是多 agent 项目的**心脏**：

- 这里的一条规则，每个 worker 都会照做；
- 这里过时的一行，会误导每个 worker；
- 这里的文件太长，每个 worker 在读第一个源文件之前，就先花掉了一大块上下文。

PAI 把平常堆在这一个文件里的东西拆成四层。每一层的负责人不同、寿命不同、放在磁盘上的位置也不同。

| 层 | 放什么 | 谁来写 | 放在哪里 | 寿命 |
| --- | --- | --- | --- | --- |
| **L0 宪法** | 对这个仓库里每个会话都成立的少数几条规则：agent 绝对不能做的事、完成的定义、证据和范围的规则 | owner | 仓库根目录的 `AGENTS.md`（目标是不超过 250 行） | 直到 owner 修改 |
| **L1 契约** | 一个子系统怎么工作、必须保持哪些行为：数据模型、不变量、命令、测试 | owner，或者经 owner 批准的 worker | 每个领域一份，例如 `docs/contracts/extraction.md` | 和子系统一样长 |
| **L2 本线指令** | 这条 lane 的目标、范围、分支和 base、检查命令、其他 lane 负责的区域、worker 可以自己做的决定 | PAI（`pai worktree init`），根据这一轮的计划 | Claude Code 的 lane：lane worktree 里的 `CLAUDE.local.md`，不进 git；Codex 的 lane：`AGENTS.md` 里的托管段 | 一轮 |
| **L3 提议** | 有人建议的规则改动：JEV 排过序的规则、prompt 的改进、"这条规则不对"的发现 | worker、mastermind、JEV | `pai worktree init --jev` 的提议输出、决策单、NEXT-PR 里的备注 | 直到 owner 接受或拒绝 |

没有任何东西会自己升层。L3 的提议只有在 owner 接受之后，才会变成 L1 或 L0 的规则。

## L0：宪法

简短、稳定、对每个任务都成立。常见的几节：

- 安全规则（例如"agent 不跑任何 git 写操作"）；
- 完成的定义；
- 证据标签；
- 范围纪律；
- 契约放在哪里。

两种 agent 官方文档里的两条限制（reported-by-prompts，`docs/research/LANDSCAPE.md`，2026-11-22 重新核实）：
- Codex 读指令文件，最多读 32 KiB，L0 和所有嵌套文件合起来算。真实的例子：FLH 的 AGENTS.md 有 63 KiB，第 519 行之后的规则 Codex 全都读不到（[分析 §6](research/FLH-AGENTS-ANALYSIS.md#6-context-budget-r6-doc-060)）。
- 有 `CLAUDE.md` 时，Claude Code 不读 `AGENTS.md`，除非每个用户自己改一个设置，而这个设置写在仓库的设置文件里是无效的。用一个只写 `@AGENTS.md` 的 `CLAUDE.md`，两种 agent 就会读同一份 L0。Claude Code 不读 `.agents/`、`AGENTS.local.md` 和 `AGENTS.override.md`。

**Claude Code 实际读哪些文件**（mastermind 2026-11-22 real run，Claude Code 2.1.330，haiku）。严格复测时禁用了所有文件工具，模型只能根据它装载的说明文件作答。每个用例跑 2 次，10 次全部符合预期，每次都是 1 轮就答。

| # | 文件夹里有什么 | Claude Code 读什么 |
| --- | --- | --- |
| 1 | linked worktree 里一个被 git 忽略的 `CLAUDE.local.md` | 会读 `CLAUDE.local.md`，所以 L2 可以放在这里 |
| 2 | `CLAUDE.md` 和 `AGENTS.md` | 只读 `CLAUDE.md`，**不读** `AGENTS.md` |
| 3 | 写着 `@AGENTS.md` 的 `CLAUDE.md`，加上 `AGENTS.md` | 两个都读：导入把 `AGENTS.md` 带了进来 |
| 4 | 只有 `AGENTS.md`，没有 `CLAUDE.md` | 会读 `AGENTS.md` |

最要记住的是第 2 条：文件夹里一旦加了 `CLAUDE.md`，Claude Code 就不再读 `AGENTS.md`，而且不会有任何提示，除非这个 `CLAUDE.md` 导入了它。

**不该**放在这里的：进度报告（"第 4 轮交付了……"）、某一个子系统的细节、任何带日期的内容。一个真实的 1074 行文件怎么拆，见 [research/FLH-AGENTS-ANALYSIS.md](research/FLH-AGENTS-ANALYSIS.md)（英文）。

**Windows 注意。** 如果 `CLAUDE.md` 是指向 `AGENTS.md` 的符号链接，在不支持符号链接的 Windows checkout 里，它会变成一个 9 字节的文本文件，Claude Code 就读不到任何规则。可以换成一个只写一行 `@AGENTS.md` 的普通 `CLAUDE.md`，也可以直接删掉它（第 4 条）。两种做法见 [下文：把 CLAUDE.md 符号链接改成导入](#把-claudemd-符号链接改成导入)。

## L1：契约

每个领域一份文件，只有会话碰到那个领域时才装载。

**PAI 还没有实现。** 目前没有任何 `pai` 命令会创建、列出或装载契约文件。现在的做法是在 L0 里写上链接（"改 tools/extraction 之前，先读 docs/contracts/extraction.md"）。两种 agent 也都有按位置装载文件的原生方式（reported-by-prompts：`docs/research/LANDSCAPE.md`）：

- Claude Code：带 `paths:` front matter 的 `.claude/rules/*.md`；
- Codex：每个目录各自的嵌套 `AGENTS.md`。

这两种方式都还没有在 PAI 的真实运行里试过（待核实）。

## L2：本线指令

只有这条 lane、这一轮需要知道的内容。PAI 把它写成标记之间的托管段，所以永远不会覆盖你写的内容：

```
<!-- pai:managed:begin pai-agents/1 … -->
… 目标、范围、分支和 base、检查命令、其他 lane、worker 可以自己做的决定 …
<!-- pai:managed:end -->
```

| agent | L2 放在哪里 | 命令（core 线，reported-by-core） | 状态 |
| --- | --- | --- | --- |
| Claude Code | lane worktree 里的 `CLAUDE.local.md`。Claude Code 会和 `CLAUDE.md` 一起读它；PAI 把它加进仓库的 `info/exclude`，所以 git 看不到它，worktree 保持干净 | `pai worktree init PATH --target claude-local --write` | 文件处理用 fake 测过；真实的 Claude Code 会话会读它（上面第 1 条，mastermind 2026-11-22 real run，Claude Code 2.1.330，haiku） |
| Codex | worktree 里 `AGENTS.md` 的托管段（Codex 不读 `CLAUDE.local.md`） | `pai worktree init PATH --write`（默认目标） | 真实的 Codex 运行**待核实**；托管段会让 worktree 变成 dirty，续做流程要能容忍这一点 |

`pai context --worktree PATH`（core 线）按 agent 的方式读一条 lane 的指令：先读 `CLAUDE.local.md`，再读 `AGENTS.md`。

## L3：提议

提议本身永远不会改任何文件。

- `pai worktree init --jev` 用 JEV 给一个小规则库排序，列出提议；只有 `--accept RULE` 才会把某一条写进这条 lane 的 L2 托管段。
- worker 把规则问题写进交接的 "Decisions for the owner" 和 NEXT-PR.md，mastermind 再把它们整理成决策单。例子：`Agent Inbox/R6-20261122/docs/FLH-RULE-CONFLICTS.md` 里 FLH 规则和做法冲突的 5 处。
- `pai prompt optimize`（prompts 线，离线原型）提出 prompt 的改动，用契约和 JEV 打分，每一条都要你批准。

## PAI 在哪里处理每一层

| | L0 | L1 | L2 | L3 |
| --- | --- | --- | --- | --- |
| `pai init` | 读它（`--instructions`，也就是 `pai context` 会传下去的文件） | — | — | — |
| `pai worktree init` | 在 lane 托管段里引用它的规则，附 `file:line` | — | **写它**（AGENTS.md 的托管段；加 `--target claude-local` 时写 CLAUDE.local.md） | `--jev` 提议，`--accept` 采纳 |
| `pai round new`（round 线） | prompt 的共享部分会重复这一轮的规则 | — | 为每条 lane 渲染 prompt；**还不会**写 CLAUDE.local.md（未实现） | 决策写进 `DECISIONS.md` 和决策笔记 |
| `pai context` | 传给 agent | — | `--worktree PATH` 先读这条 lane 的 L2 | — |
| 网页工作台（`pai worktree web`、`pai round web`） | 显示 worktree 的 AGENTS.md | — | 显示托管段 | 显示还没回答的决策 |
| doc_check / lint | — | — | — | —（还没有分层检查） |

每一个"—"都是目前的空白。R7 轮会围绕补上这些空白来安排，先从 L1 开始。

## 把 CLAUDE.md 符号链接改成导入

下面只是文档，以 FLH 为例。请在你自己的 checkout 里运行，并自己提交。

FLH 把 `CLAUDE.md` 链接到 `AGENTS.md`，只是为了让 Claude Code 读到规则。不用链接也有两种做法能做到（见 [L0](#l0宪法) 的第 3、4 条）：

| | A：只写 `@AGENTS.md` 的 `CLAUDE.md`（推荐） | B：删掉 `CLAUDE.md` |
| --- | --- | --- |
| Claude Code 读不读 `AGENTS.md` | 读，靠导入（第 3 条） | 读，因为没有 `CLAUDE.md`（第 4 条） |
| 要维护的文件 | 两个；`CLAUDE.md` 只有一行 | 一个 |
| 只给 Claude 的内容 | 可以写在 `CLAUDE.md` 导入行的下面 | 没有共用的地方；只属于某个 worktree 的内容写进 `CLAUDE.local.md` |
| 以后有人加了 `CLAUDE.md` | 没有影响 | Claude Code 不再读 `AGENTS.md`，而且没有提示（第 2 条） |
| 依据 | 第 3 条 | 第 4 条，只在 Claude Code 2.1.330 上验证过；更早的 Claude Code，或者只读 `CLAUDE.md` 的其他工具，就读不到规则 |
| Windows | 普通文件，没有链接问题 | 没有东西会坏 |

除非你只想维护一个文件、并且接受最后三行的影响，否则选 A。A 的步骤：

1. 先确认它是链接：`git -C ~/workspace/french-learning-app ls-files -s CLAUDE.md`。mode 是 `120000` 就说明是符号链接。
2. 换成只有一行的普通文件：

   ```sh
   cd ~/workspace/french-learning-app
   rm CLAUDE.md && printf '@AGENTS.md\n' > CLAUDE.md
   git ls-files -s CLAUDE.md          # stage 之前仍然显示 120000；运行 `git add CLAUDE.md` 之后是 100644
   ```

3. 核对：在这个文件夹里启动 Claude Code，让它列出装载的指令文件（`/memory`）。`AGENTS.md` 必须显示为从 `CLAUDE.md` 导入。Claude Code 按导入文件的相对路径解析 `@path`，最多嵌套四层（reported-by-prompts，来自 Claude Code 的 memory 文档）。
4. 在 Windows 上，下一次 pull 之后打开 `CLAUDE.md`：里面必须是 `@AGENTS.md`，而不是坏掉的链接留下的文本 `AGENTS.md`。
5. Codex 继续直接读 `AGENTS.md`，Codex 的 lane 不受影响。

选 B 时，第 2 步换成 `git rm CLAUDE.md`；第 3 步核对 `/memory` 列出的是 `AGENTS.md` 本身。第 4、5 步不变。
