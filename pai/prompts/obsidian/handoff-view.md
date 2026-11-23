---
type: pai-view
template: pai-handoff-view/1
project: 
round: 
created: {{date:YYYY-MM-DD}}
---
# {{title}}

这一页只用来看，不是状态来源：状态在各 lane 的 HANDOFF.md front matter 里。

## 有 Dataview 时

```dataview
TABLE lane, checkpoint, status, approved
FROM "Agent Inbox"
WHERE format = "pai-handoff/2" AND round = this.round
SORT lane ASC, checkpoint ASC
```

## 没有插件时

打开 `Agent Inbox/<round>/<lane>/cp-*/HANDOFF.md`；或者看 PAI 生成的纯文本看板（round 线的 `pai round status`）。
