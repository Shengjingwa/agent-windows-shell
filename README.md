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

默认会：写 `%USERPROFILE%\.agent-windows-shell.json`（只存本机找到的路径和你自己的覆盖项）、接好 Cursor hook、在 PowerShell 7 profile 里写一个带标记的块、把技能链接到 `~\.cursor\skills`、设 `PYTHONUTF8`。安装器先跑单元测试，没通过就什么都不写。可以重复运行，旧版安装器留下的块和默认值会被升级掉。然后**重载** Cursor / 对应 agent，**新开一轮对话**。

| 开关 | 作用 |
|------|------|
| `-Cursor` | `~\.cursor\hooks.json`（保留别的 Hook，替换本工具包的旧条目，路径写成 8.3 短路径的也认得出）+ 合并终端相关 settings（只在受管键有变化时才改写；文件里有注释或不是纯 JSON 时不动，只给出提示）；清掉旧版拷进 `~\.cursor\rules` 的规则文件（Cursor 不读那个目录） |
| `-Claude` | 在 `~\.claude\settings.json` 的 `PreToolUse` 里挂 PowerShell 工具（exec 形式启动 Python），替换本项目旧版写的条目（包括 Git Bash 跑不了的 shell 形式），其他钩子和设置不动；文件不是纯 JSON 时不改，只提示手动合并。并链接技能 |
| `-Profile` | 在 PowerShell 7 profile（`文档\PowerShell`，跟随 OneDrive 等文档文件夹重定向）写 `# >>> agent-windows-shell` 块，直接点源 `src\profile.ps1`；重装会刷新这个块，块外内容不动，原有的 BOM 保留。第一版安装器写的旧块只有和原样一致时才替换，替换前把原文件存为 `….agent-windows-shell.bak`；旧块被改过时不动，只提示手动删除。profile 不是 UTF-8 编码（包括没有 BOM 的 UTF-16）时不改，只提示 |
| `-Skill` | 用 junction 把 `skills\windows-agent-shell` 链到 `~\.cursor\skills`，仓库更新即生效；不写 `~\.agents` |
| `-Env` | 用户环境变量 UTF-8 / 工具包根目录 |
| `-DisableCursorAttribution` | 关掉 Cursor CLI 的 Co-authored-by（IDE 里还要再关 Attribution） |
| `-All` | 与无参数相同：Cursor + Profile + Skill + Env |

只给 Codex 等「没有 Cursor hook」的产品：

```powershell
.\install.ps1 -Profile -Skill -Env
```

再把 `adapters\generic\AGENTS.md` 贴进那个产品的说明文件。

无法接入命令前置 Hook 时，先确认 `AGENT_WINDOWS_SHELL_ROOT` 和 wrapper 文件存在，再把路由器作为统一命令入口；wrapper 不可用时直接在已选 shell 中执行命令：

```powershell
py -3 "$env:AGENT_WINDOWS_SHELL_ROOT\src\rewrite_windows_shell.py" --explain 'export NAME=agent'
py -3 "$env:AGENT_WINDOWS_SHELL_ROOT\src\rewrite_windows_shell.py" --run 'export NAME=agent; printf "BASH:%s\n" "$NAME"'
```

（`python.exe` 在 PATH 里时也可以直接用。Cursor 的 `PATH` 过期时 `python` 可能找不到，`py -3` 通常仍可用。）

`--explain` 只输出 `shell`、`reason`、解释器路径和改写项，不执行命令。`--run` 使用同一份路由计划直接启动 PowerShell、Git Bash 或 `cmd.exe`，并把目标命令的退出码传回调用方。这样，不支持 Hook 但允许配置命令包装器的 Agent 也能复用自动路由。

## 配置

`%USERPROFILE%\.agent-windows-shell.json`。安装器只写前三项（本机找到的路径）；其余开关的默认值在代码里（`DEFAULTS`），要改某一项，只写那一项：

- `gitBash`：空则自动找 `Git\bin\bash.exe`（跳过 0 字节文件）
- `powerShell7`：PowerShell 7 路径；空则从常见安装目录和 `PATH` 查找
- `commandPrompt`：`cmd.exe` 路径；空则从 `%SystemRoot%\System32` 和 `PATH` 查找
- `useNoProxyEnv`（默认开）：为 `NO_PROXY` 里的主机给 `Invoke-RestMethod` 加 `-NoProxy`
- `internalHostPatterns`：额外正则，例如 `["intranet\\.example\\.com"]`
- `rfc1918NoProxy`（默认开）：命令里出现 `10.` / `192.168.` / `172.16–31` 等也加 `-NoProxy`
- `wrapAndAnd`（默认开）：命令包含代码位置的 `&&` 或 `||` 时保留短路语义。宿主已是 PowerShell 7 就原地执行；宿主是 5.1 或认不出时，交给 PowerShell 7 子进程
- `wrapHeredoc`（默认开）：检测到真实 bash heredoc 时，交给 Git Bash 执行
- `autoRouteBash`（默认开）：识别到 `export`、`source`、`set -e`、`[[ ... ]]` 等明确 Bash 语法时，交给 Git Bash
- `autoRouteCmd`（默认开）：识别到 `@echo off`、`setlocal`、`%VAR%` 等明确批处理语法时，交给 `cmd.exe /d`
- `flushImplicitOutput`（默认开）：命令可能打印自动表格时，包一层 `. { … } | Out-Host`，避免 Cursor 脚本末尾的 `exit` 把这些输出连同后面的输出一起吞掉。判断规则：`Select-Object` 列出 1–4 个属性（`Get-ChildItem | Select-Object Name`）；或者命令里任何位置出现 `ConvertFrom-Json`、`ConvertFrom-Csv`、`Import-Csv`、`Invoke-RestMethod`、`[pscustomobject]`、`Get-Location`、`Get-Member`、`$PSVersionTable`。后一类对象到达输出的路径太多（`(…).items`、先赋值再打印、`Select-Object -First 2`），逐一判断会漏，所以出现就包，多包无害。包了之后原生程序的输出改走 PowerShell 管道，200 行约多 20 ms，20,000 行约多 1.2 s；原生输出按控制台编码解码，所以控制台要是 UTF-8（profile 或 `utf8Prefix` 负责），否则非 ASCII 字符会乱码。另有三点小差别：顶层 `return` 之后的退出码以块内最后一个原生程序为准；块内 `$MyInvocation.MyCommand.Path` 为空；报错行号多 1
- `utf8Prefix`（默认关）：每条命令前补一段 UTF-8 设置。profile 块只写给 PowerShell 7；宿主是 Windows PowerShell 5.1 时（看 `PSModulePath` 第一项是不是用户自己的 `WindowsPowerShell\Modules`，不额外查进程；只有系统目录说明不是从 PowerShell 启动的，不算），Hook 自动补这段，不用打开。命令以 `using` 或 `param(` 开头时不补，因为这两种语句必须在脚本最前面。宿主以 `-NoProfile` 启动的 PowerShell 7，或没装 profile 块时才需要打开。只装 `-Cursor`、profile 里又没有本项目的块时，安装器会提示

改完不必重装。钩子每次启动会读这份文件。

## 目录

| 路径 | 用途 |
|------|------|
| `src\rewrite_windows_shell.py` | 路由逻辑（Hook JSON stdin，或 `--cmd` / `--explain` / `--run` / `--test`） |
| `src\profile.ps1` | pwsh 的 UTF-8 与 Python 默认值，并把注册表里有、当前会话缺的 PATH 目录追加到末尾；`-NoProfile` 不会加载 |
| `src\session_start.py` | Cursor sessionStart：固定规则 + 本机事实（例如 `python` 不在 PATH） |
| `skills\windows-agent-shell\` | 技能（`SKILL.md`），安装时链接到 `~\.cursor\skills` |
| `adapters\cursor\` | Cursor settings 片段与说明 |
| `adapters\claude\` | Claude Code 合并说明 |
| `adapters\generic\` | `AGENTS.md`，给任意 agent 贴 |

## 如何选择 shell

Hook 只在高置信条件下切换解释器：

- `$env:`、PowerShell cmdlet、类型表达式等 PowerShell 语法留在宿主。
- `git status`、`npm test`、编译器等普通原生 CLI 留在宿主，避免无意义的套壳和路径转换。
- `export`、`source`、Bash heredoc、`[[ ... ]]` 等明确 Bash 语法交给 Git Bash。
- `@echo off`、`setlocal`、`%VAR%` 等明确批处理语法交给 `cmd.exe /d`，并禁用 AutoRun。
- 以 `pwsh`、`powershell`、`cmd`、`bash`、`sh` 或 `wsl` 开头的显式选择原样保留。
- WSL 不自动选择。Windows 项目自动送入 `/mnt/c` 会有跨文件系统性能和路径语义问题；需要 Linux 工具链时显式使用 `wsl.exe --distribution <发行版> -- <命令>`。
- 路由扫描会屏蔽字符串、注释和 PowerShell here-string，避免把示例文本当成 shell 语法；双引号里的 `$( … )` 子表达式是代码，保持可见，所以 `git commit -m "$(cat <<'EOF' … EOF)"` 能被识别成 Bash。
- heredoc 开头那一行出现 PowerShell 语法（`Set-Location …; & $py - <<'PY'`）时留在宿主，由 PowerShell 报出清楚的解析错误，而不是让 Git Bash 报 `command not found`。
- URL 中的 `%20`、`%2F` 等百分号编码不会被当成批处理变量；闭合 `%VAR%`、参数变量和 `for` 变量仍可识别。
- `rm`、`del`、`rd`、`diskpart`、`mkfs`、`dd`、`shutdown`、`shred`、`unlink`、`find -delete`、`rsync --delete`（及 `--del`）、`git clean`（包括 `git -C . clean`）、`git reset --hard`、`git branch -D`、任何 `robocopy`、`format D:`、`truncate FILE -s 0` 等命令不触发自动路由。`reg`、`sc`、`schtasks`、`net`、`vssadmin`、`wmic`、`bcdedit` 和 `delete` 出现在同一条命令里也算，不管两者隔多远（`sc \\server delete svc`、`schtasks /tn x /delete`）。这项检查看原始文本，命令词出现在任何位置都算，包括 `env rm`、`xargs rm`、heredoc 正文和 heredoc 之后的行，宁可多拦。它还会接上续行（`\`、`^`、反引号加换行），去掉反斜杠、引号、`^`、`%VAR%`、`${X:-…}`、`$1`、单行的 `$(…)` 和反引号命令替换再查一遍，所以 `r\m`、`r''m`、`r^d`、`r$(echo)m` 这类写法也拦得住。这只是尽力而为的检查，不是沙箱。唯一的例外是提交信息：`git` 或 `gh` 用 `-m`、`--message`、`-b`、`--body` 接 `"$(cat <<'EOF' … EOF)"`，且结束标记带引号时，正文不展开，只会成为提交或 PR 的文字，所以不查正文，结束标记之后的内容照查。结束标记不带引号、前面有 `eval`、或用 `git commit -F - <<EOF` 时，正文照查；正文提到 `rm` 等词就会留在 PowerShell 报解析错误，这时改用 `git commit --file`。显式选择 shell 时仍尊重调用者意图。

路由后仍保留原有兼容处理：

- 只在命令位置改写 `ls -la`、`curl`、`which`、`touch`、`cd /d`。
- `&&` / `||` 不会替换成 `;`。宿主是 PowerShell 7 时原地执行；宿主是 5.1 时交给 PowerShell 7 执行并回传退出码；找不到 PowerShell 7 时保持原样，让错误可见。宿主版本从父进程的映像名判断（`pwsh.exe` 或 `powershell.exe`），只在命令含 `&&`、`||` 或要加 `-NoProxy` 时才查。
- 子 shell 每次使用唯一临时脚本，执行结束后清理并传播退出码；超过 24 小时的 `agent-*` 残留脚本在下次写临时脚本时顺手清掉。
- Hook 改写、`--explain` 和 `--run` 共用同一份路由计划，避免诊断结果与实际执行分叉。
- `--run` 直接启动判定出的解释器，不要求调用方产品提供 Hook，也不会额外嵌套一层 PowerShell 子 shell。
- PowerShell 7 下可为匹配 `NO_PROXY`、额外主机规则或 RFC1918 地址的 `Invoke-RestMethod` / `Invoke-WebRequest` 加 `-NoProxy`。
- 命令里有自动表格时，包一层 `. { … } | Out-Host` 防止输出被吞（见上面的 `flushImplicitOutput`）。
- Hook 自身失败时 **fail-open**，不会挡住原命令。常规路径只加载 `json`、`os`、`re`、`sys`，其余模块和可执行文件查找都等到真的要用时才做；`tests` 里有一条测试守着这一点。

Hook **不会**通过翻译或切换 shell 激活删除命令。删除命令需要 agent 明确理解目标路径并显式选择正确语法。

调研依据和取舍见 `docs\windows-agent-shell-research.md`。

## 仍然修不了

部分 IDE/agent 会自己生成临时 `.ps1`、使用 `-NoProfile`，并忽略集成终端的 `automationProfile`。Cursor 3.21.9 就是这样：Shell 工具和 Hook 都按 Cursor 进程自己的 `PATH` 找 `pwsh`，不读 `automationProfile`；Cursor 从常驻启动器（例如 uTools）打开时，这个 `PATH` 停在启动器启动那一刻，之后装的工具它看不到。`src\profile.ps1` 会给新开的对话补上缺的 PATH 目录，已经开着的对话保留原来的环境。机器上没有 PowerShell 7 时，Cursor 改用 Windows PowerShell 5.1，本项目的 profile 块不会加载：UTF-8 设置由 Hook 补上，PATH 补齐没有。规则和 Hook 能减少命令不兼容，但不能修复宿主产品的编码、PTY 或退出码实现。安装程序也不会开启系统级“使用 Unicode UTF-8”测试版区域设置，因为它可能破坏旧程序。
