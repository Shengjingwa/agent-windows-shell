#!/usr/bin/env python3
"""sessionStart hook: UTF-8 env plus a short Windows shell briefing.

The briefing states rules that hold everywhere, then adds facts that are true
on this machine (for example, that `python` is not on PATH).
"""
import json
import os
import shutil
import sys

RULES = (
    "Windows shell host is PowerShell 7 when available. Keep Windows tasks and "
    "native CLIs in PowerShell; use Git Bash only for real Bash syntax, "
    "`cmd /d /c` for batch syntax, and `wsl.exe` only for Linux projects. The "
    "pre-tool hook routes clear Bash or batch syntax and never routes "
    "destructive commands. `;` is unconditional in PowerShell: check "
    "$LASTEXITCODE after native programs. Use `git commit --file` for "
    "multi-line messages. For direct HTTP use `curl.exe --noproxy '*'` or "
    "`Invoke-RestMethod -NoProxy`. A command that ends in an auto-formatted "
    "object table can print nothing; end it with `| Format-Table | Out-String`."
)


def shell_path() -> str:
    """PATH as the agent's shell sees it after src/profile.ps1 runs.

    The hook inherits the editor's PATH from launch time; the profile appends
    registry entries that PATH lacks, so do the same before judging.
    """
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    try:
        import winreg
    except ImportError:
        return os.pathsep.join(dirs)
    have = {d.rstrip("\\").lower() for d in dirs}
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    )
    for root, subkey in keys:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value = winreg.QueryValueEx(key, "Path")[0]
        except OSError:
            continue
        for entry in winreg.ExpandEnvironmentStrings(value).split(";"):
            entry = entry.rstrip("\\")
            if entry and entry.lower() not in have:
                have.add(entry.lower())
                dirs.append(entry)
    return os.pathsep.join(dirs)


def usable(name: str, path: str) -> bool:
    found = shutil.which(name, path=path)
    # The WindowsApps python.exe is a Store launcher, not an interpreter.
    return bool(found) and "\\WindowsApps\\" not in found


def machine_facts() -> list:
    facts = []
    path = shell_path()
    if not usable("python", path):
        if usable("py", path):
            facts.append("`python` is not on PATH here; use `py -3` or a full path.")
        else:
            facts.append("No Python interpreter is on PATH here.")
    return facts


def main() -> None:
    context = " ".join([RULES, *machine_facts()])
    json.dump(
        {
            "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            "additional_context": context,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
