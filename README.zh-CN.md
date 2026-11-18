# pai：个人 AI 工作台

[English](README.md) | 简体中文

**一行安装**（Linux、macOS 或 WSL；需要 [uv](https://docs.astral.sh/uv/) 和 git）：

```sh
uv tool install git+https://github.com/WorldVanquisher/personal-ai-workbench@v0.3.0
```

如果 uv 提示它的 bin 目录不在 PATH 里，运行它打印的那行 `export PATH=…`（或 `uv tool update-shell`），然后打开一个新终端。接着在你的 Git checkout 里依次运行：`pai doctor` → `pai init` → `pai task new "标题" --write` → `pai run --dry-run`。升级：把这一行里的版本号换成新版本再运行一次。卸载：`uv tool uninstall pai-workbench`（配置和任务笔记会保留）。没有 uv？请从 [v0.3.0 发布页](https://github.com/WorldVanquisher/personal-ai-workbench/releases/tag/v0.3.0) 下载压缩包，按下面的“快速开始”操作。

PAI 把一个 AI 辅助项目的“工作记忆”保存在普通的 Markdown 文件里，并在你的控制下让编码 agent 基于它干活。

- **核心**（`pai context`、`pai handoff`）：为 agent 会话提供它必须遵守的项目上下文（规则、人的决定、状态、限制），结束时生成一份 handoff 草稿。支持 Windows、macOS、Linux；需要 Python 3.11+；只用标准库；不联网、不调用模型；不执行任何命令，包括 Git。
- **工作台**（`pai doctor`、`init`、`task`、`run`、`status`、`pr`、`next`、`cleanup`；从 0.3.0 起在同一个压缩包里，位于 `swarm/`）：并行运行 Claude Code 和 Codex，每条 lane 一个 tmux 窗口、一个 Git worktree、一个分支。任务来自 Markdown 笔记；发布只经过你批准的检查和你自己合并的 PR。支持 Linux、macOS 和 WSL（需要 tmux）。

## 快速开始：从压缩包到第一个任务

Linux、macOS 或 WSL，需要 Python 3.11+、git 和 tmux。全程不需要 sudo，也不需要把脚本管道给 shell：

```sh
sha256sum -c pai-workbench-0.3.0.tar.gz.sha256      # macOS：shasum -a 256 -c pai-workbench-0.3.0.tar.gz.sha256
tar -xzf pai-workbench-0.3.0.tar.gz
pai-workbench-0.3.0/bin/pai install                  # 校验后复制到 ~/.local/share/pai-workbench，`pai` 放在 ~/.local/bin
```

如果 `install` 提示 `~/.local/bin is not on PATH`，运行它为你的 shell 打印的那一行（bash：`echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc`），然后打开一个新终端。接着在你的 Git checkout 里：

```sh
pai doctor               # 工具、agent 登录、远程访问；不做任何改动
pai init                 # 问几个问题；直接回车即采用建议的默认值
pai task new "Add a --json flag to export" --body "Done when: make test passes" --write
pai run --dry-run        # 只显示将要做什么；不做任何改动
pai run                  # 启动 agent，每个一个 tmux 窗口
pai status               # 每条 lane：进程、信任提示、输入框、任务派发、Remote Control
```

单独运行 `pai` 会再次显示这条路径；`pai --help` 列出所有命令。如果某条 lane 在等 agent 首次使用时的“是否信任此文件夹”提示，`pai status` 会说明，`pai trust` 会逐个打开提示让你回答，任务随后送达。升级就是用新压缩包重复上面三行：任何一步失败，旧版本都照常可用；你的配置和任务笔记不会被改动。

**获取压缩包。** 从 [v0.3.0 发布页](https://github.com/WorldVanquisher/personal-ai-workbench/releases/tag/v0.3.0) 下载 `pai-workbench-0.3.0.tar.gz` 和它的 `.sha256`（`.zip` 内容相同，供 Windows 使用）。也可以在源码 checkout 里构建同样的压缩包：`python3 -B tools/build_bundle.py --out dist`。

从远程仓库开始时，pai 会克隆到它管理的 checkout，在你审阅之前不启动任何 agent：`pai run --repo https://github.com/OWNER/REPO.git --workers 2 --agent claude --agent codex`。

## 平台

| | 核心 | 工作台 worker |
| --- | --- | --- |
| Linux（Debian/Ubuntu、Arch、Fedora/RHEL 系、Amazon Linux） | 支持 | 支持（tmux） |
| macOS | 支持 | 支持（tmux） |
| WSL | 支持 | 支持（tmux，在发行版内部） |
| 原生 Windows | 支持（`pai context`、`pai handoff`、`pai doctor --agents`） | **不支持**：worker 需要 tmux；请在 WSL 里安装 pai |

在原生 Windows 上，工作台命令会停下并提示改用 WSL。核心在 Windows 上的安装步骤见 INSTALL.md。

## 已测试、尚未实测、可选

- **已测试：** 核心、两种压缩包、`pai install` 和用 uv 安装的包，在 CI 中于 Windows、macOS、Linux 上测试（见 RELEASE_NOTES.md）。工作台用假 agent、假 GitHub、假 SSH 和隔离的 tmux server 在 Linux 上测试：分别从压缩包和在干净容器里 `uv tool install` 发布 tag 开始，一直到第一个任务送达。
- **开发者尚未实测：** 工作台对接真实的 Claude Code、Codex、GitHub 和 SSH；工作台在 macOS 上的运行与派发（那里的 CI 只检查安装、核心以及工作台能否加载）。由所有者执行的检查清单是 `swarm/SMOKE.md` 和 `swarm/LIVE-VALIDATION.md`。
- **可选：** JEV（TypeSafe）作为任务分诊和审阅的参考信号；它永远不能推翻检查结果。目前只对模拟端点测试过；`pai jev check` 会在你决定时发送那一次真实请求。
- **实验性、可选：** 只监听本机回环地址的 Markdown 服务器（远程版 Nano，带预览和确认保存），只有执行 `pai setup --experimental server` 才会安装；见 `swarm/mdserver/README.md`。通过 SSH 隧道或你的私有 tailnet 访问。

## 文档

- [INSTALL.md](INSTALL.md)：用 uv 或压缩包安装、升级、Windows；[REFERENCE.md](REFERENCE.md)：核心命令。
- `swarm/WORKBENCH.md`（压缩包和 checkout 里都有）：工作台指南，包括平台安装、配置、文件夹信任、SSH、清理和 JEV 费用。
- [SESSION_START.md](SESSION_START.md)：agent 会话的开场提示；[RELEASE_NOTES.md](RELEASE_NOTES.md)：变更与测试记录。

许可证：MIT，见 [LICENSE](LICENSE)。
