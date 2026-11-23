# 用 `pai round` 做刷新 (b)：可以直接替换的版本

[English](REFRESH-b-round.md) | 简体中文

<!-- doc_check: pending pai round -->

**目前还没生效。** [REFRESH.zh-CN.md](REFRESH.zh-CN.md#b-每一轮的刷新) 的 (b) 部分写的仍然是 R6 的脚本。这一页是它的替换版本，等 round 线合并之后再换上（owner 决策 D-DOC-4）。

替换方法：把 REFRESH.zh-CN.md 里 `<!-- refresh-b:begin -->` 和 `<!-- refresh-b:end -->` 之间的内容，换成下面同样两个标记之间的内容（英文版 REFRESH.md 也一样），然后运行 `python3 -B tools/doc_check.py`。

命令写法见 `docs/ROUND.md`（reported-by-round）。下面每一条命令和参数，都在 2026-11-22 四条线的 scratch merge 上用 doc_check 核对过。

<!-- refresh-b:begin -->
每一轮都从**合并后**的 main 开始，在同一个 trusted root 下用**新的** worktree，发新填好的 prompt。以前 R6 脚本做的那几步，现在由 `pai round` 来做，整轮的信息保存在 `<vault>/Agent Inbox/<ROUND>/ROUND.json`。

**1. 先预览这一轮，再创建。**

```sh
pai round new pai R7-YYYYMMDD --prompt "99 Templates/PAI Worker Prompt R7.md" --vault "$VAULT"           # 预览；有东西挡路时以 1 退出
pai round new pai R7-YYYYMMDD --prompt "99 Templates/PAI Worker Prompt R7.md" --vault "$VAULT" --write   # 真正执行
```

它会做这些事：

- 用 `git ls-remote` 读 main 的 SHA（或者用 `--base SHA` 指定）；
- 找到唯一的主 clone（找到多个时会拒绝并列出来，这时加 `--repo 路径`）；
- 按 lane 填好并拆开 prompt，有占位符没填就拒绝；
- 在 trusted root 下建好 `worker/<ROUND>/<lane>` 的 worktree。

重复运行是安全的。如果某条 lane 显示 `CONFLICT`（有文件夹挡路，或者分支已经在别处签出），在你解决之前不会写入任何东西。

**2. 启动每条 lane**：在 tmux 里、带 Remote Control 启动，并贴上它的 prompt：

```sh
pai round launch docs --paste-prompt --write
pai round status                                  # 每条 lane 一行：running / idle / blocked-on-dialog / exited 等
```

如果某条 lane 显示 `blocked-on-dialog`，而且 dialog 是 `trust`，到那个窗口里自己回答文件夹信任提示。然后核对第一条回复：里面必须列出 worker 读过的文件和最先要做的条目 ID。

**3. 日常：** 运行 `pai round status`（加 `--write-vault` 会同时刷新 `PAI/Home.md`）；某条 lane 停在 checkpoint 时，运行 `pai round next LANE --send`。

**4. 所有 PR 都合并之后，收尾这一轮：**

```sh
pai round close              # 验收清单，以及会回收哪些 worktree
pai round close --write      # 只回收干净又空闲的 worktree；分支保留
```
<!-- refresh-b:end -->
