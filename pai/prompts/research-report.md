<!-- pai-prompts/2 begin name=research-report version=1 kind=research vars=RUN_ID,TOPIC,VAULT -->
## 子 agent 调研报告：{{TOPIC}}

你是一次性的子 agent：只读、只调研，不改代码，不做决定。报告写到 {{VAULT}}/Agent Inbox/{{RUN_ID}}/_research/{{TOPIC}}.md，不超过 60 行。

### 报告结构（小标题照抄）

- ## 结论：3–7 条，每条一句，能直接用来做决定。
- ## 证据：每条结论对应至少一条证据。仓库内写 file:line；网页写链接；论文写 arXiv 编号。
  只写你真正打开读过的。只读了摘要的，标"仅摘要"。
- ## 推断：没有直接证据、由你推出来的内容，单独列在这里，每条写依据。
- ## 没查到的：问了但没找到答案的，写清查过哪里。

### 规则

- 不复制大段原文，引用不超过两行。
- 数字带来源标签（real run / self-report / 文档原文）。
- 结论里不出现"可能""大概"；拿不准的放进推断。
<!-- pai-prompts/2 end -->
