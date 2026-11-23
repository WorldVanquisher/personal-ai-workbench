# 刷新手册：升级 pai，开始每一轮新的工作

[English](REFRESH.md) | 简体中文

[FIRST-USE.zh-CN.md](FIRST-USE.zh-CN.md) 带你跑完第一轮。这一页管之后的每一轮，分五部分：

- (a) 升级 pai 本身；
- (b) 从合并后的 main 开始新的一轮；
- (c) R6 当天（2026-11-22）实际踩过的坑；
- (d) 日常命令速查；
- (e) worker、会话或机器停下来时怎么办。

术语见 [GLOSSARY.zh-CN.md](GLOSSARY.zh-CN.md)。

## (a) 升级 pai

当初怎么装的，就怎么升级。`pai doctor` 的第一行会告诉你当初是怎么装的：

- `bundle pai X intact: … match SHA256SUMS`：用的是发布归档；
- `package pai-workbench X intact: … match its RECORD`：用的是 uv 或 pip。

新版本号在[发布页](https://github.com/WorldVanquisher/personal-ai-workbench/releases)上查。下面的例子都用 `0.4.1`，换成你的版本号，填进 `V`。

**用发布归档**（每个旧版本都会保留，方便回滚）：

```sh
V=0.4.1
mkdir -p ~/Downloads && cd ~/Downloads
U=https://github.com/WorldVanquisher/personal-ai-workbench/releases/download/v$V
curl -fLO "$U/pai-workbench-$V.tar.gz" && curl -fLO "$U/pai-workbench-$V.tar.gz.sha256"
sha256sum -c "pai-workbench-$V.tar.gz.sha256" && tar -xzf "pai-workbench-$V.tar.gz"
"pai-workbench-$V/bin/pai" install
```

如果 `sha256sum -c` 显示 `FAILED`，删掉两个文件重新下载。

预期：

- 先显示 `launcher … -> …/versions/0.4.1/bin/pai (was 0.4.0)`；
- 然后是 `kept  earlier version(s) 0.4.0 in …/versions (delete them when satisfied)`。

任何一步失败，旧版本都照常可用。配置、任务笔记和凭据都不会被碰。

**回滚**到某个保留着的版本：在它的目录里运行 `install`。`ls ~/.local/share/pai-workbench/versions/` 会列出保留的版本。

```sh
~/.local/share/pai-workbench/versions/0.4.0/bin/pai install
```

预期：显示 `version … 0.4.0 (already installed and intact; kept)` 和 `launcher … -> …/versions/0.4.0/bin/pai (was 0.4.1)`。

**用 uv：**

```sh
V=0.4.1
uv tool install --force "git+https://github.com/WorldVanquisher/personal-ai-workbench@v$V"
```

成功时显示 `Installed 1 executable: pai`，`pai --version` 打印 `pai 0.4.1`。如果 uv 说 tag 不存在，到发布页核对版本号；tag 的写法是 `v` 加版本号。要回滚，就把 `V=` 改成旧版本号，再运行这两行。

**用 venv 里的 pip**（FIRST-USE 的方式 B2）：

```sh
V=0.4.1
~/.venvs/pai/bin/pip install --upgrade "git+https://github.com/WorldVanquisher/personal-ai-workbench@v$V"
```

成功时，pip 的最后一行是 `Successfully installed pai-workbench-0.4.1`，`pai --version` 打印 `pai 0.4.1`。

- 如果 pip 报 `Cannot import 'setuptools.build_meta'`，运行 `~/.venvs/pai/bin/pip install setuptools` 再重来。
- 要回滚，就把 `V=` 改成旧版本号，再运行这两行。

uv 和 pip 都不保留旧版本的副本，所以回滚时要重新下载。

**确认升级成功：**

```sh
pai --version        # 新版本号
pai doctor           # 第一行是 "… intact"；ESSENTIAL 里每一行都是 ok
```

如果 `pai --version` 还是旧版本，说明 PATH 里有另一个 `pai` 排在前面：运行 `type -a pai`，删掉多余的那个，或者调整顺序。如果在 uv 或 pip 装的 pai 上运行 `pai install`，它会停下并说 "an installed Python package …"：这时改用上面 uv 或 pip 的那一行。

**升级之后**，如果新版本带了 prompt 库（reported-by-prompts；目前在 prompts 线里，还没发布），先对比项目里的那一份，再写入更新：

```sh
pai prompt diff --dest <vault>/PAI/prompts
pai prompt init --dest <vault>/PAI/prompts --write
```

你手工改过的部分在托管标记之外，会保留下来。

## (b) 每一轮的刷新

<!-- refresh-b:begin -->
每一轮都从**合并后**的 main 开始，在同一个 trusted root 下用**新的** worktree，发新填好的 prompt。以前 R6 脚本做的那几步，现在由 `pai round` 来做，整轮的信息保存在 `<vault>/Agent Inbox/<ROUND>/ROUND.json`。

**0. 每台机器做一次：写明项目的 GitHub 仓库。** `pai round new` 靠 GitHub 远端来找主 clone。没有这个文件时，它会停下并提示 `which GitHub repository? give --github OWNER/NAME or set [project.pai] github`：

```sh
mkdir -p ~/.config/pai && printf '[project.pai]\ngithub = "OWNER/NAME"\n' >> ~/.config/pai/round.toml
```

其他设置（vault、trusted root、tmux 会话）都是可选的，见 `docs/ROUND.md` 的 Configuration 一节。查找 clone 的范围是 `~` 和 `/srv`，往下五层。

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

如果某条 lane 显示 `blocked-on-dialog`，而且 dialog 是 `trust`，到那个窗口里自己回答文件夹信任提示。这时 `launch` 什么都没发，所以再运行一次同一行 `pai round launch docs --paste-prompt --write`：它不会再启动新会话，只把 prompt 贴进去（`sent (… bracketed paste True, then Enter)`）。然后核对第一条回复：里面必须列出 worker 读过的文件和最先要做的条目 ID。

**3. 日常：** 运行 `pai round status`（加 `--write-vault` 会同时刷新 `PAI/Home.md`）；某条 lane 停在 checkpoint 时，运行 `pai round next LANE --send`。它读这条 lane 的 `RESTART.md`（最新的 `cp-*` 文件夹，或者 lane 的工作目录），没有就拒绝。

**4. 所有 PR 都合并之后，收尾这一轮：**

```sh
pai round close              # 验收清单，以及会回收哪些 worktree
pai round close --write      # 只回收干净又空闲的 worktree；分支保留
```

只要 worktree 里的 Claude 会话还在运行，哪怕是空闲的，这个 worktree 也会保留（`keep … in use by …`）。先结束那个会话（在它的窗口里输入 `/exit`），再运行一次 `pai round close --write`；所有 lane 的 worktree 都回收之后，ROUND.json 会归档为 closed。
<!-- refresh-b:end -->

## (c) 常见的坑

下面每一条都是 R6 当天实际踩过的。

| 坑 | 会发生什么 | 怎么做 |
| --- | --- | --- |
| **容器路径和主机路径不是一回事** | Syncthing 跑在 Docker 里，它的界面把 vault 显示为 `/vaults/MainVault`。这个路径在主机上不存在，主机上是 Syncthing 的卷所映射的那个文件夹（R6 的 NAS 上在 `/srv/…/obsidian/MainVault`）。用容器路径填 prompt，worker 就会去找一个不存在的文件夹。 | 永远用主机路径。`r6-prepare.sh` 会根据自己所在的位置推算出来，也可以用 `VAULT=<主机路径>` 指定。在主机上用 `ls "$VAULT/Agent Inbox"` 核对。 |
| **同步有延迟** | Syncthing 在设备之间复制笔记，要几秒到几分钟。2026-11-22 从 Windows 到 NAS 的同步慢过两次（owner 报告），所以 NAS 上的 worker 可能读到的是刚在 Windows 上改过的笔记的旧版本。 | 让 worker 读某篇笔记之前，先在两台机器上核对校验和：两边各运行 `sha256sum "$VAULT/PAI/decisions/R6-20261122-D9.md"`，一致了再继续。把 sha 写进给 worker 的消息里（"读 X，sha256 abc123…"），它也能自己核对。用 core 线的 `pai vault wait FILE --sha SHA --timeout 120`，跑这一轮的机器会一直等到这个版本同步过来。 |
| **哪些不同步：`.stglobalignore`** | 每台设备自己的文件（Obsidian 的 `workspace.json`）每点一下就会冲突；worker 产生的东西，比如 `__pycache__`，也不该同步。 | 共享的忽略规则写在 `$VAULT/.stglobalignore` 里，它本身会同步。每台设备自己的 `.stignore` 里只写一行：`#include .stglobalignore`。要忽略的有 `.obsidian/workspace.json`、`.obsidian/workspace-mobile.json`、`.obsidian/**/*.sync-conflict-*` 和 `(?d)__pycache__`。不要忽略笔记：笔记的冲突副本必须同步过来，你才看得到。 |
| **同步冲突文件** | 两台设备在同步之前都改了同一篇笔记。Syncthing 两份都留：`NAME.md` 旁边多出 `NAME.sync-conflict-<日期>-<时间>-<设备>.md`。PAI 永远不把冲突副本当成状态来读，旁边有冲突副本的笔记也不会去重写（reported-by-round）。 | 用 `find "$VAULT" -name '*.sync-conflict-*'` 找出来。逐个和原笔记 `diff`，把要保留的内容留在 `NAME.md` 里，然后删掉副本。之后 PAI 下一次 `--write-vault` 会照常更新这篇笔记。 |
| **一台机器上有多份 clone** | 同一个仓库有两个 checkout，worktree、fetch 和 `pai` 的默认设置可能落在错的那一份上。 | 明确指定用哪一份：脚本用 `PAI_REPO=路径`（或 `FLH_REPO=`），pai 用 `pai init --repo 路径`，git 用 `git -C 路径 …`。 |
| **文件不存在时检查反而通过** | 旧的占位符检查用重定向写入输入文件。prompt 文件不存在时，它先生成一个空文件，在空文件里找不到占位符，于是报告"已全部填好"。 | 先检查文件存在且非空：`[ -s "$f" ] \|\| { echo missing; exit 1; }`。`r6-prepare.sh` 已经这样做了。 |
| **prompt 正文里的花括号被当成占位符** | 如果 prompt 正文在*讲*占位符（两个花括号夹着大写字母），"没有遗漏"的检查就会失败。 | 用文字描述它（"两个左花括号后面跟着大写字母"），R6 的 prompt 就是这么写的。用 prompts 线的 `pai prompt render` 时，写成 `\{{NAME}}`（reported-by-prompts，docs/PROMPTS.md §2）。 |
| **每个新文件夹都弹信任提示** | Claude Code 对每个新目录问一次。每轮都把 worktree 放在新位置，就意味着每轮每条 lane 都要回答一次提示，每个提示后面都有一个任务在等。 | 所有轮次都放在同一个 trusted root 下：`~/workspace/pai-workers/<RUN>/<线名>`。 |
| **删掉了正在使用的 worktree** | `git worktree remove --force` 会丢掉未提交的工作；还在运行的会话也会继续往已删除的文件夹里写。 | 只删干净又空闲的 worktree：`git -C WT status --porcelain` 必须没有输出，并且没有进程的 cwd 在里面（`r6-worktrees.sh` 会检查 `/proc/*/cwd`）。永远不删分支。 |
| **工具版本和 CI 不一致** | FLH 在 Node 22.12.0 上有 10 个录音器测试失败，而在 Node 22.23.5（CI 用的版本）上都通过。 | 测试前固定并检查版本：Node 看 `.nvmrc`（`node -v` 必须一致），Go 看 `go.mod`（`go version`）。版本不对就停下，不要凑合跑。 |

## (d) 日常操作速查

| 我想…… | 命令 |
| --- | --- |
| 看一次 `pai run` 的所有 lane | `pai status`（`--brief` 每条 lane 一小段；`--json` 给脚本用） |
| 看 worker 做到哪个条目了 | `pai queue status ~/.cache/pai-<RUN>/<线名>/queue` |
| 给新会话生成续跑 prompt | `pai queue restart ~/.cache/pai-<RUN>/<线名>/queue` |
| 核对交接的 Items 表和 queue 是否一致 | `pai queue check QUEUE HANDOFF.md` |
| 记录一个 checkpoint 和它的 sha256 | `pai queue checkpoint QUEUE R6-XXX-NNN --handoff HANDOFF.md` |
| 给 `pai run` 的某条 lane 派下一份笔记 | `pai next a` |
| 往 lane 里发一段长消息 | `pai paste a message.md --enter` |
| 在浏览器里看所有 lane | `pai web` |
| 往 vault 写一份交接草稿 | `pai handoff new PROJECT --task-id ID --summary "…"`（全部字段见 `pai handoff new --help`） |
| 看会发布什么 | `pai pr plan a`，然后 `pai pr check a` |
| 收掉一轮的进程（文件保留） | `pai cleanup`，然后 `pai cleanup --apply --stop-running` |
| 合并之后要一个新的 worktree | `pai worktree refresh`（只打印命令，什么都不改） |

**写接手包**（清空 mastermind 的上下文之前）：

1. 把最近的决定写进 `COORDINATOR-LOG.md`；
2. 每条 lane 写下它的 queue 状态和最新 checkpoint 的路径。

`pai round brief` 以后会自动生成这个（计划中，round 线）。

**收尾一轮：**

1. 每条 lane 都有封存好的 final checkpoint，里面有 `HANDOFF.md` 和 `NEXT-PR.md`；
2. 决策单已经回答；
3. PR 由你合并；
4. `pai cleanup --apply --stop-running`（`pai run` 的 lane），或者结束 tmux 会话；
5. 用 `r6-worktrees.sh`（先 plan，再 apply）删掉干净、空闲的 worktree。

## (e) 恢复

不管是什么停了，工作都安全地留在磁盘上：

- worktree 和它的分支；
- queue 和它的日志（`~/.cache/pai-<RUN>/<线名>/queue.log`，每次 mark 都会 fsync）；
- `Agent Inbox/<RUN>/<线名>/cp-*` 里每一个封存好的 checkpoint。

恢复的意思是开一个会话去读这些状态，而不是把工作重做一遍。

先看这条 lane 做到了哪里：

```sh
pai queue status ~/.cache/pai-<RUN>/<线名>/queue     # 按 ID 列出 done / blocked / todo，以及 "next: R6-…"
pai queue show   ~/.cache/pai-<RUN>/<线名>/queue     # checkpoint 列表；每一个都应该显示 "matches its recorded sha256"
```

如果 `show` 显示某份交接 **no longer matches**，说明它在封存之后被改过。续跑之前先看清改了什么。

### worker 提前停了

你会看到：会话空闲，最后一条消息既不是 "final"，也不是 "有意停在 cp-X"。

1. 运行 `pai queue status QUEUE`，记下 `next:`。
2. 在会话里输入：`Continue from R6-XXX-NNN (pai queue next QUEUE). Rules unchanged.`
3. 成功的话，它的下一条消息会提到这个 ID，并把它标成 `doing`。如果它又从 001 开始，就让它停下，改为粘贴 `pai queue restart QUEUE` 的输出。

### 上下文满了

你会看到：worker 说它停在 cp-X，它的工作目录里有 `RESTART.md`。

1. 运行 `pai queue show QUEUE`：cp-X 在列表里，而且显示 `matches its recorded sha256`。如果没有 cp-X，趁会话还在，先让 worker 把它封存。
2. 在那个会话里输入 `/clear`（或者在**同一个** worktree 里开一个新会话，见 (b) 的第 4 步）。
3. 粘贴 `~/.cache/pai-<RUN>/<线名>/RESTART.md` 里的开场白。
4. 成功的话，它的第一条回复会列出重新读过的东西，以及第 1 步看到的下一个 ID。

### 5 小时额度用完了

你会看到：Claude Code 说用量到了上限，以及什么时候重置。

1. 这条 lane 什么都不用动。**不要**再开第二个会话，否则两个会话会改同一个 worktree。
2. 过了重置时间，运行 `pai queue status QUEUE`，记下 `next:`，然后在会话里输入：`Continue from R6-XXX-NNN (pai queue next QUEUE). Rules unchanged.`
3. 成功的话，它的下一条消息会提到这个 ID。如果它还是说额度到了上限，说明还没到重置时间：等一等，再做第 2 步。如果有好几条 lane 都停了，一条一条地续跑。

### Remote Control 断线了

你会看到：claude.ai 显示会话离线，或者 `pai status` 显示 `rc not-connected`，但 tmux 窗口还在运行。

1. `tmux attach -t r6`（R6 那种 lane）或者 `pai attach LANE`（`pai run` 的 lane）。worker 还在干活，你可以在这里跟进，也可以输入。
2. 用 `Ctrl-b d` 离开。这只是脱离，什么都不会停。
3. 如果窗口里显示的是 shell 提示符而不是 Claude Code，说明会话已经退出了，按下一节处理。

### 会话退出了，或者机器重启了

你会看到：`tmux ls` 里没有会话（或者显示 `no server running`），`pai status` 显示 `exited`。

1. `pai run` 的 lane：运行 `pai resume`，它会在原来的分支上重新启动已关闭的 worker。成功的话，`pai status` 会显示这条 lane 又在 `running`。
2. R6 那种 lane：在**同一个** worktree 里重新启动会话：

   ```sh
   tmux new-session -d -s r6 -n <线名> -c ~/workspace/pai-workers/<RUN>/<线名>   # 机器重启之后
   tmux new-window -t r6 -n <线名> -c ~/workspace/pai-workers/<RUN>/<线名>       # 会话 r6 还在的话
   tmux send-keys -t r6:<线名> 'claude --remote-control --name "PAI <RUN> <线名>"' Enter
   pai queue restart ~/.cache/pai-<RUN>/<线名>/queue                              # 打印续跑 prompt
   ```

   把这个 prompt（或者 `RESTART.md` 的开场白）粘贴进会话。成功的话，第一条回复会提到 `pai queue status` 里的下一个 ID。如果出现了信任提示，自己去回答（`tmux attach -t r6`）。
3. 不要用 `r6-worktrees.sh apply` 来"重置"一条 lane：已经存在的 worktree 它会跳过，从不重置。
