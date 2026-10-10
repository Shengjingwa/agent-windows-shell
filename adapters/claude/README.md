# Claude Code / Claude Desktop (Windows)

The rewrite script speaks Claude-style hook JSON (`updatedInput` / `hookSpecificOutput`) as well as Cursor's `updated_input`.

User settings file: `%USERPROFILE%\.claude\settings.json`

Merge (keep your existing hooks):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "PowerShell",
        "hooks": [
          {
            "type": "command",
            "command": "PYTHON_EXE",
            "args": ["KIT_ROOT\\src\\rewrite_windows_shell.py"]
          }
        ]
      }
    ]
  }
}
```

`install.ps1 -Claude` writes `PYTHON_EXE` and `KIT_ROOT` for you, and links `skills/windows-agent-shell` into `~/.claude/skills/`.

- The hook uses exec form (`args`). Claude Code runs a shell-form hook command in Git Bash when Git Bash is installed, and bash cannot parse a PowerShell `& "…"` call.
- Only the PowerShell tool is matched. It is on by default on Windows without Git Bash; elsewhere set `CLAUDE_CODE_USE_POWERSHELL_TOOL=1`. The Bash tool already runs in Git Bash, so PowerShell rewrites would break it, and the script ignores Bash calls even if an older matcher still sends them.
- The response carries `updatedInput` without a `permissionDecision`, so Claude's own permission rules still decide whether the command runs. This path has not been tested against a live Claude Code install.

Optional: set `CLAUDE_CODE_GIT_BASH_PATH` to a real Git Bash (`...\\Git\\bin\\bash.exe`), never the 0-byte Store stub under `WindowsApps`.
