# 第一次使用：从零到跑完第一轮

[English](FIRST-USE.md) | 简体中文

这一页带你从一台什么都没装的机器开始，跑完一轮只有一个 worker 的工作。每一步都写了三样东西：命令、应该看到什么、看到别的怎么办。

写这一页之前，我在全新的 home 目录里实际走了两遍：一遍用发布归档，一遍用 Python 包。走的过程中所有卡住的地方，这一页都有答案（2026-11-22，pai 0.4.0，Linux，假 agent）。

*lane*、*worker*、*handoff*、*trusted root* 这些词见[术语表](GLOSSARY.zh-CN.md)。这些步骤真正在哪些平台上跑过，见 [FIRST-USE-PLATFORMS.md](FIRST-USE-PLATFORMS.md)（英文）。

> **必须由人来做的事。** 其余的准备工作 pai 都会做好，但下面五件事 pai 从不替你做，worker 也不能做：
>
> 1. **回答文件夹信任提示。** agent 问要不要信任某个文件夹时，由你读问题、回答（`pai trust`）。见[第 6 步](#6-第一轮一个-worker)。
> 2. **登录。** `claude` 然后 `/login`，或者 `codex login`。pai 只检查你有没有登录。
> 3. **合并。** 检查通过后 pai 可以开 PR（`pai pr open`），在 GitHub 上合并由你来做。
> 4. **定预算。** 开几个 worker、用哪个模型、每轮花多少钱。pai 显示它能看到的花费，但从不替你提高上限。
> 5. **回答决策单。** 每个问题都附了选项和推荐，由你勾选。

## 1. 需要准备什么

- Linux、WSL（Ubuntu）或 macOS。workbench 在 macOS 上还没实际跑过，见平台页。原生 Windows 上请把 pai 装进 WSL。
- Python 3.11 或更新、`git`、`tmux`。用下面的命令检查：

  ```sh
  python3 --version && git --version && tmux -V
  ```

  预期：三行输出，例如 `Python 3.12.3`、`git version 2.43.0`、`tmux 3.4`。要求是 Python 3.11 或更高、任意 git 2.x、任意 tmux 3.x。

  如果缺了某一个（`command not found`），或者版本太旧：

  - Ubuntu 或 WSL：`sudo apt-get install -y python3 git tmux`
  - Fedora：`sudo dnf install -y python3 git tmux`
  - Arch：`sudo pacman -S python git tmux`
  - macOS：`brew install python git tmux`

  装好后再跑一遍上面的检查命令。装好 pai（第 2 步）之后，`pai setup --install-missing` 也能帮你装 git 和 tmux。Python 低于 3.11 时，`pai` 会停下，并说出它找到的版本。
- 以后做真实的工作时，还需要装好并登录 Claude Code 或 Codex。**这一页用不到它们**：第 4 步的演练用的是假 worker。

## 2. 安装 pai

下面两种方式选**一种**。拿不准就选方式 A：下载之后不再需要联网，而且会保留上一个版本，方便回滚。

### 方式 A：发布归档（下载完就不需要联网，也不需要 Python 打包工具）

从[发布页](https://github.com/WorldVanquisher/personal-ai-workbench/releases)下载 `pai-workbench-<版本>.tar.gz` 和对应的 `.sha256`，或者直接用下面的命令下载。在仓库的 checkout 里也可以自己生成：`python3 -B tools/build_bundle.py --out dist`。

```sh
mkdir -p ~/Downloads && cd ~/Downloads
U=https://github.com/WorldVanquisher/personal-ai-workbench/releases/download/v0.4.0
curl -fLO "$U/pai-workbench-0.4.0.tar.gz" && curl -fLO "$U/pai-workbench-0.4.0.tar.gz.sha256"
sha256sum -c pai-workbench-0.4.0.tar.gz.sha256     # macOS：shasum -a 256 -c …
tar -xzf pai-workbench-0.4.0.tar.gz
pai-workbench-0.4.0/bin/pai install
```

预期：

- 先看到 `pai-workbench-0.4.0.tar.gz: OK`；
- 然后 `install` 打印 `76 files match SHA256SUMS`、版本目录（`~/.local/share/pai-workbench/versions/0.4.0`）和启动器（`~/.local/bin/pai`）。

如果它说 **`~/.local/bin is not on PATH`**，就运行它打印的这两行：

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc    # 以后每个新终端都生效
export PATH="$HOME/.local/bin:$PATH"                          # 当前这个终端立即生效
```

如果 `sha256sum -c` 显示 `FAILED`，删掉两个文件重新下载，不要安装。如果 `bin/pai install` 报错停下，说明什么都没改；错误信息里会写出是哪个文件或文件夹的问题（例如 `~/.local/share` 不可写）。处理好之后，再运行同一行命令。

手册（README、QUICKSTART、INSTALL 和本页）留在解压出来的文件夹里，安装后的版本目录里也有一份。

### 方式 B：Python 包

这个包里**没有手册的本地副本**，请到 GitHub 上的 `docs/` 目录看。先看看有没有 uv：运行 `uv --version`。打印出版本号就用 B1；显示 `command not found` 就用 B2。

**B1，用 [uv](https://docs.astral.sh/uv/)：**

```sh
uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.4.0
```

成功时显示 `Installed 1 executable: pai`。如果 uv 提示它的 bin 目录不在 PATH 里，运行 `uv tool update-shell`，然后开一个新终端。

**B2，没有 uv（用虚拟环境加 pip）：**

```sh
python3 -m venv ~/.venvs/pai
~/.venvs/pai/bin/pip install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.4.0
mkdir -p ~/.local/bin && ln -s ~/.venvs/pai/bin/pai ~/.local/bin/pai
```

成功时，pip 的最后一行是 `Successfully installed pai-workbench-0.4.0`，`ln` 什么都不输出。`~/.local/bin` 必须在 PATH 里，用方式 A 里那两行 `PATH` 命令；这种方式不会替你打印它们。

B2 出错时：

- `python3 -m venv` 报 `ensurepip is not available`：先装 venv 模块（Ubuntu 或 WSL 上是 `sudo apt-get install -y python3-venv`），再重来。
- pip 报 `Cannot import 'setuptools.build_meta'`：pip 构建这个包需要 setuptools，但没能下载下来。连上网络，或者先运行 `~/.venvs/pai/bin/pip install setuptools`，再重跑安装那一行。
- `ln` 报 `File exists`：那里已经有一个旧的 `pai`。用 `ls -l ~/.local/bin/pai` 看一下，如果是你自己装的就删掉，再重跑 `ln` 那一行。

### 确认安装成功

```sh
pai --version
```

预期：`pai 0.4.0`。如果看到 **`pai: command not found`**，开一个新终端，或者运行上面那行 `export PATH=…`。

## 2a. 一个带远端的 Git 仓库

pai 要在一个 Git checkout 里运行，而且它的 `origin` 上要有 `main` 分支，因为每一轮都从拉下来的 `origin/main` 开始。用你项目的 clone 就行：

```sh
git clone https://github.com/YOU/yourproject.git ~/code/yourproject
cd ~/code/yourproject
```

想练习、又不想碰真实项目的话，可以建一个用完就扔的仓库，用本地目录充当"远端"：

```sh
git init -q --bare -b main ~/code/practice-origin.git
git clone -q ~/code/practice-origin.git ~/code/practice && cd ~/code/practice
echo '# practice' > README.md && git add README.md && git commit -qm init && git push -q origin main
```

这时 Git 提示 `You appear to have cloned an empty repository` 是正常的。远端是本地目录时，pai 开不了 GitHub PR；本页其余的步骤都能照常做。

检查一下：

```sh
git remote -v && git log --oneline -1 origin/main
```

成功时，会看到两行 `origin` 和一个提交（`init`）。

- 如果提示 `origin/main` 不存在，说明刚才没推上去，运行 `git push -u origin main`。
- 如果 `git commit` 提示 `Please tell me who you are`，先设一次名字（`git config --global user.name "Your Name"` 和 `user.email`），再重跑最后那一行。

## 3. 检查机器：`pai doctor`

```sh
cd ~/code/practice        # 你的 checkout
pai doctor
```

预期输出：

- `ESSENTIAL` 段：pai、pai on PATH、git、tmux、ps 各一行，都是 `ok`；装了 agent 的话也会列出来。
- `OPTIONAL` 段：gh。
- 最后是：

```
info           project                …/practice is a Git checkout without pai defaults yet   -> pai init
doctor changed nothing. (No pai project here: `pai init` in your checkout.)
```

`pai doctor` 什么都不改。这一页用不到 `claude` 或 `codex`，没装也没关系。

如果 `ESSENTIAL` 里有一行不是 `ok`，这一行末尾会有 `->`，后面跟着修复它的命令。运行那条命令，再跑 `pai doctor`，直到这一段全是 `ok`。常见的几种：

- 缺 `tmux` 或 `git`：`pai setup --install-missing`。
- "pai on PATH" 那一行指向的是另一个 pai：按第 2 步改 PATH。
- `pai` 那一行本身不是 `ok`（文件和 SHA256SUMS 或包的 RECORD 对不上）：安装的副本被改过了，重新安装（第 2 步）。

## 4. 离线演练：`pai guide`

```sh
pai guide
```

大约 5 秒跑完。pai 会在 `/tmp/pai-guided-demo-…` 下建一个用完就扔的演示：一个合成项目、一份任务笔记、一个**假的** worker。然后用正常的命令走一遍：预演（dry-run）、正式运行、看状态、看 worker 的交接、收尾。

预期的最后几行：

```
Done: the guided run went plan -> dispatch -> delivered -> completed, with a fake worker.
Remove this demo: pai guide --cleanup /tmp/pai-guided-demo-XXXX
```

看完之后，运行它打印的那行 `--cleanup`。这个命令一次只接受**一个**文件夹，有两个演示就运行两次。

如果最后没有出现 `Done:`：

- 显示 `pai guide needs tmux (the run itself uses them). Nothing was created.`（或者缺的是 `git`）：先把它装上（第 1 步），再运行 `pai guide`。
- 卡在第 3 步（dispatch）大约 20 秒后停下：运行 `pai doctor`，把所有不是 `ok` 的行修好。重试之前，用它打印的 `pai guide --cleanup …` 删掉做了一半的演示，然后再运行 `pai guide`。
- 其他情况：保留演示文件夹，把输出的最后 20 行和 `pai doctor` 的输出一起报告。

## 5. 保存这个仓库的默认设置：`pai init`

```sh
pai init
```

它会问几个问题，直接回车就保留 `[方括号]` 里的值。按下表回答：

| 问题 | 输入什么 |
| --- | --- |
| `Default agent [not set yet]` | claude 输入 `1`，codex 输入 `2` |
| `Default model …`、`Reasoning effort …` | 回车，用 agent 自己的默认值 |
| `Remote [origin]`、`Base branch [main]` | 回车 |
| `Task notes folder (an Obsidian folder, or Enter for pai's own …)` | 回车。**不需要** Obsidian，笔记就是普通的 Markdown 文件 |
| `Review checks, NAME=COMMAND …  [-]` | **填一个真实的检查**，例如 `test=make test`，原因见表下 |
| `Save to …/projects/<名字>.toml? [Y/n]` | 回车 |
| `Enter JEV API key (optional)` | 回车。JEV 是可选的第二意见（见[术语表](GLOSSARY.zh-CN.md#jev)） |

**现在就设一个检查。** 没有检查时，`pai pr` 什么都不会发布，会报 `BLOCKED no approved check is configured`。检查是在每次 `pai run` 开始时复制进去的，所以之后再加的检查，要到**下一次** `pai run` 才生效。练习仓库用 `files=test -f CONTRIBUTING.md` 就够了。

预期的结尾：

```
saved       ~/.config/pai-workbench/projects/practice.toml
Next:
  pai doctor
  pai task new "What the agent should do" --body "How to check it" --write
  …
```

然后再跑一次 `pai doctor`，最后几行里会多出 `ok  project  practice: …`。

如果 `pai init` 中途停下了：

- `the base branch of origin is not recorded locally`：加上 `--base main`，见下文。
- `… is not inside a Git repository. Run it in your checkout, or pass --repo PATH.`：`cd` 进你的仓库（第 2a 步），再运行一次。
- 以后想改某个回答：用 `pai init --update`，只传要改的值，例如 `pai init --update --check "test=make test"`。`pai config edit` 会打开整个配置文件。

在脚本里、没有终端可以交互时，用：

```sh
pai init --default-agent claude --base main --check "test=make test" --no-input
```

如果 clone 时仓库是空的，必须加 `--base main`，否则会停在 `the base branch of origin is not recorded locally. Pass --base BRANCH.`。

## 6. 第一轮：一个 worker

```sh
pai task new "Add a contributing note" --body "Create CONTRIBUTING.md with three lines on how to run the tests." --write
pai run --dry-run --workers 1
pai run --workers 1 --yes
pai status
```

每条命令会显示什么：

- `task new … --write`：先显示笔记内容，然后是 `saved. Next: pai run --dry-run`。不加 `--write` 只是预览；已经存在的笔记永远不会被覆盖。
- `run --dry-run`：显示 `lane a … worktree …/practice-pai-lanes/a (new directory: the agent will ask for folder trust)` 和 `DRY RUN: nothing was fetched, created, written or started`。它打印的 run ID 不会沿用，正式运行时会换一个新的。
- `run --workers 1 --yes`：先显示 `started a …`，然后每条 lane 是下面两种情况之一：
  - `task: delivered`：worker 已经收到任务，去第 7 步。
  - `waiting for folder trust`：agent 在问要不要信任这个新的 lane 文件夹，这时 `pai run` 以 1 退出。往下看。
- `status`：每条 lane 一段，包括 `lifecycle`、`trust`、`input`、`dispatch`、`rc`。只要还有 lane 在干活或在等你，它就**以 1 退出**，这是正常的。

**lane 在等信任提示时：**

```sh
pai trust
```

它会在当前终端里打开那个等着的提示。读完问题，移到 **"Yes, I trust this folder"**（光标一开始停在 "No, exit"），按回车。之后 `pai trust` 会把等着的任务发出去（这是 `swarm/WORKBENCH.md` 第 4 节的说法；写这一页时实测确认的是下面 `pai resume` 那条路径）。

如果你是在别处回答的（`pai attach a`、`pai web` 或 claude.ai 上的会话），而且回答时 `pai run` 已经返回了，任务会停在 `queued`。这时用下面的命令发出去：

```sh
pai resume
```

两种情况的预期结果都是：`dispatch  delivered: task add-a-contributing-note delivered`。

以后的轮次想避开这个提示，就把 worktree 都放在同一个已经信任过一次的文件夹下（[trusted root](GLOSSARY.zh-CN.md#trusted-root已信任的根目录)）。设置命令是 `pai init --update --worktree-root DIR`。

## 7. 收尾

**怎么判断 worker 做完了。** `pai status` 显示 `input ready`，只说明 agent 在等输入，不代表任务做完了。要看它实际交出了什么：

```sh
git -C ~/code/practice-pai-lanes/a log --oneline -3     # 这条 lane 的提交
pai pr plan a                                           # 会发布什么，还缺什么
```

`pai pr plan a` 什么都不改。它列出提交、PR 标题、配置的检查，以及每一条 `BLOCKED` 和原因；只要还有被拦住的项，它就以 1 退出。接下来：

```sh
pai pr check a      # 跑检查；预期：check test PASSED … review checks passed at <sha>
pai pr open a       # 推送分支并开 PR（只支持 GitHub 远端）；合并由你在 GitHub 上做
```

练习仓库的远端是本地目录，所以 `pr plan` 会说 `BLOCKED cannot ask GitHub about existing PRs: the remote is not a GitHub repository`。这是预期的，做到 `pai pr check a` 就可以停了。

最后停掉 worker、关掉它的窗口。worktree、分支和笔记都会保留：

```sh
pai cleanup                        # 预览：会停掉什么
pai cleanup --apply --stop-running
```

如果 `pai pr check a` 对某个检查显示 `FAILED`，说明 worker 的改动没通过。先看那一行上面的检查输出，然后二选一：

- 在 worker 的窗口里让它接着改（`pai attach a`，或者 `pai paste a fix.md --enter`）；
- 自己在 lane 文件夹里改好并提交。

改完再跑一次 `pai pr check a`。如果 `pai cleanup` 显示 `keep: the worker is live`，就要像上面那样加 `--apply --stop-running`。

第一轮到这里就跑完了。接下来：

- **之后的每一轮**：看 [REFRESH.zh-CN.md](REFRESH.zh-CN.md)，内容包括升级 pai、从合并后的 main 开始、新建 worktree、发 prompt、续跑。
- **术语**：[GLOSSARY.zh-CN.md](GLOSSARY.zh-CN.md)。**其他文档**：[docs/README.md](README.md)。
- **多个 worker**：写两份任务笔记，然后 `pai run --workers 2`；`pai web` 可以在浏览器里看所有 lane。完整的两条 lane 的例子（附终端记录）见 [examples/two-lane-round/](../examples/two-lane-round/README.md)。

## 8. 出错时怎么办

用法：

1. 在**现象**一列里找到你看到的那条信息的第一行；
2. 运行**解决办法**一列里的命令；
3. 重做刚才失败的那一步。

表里每一行都是测试这一页时实际遇到过的。如果你遇到的情况不在表里，先运行 `pai doctor`，大多数问题它都会连同修复办法一起列出来。还是不行的话，把完整输出和 `pai doctor` 的输出一起报告。

| 现象 | 原因 | 解决办法 |
| --- | --- | --- |
| 刚装完就 `pai: command not found` | 当前终端的 PATH 里没有 `~/.local/bin` | `export PATH="$HOME/.local/bin:$PATH"`，并写进 `~/.bashrc`（第 2 步） |
| `sha256sum: WARNING: 1 computed checksum did NOT match` | 下载不完整或损坏 | 两个文件都重新下载；校验不过的归档永远不要安装 |
| pip 报 `Cannot import 'setuptools.build_meta'` | 没联网，或者没有 setuptools 却用了 `--no-build-isolation` | 让 pip 用隔离方式构建（需要联网一次），或者先在 venv 里 `pip install setuptools` |
| `pai install` 说 "an installed Python package …, not an extracted release archive" | 你是用 uv 或 pip 装的（方式 B） | 用当初安装的方式升级（[REFRESH.zh-CN.md](REFRESH.zh-CN.md#a-升级-pai)） |
| `pai doctor` 显示 `-> pai init` | 这个仓库还没有 pai 的默认设置 | 做第 5 步 |
| `pai init --no-input` 报 `the base branch of origin is not recorded locally. Pass --base BRANCH.` | clone 时仓库是空的，`origin/HEAD` 未知 | 加上 `--base main` |
| `pai task new …` 报 `no pai project here … Run pai init` | 这个仓库还没保存过 `pai init`，或者你不在仓库目录里 | `cd` 进仓库，做第 5 步 |
| lane 显示 `waiting for folder trust` | agent 在问新 lane 文件夹要不要信任 | `pai trust`（第 6 步）；pai 永远不会替你回答 |
| lane 显示 `queued: … the next pai resume … sends it` | 信任提示是在 `pai run` 返回之后才回答的 | `pai resume` |
| `pai status` 以 1 退出 | 还有 lane 在干活，或者在等你 | 正常现象，看 `lifecycle` 那一行 |
| `pai pr plan` 报 `BLOCKED no approved check is configured` | 没有设检查，或者检查是这次 run 开始之后才加的 | `pai init --update --check "test=make test"`，下一次 `pai run` 就会用上 |
| `pai pr plan` 报 `BLOCKED … the remote is not a GitHub repository` | `origin` 是本地或非 GitHub 的远端 | 练习时是预期的；真实工作请从 GitHub clone |
| `pai next` 报 `error: the following arguments are required: lane` | `pai next` 是给某一条 lane 派下一份任务笔记 | `pai next a`（先加 `--dry-run` 看看会派哪一份） |
| `pai guide --cleanup …` 报 `unrecognized arguments` | 一次给了多个文件夹，比如用了 `*` 通配 | 每个文件夹单独运行一次 `pai guide --cleanup DIR` |
| `pai status \| head` 之后出现 Python 的 `BrokenPipeError` 报错 | 已知 bug：status 的输出接给了提前关闭的程序 | 无害；想翻页看的话，先 `pai status > status.txt` 再打开文件（修复已提给 core 线） |
| 收尾时提到 `rc up MANIFEST --resume` | 这是内部命令名 | 用 `pai resume` |
| `pai doctor` 显示 `linux (arch ), …` | 发行版没有版本号（Arch 是滚动更新的） | 无害 |


## 9. 可选：用 Obsidian 当控制台

<!-- doc_check: pending pai round, pai decide, pai handoff approve -->

一轮里有好几条 lane 之后，PAI 可以把这一轮的状态存成 vault 里的笔记：`PAI/Home.md`、每轮一张看板、每条 lane 一篇笔记、每个决策一篇笔记。之后，Obsidian（或者任何编辑器）就是你看、批、选的地方。这个功能来自 R6 的 round 线，pai 0.4.0 里还没有（reported-by-round）。完整说明见 [OBSIDIAN.zh-CN.md](OBSIDIAN.zh-CN.md)。

1. 在跑这一轮的机器上写出这些笔记：

   ```sh
   pai round status ROUND.json --write-vault
   ```

   成功的话，vault 里会出现 `PAI/Home.md`，它的 `updated:` 字段就是这次运行的时间。
2. 打开 `PAI/Home.md`。里面有四个区块：进行中的轮次、需要你处理的 lane（附原因）、还没回答的决策、还没批准的交接。不装插件时看到的是纯文本表格；装了 Dataview 会看到实时表格。
3. **回答决策。** 在 `PAI/decisions/<轮次>-<ID>.md` 的 front matter 里，把 `choice:` 设成 `options` 中的一项，然后运行 `pai decide answer --from-note "PAI/decisions/<轮次>-<ID>.md" --write`。成功的话，笔记会显示 `status: answered`。
4. **批准交接。** 读 `Agent Inbox/<轮次>/<线名>/cp-X/HANDOFF.md`，在它的 front matter 里设好 `approved: true`，然后运行 `pai handoff approve PROJECT "Agent Inbox/<轮次>/<线名>/cp-X" --from-vault`。成功的话，会显示 `approval: approved`，旁边多出一个 `DECISION.json`。封存后正文被改过的交接会被拒绝，并标上 `approval: stale`（core 线，reported-by-core）。

命令拒绝执行时，什么都不会写。提示里会写明原因：笔记被改过、choice 不在选项里，或者决策的文字变了。每种情况怎么处理，见 [OBSIDIAN.zh-CN.md](OBSIDIAN.zh-CN.md) 第 5 节。
