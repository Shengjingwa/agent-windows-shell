# Codex / other agents

There is no single hook format. Use:

1. `install.ps1 -Profile -Env -Skill` so interactive `pwsh` and Python default to UTF-8, and the skill is linked into `~/.cursor/skills`. Other products load skills from their own folder; point that folder at `skills/windows-agent-shell` yourself.
2. Paste `adapters/generic/AGENTS.md` into that product's instruction file (`AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, …).
3. If the product can set one agent shell, point it at `pwsh.exe` (PowerShell 7) as the stable host. Use explicit `bash`, `cmd.exe /d /c`, or `wsl.exe` commands when another environment is required.
4. If the product supports a pre-tool hook, connect `src\rewrite_windows_shell.py` so clear Bash and batch syntax can be routed automatically. Do not point the host at the Store `WindowsApps\bash.exe` stub (0 bytes).
5. If the product has no pre-tool hook but accepts a command wrapper, execute commands through `py -3 "$env:AGENT_WINDOWS_SHELL_ROOT\src\rewrite_windows_shell.py" --run '<command>'` (or `python.exe` when it is on PATH). Use `--explain '<command>'` to inspect the selected shell without running it.
6. Optional: `src\profile.ps1` sets UTF-8 for interactive hosts. Agents that pass `-NoProfile` skip it; `--run` sets the same UTF-8 state itself, and the hook can do it per command with `"utf8Prefix": true` in `~/.agent-windows-shell.json`.
