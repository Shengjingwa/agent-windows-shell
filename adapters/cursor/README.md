# Cursor adapter

`install.ps1 -Cursor` writes:

- `%USERPROFILE%\.cursor\hooks.json` pointing at `src\rewrite_windows_shell.py`
- `%USERPROFILE%\.cursor\rules\windows-powershell-agent.mdc`
- merges terminal keys into `%APPDATA%\Cursor\User\settings.json` (does not delete other keys)
- when PowerShell 7 is found, sets `terminal.integrated.automationProfile.windows` to its real path

The installer preserves existing hooks, settings keys, and terminal environment variables. Running it again replaces only this kit's managed hook entries, so it does not create duplicates.

The automation profile is the stable host, not a requirement that every command use PowerShell syntax. The pre-tool Hook keeps ordinary native CLIs in that host, routes clear Bash syntax to Git Bash, routes batch syntax to `cmd.exe`, and leaves explicit `wsl.exe` calls alone. The rule and Hook remain necessary for hosts that launch their own PowerShell process.

Reload Cursor, then start a **new** chat. Check the Hooks output channel if rewrite does not fire.

IDE Attribution (Co-authored-by) is not in settings.json. Toggle **Cursor Settings > Agents > Attribution**, or `install.ps1 -DisableCursorAttribution` for the CLI config.
