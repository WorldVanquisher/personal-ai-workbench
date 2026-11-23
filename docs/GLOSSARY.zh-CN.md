# 术语表

<!-- doc_check: fields -->
[English](GLOSSARY.md) | 简体中文

PAI 手册里用到的词，按第一次遇到的顺序排列。用 `代码` 写的命令在 pai 0.5.0 里已经存在；标了*（计划中）*的还没有。

## round（轮次）

一批并行工作，从同一个 base commit 开始，到它的 PR 合并或放弃为止，例如 `R6-20261122`。一轮有：

- 几条 lane，每条 lane 一份 prompt、一个交接目录；
- 一张决策单。

`pai round` 负责准备、运行和收尾一轮（[REFRESH.zh-CN.md](REFRESH.zh-CN.md#b-每一轮的刷新) 的 (b) 部分）。用几份任务笔记跑一次 `pai run`，本身也算一个小的轮次。

## lane（线）

一轮里的一条工作线。它有自己的 Git worktree、分支（`worker/<轮次>/<线名>`）、agent 会话和条目 ID 列表。各条 lane 拥有的文件互不重叠，所以不会改到同一行。在 `pai run` 里，lane 叫 `a`、`b`……，每条 lane 拿一份任务笔记。

## worker

在一条 lane 里干活的 agent 会话：Claude Code 或 Codex，开在一个 tmux 窗口里，常常带 Remote Control，可以在 claude.ai 上跟进。

- 它在自己的 worktree 里改文件、跑测试。
- 它不 commit、不 push、不合并，也不回答信任提示。

第一次使用的演练里，worker 是一个**假的**（伪装成 Claude Code 的测试程序），所以不需要任何账号。

## mastermind

负责协调的 agent 会话（早期也可以就是你自己）。它：

- 把你的需求拆成 lane、条目和决策单上的问题；
- 发 prompt 和续跑消息；
- 读 checkpoint，判断哪些东西可以交给你。

它**不**替你做决定，只把需要你决定的事写成决策单。PAI 就是它用来少做手工活的那一套命令。

## checkpoint

一条 lane 进度的封存快照。大约每 10 条（一个 Block）写一次，worker 提前停下之前也要写。

- 它是 lane 交接目录下的一个 `cp-<名字>/` 文件夹，至少包含 `HANDOFF.md` 和 `NEXT-PR.md`。
- 封存之后不再修改，要更正就写进下一个。
- `pai queue checkpoint FILE ID --handoff PATH` 会把它的 sha256 记进 queue 日志。

## handoff（交接）

worker 留给下一位的报告，下一位可能是你、mastermind，或者一个新会话。PAI 现在有好几种都叫这个名字的格式：`pai handoff new` 的草稿、分支上的 `HANDOFF.md`、queue 的 checkpoint 表。core 线正在定义 HANDOFF v2（`pai-handoff/2`）来统一它们。v2 交接包含：

- State 表：implementation / validation / commit / merge / deployment，每行写明来源；
- Items 表，末尾有 `Totals:` 行；
- Evidence：路径和 sha256；
- 两段 Decisions：worker 权限内的决定、需要 owner 做的决定；
- Blockers：每条附上解除它的命令；
- Next。

## NEXT-PR

`NEXT-PR.md`，放在每个 checkpoint 的 `HANDOFF.md` 旁边，是 worker 对 PR 的提案。内容包括：

- 分支；
- 按 commit 拆好的 `git add` 列表和 commit message；
- PR 正文；
- 合并前还要跑的检查。

worker 不执行其中任何一步。你批准之后，由你或 `pai worktree publish` 来执行。

## 决策单（decision sheet）

只有你能回答的问题清单，由 mastermind 或 worker 来写。每一项写清楚：

- 编号和问题；
- 可选项，以及各自的取舍；
- 推荐；
- 你不回答时的默认做法；
- 它阻塞了哪些条目。

你在 Markdown 文件里勾选就算回答了，用任何编辑器或 Obsidian 都可以。worker 开工前会先读一遍。

## JEV

TypeSafe 的判断模型，PAI 拿它当便宜的第二意见。它回答固定的是非题或选择题，并给出概率，例如"这条笔记是不是可以直接开工的任务"、"这份报告有没有说检查通过了"。每个问题大约 480 个输入 token。

- 可选：`pai init` 会问 key，直接回车就跳过。
- 它永远不会推翻真实的检查结果。
- 它判断的是报告**怎么写**，不核实是不是真的。一份只声称"测试全过"的报告，"checked"这一题照样会拿高分。

key 来自 `TYPESAFE_API_KEY`（或系统的凭据存储），pai 从不把它写进文件。

## trusted root（已信任的根目录）

agent 已经视为可信的文件夹，它下面的子文件夹也被视为可信。Claude Code 和 Codex 第一次在一个新目录里启动时，会问"你信任这个文件夹吗？"，pai 从不替你回答。如果 worktree 每轮都放在一个新地方，那么每轮每条 lane 都要再回答一次。

R6 当天（2026-11-22，Claude Code 2.1.330），owner 观察到：建在已经信任过的文件夹下面的 worktree 不会再问。所以：

- 所有轮次都放在同一个根目录下，例如 `~/pai-lanes/<轮次>/<线名>`；
- 信任一次，以后一直用它。

这只是在一个 Claude Code 版本上观察到的现象，不是官方保证。如果还是出现了提示，就回答它（`pai trust`）。

## 指令分层（L0–L3）

agent 开工前要读的东西分成四层，见 [AGENTS-LAYERS.zh-CN.md](AGENTS-LAYERS.zh-CN.md)。

- **L0，宪法**：仓库根目录那份简短的 `AGENTS.md`，写对每个会话都成立的规则：agent 绝对不能做的事、完成的定义、证据和范围。由 owner 写，目标是不超过 250 行。
- **L1，契约**：每个子系统一份文件（例如 `docs/contracts/extraction.md`），写这个领域怎么工作、哪些行为必须保持。只有任务碰到这个领域时才读。目前还没有 PAI 命令处理契约。
- **L2，本线指令**：一条 lane 在一轮里需要的东西（目标、范围、base、检查命令、其他 lane），由 `pai worktree init` 写成托管段：Claude Code 的 lane 写进 `CLAUDE.local.md`，Codex 的 lane 写进 `AGENTS.md`。
- **L3，提议**：建议的规则改动（JEV 排过序的规则、worker 的发现、prompt 的改进）。owner 接受之前，它什么都不会改。

## vault 笔记（用 Obsidian 当控制台）

这些来自 R6 的 round 线（reported-by-round）。使用说明见 [OBSIDIAN.zh-CN.md](OBSIDIAN.zh-CN.md)。

- **Home**：`PAI/Home.md`，控制台。四个区块：进行中的轮次、需要你处理的 lane、还没回答的决策、还没批准的交接。每个区块都是一段 Dataview 查询加一张纯文本表格。
- **看板（board）**：`PAI/rounds/<轮次>/ROUND.md`，一轮里每条 lane 一行。（`pai round status --board` 写出的纯文本 `BOARD.md` 也叫看板。）
- **lane 笔记**：`PAI/rounds/<轮次>/<线名>.md`，一条 lane 的状态，写在 front matter 里。
- **决策笔记**：`PAI/decisions/<轮次>-<ID>.md`，决策单上的一个问题。你的回答写在它的 `choice:` 字段里。
- **托管笔记**：PAI 写的笔记。它带有 `pai_managed:`、`pai_sha256:`，以及标记 `<!-- pai:managed:begin pai-vault/1 -->` … `<!-- pai:managed:end -->`。只要没人改过，PAI 就会重写它。没有这个标记的笔记是你的，永远不会被碰。

**front matter 字段**，按笔记分（front matter 就是状态，表格和查询只负责显示）：

| 字段 | 出现在 | 含义 |
| --- | --- | --- |
| `pai_managed` | 每篇 PAI 笔记 | 笔记的种类和格式：`home/1`、`round/1`、`lane/1`、`decision/1` |
| `pai_sha256` | 每篇 PAI 笔记 | PAI 所写内容的校验和（不含 `updated` 和你的字段）：有人改过就会对不上 |
| `updated` | 每篇 PAI 笔记 | PAI 最近一次写它的 UTC 时间 |
| `rounds_open`、`lanes_need_owner`、`decisions_open`、`handoffs_unapproved` | Home | 四个计数 |
| `round`、`project` | 看板、lane、决策 | 属于哪一轮、哪个项目 |
| `status` | 看板：`open` / `closed`；lane：`running`、`idle`、`blocked-on-dialog`、`owner-draft`、`queued`、`exited`、`no-window`；决策：`open` / `answered` | 当前状态 |
| `base`、`lanes`、`round_json` | 看板 | base 提交、lane 数、ROUND.json 的路径 |
| `dialog`、`queued` | lane | 挡住这条 lane 的是哪个 Claude Code 对话框（`trust`、`permission`、`teach-auto-mode`、`other`），以及是否有消息在排队 |
| `lane`、`current_id`、`last_cp`、`totals`、`blockers`、`branch`、`worktree_state` | lane | 线名、正在做的条目、最近的 checkpoint、各状态的计数、blockers、分支、worktree 是干净还是有 N 处改动 |
| `needs_owner`、`needs_owner_why` | 看板、lane | 只有你能解开时为 `true`，并列出原因 |
| `id`、`question`、`options`、`recommendation`、`default`、`deadline`、`blocks` | 决策 | 决策单上写的内容 |
| `choice`、`choice_note` | 决策 | **你的**：你的回答，由 `pai decide answer --from-note` 应用 |
| `answer`、`source`、`source_sha` | 决策 | 记录下来的回答；它来自哪份决策单，以及那段文字的校验和 |

封存的交接（`HANDOFF.md`，格式 `pai-handoff/2`，reported-by-core）的 front matter 包括：

- `format`、`status`、`approved`、`project`、`round`、`lane`、`checkpoint`、`task`；
- `base_sha`、`head_sha`、`queue`、`queue_sha256`、`created`、`generator`。

`pai handoff seal` 会加上 `sealed_sha256`（正文的 sha256）和 `approval: pending`。批准之后，front matter 变成 `approved: true`、`approval: approved`（封存后正文被改过则是 `stale`），并在旁边写出 `DECISION.json`。只是手工把 `approved` 改成 `true`，什么都不会被批准。

## 其他会看到的词

- **base**：一轮或一次 run 的起点 commit，开始时固定下来（`origin/main @ <sha>`）。
- **任务笔记（task note）**：一个写着 `status: todo` 的 Markdown 文件，告诉一条 lane 要做什么（`pai task new`）。
- **queue**：`pai queue` 记的条目列表。它记录 worker 做到了哪里，新会话可以接着做（`pai queue restart FILE`）。
- **RC（Remote Control）**：Claude Code 的一种模式，可以在 claude.ai 上跟进会话、往里面打字。`pai status` 按 lane 显示它的状态：`verified`、`not-connected` 或 `not-supported`。
- **证据标签**：报告里每个数字都要带的标签：`real run`、`synthetic`、`fake agent`、`mock endpoint`、`JEV`、`self-report`，或者 `reported-by-<线名>`。
