#!/usr/bin/env python3
"""Conservatively adapt common agent commands for Windows PowerShell.

The script accepts Cursor or Claude Code PreToolUse JSON on stdin. It can also
be used directly:

  python rewrite_windows_shell.py --cmd "ls -la"
  python rewrite_windows_shell.py --explain "export NAME=agent"
  python rewrite_windows_shell.py --run "export NAME=agent; printf '%s\\n' \"$NAME\""
  python rewrite_windows_shell.py --test

Configuration files are merged in this order: kit/config.json, the user's
%USERPROFILE%\\.agent-windows-shell.json, then AGENT_WINDOWS_SHELL_CONFIG.
Hook failures are fail-open.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
UTF8_PREFIX = (
    "$utf8=[System.Text.UTF8Encoding]::new($false); "
    "[Console]::InputEncoding=$utf8; [Console]::OutputEncoding=$utf8; "
    "$OutputEncoding=$utf8; $ProgressPreference='SilentlyContinue'; "
    "if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) { "
    "$PSNativeCommandUseErrorActionPreference=$true }; "
)
BASH_HEREDOC_START = re.compile(r"(?:\$\(\s*cat\s+)?<<", re.I)
EXPLICIT_SHELL = re.compile(
    r"^\s*(?:&\s*)?(?P<shell>pwsh(?:\.exe)?|powershell(?:\.exe)?|"
    r"cmd(?:\.exe)?|bash(?:\.exe)?|sh(?:\.exe)?|wsl(?:\.exe)?)(?=\s|$)",
    re.I,
)
POWERSHELL_SYNTAX = re.compile(
    r"\$(?:env:|global:|script:|local:|LASTEXITCODE\b|\?|_)|"
    r"\b(?:Get|Set|New|Remove|Test|Write|Select|Where|ForEach|Invoke|Start|Stop)-"
    r"[A-Za-z][A-Za-z0-9]*\b|-(?:LiteralPath|ErrorAction)\b|"
    r"\[(?:System\.|IO\.|Text\.)|::|@\{|@\(",
    re.I,
)
UNSAFE_AUTO_ROUTE = re.compile(
    r"(?:^|[;\n|&()]|\b(?:then|do|else|call)\s+)\s*(?:sudo\s+)?(?:"
    r"rm|del|erase|rd|rmdir|format|diskpart|"
    r"mkfs(?:\.[A-Za-z0-9]+)?|dd|shutdown|reboot"
    r")\b",
    re.I | re.M,
)
BASH_SIGNATURES = (
    ("heredoc", BASH_HEREDOC_START),
    ("shebang", re.compile(r"^\s*#!\s*/(?:usr/bin/env\s+)?(?:ba|z|k)?sh\b", re.I)),
    ("source", re.compile(r"(?:^|[;\n])\s*source\s+[^\s;]+", re.I)),
    ("export", re.compile(r"(?:^|[;\n])\s*export\s+[A-Za-z_][A-Za-z0-9_]*=", re.I)),
    ("set-options", re.compile(r"(?:^|[;\n])\s*set\s+-[a-zA-Z]*[euxo][a-zA-Z]*\b")),
    ("double-brackets", re.compile(r"\[\[.*?\]\]", re.S)),
    ("parameter-expansion", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::[-+=?])")),
    ("control-flow", re.compile(r"(?:^|\n)\s*(?:if\b.*;\s*then|for\b.*;\s*do|while\b.*;\s*do|fi\b|done\b)", re.I)),
    ("unix-device", re.compile(r"/dev/(?:null|zero|stdin|stdout|stderr|tty)\b", re.I)),
)
CMD_SIGNATURES = (
    ("echo-off", re.compile(r"^\s*@echo\s+off\b", re.I)),
    ("setlocal", re.compile(r"(?:^|[&\n])\s*(?:setlocal|endlocal)\b", re.I)),
    ("set-variable", re.compile(r"(?:^|[&\n])\s*set\s+\"?[A-Za-z_][A-Za-z0-9_]*=", re.I)),
    (
        "percent-variable",
        re.compile(
            r"%(?:[A-Za-z_][A-Za-z0-9_]*%|[0-9](?![0-9A-Fa-f])|\*|"
            r"~[A-Za-z0-9$:_-]*[0-9A-Za-z])"
        ),
    ),
    (
        "for-variable",
        re.compile(
            r"(?:^|[&\n])\s*for(?:\s+/[A-Za-z]+)*\s+%{1,2}[A-Za-z]\b",
            re.I,
        ),
    ),
    ("if-errorlevel", re.compile(r"(?:^|[&\n])\s*if\s+(?:not\s+)?errorlevel\b", re.I)),
    ("for-f", re.compile(r"(?:^|[&\n])\s*for\s+/f\b", re.I)),
    ("label", re.compile(r"(?:^|\n)\s*:[A-Za-z_][A-Za-z0-9_.-]*\s*$", re.M)),
)
RFC1918 = re.compile(
    r"(://127\.0\.0\.1|://localhost\b|://10\.|://192\.168\.|"
    r"://172\.(1[6-9]|2\d|3[0-1])\.)",
    re.I,
)
COMMAND_PREFIX = r"(?P<prefix>(?:^|[;\n|{}()])\s*(?:&\s*)?)"

DEFAULTS = {
    "gitBash": None,
    "powerShell7": None,
    "commandPrompt": None,
    "internalHostPatterns": [],
    "useNoProxyEnv": True,
    "utf8Prefix": True,
    "wrapHeredoc": True,
    "wrapAndAnd": True,
    "autoRouteBash": True,
    "autoRouteCmd": True,
    "rfc1918NoProxy": True,
}


@dataclass(frozen=True)
class RouteDecision:
    shell: str
    reason: str


@dataclass(frozen=True)
class CommandPlan:
    shell: str
    reason: str
    command: str
    executable: Path | None
    changes: tuple[str, ...] = ()


def load_config() -> dict:
    merged = dict(DEFAULTS)
    env_path = os.environ.get("AGENT_WINDOWS_SHELL_CONFIG")
    candidates = [
        KIT_ROOT / "config.json",
        Path.home() / ".agent-windows-shell.json",
    ]
    if env_path:
        candidates.append(Path(env_path))
    for path in candidates:
        try:
            if path.is_file():
                # utf-8-sig reads both BOM and BOM-less files. Older installers
                # wrote a BOM from Windows PowerShell 5.1.
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    merged.update(data)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    return merged


def _first_real_file(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def find_git_bash(cfg: dict) -> Path | None:
    paths: list[Path] = []
    if cfg.get("gitBash"):
        paths.append(Path(os.path.expandvars(str(cfg["gitBash"]))))
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    paths.extend(
        [
            Path(pf) / "Git" / "bin" / "bash.exe",
            Path(pf) / "Git" / "usr" / "bin" / "bash.exe",
            Path(pf86) / "Git" / "bin" / "bash.exe",
            Path(local) / "Programs" / "Git" / "bin" / "bash.exe",
        ]
    )
    return _first_real_file(paths)


def find_pwsh(cfg: dict) -> Path | None:
    paths: list[Path] = []
    if cfg.get("powerShell7"):
        paths.append(Path(os.path.expandvars(str(cfg["powerShell7"]))))
    discovered = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if discovered:
        paths.append(Path(discovered))
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    paths.extend(
        [
            Path(pf) / "PowerShell" / "7" / "pwsh.exe",
            Path(local) / "Microsoft" / "WindowsApps" / "pwsh.exe",
        ]
    )
    return _first_real_file(paths)


def find_cmd(cfg: dict) -> Path | None:
    paths: list[Path] = []
    if cfg.get("commandPrompt"):
        paths.append(Path(os.path.expandvars(str(cfg["commandPrompt"]))))
    discovered = shutil.which("cmd.exe") or shutil.which("cmd")
    if discovered:
        paths.append(Path(discovered))
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    paths.append(Path(system_root) / "System32" / "cmd.exe")
    return _first_real_file(paths)


def find_windows_powershell() -> Path | None:
    paths: list[Path] = []
    discovered = shutil.which("powershell.exe") or shutil.which("powershell")
    if discovered:
        paths.append(Path(discovered))
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    paths.append(
        Path(system_root)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return _first_real_file(paths)


def classify_command(
    command: str,
    cfg: dict,
    *,
    bash: Path | None,
    cmd: Path | None,
) -> RouteDecision:
    """Choose a shell only when syntax identifies it with high confidence."""
    masked = code_mask(command)
    explicit = EXPLICIT_SHELL.match(masked)
    if explicit:
        return RouteDecision("host", f"explicit-shell:{explicit.group('shell').lower()}")
    if POWERSHELL_SYNTAX.search(masked):
        return RouteDecision("host", "powershell-syntax")
    if UNSAFE_AUTO_ROUTE.search(masked):
        return RouteDecision("host", "unsafe-command")

    for reason, pattern in BASH_SIGNATURES:
        if reason == "heredoc" and not cfg.get("wrapHeredoc"):
            continue
        if reason != "heredoc" and not cfg.get("autoRouteBash"):
            continue
        candidate = command if reason == "shebang" else masked
        if pattern.search(candidate):
            if bash:
                return RouteDecision("git-bash", f"bash-syntax:{reason}")
            return RouteDecision("host", f"bash-unavailable:{reason}")

    if cfg.get("autoRouteCmd"):
        for reason, pattern in CMD_SIGNATURES:
            if pattern.search(masked):
                if cmd:
                    return RouteDecision("cmd", f"cmd-syntax:{reason}")
                return RouteDecision("host", f"cmd-unavailable:{reason}")
    return RouteDecision("host", "native-or-powershell")


def no_proxy_patterns(cfg: dict) -> list[re.Pattern[str]]:
    patterns: list[str] = []
    if cfg.get("useNoProxyEnv"):
        raw = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
        for part in raw.split(","):
            part = part.strip().strip(".")
            if not part or "/" in part or part.lower() in {"localhost", "::1"}:
                continue
            patterns.append(re.escape(part))
    patterns.extend(str(item) for item in (cfg.get("internalHostPatterns") or []))
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.I))
        except re.error:
            continue
    return compiled


def code_mask(text: str) -> str:
    """Return same-length text with strings and comments replaced by spaces."""
    chars = list(text)
    mask = list(text)
    state = "code"
    i = 0
    while i < len(chars):
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if state == "code":
            if c == "<" and nxt == "#":
                mask[i] = mask[i + 1] = " "
                state = "block-comment"
                i += 2
                continue
            if c == "#":
                mask[i] = " "
                state = "line-comment"
            elif c == "'":
                mask[i] = " "
                state = "single"
            elif c == '"':
                mask[i] = " "
                state = "double"
        elif state == "line-comment":
            if c == "\n":
                state = "code"
            else:
                mask[i] = " "
        elif state == "block-comment":
            mask[i] = " "
            if c == "#" and nxt == ">":
                mask[i + 1] = " "
                state = "code"
                i += 2
                continue
        elif state == "single":
            mask[i] = " "
            if c == "'" and nxt == "'":
                mask[i + 1] = " "
                i += 2
                continue
            if c == "'":
                state = "code"
        elif state == "double":
            mask[i] = " "
            if c == "`" and nxt:
                mask[i + 1] = " "
                i += 2
                continue
            if c == '"':
                state = "code"
        i += 1
    return "".join(mask)


def _apply_masked_substitutions(
    text: str,
    pattern: re.Pattern[str],
    replacement,
) -> str:
    mask = code_mask(text)
    edits: list[tuple[int, int, str]] = []
    for match in pattern.finditer(mask):
        start, end = match.span("target")
        value = replacement(match, text[start:end])
        if value is not None:
            edits.append((start, end, value))
    for start, end, value in reversed(edits):
        text = text[:start] + value + text[end:]
    return text


def rewrite_safe_aliases(cmd: str) -> str:
    """Rewrite only command-position aliases; never activate destructive rm."""
    flags = re.I | re.M
    cmd = _apply_masked_substitutions(
        cmd,
        re.compile(COMMAND_PREFIX + r"(?P<target>curl)(?=\s|$)", flags),
        lambda _match, _old: "curl.exe",
    )
    cmd = _apply_masked_substitutions(
        cmd,
        re.compile(
            COMMAND_PREFIX
            + r"(?P<target>ls\s+-(?:la|al|lA|Al|aL|La))"
            + r"(?=\s*(?:$|[;\n|)}]))",
            flags,
        ),
        lambda _match, _old: "Get-ChildItem -Force",
    )

    def which_replacement(match: re.Match[str], old: str) -> str:
        argument = old.split(None, 1)[1]
        return f"(Get-Command {argument} -ErrorAction SilentlyContinue).Source"

    cmd = _apply_masked_substitutions(
        cmd,
        re.compile(
            COMMAND_PREFIX
            + r"(?P<target>which\s+[A-Za-z0-9_.-]+)(?=\s*(?:$|[;\n|)}]))",
            flags,
        ),
        which_replacement,
    )

    def touch_replacement(match: re.Match[str], old: str) -> str | None:
        argument = old.split(None, 1)[1]
        if argument.startswith("-"):
            return None
        return (
            "New-Item -ItemType File -Force -LiteralPath "
            f"{argument} | Out-Null"
        )

    cmd = _apply_masked_substitutions(
        cmd,
        re.compile(
            COMMAND_PREFIX
            + r"(?P<target>touch\s+[^\s;|&{}()]+)(?=\s*(?:$|[;\n|)}]))",
            flags,
        ),
        touch_replacement,
    )
    cmd = _apply_masked_substitutions(
        cmd,
        re.compile(COMMAND_PREFIX + r"(?P<target>cd\s+/d)(?=\s)", flags),
        lambda _match, _old: "Set-Location",
    )
    return cmd


def needs_direct(cmd: str, cfg: dict, host_pats: list[re.Pattern[str]]) -> bool:
    if cfg.get("rfc1918NoProxy") and RFC1918.search(cmd):
        return True
    return any(pattern.search(cmd) for pattern in host_pats)


def inject_noproxy(cmd: str) -> str:
    if re.search(r"(?i)(?:^|\s)-NoProxy(?:\s|$)", code_mask(cmd)):
        return cmd
    pattern = re.compile(
        COMMAND_PREFIX + r"(?P<target>Invoke-RestMethod|Invoke-WebRequest|irm|iwr)(?=\s)",
        re.I | re.M,
    )
    return _apply_masked_substitutions(
        cmd, pattern, lambda _match, old: old + " -NoProxy"
    )


def tmp_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "agent-windows-shell" / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_temp_script(suffix: str, body: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="agent-", suffix=suffix, dir=tmp_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _wrapper(executable: Path, args: str, script: Path, env_prefix: str = "") -> str:
    script_literal = _ps_literal(script)
    return (
        env_prefix
        + "$agentShellExit=0; try { "
        + f"& {_ps_literal(executable)} {args} {script_literal}; "
        + "$agentShellExit=$LASTEXITCODE "
        + "} finally { "
        + f"Remove-Item -LiteralPath {script_literal} -Force "
        + "-ErrorAction SilentlyContinue }; "
        + "if ($agentShellExit -ne 0) { exit $agentShellExit }"
    )


def wrap_git_bash(cmd: str, bash: Path) -> str:
    body = git_bash_body(cmd)
    script = _write_temp_script(".sh", body)
    return _wrapper(
        bash,
        "--noprofile --norc",
        script,
        "$env:MSYS_NO_PATHCONV='1'; ",
    )


def git_bash_body(cmd: str) -> str:
    return (
        "export LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUTF8=1 "
        "PYTHONIOENCODING=utf-8 MSYS_NO_PATHCONV=1\n"
        + cmd
        + "\n"
    )


def powershell_exit_body(cmd: str) -> str:
    return (
        "$global:LASTEXITCODE = 0\n"
        + cmd
        + "\n"
        + "$agentShellSucceeded = $?\n"
        + "$agentShellNativeExit = $LASTEXITCODE\n"
        + "if (-not $agentShellSucceeded) {\n"
        + "    if ($null -ne $agentShellNativeExit -and "
        + "$agentShellNativeExit -ne 0) { exit $agentShellNativeExit }\n"
        + "    exit 1\n"
        + "}\n"
    )


def wrap_pwsh(cmd: str, pwsh: Path, cfg: dict) -> str:
    body = powershell_exit_body(prepend_utf8(cmd, cfg))
    script = _write_temp_script(".ps1", body)
    return _wrapper(pwsh, "-NoLogo -NoProfile -File", script)


def wrap_cmd(command: str, cmd: Path) -> str:
    body = cmd_body(command)
    script = _write_temp_script(".cmd", body)
    return _wrapper(cmd, "/d /s /c", script)


def cmd_body(command: str) -> str:
    return "@echo off\r\nchcp 65001 >nul\r\n" + command + "\r\nexit /b %errorlevel%\r\n"


def prepend_utf8(cmd: str, cfg: dict) -> str:
    if not cfg.get("utf8Prefix"):
        return cmd
    if "[Console]::OutputEncoding" in cmd or cmd.lstrip().startswith("$utf8="):
        return cmd
    return UTF8_PREFIX + cmd


def plan_command(cmd: str, cfg: dict | None = None) -> CommandPlan:
    """Build one side-effect-free execution plan shared by hooks and the CLI."""
    if not cmd or not cmd.strip():
        return CommandPlan("host", "empty-command", cmd, None)
    cfg = cfg or load_config()
    bash = find_git_bash(cfg)
    pwsh = find_pwsh(cfg)
    command_prompt = find_cmd(cfg)
    decision = classify_command(cmd, cfg, bash=bash, cmd=command_prompt)
    if decision.shell == "git-bash":
        return CommandPlan(
            "git-bash",
            decision.reason,
            cmd,
            bash,
        )
    if decision.shell == "cmd":
        return CommandPlan(
            "cmd",
            decision.reason,
            cmd,
            command_prompt,
        )

    changes: list[str] = []
    updated = rewrite_safe_aliases(cmd)
    if updated != cmd:
        changes.append("safe-aliases")
    cmd = updated
    host_pats = no_proxy_patterns(cfg)
    masked_cmd = code_mask(cmd)
    pipeline_chain = bool(
        cfg.get("wrapAndAnd") and ("&&" in masked_cmd or "||" in masked_cmd)
    )
    no_proxy = False
    if pwsh and needs_direct(cmd, cfg, host_pats):
        updated = inject_noproxy(cmd)
        no_proxy = updated != cmd
        if no_proxy:
            changes.append("no-proxy")
        cmd = updated

    reasons: list[str] = []
    if pipeline_chain:
        reasons.append("pipeline-chain")
    if no_proxy:
        reasons.append("no-proxy")
    if reasons and pwsh:
        return CommandPlan(
            "powershell7",
            "powershell7:" + "+".join(reasons),
            cmd,
            pwsh,
            tuple(changes),
        )
    if pipeline_chain and not pwsh:
        return CommandPlan(
            "host",
            "powershell7-unavailable:pipeline-chain",
            cmd,
            None,
            tuple(changes),
        )
    return CommandPlan(
        "host",
        decision.reason,
        cmd,
        None,
        tuple(changes),
    )


def explain_command(cmd: str, cfg: dict | None = None) -> dict:
    plan = plan_command(cmd, cfg)
    return {
        "shell": plan.shell,
        "reason": plan.reason,
        "executable": str(plan.executable) if plan.executable else None,
        "commandChanged": plan.command != cmd,
        "changes": list(plan.changes),
    }


def rewrite_command(cmd: str, cfg: dict | None = None) -> str:
    if not cmd or not cmd.strip():
        return cmd
    cfg = cfg or load_config()
    plan = plan_command(cmd, cfg)
    if plan.shell == "git-bash":
        return wrap_git_bash(plan.command, plan.executable)
    if plan.shell == "cmd":
        return wrap_cmd(plan.command, plan.executable)
    if plan.shell == "powershell7":
        return wrap_pwsh(plan.command, plan.executable, cfg)
    return prepend_utf8(plan.command, cfg)


def run_command(cmd: str, cfg: dict | None = None) -> int:
    """Execute through the same router when an agent has no pre-tool hook."""
    cfg = cfg or load_config()
    plan = plan_command(cmd, cfg)
    env = None

    if plan.shell == "git-bash":
        script = _write_temp_script(".sh", git_bash_body(plan.command))
        argv = [
            str(plan.executable),
            "--noprofile",
            "--norc",
            str(script),
        ]
        env = os.environ.copy()
        env.update(
            {
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "MSYS_NO_PATHCONV": "1",
            }
        )
    elif plan.shell == "cmd":
        script = _write_temp_script(".cmd", cmd_body(plan.command))
        argv = [str(plan.executable), "/d", "/s", "/c", str(script)]
    else:
        host = plan.executable or find_pwsh(cfg) or find_windows_powershell()
        if not host:
            print(
                "agent-windows-shell: PowerShell host not found",
                file=sys.stderr,
            )
            return 127
        script = _write_temp_script(
            ".ps1",
            powershell_exit_body(prepend_utf8(plan.command, cfg)),
        )
        argv = [
            str(host),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
        ]

    try:
        completed = subprocess.run(argv, check=False, env=env)
        return completed.returncode
    finally:
        script.unlink(missing_ok=True)


def extract_command(payload: dict) -> tuple[dict | None, str]:
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    if tool and tool.lower().split(".")[-1] not in {"shell", "bash", "powershell"}:
        return None, ""
    tool_input = payload.get("tool_input") or payload.get("toolInput")
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except json.JSONDecodeError:
            tool_input = None
    if isinstance(tool_input, dict):
        for key in ("command", "cmd"):
            if tool_input.get(key):
                return tool_input, str(tool_input[key])
    if not tool and payload.get("command"):
        return None, str(payload["command"])
    return (tool_input if isinstance(tool_input, dict) else None), ""


def is_claude_payload(payload: dict) -> bool:
    # Cursor payloads also carry transcript_path; only Claude Code sends the
    # PascalCase event name.
    return str(payload.get("hook_event_name") or "") == "PreToolUse"


def hook_response(
    original_payload: dict,
    updated: dict | None = None,
    new_cmd: str | None = None,
) -> dict:
    if updated is not None and new_cmd:
        tool_input = dict(updated)
        key = "command" if "command" in updated or "cmd" not in updated else "cmd"
        tool_input[key] = new_cmd
        if is_claude_payload(original_payload):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": tool_input,
                }
            }
        return {"permission": "allow", "updated_input": tool_input}
    if is_claude_payload(original_payload):
        return {}
    return {"permission": "allow"}


def hook_main() -> None:
    # Cursor writes UTF-8 with a BOM; the default stdin decoder uses the ANSI
    # code page unless PYTHONUTF8 is set for the hook process.
    raw = sys.stdin.buffer.read().decode("utf-8-sig")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        json.dump({"permission": "allow"}, sys.stdout)
        return
    tool_input, command = extract_command(payload)
    if not command:
        json.dump(hook_response(payload), sys.stdout)
        return
    new_cmd = rewrite_command(command)
    if new_cmd == command:
        json.dump(hook_response(payload), sys.stdout)
        return
    json.dump(hook_response(payload, tool_input or {}, new_cmd), sys.stdout)


def run_tests() -> None:
    import unittest

    start = str(KIT_ROOT / "tests")
    suite = unittest.defaultTestLoader.discover(start, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


def cli_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Route Windows agent commands to a suitable shell."
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--cmd", metavar="COMMAND", help="print the hook rewrite")
    actions.add_argument(
        "--explain",
        metavar="COMMAND",
        help="print the routing decision as JSON without executing",
    )
    actions.add_argument(
        "--run",
        metavar="COMMAND",
        help="execute using the same routing logic as the hook",
    )
    actions.add_argument("--test", action="store_true", help="run unit tests")
    args = parser.parse_args(argv)

    if args.test:
        run_tests()
        return 0
    if args.cmd is not None:
        print(rewrite_command(args.cmd))
        return 0
    if args.explain is not None:
        json.dump(
            explain_command(args.explain),
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        print()
        return 0
    return run_command(args.run)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(cli_main(sys.argv[1:]))
    else:
        try:
            hook_main()
        except Exception:
            json.dump({"permission": "allow"}, sys.stdout)
            sys.exit(0)
