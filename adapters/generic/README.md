# Codex / other agents

There is no single hook format. Use:

1. `install.ps1 -Profile -Env -Skill` so interactive `pwsh` and Python default to UTF-8, and a skill is copied into `~/.cursor/skills` and `~/.agents/skills` if those folders exist.
2. Paste `adapters/generic/AGENTS.md` into that product's instruction file (`AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, …).
3. If the product can set one agent shell, point it at `pwsh.exe` (PowerShell 7) as the stable host. Use explicit `bash`, `cmd.exe /d /c`, or `wsl.exe` commands when another environment is required.
4. If the product supports a pre-tool hook, connect `src\rewrite_windows_shell.py` so clear Bash and batch syntax can be routed automatically. Do not point the host at the Store `WindowsApps\bash.exe` stub (0 bytes).
5. If the product has no pre-tool hook but accepts a command wrapper, execute commands through `python.exe "$env:AGENT_WINDOWS_SHELL_ROOT\src\rewrite_windows_shell.py" --run '<command>'`. Use `--explain '<command>'` to inspect the selected shell without running it.
6. Optional: a wrapper that prepends the UTF-8 snippet is `src\profile.ps1`. Agents that pass `-NoProfile` will skip it unless they also run the Python rewrite hook or `--run` entry point.
