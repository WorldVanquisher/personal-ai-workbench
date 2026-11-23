<!-- pai-prompts/2 begin name=lane version=1 kind=lane vars=LANE,LANE_CODE,RUN_ID -->
## 线名：{{LANE}}。线缩写：{{LANE_CODE}}。

### 本线的文件

（mastermind 填写：本线可以改的文件或目录；"单行追加"文件；只读参考的文件。）

### 条目写法

每个条目一行起头，格式固定，后面可以有缩进的补充行：

    {{RUN_ID}}-{{LANE_CODE}}-NNN  做什么（一句话，动词开头）
                  完成标准：做到什么算 done（可以检查的结果：文件、命令和它的输出、测试名）
                  证据：要交的东西和标签（real run / synthetic / fake agent / mock endpoint / JEV / self-report）
                  依赖：无；或 依赖 <线名> 的 <ID>，以及没等到时怎么办

- 编号在本线内连续，不和别的线重复，不从 001 重新编。
- 每个 Block 放 10 条左右；Block 的最后一条写成"封存 cp-<Block 字母>"；整个 lane 的最后一条写成 final。

### 完成标准和证据

- 完成标准写成别人能复查的结果。"改好""优化"不算完成标准。
- 需要真实服务、付费或 owner 操作的条目，在完成标准里写明；拿不到时标 blocked 或 owner-only，不要降级成 mock 再报 done。

### 依赖的标注

- 依赖别的线：写"依赖 <线名> 的 <ID>"，再写没等到时的做法（先做不依赖的部分；或者按某个默认格式先做，标"待对齐"）。
- 被别的线依赖：写"<线名> 的 <ID> 会用到"，提醒自己先交接口。
- 依赖 owner：写"owner-only：<要 owner 做的那一步>"。
<!-- pai-prompts/2 end -->
