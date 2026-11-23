<!-- pai-prompts/2 begin name=checkpoint version=3 kind=checkpoint vars=RUN_ID,LANE,VAULT,BASE_SHA -->
## checkpoint：每个 Block 结束时交什么

目录：{{VAULT}}/Agent Inbox/{{RUN_ID}}/{{LANE}}/cp-<Block>/，Block 名是 1–16 个字母或数字；final 用 cp-final/。里面必须有：
- HANDOFF.md：用 pai-handoff/2 格式，包含下面列出的全部 front matter 键和全部段落；
- NEXT-PR.md：下一步的 PR 计划，格式见 next-pr.md（下面的 ### NEXT-PR.md 一节）；
- SHA256SUMS：本目录里其他文件和 Evidence 引用的文件的 sha256，每行"sha256  路径"。
封存后不再修改。要更正，写进下一个 checkpoint 的 Summary。
封存后跑 pai queue checkpoint <queue> <本 Block 最后一个 ID> --handoff <HANDOFF.md 路径>，把 sha 记进 queue log。

### HANDOFF.md（pai-handoff/2）

front matter，每个键都写，不知道的写 UNKNOWN：

    ---
    format: pai-handoff/2
    status: sealed
    approved: false
    project: <项目>
    round: {{RUN_ID}}
    lane: {{LANE}}
    checkpoint: cp-<Block>
    base_sha: {{BASE_SHA}}
    head_sha: <worktree 的 HEAD；没提交就和 base_sha 相同>
    queue: <queue 文件路径>
    queue_sha256: <queue.log 的 sha256>
    ---

正文依次是下面这些段落，段落名照抄：
- ## Summary：三五句，本 Block 做成了什么、没做成什么。
- ## State：三列表格 | State | Value | Source |，不多不少，固定五行 Implementation、Validation、Commit、Merge、Deployment。
  每行各写各的，不互相推断。Source 列只能是 real run、synthetic、fake agent、mock endpoint、JEV、reported-by-<线名>、self-report 之一，后面可以加括号说明，例如 real run (Arch, Python 3.14)。
  没做的写 none 或 not merged (owner-only)，不要写 UNKNOWN 来代替"没做"。
- ## Items：直接粘贴 pai queue table <queue> 的输出：表格 | ID | state | evidence | 和紧跟的 Totals 行，包括 todo 行。
  不手写、不删行、不改数字。
- ## Evidence：引用的文件先复制进本 cp 目录的 evidence/ 下，再写"路径  sha256（64 位）  生成它的命令"，sha256 直接粘贴 sha256sum 的输出。
  大于 5 MB 的文件不复制，只写原路径和 sha256，并在行尾注明 >5 MB。没有证据的行以 none 开头。不要截短 sha256，不要只写"见某处"。
- ## Decisions within authority：你自己定的事，每条写选了什么、为什么、怎么撤回。
- ## Decisions for the owner：按 decision-sheet.md 的格式写；这里列的事你都没有做。
- ## Blockers：每条一句原因，加上 owner 要执行的命令。没有就写 none。
- ## Next：写下一个要做的条目 ID，以及 RESTART.md 的完整路径。

### NEXT-PR.md

按 next-pr.md 写，五个小标题都要有。它引用的条目 ID 必须出现在 HANDOFF.md 的 Items 表里，数字和 HANDOFF.md 一致。
封存用 pai handoff seal（core 线；docs/HANDOFF-V2.md），它按上面的规则逐项检查。
<!-- pai-prompts/2 end -->
