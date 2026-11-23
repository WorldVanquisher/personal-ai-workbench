---
pai_managed: "decision/1"
round: "R-EXAMPLE"
id: "D2"
status: "open"
question: "README 语言：README 只写英文，还是中英两份？"
options: ["A 只英文", "B 中英两份"]
choice: ""
choice_note: ""
recommendation: "A"
default: "A"
deadline: ""
blocks: ["无"]
answer: ""
source: "Agent Inbox/R-EXAMPLE/DECISIONS.md"
source_sha: "3d4ff2d124a7911d"
updated: "2026-11-22T19:39:01Z"
pai_sha256: "0b3d9e59da4465c09efd82df6b9703b7c266af451568aec0e62a45b5b3584a3e"
---

<!-- pai:managed:begin pai-vault/1 -->
# D2 README 语言

README 语言：README 只写英文，还是中英两份？

| 项 | 内容 |
|---|---|
| 选项 | A 只英文；B 中英两份 |
| 推荐 | A |
| 默认做法 | A |
| 阻塞 | 无 |
| 来源 | [[Agent Inbox/R-EXAMPLE/DECISIONS\|DECISIONS.md]]（sha 3d4ff2d124a7） |
| 状态 | open |

回答：在上面的 front matter 里填 `choice:`（可以再填 `choice_note:`），然后运行
`pai decide answer --note "PAI/decisions/R-EXAMPLE-D2.md" --write`。PAI 会先核对来源决策单的这一段没有变过（sha 3d4ff2d124a7），再写回去。
<!-- pai:managed:end -->
