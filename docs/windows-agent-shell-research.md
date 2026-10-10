# Windows 终端对 AI agent 的兼容性调研

## 结论

Windows 下的 AI agent 终端问题不能靠固定使用一个 shell，也不能靠“把 bash 命令批量翻译成 PowerShell”解决。更可靠的顺序是：

1. 使用 PowerShell 7 作为稳定宿主。
2. PowerShell 和普通原生 CLI 留在宿主；明确 Bash 语法交给 Git Bash，明确批处理语法交给 `cmd.exe`。
3. WSL 只在需要 Linux 环境时显式进入，不根据命令外观自动选择。
4. 用长期规则要求 agent 先选择正确解释器，再生成该解释器的原生命令。
5. 对删除、磁盘写入、关机等破坏性命令保持失败可见，不通过自动路由激活。

本项目已按这个顺序实现。Hook 不再把 `&&` 替换为 `;`，也不会通过翻译或切换 shell 激活删除命令。

## Cursor 实测记录（2026-09-02 至 2026-10-10）

结论：这台机器上，agent 的 Shell 调用 98% 以上是 PowerShell 或原生 CLI，需要换 shell 的极少。随机抽查“调用之后 agent 说出了问题”的记录，约七分之一是 shell 层的问题，最常见的是“命令跑了但输出没了”，主要原因是 Cursor 脚本结尾的 `exit` 丢掉了 PowerShell 还没输出的表格。Hook 本身每次调用要花约 0.6 秒，所以这一轮把它的开销降下来，并修了“输出被吞”这个最大的可修项。

数据来源：Cursor 的 `cursor.hooks*.log`（每次 `preToolUse` 都记录输入、输出和耗时）与 `~\.cursor\projects` 下的会话记录。只有一台机器、一个用户，不能外推到别的环境。

### 规模和命令构成

| 项目 | 数值 | 基数 |
|------|------|------|
| Shell 调用 | 12,401 次，11,855 条不同命令 | 23 个活跃日，270 个会话；Cursor 3.17.19 占 2,253 次，3.21.9 占 10,148 次 |
| 含 PowerShell 语法 | 9,745（78.6%） | 全部调用 |
| 无明显语法特征（`git`、`rg` 等原生 CLI） | 2,417（19.5%） | 全部调用 |
| 显式指定 shell（`powershell -Command`、`cmd /c`、`pwsh`、`wsl.exe`） | 173（1.4%） | 全部调用 |
| 需要 PowerShell 7 语义（代码位置的 `&&`、`\|\|`，或 `-NoProxy`） | 59（0.5%）；文本里出现 `&&` 106 次、`\|\|` 53 次（含字符串里的） | 全部调用 |
| 真正的 Bash heredoc | 2 条 | 50 条命令含 `<<`，其余 48 条是 PowerShell 命令里的 `<<<<<<<` 冲突标记，或把 heredoc 错写进 PowerShell 的命令 |
| 以自动表格收尾（`… \| Select-Object Name`） | 约 11%（1,404 次，宽松正则） | 全部调用 |
| 含 here-string / 非 ASCII | 6.6%（814 次）/ 13.8%（1,706 次） | 全部调用 |

### Hook 自身的开销

| 项目 | 数值 |
|------|------|
| 每次调用耗时 | 中位 630 ms，p90 770 ms，p99 1,802 ms，最长 10,260 ms |
| 累计 | 143.5 分钟（12,401 次） |
| 超过 1 秒 / 5 秒 | 412 次 / 18 次 |
| 实际改写了命令 | 53 次（0.4%） |
| 日志里输出为 `(empty)` | 9 次（0.07%），退出码全是 0，耗时 657–1,083 ms，属正常范围 |

9 次空输出不是脚本的问题，而是 Cursor 一侧的竞争。依据有三条：

1. 用这 9 条命令原样构造 payload 重放，提交 `0561bab` 的脚本和当前脚本每次都输出了 JSON 响应。
2. 空 stdin、非法 JSON、脚本内部异常这几条路径，脚本也都会输出 `{"permission": "allow"}`。
3. Cursor 3.21.9 的 Hook 执行器（`extensionHostProcess.js` 里的 `$executeHookDirect`）一收到进程的 `exit` 事件就返回。它不等 stdout 关闭，晚到的输出就丢了。

空输出与并发无关：这 9 次里 1 次前后 1 秒内有别的 Hook 调用（11%），其余调用里是 13.7%。Cursor 收到空输出时照原样执行命令，所以最坏情况是这一次没被改写，不会被拦下。

耗时的大头是 Cursor 先启动一个 `pwsh.exe -NoProfile` 再通过管道把 JSON 交给脚本，Python 只占其中一小部分。这一轮优化前后（2026-10-10，同一台机器，同样的启动方式，中位数）：

| 项目 | 之前 | 之后 | 方法 |
|------|------|------|------|
| Python 脚本本身（裸解释器 32 ms） | 146 ms | 74 ms | 25 次 |
| 完整 Hook（pwsh → Python） | 641 ms | 573 ms | 15 次 |
| 每条 Shell 命令额外承担的 profile 与前缀开销 | +127 ms | +48 ms | 14 次，相对不加载 profile 的 569 ms |

省下的来自：不再在每次调用时导入 `dataclasses`、`argparse`、`subprocess`、`pathlib`、`tempfile`、`shutil`；不再查找用不到的 Git Bash / cmd / pwsh 路径；profile 里去掉 `chcp 65001`（要启动一个子进程）和 `Console.InputEncoding`；PowerShell 7 上不再设 `$PSDefaultParameterValues`；不再给每条命令加 UTF-8 前缀；profile 块用字面路径，不再读环境变量和解析 JSON。Hook 端到端仍有约 570 ms，Python 只占 74 ms，其余约 500 ms 是 Cursor 启动 `pwsh.exe` 并通过管道交 JSON 的成本，脚本改不了。两组数据都在同一台机器上依次测，测量噪声约 ±10 ms。

真实环境里，改动装好后（2026-10-10 17:46 起）Cursor 日志记录的 52 次 Hook 调用中位 590 ms、p90 818 ms。同一天此前 2,642 次的中位是 659 ms，2026-10-09 全天 1,102 次的中位是 621 ms。样本只有 52 次，而且同一时间还在跑测试和基准，这组数只说明没有变慢，不足以单独证明提速幅度。

### 问题分类

从会话记录里找“Shell 调用之后，下一段叙述提到出了问题”的调用：18,428 次 Shell 调用去重后命中 2,307 条。会话记录里没有工具返回值，只能靠 agent 的叙述判断，所以下面的数是“agent 自己察觉并说出来的问题”，不是发生率。

分类用的是从 2,307 条里按固定随机种子（20261010）抽的 300 条，由模型逐条打标签，我抽查了 2 条，标签对得上。早先还分类过另一批 268 条（69 个会话），但它的挑选规则已无法复现，而且 shell 层占比明显偏高，只作参照。两批共有 33 条，其中 30 条标签完全一致，31 条在“shell 层还是任务本身”这一级一致。

| 类别 | 随机 300 条 | 其中高置信 | 参照：268 条 |
|------|-------------|-----------|-------------|
| 任务本身的问题（代码、测试、数据），shell 正常 | 259 | 235 | 156 |
| 命令跑了但输出为空或丢失 | 14 | 7 | 27 |
| 代理和网络 | 5 | 3 | 1 |
| 其他 shell 层问题（`git commit-tree` 被包装器改写、PowerShell 管道弄坏文件、CRLF） | 5 | 1 | 7 |
| PowerShell 引号、转义、解析 | 4 | 4 | 35 |
| 工作目录、路径、环境变量（例如目录里有 shell 时删不掉该目录） | 4 | 2 | 11 |
| 退出码和 stderr | 4 | 1 | 5 |
| Bash 语法送进 PowerShell | 2 | 0 | 6 |
| 编码 | 2 | 0 | 11 |
| 超时、卡住 | 1 | 0 | 2 |
| 命令找不到（多为 `python` 不在 PATH） | 0 | 0 | 7 |
| 合计 | 300 | 253 | 268 |

随机样本里 41 条（13.7%，95% 置信区间 10.2%–18.0%）是 shell 层问题，按比例折算到 2,307 条约 315 条（236–416 条）。最多的是“输出丢失”：14 条（4.7%），折算约 108 条。其余各类都不超过 5 条，样本太小，排不出可靠的先后。268 条那批里引号转义有 35 条，随机样本里只有 4 条，可见那批的挑法偏向了叙述里明说 shell 出错的记录。

“输出丢失”这一类，随机样本 14 条里有 10 条、268 条那批 27 条里有 21 条，现在会被输出保护包住（见下一节）。剩下的命令里没有会被缓冲的表格，从叙述看不出原因。

### “输出丢失”的原因

Cursor 给每条 Shell 命令生成的脚本以 `exit $COMMAND_EXIT_CODE` 结尾。PowerShell 会把自动表格（`Select-Object` 只选 4 个以内属性、`[pscustomobject]`）先缓冲，等管道结束才排版输出；脚本直接 `exit` 时，这些缓冲输出连同后面的输出一起丢掉。用 Cursor 同样的脚本外壳复现：

| 命令 | 原样执行 | 加 `. { … } \| Out-Host` |
|------|---------|--------------------------|
| `Get-ChildItem C:\Windows -Directory \| Select-Object -First 3 Name` | 0 行 | 5 行 |
| `Get-Command git,py … \| Select-Object Name,Source` | 0 行 | 4 行 |
| `[pscustomobject]@{a=1;b=2}` | 0 行 | 3 行 |
| 多行管道 | 0 行 | 4 行 |
| `Set-Location …; Get-ChildItem … \| Select-Object …; (Get-Location).Path` | 0 行 | 5 行 |
| `Get-Date \| Select-Object Year, Month; "v=$v"` | 0 行 | 4 行 |
| `'{"a":1,"b":2}' \| ConvertFrom-Json; Write-Output AFTER` | 0 行 | 表格和 `AFTER` 都在 |
| `"a,b`n1,2" \| ConvertFrom-Csv; Write-Output AFTER` | 0 行 | 表格和 `AFTER` 都在 |
| 5 个属性的 JSON 对象（按列表排版） | 都在 | 都在 |
| 路径不存在（只有错误） | 5 行 | 5 行 |
| 末尾自己写 `exit 3` | 0 行 | 0 行（退出码都是 3） |

规则只在命令里有未收口的小表格时才包（`Select-Object` 带 1–4 个属性、`[pscustomobject]@{…}`；已经接了 `Format-Table`、`Out-String`、重定向或赋值的语句不算）。第二轮审查后补了一条：语句直接打印 `ConvertFrom-Json`、`ConvertFrom-Csv`、`Import-Csv`、`Invoke-RestMethod` 的结果时也包，后面只允许接 `Where-Object`、`Sort-Object` 这类不改变记录的阶段；赋值、`(…).name` 取属性、`ForEach-Object` 投影都不打印记录，不包。第一版只看命令名，重放时会多包 105 条（多数是 `foreach` 块里的 `$j = … | ConvertFrom-Json`）；按嵌套层次判断后，历史命令里没有一条因此被包，也没有找到漏掉的真实案例。含不含 `git`、`py` 这类原生程序都包，因为 PowerShell 和原生程序混在一条命令里恰恰最常见：用这套规则重放 11,855 条历史命令，652 条（5.5%）会被包，其中 355 条纯 PowerShell，297 条混有原生程序。

包一层的代价是原生程序的输出改走 PowerShell 管道，不再直通控制台。本机实测（每组 7 次取中位数，原生输出由 Python 生成）：

| 原生输出行数 | 原样执行 | 包一层 | 增加 |
|-------------|---------|--------|------|
| 200 | 571 ms | 590 ms | +19 ms |
| 3,000 | 553 ms | 860 ms | +307 ms |
| 20,000 | 584 ms | 1,794 ms | +1.2 s |

多数命令的输出在几百行以内，增加的时间可以忽略；输出特别大时多花几百毫秒到一秒多，比 agent 因为输出丢了再跑一遍便宜。这 652 条命令包前包后都能被 PowerShell 解析器解析：646 条两边都通过，6 条两边都报错（原命令本身就有语法错误），没有“原来能解析、包完不能解析”的命令。包完仍保留 `$LASTEXITCODE`，Cursor 脚本用它作退出码，单元测试里用真实 `pwsh` 验证了退出码 7 能传出。

两点没覆盖：原生程序输出二进制时，经 PowerShell 管道会按控制台编码转一次文本；`utf8Prefix` 关闭且 profile 没加载的宿主上，非 ASCII 原生输出可能多一次转码。这台机器的 Shell 工具加载 profile，控制台输出已经是 UTF-8，没有这个问题。

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

因此安装器用 .NET API 写无 BOM UTF-8 JSON；Hook 读取配置和 Cursor 交来的 payload 时按 `utf-8-sig` 解码，带 BOM 的输入也能读。

UTF-8 输出编码放在 profile 里。Cursor 的 Shell 工具启动 `pwsh.exe` 时不带 `-NoProfile`（见下一节），profile 会加载，所以只需在 profile 里设一次，不必在每条命令前重复。Hook 的命令前缀（`utf8Prefix`）默认关闭，只对 `-NoProfile` 启动的子 shell（Hook 的 PowerShell 7 包装、`--run`）和 Windows PowerShell 5.1 宿主（profile 块只写给 PowerShell 7）强制加。profile 里不再设 `chcp 65001` 和 `Console.InputEncoding`：前者要多启动一个子进程，后者没有找到对应的乱码证据（随机 300 条里编码类 2 条，都不是高置信；参照的 268 条里 11 条，高置信 1 条）。

来源：

- [about_Character_Encoding](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding)

### 集成终端设置不等于 agent 执行环境

IDE 可以为普通集成终端设置默认 profile 或 automation profile，但 agent 可能通过自己的工具启动临时 PowerShell 脚本。此时用户 profile、终端 UI 设置或当前交互会话环境不一定生效。

Cursor 3.21.9 的做法（读自安装目录里的 `resources\app\out\vs\workbench\api\node\extensionHostProcess.js`，并用 Hook 日志和运行中的临时脚本核对过）：

1. Shell 工具和 Hook 都按 Cursor 进程自己的 `PATH` 找 PowerShell：先 `pwsh`，再 `powershell`，最后 `System32\WindowsPowerShell\v1.0\powershell.exe`。代码里不读 `terminal.integrated.automationProfile.windows`。
2. Shell 工具的命令行是 `pwsh -ExecutionPolicy Bypass -NonInteractive -File ps-script-*.ps1`，会加载用户 profile。
3. 临时脚本先恢复这个对话上一次保存的环境变量（包括 `Path`），再执行命令，然后把 `$LASTEXITCODE` 存为退出码，最后 `exit`。对话的第一条命令没有保存的环境。
4. Hook 的命令行是 `pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -c …`，不加载 profile。`sessionStart` 返回的环境变量只传给之后的 Hook，不传给 Shell 工具。

由此有两个推论。第一，环境变量在对话内是“粘”的：profile 里的改动只对之后新开的对话生效。第二，Cursor 进程的 `PATH` 决定了用哪个 PowerShell、能找到哪些工具。

本机就碰到了第二点。Cursor 由 uTools 启动；uTools 在 2026-08-13 16:15 随开机启动后一直没重启，它交给子进程的环境变量停在那一刻。之后装的 Python 3.13、Node.js 和 GitHub CLI 都不在 Cursor、Hook 进程和 agent shell 的 `PATH` 里：当时 agent shell 的 `PATH` 只有 12 项，`python`、`node`、`gh` 都找不到。`src\profile.ps1` 现在会把注册表里有、当前会话缺的 PATH 目录追加到末尾，单次耗时约 0.4 ms。`session_start.py` 判断 `python` 在不在之前，也先按同样的规则补齐 PATH。

Cursor 提供 `sessionStart` 和 `preToolUse` Hook。`sessionStart` 适合注入短提示，`preToolUse` 可以在 Shell 工具执行前检查或更新输入。Hook 本身应保持 fail-open，避免兼容层故障阻断所有命令。

来源：

- [Cursor terminal tool](https://cursor.com/docs/agent/tools/terminal.md)
- [Cursor hooks](https://cursor.com/docs/hooks.md)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks.md)

## 实施方案

### 第一层：稳定宿主

安装器寻找真实 `pwsh.exe`，把路径写入用户配置，并在 Cursor 设置中配置 `terminal.integrated.automationProfile.windows`。这个设置只影响集成终端里的任务；Cursor 的 Shell 工具不读它，而是按 Cursor 进程的 `PATH` 找 `pwsh`（见上一节）。所以对 agent 来说，“稳定宿主”的前提是 PowerShell 7 在 Cursor 启动时就已经在 `PATH` 里。稳定宿主负责接收工具调用和启动子 shell，不等于所有命令都必须使用 PowerShell 语法。

### 第二层：语义路由

Hook 的选择顺序是：

1. 以 `pwsh`、`powershell`、`cmd`、`bash`、`sh` 或 `wsl` 开头时，保留显式选择。
2. 命令里有 heredoc（`<<`）时：`<<` 之前的文本不像 PowerShell（没有 `$x =`、`& $x`、`Get-*` 等 cmdlet、`$env:`、`@{`、`::`、盘符路径），且没有危险命令，才交给 Git Bash。全量历史里 50 条命令含 `<<`，只有 2 条是真的 Bash heredoc，其余多是 `<<<<<<<` 冲突标记，所以必须先看命令头。
3. 识别到 PowerShell 变量、cmdlet、类型表达式时，保留宿主。
4. 识别到删除、磁盘写入、关机等危险命令时，禁止自动切换解释器。这一步查原始文本，不查遮罩后的文本，命令词出现在任何位置都算（`env rm`、`xargs rm`、`bash -c 'rm …'`、`find -delete`、`git clean`）。原因是 heredoc 正文或 `echo` 里的一个撇号会在 PowerShell 的词法里打开字符串，把后面的行全部遮掉。宁可多拦：被拦下的命令留在 PowerShell，最多报错，不会执行。`format` 和 `truncate` 在提交信息里常见，只认 `format D:`、`truncate -s` 这种命令形式。
5. 识别到 `export`、`source`、`[[ ... ]]`、Bash 控制流等语法时，交给 Git Bash。
6. 识别到 `@echo off`、`setlocal`、`%VAR%`、`if errorlevel` 等语法时，交给 `cmd.exe /d`。命令里有 `$name` 变量时不交：批处理没有这种变量，`$i%2` 是 PowerShell 的取模。
7. 其他命令保留宿主。WSL 不自动选择。

扫描前先把字符串和注释遮住。遮罩认识 here-string、保留双引号里的 `$(…)`（里面是代码），并且只在词首把 `#` 当注释（`$a#b` 不是）。遮罩保留换行，所以带行首锚点的正则在锚点后只能用 `[ \t]*`：用 `\s*` 会从每个换行开始把后面所有空行重扫一遍，2,000 行的 here-string 要算 20 秒，超过 Hook 的 10 秒时限。现在 5,000 行在 1 秒内算完，有测试守着。

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
- 遇到代码位置的 `&&` 或 `||`：先看宿主进程。宿主已经是 `pwsh.exe`（PowerShell 7）时原地执行，不再启动子进程；宿主是 `powershell.exe`（5.1）或查不出来时，找到 PowerShell 7 就用唯一临时 `.ps1` 文件执行。`-NoProxy` 同理。
- 为匹配规则的 `Invoke-RestMethod` / `Invoke-WebRequest` 注入 `-NoProxy`。
- 命令里有可能被 `exit` 吞掉的自动表格时，包成 `. { … } | Out-Host`（见“输出丢失”一节）。脚本级语句（`using`、`param`）不包。命令自己以 `exit N` 结尾时，包了也救不回缓冲输出（见上表最后一行），退出码不受影响。
- 包装执行后清理临时文件（启动时顺带清掉 24 小时前遗留的 `agent-*` 文件）并传播退出码。

Hook 不处理以下情况：

- 不翻译或自动路由删除、磁盘写入、关机等破坏性操作。
- 找不到 PowerShell 7 时不改变 `&&` 语义。
- 找不到目标 shell 时不伪造对应语法。
- 不根据 Unix 命令名自动进入 WSL。
- 不尝试修复宿主的 PTY、临时脚本编码或输出捕获缺陷。

## 本机验证

验证环境：Windows Server 2022、Windows PowerShell 5.1.20348.4294、PowerShell 7.6.4、Python 3.13、Git for Windows。

已验证：

- 交付前的独立审查（Opus）发现两处由这一轮改动带来的回归，都已修复：一是 heredoc 正文里的撇号让后面的 `rm -rf` 逃过危险命令检查、被送进 Git Bash 执行；二是锚点后的 `\s*` 让长命令的规划时间按平方增长。第一处审查在隔离目录里复现了真删除。同一份审查指出的旧问题也一并修了：包装器里的删除命令（`env rm` 等 20 种写法）、PowerShell 取模被当成批处理变量、宿主检测每次要枚举全部进程（约 27 ms，改为直接查父进程，约 0.1 ms）、安装器的 6 处边角问题（非 UTF-8 profile 被改写成乱码、5.1 下 `py` 写 stderr 会中断安装、只装 `-Cursor` 时删掉了旧 profile 块依赖的配置、手改过的旧 profile 块会重复、行尾 `//` 注释在 PowerShell 7 下被丢掉、技能路径是文件符号链接时安装中断）。修复后用 11,855 条历史命令重放，只有 4 条的原因标签从 `native-or-powershell` 变成 `unsafe-command`，路由和改写都没变；规划全部命令的总耗时从 22.4 秒降到 10.7 秒。
- 第二轮独立审查（Opus，看最终 diff）没有发现阻断问题，提出 4 项应修，都已修复：
  - 宿主是 Windows PowerShell 5.1 时 profile 块不加载，UTF-8 设置缺失。每次查父进程要多 16–21 ms，所以改看 `PSModulePath` 第一项：每个 PowerShell 都把自己的用户模块目录放在第一位，子进程继承。7 套 5.1、5.1 套 7、两种干净环境共 6 种组合实测都判对，耗时可以忽略。判成 5.1 时 Hook 给命令补 UTF-8 设置；判错只会多补一段无害的设置。
  - 安装器按 `%USERPROFILE%\Documents` 找 profile，文档文件夹被 OneDrive 或策略重定向时写错位置。现在用文档文件夹的真实位置；`USERPROFILE` 被改到隔离目录时仍用隔离目录。
  - `rsync --delete`、`robocopy /MIR`（及 `/PURGE`、`/MOVE`）、`reg delete`、`sc delete`、`schtasks /delete` 没有被当成危险命令。重放历史命令，只有 2 条的原因标签变了（`schtasks.exe /Delete /?` 和 `git push origin --delete`），路由不变。
  - 直接打印 `ConvertFrom-Json` 等命令的结果时，输出同样会被吞（见“输出丢失”一节）。
  - 小问题也一并修了：`[[ … ]]` 的匹配限定在一行 300 个字符以内，并要求内侧有空格（PowerShell 泛型 `List[[string]]` 不再被当成 Bash）；只给测试用的 `classify_command` 删掉，测试改走 `plan_command`。
  - 查 Claude Code 官方文档时发现适配器有两处旧问题：Claude 在 Windows 上用 Git Bash 执行 shell 形式的 Hook 命令，而适配器写的是 PowerShell 的 `& "…"`；Bash 工具的命令也被送进来做 PowerShell 改写，`ls -la` 会变成 `Get-ChildItem`。现在改用 exec 形式（`command` + `args`，不经过 shell），只挂 PowerShell 工具，脚本也不再处理 Bash 工具的命令。这台机器没装 Claude Code，这条路径没有实机验证。
  - 每项修复各有测试；把 14 处修复逐个改回去，测试都能抓到。其中一处是新判断自己带来的：最初每遇到一个 `}` 都把前面整段文本拼起来查，单条语句有几千个块时按平方增长（82 KB 要 8.8 秒）；改成只看块前面几十个字符后，150–490 KB 的单行命令 0.5–2.0 秒，随长度线性增长。
- 83/83 Python 单元测试通过，其中包括：用带 BOM 的 payload 起真实子进程跑 Hook；常见路径不导入 `argparse`、`subprocess`、`dataclasses` 等重模块；PowerShell 7 / 5.1 / 未知宿主的包装分支；输出丢失保护的判定，以及用真实 `pwsh` 按 Cursor 的“命令 + `exit $LASTEXITCODE`”外壳跑一遍，确认表格、后续输出和原生程序的退出码 7 都还在；`session_start.py` 按注册表补齐 PATH 后再判断 `python`；审查发现的每个问题各有一条测试，另外用 10 个故意改坏的版本（审查里没被测试抓住的 6 个，加上把每项修复改回去）逐个跑，全部被测试抓到。
- 装好后在 Cursor 里直接验证（2026-10-10）：`Get-ChildItem C:\Windows -Directory | Select-Object -First 3 Name` 输出了表格；一条同时含表格、`cmd /c` 和 `&&` 的命令，没加保护时只输出第一行，加上后表格和 `chain ok` 都在；表格加 `py -3 -c "…sys.exit(5)"` 的命令，Cursor 报告退出码 5。Hook 日志显示这条 `&&` 命令没有被包进 PowerShell 7 子进程，说明 Hook 进程认出了宿主是 `pwsh.exe`。
- 用当前 profile 启动的新 `pwsh`，即使继承了只有 12 项的旧 `PATH`，也能找到 `python`、`node` 和 `gh`；Windows PowerShell 5.1 点源同一个文件也正常。
- 用 11,855 条历史命令对比新旧路由：11,476 条结果相同；355 条（只算纯 PowerShell 命令，后来又放宽到含原生程序的命令，见上）只多了输出保护；21 条只是原因标签变了（字符串里的 `$(…)` 现在被识别为 PowerShell 语法）；3 条被改成 Bash heredoc，收紧命令头判断后只剩 2 条真正的 heredoc。没有异常。
- 安装器在隔离目录里分别用 PowerShell 7.6.4 和 Windows PowerShell 5.1 各跑两遍：第二遍无改动，用户已有的 hooks 条目、profile 内容保留，`hooks.json` 没有 BOM。`settings.json` 带注释（包括行尾注释）时两种 PowerShell 都不改它，只提示要手动加的键；用本机真实 `settings.json` 的副本跑，结果是“不变”，文件哈希一致。审查修复后另跑了 31 项场景检查（GBK profile、手改过的旧块、只装 `-Cursor`、行尾注释、文件符号链接、重复安装），PowerShell 7 和 5.1 下全部通过。安装器没有带 `-Env` 运行，没有改真实环境变量。
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
- Claude Code stdin/stdout 返回 `hookSpecificOutput.updatedInput`（单元测试验证格式；没有实机验证）。Bash 工具的调用原样放行。
- PowerShell 5.1 隔离环境连续安装两次后，多 shell 配置写入成功，本项目 Hook 没有重复，生成 JSON 无 BOM。

## 后续建议

宿主产品应进一步提供明确、稳定的 agent shell 配置，并公开实际执行器、PowerShell 版本、编码和退出码规则。若宿主始终使用临时 `.ps1`，应由宿主保证无 BOM UTF-8、正确传播原生程序退出码，并让用户能选择 `pwsh.exe`，而不是依赖提示词或 profile 补救。
