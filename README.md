# agent-windows-shell

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Windows 上的 AI agent 常进入 PowerShell，却仍按 bash 或批处理语法生成命令。这套工具保留 PowerShell 7 作为稳定宿主，再按可识别的命令语义选择 Git Bash 或 `cmd.exe`。WSL 涉及不同文件系统和工具链，只接受 agent 或用户显式选择。

目标不是频繁切换交互终端，而是让每次 Shell 工具调用进入适合它的解释器：

- PowerShell 命令和普通原生 CLI 留在 PowerShell 7。
- 明确的 Bash 语法交给 Git Bash。
- 明确的批处理语法交给 `cmd.exe /d`。
- Linux 工具链由 `wsl.exe ...` 显式进入 WSL。
- 删除、磁盘写入、关机等危险命令不自动路由，避免把原本会失败的命令激活。

不绑定某台机器的用户名、Python 路径、公司域名。内网主机从环境变量 `NO_PROXY` 读；需要再加域名时改用户配置，不要改仓库。

## 换电脑

1. 整目录拷走（或 clone）。
2. 安装 [Python 3](https://www.python.org/downloads/) 和（建议）[PowerShell 7](https://github.com/PowerShell/PowerShell/releases)、[Git for Windows](https://git-scm.com/download/win)。不要用 Microsoft Store 那个 0 字节的 `WindowsApps\bash.exe`。
3. 在本目录打开 PowerShell，执行：

```powershell
Set-Location <这个目录>
.\install.ps1
```

默认会：写 `%USERPROFILE%\.agent-windows-shell.json`、接好 Cursor hook/规则、点进 PowerShell 7 profile、拷技能、设 `PYTHONUTF8`。然后**重载** Cursor / 对应 agent，**新开一轮对话**。

| 开关 | 作用 |
|------|------|
| `-Cursor` | `~\.cursor\hooks.json` + 规则 + 合并终端相关 settings |
| `-Claude` | 写入或提示合并 `~\.claude\settings.json` |
| `-Profile` | PowerShell 7 profile 点源 `src\profile.ps1` |
| `-Skill` | 若存在则拷到 `~\.cursor\skills` 与 `~\.agents\skills` |
| `-Env` | 用户环境变量 UTF-8 / 工具包根目录 |
| `-DisableCursorAttribution` | 关掉 Cursor CLI 的 Co-authored-by（IDE 里还要再关 Attribution） |
| `-All` | 与无参数相同：Cursor + Profile + Skill + Env |

只给 Codex 等「没有 Cursor hook」的产品：

```powershell
.\install.ps1 -Profile -Skill -Env
```

再把 `adapters\generic\AGENTS.md` 贴进那个产品的说明文件。

无法接入命令前置 Hook 时，可以把路由器作为统一命令入口：

```powershell
python.exe "$env:AGENT_WINDOWS_SHELL_ROOT\src\rewrite_windows_shell.py" --explain 'export NAME=agent'
python.exe "$env:AGENT_WINDOWS_SHELL_ROOT\src\rewrite_windows_shell.py" --run 'export NAME=agent; printf "BASH:%s\n" "$NAME"'
```

`--explain` 只输出 `shell`、`reason`、解释器路径和改写项，不执行命令。`--run` 使用同一份路由计划直接启动 PowerShell、Git Bash 或 `cmd.exe`，并把目标命令的退出码传回调用方。这样，不支持 Hook 但允许配置命令包装器的 Agent 也能复用自动路由。

## 配置

`%USERPROFILE%\.agent-windows-shell.json`（安装时生成）：

- `gitBash`：空则自动找 `Git\bin\bash.exe`（跳过 0 字节文件）
- `powerShell7`：PowerShell 7 路径；空则从 `PATH` 和常见安装目录查找
- `commandPrompt`：`cmd.exe` 路径；空则从 `PATH` 和 `%SystemRoot%\System32` 查找
- `useNoProxyEnv`：为 `NO_PROXY` 里的主机给 `Invoke-RestMethod` 加 `-NoProxy`
- `internalHostPatterns`：额外正则，例如 `["intranet\\.example\\.com"]`
- `rfc1918NoProxy`：命令里出现 `10.` / `192.168.` / `172.16–31` 等也加 `-NoProxy`
- `wrapAndAnd`：命令包含代码位置的 `&&` 或 `||` 时，交给 PowerShell 7 执行以保留短路语义
- `wrapHeredoc`：检测到真实 bash heredoc 时，交给 Git Bash 执行
- `autoRouteBash`：识别到 `export`、`source`、`set -e`、`[[ ... ]]` 等明确 Bash 语法时，交给 Git Bash
- `autoRouteCmd`：识别到 `@echo off`、`setlocal`、`%VAR%` 等明确批处理语法时，交给 `cmd.exe /d`

改完不必重装。钩子每次启动会读这份文件。

## 目录

| 路径 | 用途 |
|------|------|
| `src\rewrite_windows_shell.py` | 路由逻辑（Hook JSON stdin，或 `--cmd` / `--explain` / `--run` / `--test`） |
| `src\profile.ps1` | 交互 pwsh 的 UTF-8；`-NoProfile` 不会加载 |
| `src\session_start.py` | Cursor sessionStart |
| `adapters\cursor\` | Cursor 规则与 settings 片段 |
| `adapters\claude\` | Claude Code 合并说明 |
| `adapters\generic\` | SKILL.md、AGENTS.md，给任意 agent 贴 |

## 如何选择 shell

Hook 只在高置信条件下切换解释器：

- `$env:`、PowerShell cmdlet、类型表达式等 PowerShell 语法留在宿主。
- `git status`、`npm test`、编译器等普通原生 CLI 留在宿主，避免无意义的套壳和路径转换。
- `export`、`source`、Bash heredoc、`[[ ... ]]` 等明确 Bash 语法交给 Git Bash。
- `@echo off`、`setlocal`、`%VAR%` 等明确批处理语法交给 `cmd.exe /d`，并禁用 AutoRun。
- 以 `pwsh`、`powershell`、`cmd`、`bash`、`sh` 或 `wsl` 开头的显式选择原样保留。
- WSL 不自动选择。Windows 项目自动送入 `/mnt/c` 会有跨文件系统性能和路径语义问题；需要 Linux 工具链时显式使用 `wsl.exe --distribution <发行版> -- <命令>`。
- 路由扫描会屏蔽字符串和注释，避免把示例文本当成 shell 语法。
- URL 中的 `%20`、`%2F` 等百分号编码不会被当成批处理变量；闭合 `%VAR%`、参数变量和 `for` 变量仍可识别。
- `rm`、`del`、`rd`、`format`、`diskpart`、`mkfs`、`dd`、`shutdown` 等命令不触发自动路由。显式选择 shell 时仍尊重调用者意图。

路由后仍保留原有兼容处理：

- 只在命令位置改写 `ls -la`、`curl`、`which`、`touch`、`cd /d`。
- `&&` / `||` 不会替换成 `;`。需要时交给 PowerShell 7 执行并回传退出码；找不到时保持原样，让错误可见。
- 子 shell 每次使用唯一临时脚本，执行结束后清理并传播退出码。
- Hook 改写、`--explain` 和 `--run` 共用同一份路由计划，避免诊断结果与实际执行分叉。
- `--run` 直接启动判定出的解释器，不要求调用方产品提供 Hook，也不会额外嵌套一层 PowerShell 子 shell。
- PowerShell 7 下可为匹配 `NO_PROXY`、额外主机规则或 RFC1918 地址的 `Invoke-RestMethod` / `Invoke-WebRequest` 加 `-NoProxy`。
- PowerShell 命令前补 UTF-8 设置。Hook 自身失败时 **fail-open**，不会挡住原命令。

Hook **不会**通过翻译或切换 shell 激活删除命令。删除命令需要 agent 明确理解目标路径并显式选择正确语法。

调研依据和取舍见 `docs\windows-agent-shell-research.md`。

## 仍然修不了

部分 IDE/agent 会自己生成临时 `.ps1`、使用 `-NoProfile`，甚至忽略集成终端的 `automationProfile`。规则和 Hook 能减少命令不兼容，但不能修复宿主产品的编码、PTY 或退出码实现。安装程序也不会开启系统级“使用 Unicode UTF-8”测试版区域设置，因为它可能破坏旧程序。
