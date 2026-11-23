# 用 Obsidian 当 PAI 的控制台

[English](OBSIDIAN.md) | 简体中文

<!-- doc_check: fields -->
PAI 把一轮工作的状态存成 vault 里的 Markdown 笔记：一张看板，每条 lane 一篇笔记，每个决策一篇笔记。你在 Obsidian 里**看、批、选**。规则如下（owner 决策 D9，2026-11-22）：

- **状态一律是 Markdown 加 front matter。** agent 和脚本直接读写。
- **查询只用来展示。** Dataview 或 Bases 的查询只负责显示状态，永远不是状态的来源。
- **插件是可选的。** 每个视图在笔记里都另有一份纯文本表格。
- **绝不改你自己的笔记。** PAI 写的每篇笔记都带托管标记；PAI 只重写自己写的、而且没人改过的笔记。

vault 就是一个文件夹。任何编辑器、`cat` 或 `grep` 看到的内容，都和 Obsidian 看到的一样。

这一页命令的完整写法见 `docs/ROUND.md` 和 `docs/HANDOFF-V2.md`（英文）。下面的目录结构是 2026-11-22 由 `pai round status --write-vault` 实际写进 owner 的 vault 的。

## 1. 目录约定

| vault 里的路径 | 是什么 | 由谁写 |
| --- | --- | --- |
| `PAI/Home.md` | 控制台：进行中的轮次、需要你处理的 lane、还没回答的决策、还没批准的交接 | `pai round status --write-vault` |
| `PAI/rounds/<轮次>/ROUND.md` | 一轮的看板：每条 lane 一行 | 同上 |
| `PAI/rounds/<轮次>/<线名>.md` | 一条 lane：状态、当前条目、最近的 checkpoint、Totals、blockers | 同上 |
| `PAI/decisions/<轮次>-<ID>.md` | 一个决策：问题、选项、推荐、默认做法，以及你的 `choice` | `pai round collect --write-vault` |
| `Agent Inbox/<轮次>/<线名>/cp-*/` | 封存好的 checkpoint：`HANDOFF.md`、`NEXT-PR.md`、`SEAL.json` | `pai handoff seal` |
| `Agent Inbox/<轮次>/<线名>/NOTES-FOR-<线名>.md` | 线与线之间的留言 | 各条线的 worker |

名为 `round` 的 lane，笔记叫 `round-lane.md`：在 Windows 和 macOS 上，`round.md` 和 `ROUND.md` 会被当成同一个文件。

核对一下：运行 `ls "$VAULT/PAI" "$VAULT/PAI/rounds"`。成功的话，会看到 `Home.md`、`decisions`、`rounds`，以及每个轮次一个文件夹。如果没有 `PAI/`，就在跑这一轮的机器上运行一次 `pai round status ROUND.json --write-vault`，然后等同步完成（见 [REFRESH.zh-CN.md](REFRESH.zh-CN.md#c-常见的坑)），再到别的设备上看。

## 2. 打开控制台：`PAI/Home.md`

1. 在 Obsidian 里打开 vault，然后打开 `PAI/Home.md`（Ctrl/Cmd-O，输入 `Home`）。可以把它固定住；如果你在用 Homepage 插件，也可以把它设成起始页。
2. 看顶部的 front matter（Obsidian 里显示为 **Properties**）：
   - `updated:` 是 PAI 最近一次写这篇笔记的时间；
   - 后面是四个计数：`rounds_open`、`lanes_need_owner`、`decisions_open`、`handoffs_unapproved`。
3. 从上到下处理四个区块（笔记里的标题分别是：进行中的轮次、需要 owner 处理的 lane、还没回答的决策、还没批准的交接）：
   1. **进行中的轮次**：点进去打开它的看板（`ROUND.md`）。
   2. **需要你处理的 lane**：每一行都写了原因（`needs_owner_why`），按原因处理：
      - 信任提示在等你：到那条 lane 的窗口里回答；
      - JEV 和证据对不上，或者校验和不一致：先读这条 lane 最近的交接，再做别的；
      - 有条目 blocked 或 owner-only：交接的 Blockers 段写了要运行的命令；
      - 会话已经退出但还有条目没做完：在跑这一轮的机器上运行 `pai round launch LANE --write`，再运行 `pai round next LANE --send`（其他情况见 [REFRESH.zh-CN.md](REFRESH.zh-CN.md#e-恢复)）。
   3. **还没回答的决策**：逐个打开并回答（第 5 节）。
   4. **还没批准的交接**：逐个读完，批准或拒绝（第 5 节）。

成功的话：你处理完之后，跑这一轮的机器再运行一次 `pai round status --write-vault`，对应的那一行就会消失，计数随之减少。

如果看起来不对：

- `updated:` 比你预想的旧：说明跑这一轮的机器还没刷新。到那台机器上运行上面的命令，再等同步。
- 笔记根本不存在：见第 1 节。
- 旁边多出了 `Home (conflicted copy …).md` 或 `Home.sync-conflict-….md`：两台设备同时写了。删掉这份副本，PAI 下次运行时会重写 Home.md。

## 3. 不装插件、装 Dataview、装 Bases

Home.md 的每个区块都有两种形式：一段 `dataview` 查询，以及一张**纯文本表格**（PAI 写笔记那一刻的快照）。你看到哪一种，取决于装了什么插件：

| 你装了 | 你会看到 |
| --- | --- |
| 什么插件都没装（或者用别的编辑器） | 纯文本表格。`dataview` 代码块会显示成普通代码，忽略即可 |
| **Dataview**（社区插件） | 纯文本表格，以及每张表上方一张**实时**表格：lane 或决策笔记一变，它就跟着更新 |
| **Bases**（核心插件：设置 → 核心插件 → Bases；owner 的 vault 已启用，见 `.obsidian/core-plugins.json`） | 暂时没有额外内容。PAI 还不生成 `.base` 文件，因为这个格式还没对照 Obsidian 的文档核实过（reported-by-round） |

打开实时表格的步骤：

1. 设置 → 第三方插件（Community plugins）→ 浏览，搜索 "Dataview"，安装，启用。
2. 重新打开 `PAI/Home.md`。
3. 成功的话，每个 `dataview` 代码块都会变成一张表，列和下面的纯文本表格一样。如果还是显示代码，说明 Dataview 装了但没启用，到设置 → 第三方插件里启用它。

插件只改变状态的显示方式。关掉任何一个都不会丢东西，因为状态就是 front matter。

## 4. lane 笔记和决策笔记

**lane 笔记**（`PAI/rounds/<轮次>/<线名>.md`）的 front matter 字段有：

- `lane`、`status`（`running`、`idle`、`blocked-on-dialog`、`owner-draft`、`queued`、`exited`、`no-window`）、`dialog`（`trust`、`permission` 等）、`queued`、`current_id`、`last_cp`；
- `totals`：done / prototype / blocked / owner-only / doing / todo；
- `blockers`、`needs_owner`、`needs_owner_why`；
- `round`、`project`、`branch`、`worktree_state`。

下面是一张内容相同的表格。只读，不要改：你一改，PAI 就不会再更新这篇笔记。

**决策笔记**（`PAI/decisions/<轮次>-<ID>.md`）的字段有 `question`、`options`、`recommendation`、`default`、`deadline`、`blocks`、`status`（`open` 或 `answered`），还有两个**属于你**的字段：`choice` 和 `choice_note`。

PAI 写的每篇笔记还带有 `pai_managed: <种类>/1`、`pai_sha256` 和 `updated`，正文夹在 `<!-- pai:managed:begin pai-vault/1 -->` 和 `<!-- pai:managed:end -->` 之间。

## 5. 回答决策、批准交接

**回答决策。** 在决策笔记的 front matter 里，把 `choice:` 填成 `options` 中的一项（全文，或者它的第一个词，比如 `A`），需要的话再填 `choice_note:`。然后在跑这一轮的机器上运行：

```sh
pai decide answer --from-note "PAI/decisions/R6-20261122-D9.md"           # 只检查，什么都不改
pai decide answer --from-note "PAI/decisions/R6-20261122-D9.md" --write   # 在决策单里打勾
```

成功的话，源决策单里那一项会打上勾并多出一行 `回答：…`，笔记变成 `status: answered`。下面三种情况它会拒绝，并且什么都不写：

- 笔记里 PAI 写的部分被改过（`pai_sha256` 对不上）；
- 你的 `choice` 不在选项里；
- 笔记生成之后，源决策单里这个决策的文字变了（`source_sha`）。

遇到拒绝时，先读提示，然后改正 `choice`，或者请人重新生成这篇笔记。

**批准交接。** 读 `Agent Inbox/<轮次>/<线名>/cp-X/HANDOFF.md` 和它的 `NEXT-PR.md`。批准有两种方式，都会核对交接正文是否和封存时逐字节一致，依据是 `sealed_sha256` 和 `SEAL.json` 里的 sha256（reported-by-core：`pai/handoff.py`、`tests/test_handoff_v2.py`）。

*在 Obsidian 里：*

1. 在交接的 front matter 里，把 `approved: false` 改成 `approved: true`。光改这一处，什么都不会被批准。
2. 到跑这一轮的机器上运行：

   ```sh
   pai handoff approve PROJECT "Agent Inbox/R6-20261122/docs/cp-C" --from-vault
   ```

*在终端里：* 用 `pai handoff seal` 在 `approve` 那一行打印出来的命令。里面的 sha 是交接正文的 sha256，不是对整个文件跑 `sha256sum` 得到的值。

```sh
pai handoff approve PROJECT "Agent Inbox/R6-20261122/docs/cp-C" --expect-sha <seal 打印的 16 位以上十六进制>
pai handoff reject  PROJECT "Agent Inbox/R6-20261122/docs/cp-C" --expect-sha <…> --reason "哪里不对"
```

成功时：

- 交接旁边会出现 `DECISION.json`；
- 它的 front matter 变成 `approved: true`、`approval: approved`。
- 只有 front matter 会变，正文永远不会被改动。

封存之后正文如果被改过（哪怕只是在 Obsidian 里改了一个错字），命令都会拒绝（exit 2），什么都不批准，并且给笔记标上 `approval: stale`。把更正写进下一个 checkpoint，然后批准那一个。

## 6. PAI 绝不会对你的 vault 做的事

- 绝不写它没有创建过的笔记。没有 `pai_managed`、标记坏了、或者内容和 `pai_sha256` 对不上的笔记，都会报告为 `SKIPPED`，原样保留。想让 PAI 重新写，就把这篇笔记删掉。
- 笔记旁边有同步冲突副本时（`NAME.sync-conflict-<日期>-<时间>-<设备>.md` 或 `NAME (conflicted copy …).md`），绝不写这篇笔记；也绝不把冲突副本当成状态来读。
- 绝不自己回答决策或批准交接：`--write` 和 `approve` 都是你的命令。
- 同样的状态写两遍不会有任何变化：第二次 `--write-vault` 报告 0 处改动。
