# 常见问题

[English](FAQ.md) | 简体中文

这里是一些不属于操作步骤的问题，每个都给简短的回答。操作步骤见 [FIRST-USE.zh-CN.md](FIRST-USE.zh-CN.md) 和 [REFRESH.zh-CN.md](REFRESH.zh-CN.md)；术语见 [GLOSSARY.zh-CN.md](GLOSSARY.zh-CN.md)。

## 1. 一定要装 Obsidian 吗？

不需要。vault 就是一个放 Markdown 文件的文件夹，Obsidian 只是查看它的一种工具，任何编辑器都行。`pai init` 问任务笔记文件夹时，直接回车，pai 就用它自己的文件夹。

## 2. PAI 会替我回答 agent 的文件夹信任提示吗？

永远不会。`pai status` 会显示 `waiting for folder trust`，`pai trust` 会把提示打开，由你来回答。`pai web` 和 `pai paste` 都拒绝往这个提示里粘贴。所有轮次都放在同一个 [trusted root](GLOSSARY.zh-CN.md#trusted-root已信任的根目录) 下面，提示就会少很多。

## 3. PAI 能替我 push 或合并吗？

- **push**：只有你运行 `pai pr open LANE` 时才会推送，而且前提是你配置的检查已经通过（`pai pr check LANE`）。永远不会强推。
- **合并**：永远不会，由你在 GitHub 上合并。
- **worker**：一轮里的 worker 根本不跑任何 git 写操作。

## 4. 能在 Windows 上用吗？

核心功能（`pai context`、`pai handoff`、`pai queue`、`pai edit`、`pai doctor --agents`）可以在原生 Windows 上运行。workbench（`pai run`、`pai status`、`pai web` 等）需要 tmux，所以在 Windows 上要把 pai 装进 WSL；在原生 Windows 上运行 workbench 命令，会停下并给出提示。各平台实际跑过什么，见 [FIRST-USE-PLATFORMS.md](FIRST-USE-PLATFORMS.md)（英文）。

## 5. 需要 JEV 吗？要花多少钱？

不需要。JEV 是可选的第二意见；`pai init` 问 key 时直接回车就跳过。

- **花费**：2026-11-22 实测，每次请求大约 480 个输入 token，耗时 0.1–0.4 秒。
- **它判断什么**：它判断的是报告怎么写，不核实内容是不是真的，所以 pai 从不让它推翻真实的检查结果。

## 6. PAI 把状态存在哪里？

都存在文件里：

- **配置**：`~/.config/pai-workbench`（核心功能用 `~/.config/pai`）；
- **任务笔记**：你的笔记文件夹；
- **运行状态**：`~/.cache/pai-swarm`；
- **queue**：每个 queue 两个文件，queue 本身和只追加的 `.log`；
- **交接和 checkpoint**：vault 的 `Agent Inbox/` 里。

清空对话或者重启机器，都不会丢东西。见 [REFRESH.zh-CN.md](REFRESH.zh-CN.md#e-恢复)。

## 7. PAI 会把我的代码发到别处吗？

- **核心功能**：不联网，不调用模型，也不执行任何命令。
- **workbench**：会访问你的 Git 远端（fetch；只有 `pai pr open` 时才 push），并启动你配置的 agent。agent 会发送什么，是你和它的服务商之间的事。
- **JEV（启用时）**：只发送它要判断的那一段文字，triage 时是笔记，verify 时是报告。发送前会先去掉邮箱、密钥和会话链接。

## 8. `pai status` 为什么以 1 退出？

因为有 lane 需要你看一眼：在等你（信任提示、任务还没送达），或者 agent 已经退出（干完活的 worker 也是这个样子）。0 表示每条 lane 的 agent 都在运行、也都拿到了任务，哪怕还在干活。具体情况看每条 lane 的 `lifecycle` 那一行。

## 9. 信任提示已经回答了，任务却还是 `queued`

你回答的时候 `pai run` 已经返回了，所以没有谁在负责发送任务。运行 `pai resume` 即可。（如果是用 `pai trust` 回答的，它会自己把等着的任务发出去。）

## 10. 能用 Codex 代替 Claude Code 吗？

可以。`pai init` 会问默认 agent，也可以每次运行时用 `pai run --agent codex` 指定。Codex 的 lane 没有 Remote Control 模式（`pai status` 显示 `rc not-supported`），要用 `pai attach` 或 `pai web` 来跟进。

## 11. 在服务器上用 `pai web` 安全吗？

它只监听 127.0.0.1，登录链接只能用一次。要从另一台电脑访问，用 SSH 隧道：在服务器上运行 `pai web --no-browser --port 8766`，然后在你的笔记本上运行 `ssh -N -L 8766:127.0.0.1:8766 YOU@SERVER`。这个功能还是实验性的。

## 12. 怎么卸载？

| 安装方式 | 删除 |
| --- | --- |
| 发布归档 | `rm ~/.local/bin/pai` 和 `rm -r ~/.local/share/pai-workbench` |
| uv | `uv tool uninstall pai-workbench` |
| venv 里的 pip | `rm ~/.local/bin/pai` 和 `rm -r ~/.venvs/pai` |

你的配置、任务笔记、交接和运行状态都会保留，要你自己删除。

## 13. 该看 QUICKSTART 还是 FIRST-USE？

- **QUICKSTART** 是给已经熟悉 Git、tmux 和自己的 agent 的人看的短路径。
- **FIRST-USE** 是完整路径：每一步都写了预期输出，以及输出不一样时怎么办。它是在一个空的 home 目录里实际走一遍之后写出来的。

## 14. 为什么说 AGENTS.md 是心脏？

每个 agent 会话在每个任务开始时都先读它，所以它写得好不好，决定了每个 worker 干得好不好：

- 这里写错一条规则，每条 lane 都会照着错；
- 这里有一段过时的进度说明，每条 lane 都会被误导；
- 一份 1000 行的文件，会让每个会话在打开第一个源文件之前，就先花掉一大块上下文。

所以 PAI 把它拆成四层：简短的宪法（L0）、按领域分的契约（L1）、PAI 为每条 lane 写的指令（L2），以及等你决定的提议（L3）。见 [AGENTS-LAYERS.zh-CN.md](AGENTS-LAYERS.zh-CN.md)；一份真实的 1074 行文件怎么拆，见 [research/FLH-AGENTS-ANALYSIS.md](research/FLH-AGENTS-ANALYSIS.md)（英文）。

## 15. 为什么叫"润滑层"？

PAI 不替代 agent，也不替你做决定。每一轮工作所需的东西，由它准备好、串起来、收回来交给你，让你能快速、有依据地做决定。见 [WHY-PAI.md](WHY-PAI.md)（英文）。
