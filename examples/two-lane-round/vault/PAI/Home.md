---
pai_managed: "home/1"
title: "PAI"
rounds_open: 1
lanes_need_owner: 0
decisions_open: 2
handoffs_unapproved: 1
updated: "2026-11-22T19:39:01Z"
pai_sha256: "d50d5bf2c30c5512cd69c55cbcad6ce523aeb3140ddee7a77b0f719f7aa18884"
---

<!-- pai:managed:begin pai-vault/1 -->
# PAI

PAI 生成（`pai round status --write-vault`）。每一块都有 Dataview 查询（装了 Dataview 时显示）和纯文本表格（不装插件也能看，生成时的快照）。状态以笔记的 front matter 为准，查询只是视图。

## 进行中的轮次

```dataview
TABLE project, base, lanes, needs_owner AS "需要 owner" FROM "PAI/rounds" WHERE pai_managed = "round/1" AND status = "open"
```

| 轮次 | 项目 | lane | 需要 owner |
|---|---|---|---|
| [[PAI/rounds/R-EXAMPLE/ROUND\|R-EXAMPLE]] | shop | 2 | 0 |

## 需要 owner 处理的 lane

```dataview
TABLE round, status, current_id, needs_owner_why AS "原因" FROM "PAI/rounds" WHERE pai_managed = "lane/1" AND needs_owner = true
```

| lane | 轮次 | 状态 | 原因 |
|---|---|---|---|
| （没有） | | | |

## 还没回答的决策

```dataview
TABLE round, question, default, deadline FROM "PAI/decisions" WHERE pai_managed = "decision/1" AND status = "open"
```

| 决策 | 轮次 | 问题 | 默认做法 |
|---|---|---|---|
| [[PAI/decisions/R-EXAMPLE-D1\|D1]] | R-EXAMPLE | 折扣怎么取整：discount() 返回小数还是四舍五入到分？ | A |
| [[PAI/decisions/R-EXAMPLE-D2\|D2]] | R-EXAMPLE | README 语言：README 只写英文，还是中英两份？ | A |

## 还没批准的交接

```dataview
TABLE lane, checkpoint, status FROM "Agent Inbox" WHERE approved = false AND checkpoint SORT file.mtime DESC
```

| 交接 | 轮次 | lane | checkpoint |
|---|---|---|---|
| [[Agent Inbox/R-EXAMPLE/a/cp-A/HANDOFF\|HANDOFF]] | R-EXAMPLE | a | cp-A |
<!-- pai:managed:end -->
