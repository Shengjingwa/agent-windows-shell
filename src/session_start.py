#!/usr/bin/env python3
"""sessionStart hook: UTF-8 env + a short Windows shell reminder.

Agent-agnostic. No company hosts, no product names required.
"""
import json
import sys

out = {
    "env": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    },
    "additional_context": (
        "Windows shell host is PowerShell 7 when available. Use native PowerShell "
        "for Windows administration and ordinary native CLIs; use explicit Bash "
        "syntax for Git Bash, batch syntax for cmd.exe, and wsl.exe only when the "
        "task requires a Linux environment. A conservative hook can route clear "
        "Bash or batch syntax, preserve &&/|| semantics in pwsh 7, and rewrite a "
        "small safe alias set. It never auto-routes destructive commands. Use "
        "git commit --file for multi-line messages and check $LASTEXITCODE after "
        "native programs. For direct HTTP use curl.exe --noproxy '*' or "
        "Invoke-RestMethod -NoProxy in PowerShell 7."
    ),
}
json.dump(out, sys.stdout)
