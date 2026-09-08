---
name: windows-agent-shell
description: >-
  Choose PowerShell, Git Bash, cmd.exe, or WSL for AI coding agents on Windows,
  including UTF-8, proxy, path, quoting, and exit-code guidance.
---

# Windows shells for agents

Use PowerShell 7 as the stable host, then choose another interpreter only when the command needs its grammar or environment.

- PowerShell: Windows administration, filesystem operations, and ordinary native CLIs.
- Git Bash: genuine Bash scripts using `export`, `source`, heredoc, `[[ ... ]]`, or Bash control flow.
- `cmd.exe /d /c`: existing batch syntax or commands implemented by `cmd`.
- WSL: Linux distributions, Linux-resident projects, or tools unavailable on Windows. Invoke `wsl.exe` explicitly; avoid automatic routing of Windows projects through `/mnt/c`.

## Do

- Keep `git`, `npm`, Python, compilers, and similar native CLIs in the current host unless shell syntax requires otherwise
- In PowerShell, use `;` only for unconditional sequencing; gate dependent commands with `$LASTEXITCODE` or `$?`
- `$HOME` / `$env:USERPROFILE` for paths
- `curl.exe --max-time 30` for HTTP while preserving the configured proxy; add `--noproxy '*'` only for an explicitly direct request or a target covered by `NO_PROXY`; use `git commit --file` for multi-line messages
- After `git` / `npm` / compilers: check `$LASTEXITCODE`
- If `agent-windows-shell` is installed, let its hook route only clear Bash or batch syntax
- Without a pre-tool hook, use `rewrite_windows_shell.py --run '<command>'` only after resolving and verifying that script path; if it is unavailable, run the command directly in the selected host. Inspect routing with `--explain '<command>'` when the wrapper is available
- Explicitly select `bash`, `cmd`, or `wsl.exe` when ambiguity remains

## Do not

- Mix Bash heredoc or batch variable syntax into PowerShell
- Assume `&&` / `||` work before confirming PowerShell 7
- `curl` / `wget` without `.exe` on 5.1 (they are `Invoke-WebRequest`)
- `2>&1 | Select-Object` then treat exit 0 as success
- `Invoke-RestMethod` to intranet hosts on 5.1 (ignores `NO_PROXY`)
- Auto-route delete, disk-write, shutdown, or recursive move commands to another shell

## Product limits you cannot fix in a prompt

Some IDEs wrap commands in `%TEMP%\ps-script-*.ps1` and ignore the terminal profile. CJK locales may hit wrapper encoding bugs. Do not enable the system-wide UTF-8 beta locale unless the user asks — it breaks some legacy apps.
