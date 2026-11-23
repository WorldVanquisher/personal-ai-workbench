<!-- pai-prompts/2 begin name=next-pr version=1 kind=next-pr vars=RUN_ID,LANE,BASE_SHA -->
## NEXT-PR.md：下一步的 PR 计划（{{RUN_ID}} / {{LANE}}）

worker 写，owner 批准后由 pai worktree publish 执行。worker 自己不运行其中任何 git 写命令。小标题照抄：

- ## Branch：写三项：worker 分支名、base 提交（{{BASE_SHA}}）、要合并进的目标分支。
- ## Commits：每个 commit 一小节：
  - git add 列表：一行一个路径，只列这个 commit 的文件，不用通配符，不用 git add -A；
  - commit message：第一行不超过 72 个字符，正文写为什么要改；
  - 跑项目的 publish metadata 检查（PAI 是 python3 -B swarm/publish_check.py --squash-message-file <文件>），把命令和结果写在 message 后面；
  - 列出这个 commit 实现的条目 ID。
- ## PR body：PR 正文草稿。数字和 HANDOFF.md 一致，每个数字带来源标签；不写 session 链接，不写 AI 署名。
- ## Checks before merge：合并前还要跑的检查，每条写命令；标明哪些要在合并后的 main 上重跑。
- ## Not included：故意没放进这个 PR 的改动和原因。
<!-- pai-prompts/2 end -->
