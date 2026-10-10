# Cursor adapter

`install.ps1 -Cursor` writes:

- `%USERPROFILE%\.cursor\hooks.json` with a `sessionStart` and a `preToolUse` (matcher `Shell`) entry that run `src\session_start.py` and `src\rewrite_windows_shell.py` through the Python interpreter it found
- merged terminal keys in `%APPDATA%\Cursor\User\settings.json` (does not delete other keys; rewritten only when a managed key changes, and left alone with a warning if the file has comments)
- when PowerShell 7 is found, `terminal.integrated.automationProfile.windows` set to its real path (this affects the integrated terminal's tasks, not the agent's Shell tool; see below)

The installer preserves existing hooks, settings keys, and terminal environment variables. Running it again replaces only this kit's managed hook entries, so it does not create duplicates.

There is no Cursor rule file. Cursor reads rules from a project's `.cursor/rules` and from `AGENTS.md`, not from `~/.cursor/rules`; an earlier release copied a `.mdc` file there and the installer now removes it. For project-level guidance, paste `adapters\generic\AGENTS.md` into the project's `AGENTS.md`. The `sessionStart` hook and the skill carry the same rules for every project.

PowerShell 7 is the stable host, not a requirement that every command use PowerShell syntax. The pre-tool Hook keeps ordinary native CLIs in that host, routes clear Bash syntax to Git Bash, routes batch syntax to `cmd.exe`, and leaves explicit `wsl.exe` calls alone.

Facts that matter when something looks wrong (read from Cursor 3.21.9's `out\vs\workbench\api\node\extensionHostProcess.js` and checked against the hook log and live scripts on Windows Server 2022):

- The Shell tool and the hooks find PowerShell by a `PATH` lookup in Cursor's own process: `pwsh`, then `powershell`, then `System32\WindowsPowerShell\v1.0\powershell.exe`. They do not read `terminal.integrated.automationProfile.windows`. If PowerShell 7 was installed after Cursor started, Cursor keeps using Windows PowerShell 5.1 until it is restarted from a process with a fresh `PATH`.
- Hooks run as `pwsh.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -c "$OutputEncoding = UTF8; Get-Content -LiteralPath '<payload>' -Raw | & { $input | <command> }"`. The payload starts with a UTF-8 BOM, which the script strips. Environment variables returned by `sessionStart` reach later hooks, not the Shell tool.
- Each Shell command runs as `pwsh.exe -ExecutionPolicy Bypass -NonInteractive -File ps-script-*.ps1`, so your profile loads and `src\profile.ps1` applies. The script then restores the chat's saved environment (including `Path`), runs the command, saves `$LASTEXITCODE` as the exit code and ends with `exit`. PowerShell drops an auto-formatted table that is still buffered when `exit` runs; the hook's flush guard exists for that.
- Because the saved environment is restored after the profile, a profile change (such as the `PATH` refresh) reaches only chats started after the change. The first command of a new chat has no saved environment.
- The hook runner returns as soon as the hook process reports `exit` and does not wait for its stdout to close. About 1 in 1,400 calls (9 of 12,401 logged) therefore records `(empty)` output even though the script printed a response; Cursor then runs the command unchanged.

Reload Cursor, then start a **new** chat. Check the Hooks output channel (`cursor.hooks.*.log`) if a rewrite does not fire. Each call logs the payload, the response and the elapsed time.

IDE Attribution (Co-authored-by) is not in settings.json. Toggle **Cursor Settings > Agents > Attribution**, or `install.ps1 -DisableCursorAttribution` for the CLI config.
