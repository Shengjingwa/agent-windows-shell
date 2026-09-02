# Windows 终端对 AI agent 的兼容性调研

## 结论

Windows 下的 AI agent 终端问题不能靠固定使用一个 shell，也不能靠“把 bash 命令批量翻译成 PowerShell”解决。更可靠的顺序是：

1. 使用 PowerShell 7 作为稳定宿主。
2. PowerShell 和普通原生 CLI 留在宿主；明确 Bash 语法交给 Git Bash，明确批处理语法交给 `cmd.exe`。
3. WSL 只在需要 Linux 环境时显式进入，不根据命令外观自动选择。
4. 用长期规则要求 agent 先选择正确解释器，再生成该解释器的原生命令。
5. 对删除、磁盘写入、关机等破坏性命令保持失败可见，不通过自动路由激活。

本项目已按这个顺序实现。Hook 不再把 `&&` 替换为 `;`，也不会通过翻译或切换 shell 激活删除命令。

## 问题来源

### “多 shell”需要按语义路由

PowerShell、Git Bash、`cmd.exe` 和 WSL 解决的问题不同：

- PowerShell 能调用 Windows 原生程序，并适合对象管道、Windows 管理和文件系统操作。PowerShell 自己会先解析原生程序参数，因此引号、元字符和 `--%` 等规则与 Bash 不同。
- Git for Windows 提供 Bash 仿真，适合运行依赖 Bash 语法的脚本。其底层 MSYS2 会在 Unix 路径和 Windows 路径之间自动转换；混用 Unix 工具和 Windows 原生程序时，参数可能改变含义。因此普通 `git`、`npm` 或编译器命令没有必要仅因“也能在 Bash 中运行”就切换过去。
- `cmd.exe` 主要用于已有 `.cmd` / `.bat` 语法和依赖命令处理器的内建命令。使用 `/d` 可以禁用 AutoRun，减少机器级配置对 agent 命令的影响。微软也建议复杂自动化优先使用 PowerShell。
- WSL 提供真实 Linux 用户空间，但微软建议 Linux 命令行项目放在 Linux 文件系统，Windows 命令行项目放在 Windows 文件系统。把 Windows 工作区自动送入 `/mnt/c` 会引入性能和路径语义成本，所以 WSL 应由任务或项目位置显式选择。

因此路由不能只搜索 `grep`、`sed`、斜杠或管道符。当前实现只识别解释器特有的高置信语法；无法确定时保留宿主，让失败可见。

来源：

- [PowerShell about_Parsing](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing)
- [Git for Windows](https://gitforwindows.org/)
- [MSYS2 Filesystem Paths](https://www.msys2.org/docs/filesystem-paths/)
- [Windows cmd](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/cmd)
- [Working across Windows and Linux file systems](https://learn.microsoft.com/en-us/windows/wsl/filesystems)
- [Basic commands for WSL](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)

### PowerShell 5.1 和 7 的语义不同

Windows PowerShell 5.1 不支持 `&&` 和 `||` 管道链运算符；PowerShell 7 支持，并根据前一条命令的成功状态决定是否执行后一条命令。把 `&&` 机械替换成 `;` 会丢失短路语义，可能在前一步失败后继续执行后续命令。

PowerShell 还有自己的解析阶段、参数模式、引号和转义规则。bash heredoc、`$(...)`、通配符和别名不能假定与 PowerShell 等价。

来源：

- [about_Pipeline_Chain_Operators](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pipeline_chain_operators)
- [about_Parsing](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing)

### 原生程序退出码需要显式处理

PowerShell 使用 `$LASTEXITCODE` 保存最近一个原生程序或 PowerShell 脚本的退出码，`$?` 表示最近一次操作是否成功。调用 `git`、`npm`、编译器或子 shell 后，只看输出不足以判断成功。

本项目的 PowerShell 7 包装器在子脚本结束时同时检查 `$?` 和 `$LASTEXITCODE`：原生程序失败时返回原退出码，PowerShell 命令失败时返回 1。外层再删除唯一临时脚本并回传该退出码。

来源：

- [about_Automatic_Variables: `$LASTEXITCODE`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_automatic_variables)

### 编码设置受 PowerShell 版本和启动方式影响

Windows PowerShell 5.1 与较新 PowerShell 的默认文本编码不同；Windows PowerShell 中 `UTF8` 通常会写 BOM。通过 `-NoProfile` 启动时，用户 profile 中的 UTF-8 设置不会加载。

因此安装器用 .NET API 写无 BOM UTF-8 JSON，Hook 在命令前设置控制台输入、输出和管道编码，并让旧的带 BOM 配置仍可读取。profile 只改善正常交互终端，不能作为唯一方案。

来源：

- [about_Character_Encoding](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding)

### 集成终端设置不等于 agent 执行环境

IDE 可以为普通集成终端设置默认 profile 或 automation profile，但 agent 可能通过自己的工具启动临时 PowerShell 脚本。此时用户 profile、终端 UI 设置或当前交互会话环境不一定生效。

Cursor 提供 `sessionStart` 和 `preToolUse` Hook。`sessionStart` 适合注入环境与短提示，`preToolUse` 可以在 Shell 工具执行前检查或更新输入。Hook 本身应保持 fail-open，避免兼容层故障阻断所有命令。

来源：

- [Cursor terminal tool](https://cursor.com/docs/agent/tools/terminal.md)
- [Cursor hooks](https://cursor.com/docs/hooks.md)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks.md)

## 实施方案

### 第一层：稳定宿主

安装器寻找真实 `pwsh.exe`，把路径写入用户配置，并在 Cursor 设置中配置 `terminal.integrated.automationProfile.windows`。稳定宿主负责接收工具调用和启动子 shell，不等于所有命令都必须使用 PowerShell 语法。

### 第二层：语义路由

Hook 的选择顺序是：

1. 以 `pwsh`、`powershell`、`cmd`、`bash`、`sh` 或 `wsl` 开头时，保留显式选择。
2. 识别到 PowerShell 变量、cmdlet、类型表达式时，保留宿主。
3. 识别到删除、磁盘写入、关机等危险命令时，禁止自动切换解释器。
4. 识别到 `export`、`source`、heredoc、`[[ ... ]]`、Bash 控制流等语法时，交给 Git Bash。
5. 识别到 `@echo off`、`setlocal`、`%VAR%`、`if errorlevel` 等语法时，交给 `cmd.exe /d`。
6. 其他命令保留宿主。WSL 不自动选择。

分类器返回稳定的 `shell` 和 `reason`，便于单元测试验证路由原因。子 shell 使用唯一临时脚本，执行结束后清理并传播退出码。

分类、Hook 改写、诊断和直接执行现在共用同一份路由计划。`--explain` 可以在不执行命令的情况下输出选择的 shell、原因、解释器路径和改写项；`--run` 供没有命令前置 Hook 的 Agent 使用，直接启动判定出的解释器并传播退出码。

### 第三层：让 agent 生成原生命令

规则和 skill 明确要求：

- Windows 管理和普通原生 CLI 使用 PowerShell。
- 依赖 Bash 或批处理语法时，让完整脚本进入对应解释器，不在一条命令中混写。
- Linux 工具链或 Linux 文件系统项目显式使用 `wsl.exe`。
- PowerShell 中使用 `Get-ChildItem`、`New-Item`、`Set-Location` 和 `curl.exe`。
- 多行提交信息使用文件配合 `git commit --file`。
- 原生程序执行后检查 `$LASTEXITCODE`。
- PowerShell 5.1 不使用 `&&`。
- 内网直连优先使用 `curl.exe --noproxy '*' --max-time 30`；`Invoke-RestMethod -NoProxy` 仅用于支持该参数的 PowerShell 版本。

### 第四层：保守兼容

Hook 当前只做以下处理：

- 在命令位置改写 `ls -la`、`curl`、`which`、`touch` 和 `cd /d`。
- 扫描时屏蔽字符串与注释，避免改写示例文本。
- 区分 URL 百分号编码与批处理变量，避免把 `%20`、`%2F` 等 URL 片段误送给 `cmd.exe`。
- 按上一节的高置信规则选择 Git Bash 或 `cmd.exe`。
- 遇到代码位置的 `&&` 或 `||` 且找到 PowerShell 7 时，用唯一临时 `.ps1` 文件执行。
- PowerShell 7 下，为匹配规则的 `Invoke-RestMethod` / `Invoke-WebRequest` 注入 `-NoProxy`。
- 包装执行后清理临时文件并传播退出码。

Hook 不处理以下情况：

- 不翻译或自动路由删除、磁盘写入、关机等破坏性操作。
- 找不到 PowerShell 7 时不改变 `&&` 语义。
- 找不到目标 shell 时不伪造对应语法。
- 不根据 Unix 命令名自动进入 WSL。
- 不尝试修复宿主的 PTY、临时脚本编码或输出捕获缺陷。

## 本机验证

验证环境：Windows Server 2022、Windows PowerShell 5.1.20348.4294、PowerShell 7.6.4、Python 3.13、Git for Windows。

已验证：

- 34/34 Python 单元测试通过。
- `--explain` 与 Hook 使用同一份路由计划，可以稳定报告 `shell`、`reason`、解释器路径和改写项。
- `--run` 在无 Hook 路径下直接启动 PowerShell、Git Bash 或 `cmd.exe`，已验证中文输出、短路语义和退出码传播。
- Python 文件通过 `py_compile`。
- 明确 Bash 语法自动交给 Git Bash，成功路径输出 `BASH:agent` 并返回 0，失败路径传播退出码 7。
- 明确批处理语法自动交给 `cmd.exe /d`，成功路径输出 `CMD:agent` 并返回 0，失败路径传播退出码 9。
- PowerShell 语法、普通原生 CLI 和显式 `wsl.exe` 调用不会被误路由。
- `rm -rf`、`rm -fr` 和 `rd /s /q` 出现在自动路由候选中时保持宿主执行，不会因切换 shell 被激活。
- `&&` 前一条原生命令返回 7 时，后一条不执行，外层最终返回 7。
- `&&` 前一条返回 0 时，后一条执行，最终返回 0。
- 中文 heredoc 经 Git Bash 输出正确，最终返回 0。
- 本轮包装执行没有留下新的临时文件。
- `%20`、`%2F` 等 URL 百分号编码不会误触发批处理路由；闭合 `%PATH%` 和 `for %%F` 仍能识别。
- `then rm -rf` 和括号内 `rd /s /q` 等嵌套危险命令保持在宿主，不会因自动切换解释器而被激活。
- Cursor stdin/stdout 返回 `updated_input`。
- Claude Code stdin/stdout 返回 `hookSpecificOutput.updatedInput`。
- PowerShell 5.1 隔离环境连续安装两次后，多 shell 配置写入成功，本项目 Hook 没有重复，生成 JSON 无 BOM。

## 后续建议

宿主产品应进一步提供明确、稳定的 agent shell 配置，并公开实际执行器、PowerShell 版本、编码和退出码规则。若宿主始终使用临时 `.ps1`，应由宿主保证无 BOM UTF-8、正确传播原生程序退出码，并让用户能选择 `pwsh.exe`，而不是依赖提示词或 profile 补救。
