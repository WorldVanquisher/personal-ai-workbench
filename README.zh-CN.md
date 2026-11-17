# pai：个人 AI 工作台

[English](README.md) | 简体中文

PAI 把一个 AI 辅助项目的“工作记忆”保存在普通的 Markdown 文件里，并在你的控制下让编码 agent 基于它干活。

- **核心**（`pai context`、`pai handoff`；即发布包）：为 agent 会话提供它必须遵守的项目上下文（规则、人的决定、状态、限制），结束时生成一份 handoff 草稿。支持 Windows、macOS、Linux；需要 Python 3.11+；只用标准库；不联网、不调用模型；不执行任何命令，包括 Git。
- **工作台**（`pai init`、`run`、`status`、`pr`、`next`、`cleanup`；在源码 checkout 的 `swarm/` 里，不在发布包中）：并行运行 Claude Code 和 Codex，每条 lane 一个 tmux 窗口、一个 Git worktree、一个分支。任务来自 Markdown 笔记；发布只经过你批准的检查和你自己合并的 PR。

## 快速开始：在你的 checkout 里跑一批 Claude + Codex 混合任务

```sh
ln -s /path/to/personal-ai-workbench/bin/pai ~/.local/bin/pai   # 只需一次
cd ~/code/myapp                       # 你的 Git checkout
pai init                              # 默认 agent、模型、remote/base、任务笔记、检查命令
pai doctor                            # 工具、agent 登录、SSH 访问；不做任何改动
pai run --dry-run --workers 2 --agent claude --agent codex
pai run --workers 2 --agent claude --agent codex
pai status                            # 每条 lane：进程、任务派发、Remote Control
pai trust                             # 处理首次使用的“是否信任此文件夹”提示；任务随后送达
pai pr open a --draft                 # 检查、发布前检查、push、草稿 PR
pai cleanup --apply                   # 关闭已结束 worker 的窗口；工作成果保留
```

从远程仓库开始时，pai 会克隆到它管理的 checkout，在你审阅之前不启动任何 agent：`pai run --repo https://github.com/OWNER/REPO.git --workers 2 --agent claude --agent codex`。

## 平台

| | 核心 | 工作台 worker |
| --- | --- | --- |
| Linux（Debian/Ubuntu、Arch、Fedora/RHEL 系、Amazon Linux） | 支持 | 支持（tmux） |
| macOS | 支持 | 支持（tmux） |
| WSL | 支持 | 支持（tmux，在发行版内部） |
| 原生 Windows | 支持 | **不支持**：worker 需要 tmux；请使用 WSL |

## 已测试、尚未实测、可选

- **已测试：** 核心在 CI 中于 Windows、macOS、Linux 上测试（见 RELEASE_NOTES.md）。工作台用假 agent、假 GitHub、假 SSH 和隔离的 tmux server 测试。
- **开发者尚未实测：** 工作台对接真实的 Claude Code、Codex、GitHub 和 SSH，以及各操作系统上的安装器。由所有者执行的检查清单是 `swarm/SMOKE.md` 和 `swarm/LIVE-VALIDATION.md`。
- **可选：** JEV（TypeSafe）作为任务分诊和审阅的参考信号；它永远不能推翻检查结果。目前只对模拟端点测试过；`pai jev check` 会在你决定时发送那一次真实请求。
- **实验性、可选：** 只监听本机回环地址的 Markdown 服务器（远程版 Nano，带预览和确认保存），只有执行 `pai setup --experimental server` 才会安装；见 `swarm/mdserver/README.md`。通过 SSH 隧道或你的私有 tailnet 访问。

## 文档

- [INSTALL.md](INSTALL.md)：安装核心发布包；[REFERENCE.md](REFERENCE.md)：核心命令。
- `swarm/WORKBENCH.md`（在 checkout 中）：工作台指南，包括平台安装、配置、文件夹信任、SSH、清理和 JEV 费用。
- [SESSION_START.md](SESSION_START.md)：agent 会话的开场提示；[RELEASE_NOTES.md](RELEASE_NOTES.md)：变更与测试记录。

许可证：MIT，见 [LICENSE](LICENSE)。
