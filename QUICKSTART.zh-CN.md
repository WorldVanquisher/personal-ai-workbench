# PAI 快速上手

[English](QUICKSTART.md) | 简体中文

**你需要：** Linux、macOS 或 WSL；Python 3.11+、`git`、`tmux` 和 [uv](https://docs.astral.sh/uv/)；已安装并登录的 Claude Code 或 Codex；一个有远程仓库的 Git checkout（例如从 GitHub 克隆的仓库）。在原生 Windows 上只有 `pai edit`（第 5 节）和 PAI 的上下文命令能运行；其余功能请在 WSL（Ubuntu）里使用，并把文件放在 Linux 文件系统下。

## 1. 安装

```sh
uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.5.0
```

如果 uv 提示它的 bin 目录不在 PATH 里，运行 `uv tool update-shell`（或它打印的那行 `export PATH=…`），然后**打开一个新终端**。之后 `pai --version` 会显示 `pai 0.5.0`。没有 uv？[发布页](https://github.com/WorldVanquisher/personal-ai-workbench/releases/tag/v0.5.0) 上有同样的 pai，分别打包成 `.tar.gz` 和 `.zip`，各带一个 `.sha256`；[INSTALL.md](INSTALL.md) 里有校验并安装的三行命令（不需要 sudo，也不需要把脚本管道给 shell）。

**先离线体验完整流程：** `pai guide` 会创建一个可随时删除的演示文件夹，里面有一个合成项目、一条任务笔记和一个假的 worker（不需要账号、凭据或网络），并用正常的 `pai` 命令依次走完计划、派发、状态和 handoff。它会打印每样东西的位置；`pai guide --cleanup DIR` 删除该文件夹。需要 git 和 tmux。

## 2. 检查你的机器

```sh
cd ~/code/myapp      # 你的 Git checkout
pai doctor           # 不做任何改动；每个问题都附带修复它的命令
```

在 `pai init` 之前，它会报告这个 checkout 还没有设置：这就是下一步。缺少 `git`、`tmux` 或 `gh`？`pai setup --install-missing` 会用系统的包管理器安装。agent 的登录由你自己完成（`claude` 后输入 `/login`，或 `codex login`）。

## 3. 第一个任务

```sh
pai init             # 回车保留每个建议值；被问到时选择你的 agent
pai task new "Add a contributing note" --body "Create CONTRIBUTING.md with three lines on how to run the tests." --write
pai run --dry-run    # 显示将会发生什么；不做任何改动
pai run --workers 1 --yes   # 为这个任务开一个 worktree、一个分支、一个 agent 窗口
pai status
```

有 lane 在等你、还没拿到任务，或者 agent 已经退出（干完活的 worker 也是这样）时，`pai status` 以 1 退出；每个 agent 都在运行、都拿到了任务时以 0 退出。如果它显示 **waiting for folder trust**，说明 agent 在问是否信任它的文件夹：阅读问题后由你自己回答（`pai trust` 会在这个终端里打开它，也可以在下面的 `pai web` 里直接输入）；之后 pai 才会发送任务。agent 完成后，`pai pr plan a` 显示将要发布的内容和还缺什么（它需要一个审阅检查，`pai init` 会问你）；只有你运行 `pai pr open a` 时才会 push。

## 4. 在浏览器里查看 agent 并和它们交互

```sh
pai web              # 在浏览器里打开这次运行；Ctrl-C 停止
```

- 实时显示每条 lane 的屏幕，保留颜色。像普通网页一样选中文字即可复制。
- **点击屏幕即可往 lane 里输入：** 字母、回车、Esc、Ctrl-C、方向键和你的输入法都会发给 agent。屏幕下方的按钮发送同样的按键。
- **粘贴**（Ctrl-V、Cmd-V）或在 **Message** 框里写好的内容，无论多长都作为一整段发送：在你选择 **Paste and Enter** 或自己按回车之前，什么都不会提交。
- **Shell：** 在 lane 的 worktree 里打开一个 shell（`git status`、跑测试）。**Task note：** 在 Markdown 编辑器里打开任务笔记；**Notes** 列出所有笔记。
- 文件夹信任提示会被明确标出；在你回答之前，粘贴保持关闭。
- 在没有桌面的服务器上：在服务器上运行 `pai web --no-browser --port 8766`，在你的笔记本上运行 `ssh -N -L 8766:127.0.0.1:8766 你@服务器`，再打开打印出来的链接。

在终端里，`pai paste a message.md` 用同样的方式把一个文件发送到 lane `a`（加 `--enter` 才会提交）。

## 5. 在浏览器里打开一个 Markdown 文件

```sh
pai edit README.md   # 带实时预览的编辑器；保存时先显示 diff 并请你确认
```

它只提供这一个文件，只在本机可访问；如果文件在此期间被别处改过，它绝不会覆盖新版本。它在原生 Windows 上也能运行。在服务器上，加上 `--no-browser --port 8765`，再像上面那样用 SSH 隧道。

## 6. PR 合并之后：一个新的 worktree

GitHub 上的合并不会改变你机器上的任何东西；单独一次 fetch 只会移动 `origin/main`，你的
worktree 里仍然是旧文件。pai 打印出命令，由你自己运行。

```sh
pai worktree refresh     # 在你的仓库里运行；打印 Bash 和 PowerShell 命令，不做任何修改
```

squash 合并（GitHub 的常见方式）之后，它建议从刚 fetch 的 main 新建一个 worktree：

```bash
git -C ~/code/app fetch origin +refs/heads/main:refs/remotes/origin/main
BASE=$(git -C ~/code/app rev-parse origin/main) && echo "fetched origin/main = $BASE"
git -C ~/code/app worktree add -b postmerge/20261120 ~/code/app-20261120 "$BASE"
pai worktree verify ~/code/app-20261120 --base "$BASE"
```

```powershell
git -C 'C:\code\app' fetch origin +refs/heads/main:refs/remotes/origin/main
$BASE = git -C 'C:\code\app' rev-parse origin/main; "fetched origin/main = $BASE"
git -C 'C:\code\app' worktree add -b postmerge/20261120 'C:\code\app-20261120' $BASE
pai worktree verify 'C:\code\app-20261120' --base $BASE
```

- **把 worktree 交给 agent 之前先核对 SHA。** `pai worktree verify` 并排打印 worktree 的
  HEAD 和你 fetch 到的 base；只要 HEAD 不包含它，就以 1 退出。
- **未提交的修改永远不会被动到：** 不会 reset、stash 或覆盖。对这样的 worktree
  （`pai worktree refresh --worktree PATH`），pai 建议在旁边新建一个 worktree；旧的保留它的文件和分支，由你决定怎么处理。
- 一个干净、只是落后于 main 的 worktree 会得到快进（`git merge --ff-only`，不能快进时它会拒绝，而不是合并）。
- `pai worktree list` 列出每个 worktree、它的分支、未提交的路径，以及它是否包含最近一次 fetch 的 main。
  `pai worktree web` 在浏览器里显示同样的内容，外加该 worktree 的 AGENTS.md、拆分计划和 PR 预览（实验性）。

## 7. 升级和卸载

- **升级：** 把安装命令里的版本号换成新版本（例如 `@v0.4.1`）再运行一次。你的配置和任务笔记会保留。
- **卸载：** `uv tool uninstall pai-workbench`。它只删除 pai 本身；你的配置和笔记会保留。

更多：`pai --help`、[INSTALL.md](INSTALL.md)，以及完整指南 `swarm/WORKBENCH.md`。`pai web`、`pai paste` 和 `pai edit` 是实验性功能。
