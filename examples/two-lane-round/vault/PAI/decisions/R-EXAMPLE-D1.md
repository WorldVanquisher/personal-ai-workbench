---
pai_managed: "decision/1"
round: "R-EXAMPLE"
id: "D1"
status: "open"
question: "折扣怎么取整：discount() 返回小数还是四舍五入到分？"
options: ["A 保留小数", "B 四舍五入到 0.01"]
choice: ""
choice_note: ""
recommendation: "B"
default: "A"
deadline: ""
blocks: ["R-EXAMPLE-A-002"]
answer: ""
source: "Agent Inbox/R-EXAMPLE/DECISIONS.md"
source_sha: "2e140f85fb49ad8d"
updated: "2026-11-22T19:39:28Z"
pai_sha256: "6f78470f984838e24be59f9543fc8c9f2fcdf4622a8b0eb366ce2d47e7b1bfda"
---

<!-- pai:managed:begin pai-vault/1 -->
# D1 折扣怎么取整

折扣怎么取整：discount() 返回小数还是四舍五入到分？

| 项 | 内容 |
|---|---|
| 选项 | A 保留小数；B 四舍五入到 0.01 |
| 推荐 | B |
| 默认做法 | A |
| 阻塞 | R-EXAMPLE-A-002 |
| 来源 | [[Agent Inbox/R-EXAMPLE/DECISIONS\|DECISIONS.md]]（sha 2e140f85fb49） |
| 状态 | open |

回答：在上面的 front matter 里填 `choice:`（可以再填 `choice_note:`），然后运行
`pai decide answer --note "PAI/decisions/R-EXAMPLE-D1.md" --write`。PAI 会先核对来源决策单的这一段没有变过（sha 2e140f85fb49），再写回去。
<!-- pai:managed:end -->
