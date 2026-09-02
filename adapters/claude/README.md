# Claude Code / Claude Desktop (Windows)

The rewrite script speaks Claude-style hook JSON (`updatedInput` / `hookSpecificOutput`) as well as Cursor's `updated_input`.

User settings file: `%USERPROFILE%\.claude\settings.json`

Merge (keep your existing hooks):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|PowerShell|Shell",
        "hooks": [
          {
            "type": "command",
            "command": "python.exe KIT_ROOT\\src\\rewrite_windows_shell.py"
          }
        ]
      }
    ]
  }
}
```

`install.ps1 -Claude` writes `KIT_ROOT` for you.

Copy `adapters/generic/SKILL.md` into `~/.claude/skills/windows-agent-shell/SKILL.md` if you use skills.

Optional: set `CLAUDE_CODE_GIT_BASH_PATH` to a real Git Bash (`...\\Git\\bin\\bash.exe`), never the 0-byte Store stub under `WindowsApps`.
