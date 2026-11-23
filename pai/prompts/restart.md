<!-- pai-prompts/2 begin name=restart version=1 kind=restart vars=RUN_ID,LANE,NEXT_ID,CHECKPOINT,WORK_DIR,VAULT -->
续跑 {{RUN_ID}} / {{LANE}}，从 {{NEXT_ID}} 开始。
1. 依次读这两个文件：{{WORK_DIR}}/RESTART.md 和 {{VAULT}}/Agent Inbox/{{RUN_ID}}/{{LANE}}/{{CHECKPOINT}}/HANDOFF.md。
2. 跑 pai queue status {{WORK_DIR}}/queue，核对和 HANDOFF.md 的 Totals 一致；不一致先报告。
3. 规则和第一次派发时相同：打开 RESTART.md 里列出的 worker-base 和 lane prompt，照它们执行。
第一条回复只写：已读的两个文件、queue 的 Totals、接下来的 3 个 ID。
<!-- pai-prompts/2 end -->
