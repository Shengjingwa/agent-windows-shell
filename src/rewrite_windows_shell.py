#!/usr/bin/env python3
"""Conservatively adapt agent shell commands on Windows.

Hook mode reads Cursor or Claude Code PreToolUse JSON from stdin. The CLI
modes are for agents that cannot install a hook:

  python rewrite_windows_shell.py --cmd "ls -la"
  python rewrite_windows_shell.py --explain "export NAME=agent"
  python rewrite_windows_shell.py --run "export NAME=agent; printf '%s\\n' \"$NAME\""
  python rewrite_windows_shell.py --test

Configuration is merged in this order: kit/config.json, the user's
%USERPROFILE%\\.agent-windows-shell.json, then AGENT_WINDOWS_SHELL_CONFIG.
Hook failures are fail-open.

The hook runs before every Shell call, so the common path must stay cheap:
only json, os, re and sys load at start-up; everything else is imported when
a command actually needs it.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import namedtuple

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Lean UTF-8 setup for hosts that start without the kit profile. Console
# input encoding and chcp are deliberately absent: they cost 10-150 ms per
# start and do not affect how PowerShell decodes native output.
UTF8_PREFIX = (
    "$utf8=[System.Text.UTF8Encoding]::new($false); "
    "[Console]::OutputEncoding=$utf8; $OutputEncoding=$utf8; "
    "$ProgressPreference='SilentlyContinue'; "
)

DEFAULTS = {
    "gitBash": None,
    "powerShell7": None,
    "commandPrompt": None,
    "internalHostPatterns": [],
    "useNoProxyEnv": True,
    "utf8Prefix": False,
    "wrapHeredoc": True,
    "wrapAndAnd": True,
    "autoRouteBash": True,
    "autoRouteCmd": True,
    "rfc1918NoProxy": True,
    "flushImplicitOutput": True,
}

RouteDecision = namedtuple("RouteDecision", "shell reason")
CommandPlan = namedtuple(
    "CommandPlan",
    "shell reason command executable changes",
    defaults=((),),
)

COMMAND_PREFIX = r"(?P<prefix>(?:^|[;\n|{}()])\s*(?:&\s*)?)"
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
# PowerShell has no `<<` operator, so `<<` outside a string is bash. A quoted
# delimiter (`<<'EOF'`) is blanked by code_mask, so match the operator alone.
HEREDOC_OPENER = re.compile(r"<<")
RFC1918 = re.compile(
    r"(://127\.0\.0\.1|://localhost\b|://10\.|://192\.168\.|"
    r"://172\.(1[6-9]|2\d|3[0-1])\.)",
    re.I,
)


def _lazy(builder):
    """Build a value on first use. Most commands never need the big tables."""
    cache: list = []

    def get():
        if not cache:
            cache.append(builder())
        return cache[0]

    return get


def _build_unsafe():
    return re.compile(
        r"(?:^|[;\n|&()]|\b(?:then|do|else|call)\s+)\s*(?:sudo\s+)?(?:"
        r"rm|del|erase|rd|rmdir|format|diskpart|"
        r"mkfs(?:\.[A-Za-z0-9]+)?|dd|shutdown|reboot"
        r")\b",
        re.I | re.M,
    )


def _build_bash_signatures():
    return (
        ("shebang", re.compile(r"^\s*#!\s*/(?:usr/bin/env\s+)?(?:ba|z|k)?sh\b", re.I)),
        ("source", re.compile(r"(?:^|[;\n])\s*source\s+[^\s;]+", re.I)),
        ("export", re.compile(r"(?:^|[;\n])\s*export\s+[A-Za-z_][A-Za-z0-9_]*=", re.I)),
        ("set-options", re.compile(r"(?:^|[;\n])\s*set\s+-[a-zA-Z]*[euxo][a-zA-Z]*\b")),
        ("double-brackets", re.compile(r"\[\[.*?\]\]", re.S)),
        ("parameter-expansion", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::[-+=?])")),
        (
            "control-flow",
            re.compile(
                r"(?:^|\n)\s*(?:if\b.*;\s*then|for\b.*;\s*do|while\b.*;\s*do|fi\b|done\b)",
                re.I,
            ),
        ),
        ("unix-device", re.compile(r"/dev/(?:null|zero|stdin|stdout|stderr|tty)\b", re.I)),
    )


def _build_cmd_signatures():
    return (
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
            re.compile(r"(?:^|[&\n])\s*for(?:\s+/[A-Za-z]+)*\s+%{1,2}[A-Za-z]\b", re.I),
        ),
        ("if-errorlevel", re.compile(r"(?:^|[&\n])\s*if\s+(?:not\s+)?errorlevel\b", re.I)),
        ("for-f", re.compile(r"(?:^|[&\n])\s*for\s+/f\b", re.I)),
        ("label", re.compile(r"(?:^|\n)\s*:[A-Za-z_][A-Za-z0-9_.-]*\s*$", re.M)),
    )


unsafe_auto_route = _lazy(_build_unsafe)
bash_signatures = _lazy(_build_bash_signatures)
cmd_signatures = _lazy(_build_cmd_signatures)


# --------------------------------------------------------------------------
# Configuration and executable discovery
# --------------------------------------------------------------------------

def load_config() -> dict:
    merged = dict(DEFAULTS)
    candidates = [
        os.path.join(KIT_ROOT, "config.json"),
        os.path.join(os.path.expanduser("~"), ".agent-windows-shell.json"),
    ]
    env_path = os.environ.get("AGENT_WINDOWS_SHELL_CONFIG")
    if env_path:
        candidates.append(env_path)
    for path in candidates:
        try:
            if os.path.isfile(path):
                # utf-8-sig reads BOM and BOM-less files. Windows PowerShell
                # 5.1 writes a BOM.
                with open(path, encoding="utf-8-sig") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    merged.update(data)
        except (OSError, UnicodeError, ValueError):
            continue
    return merged


def _first_real_file(paths):
    """First existing, non-empty file. The WindowsApps bash.exe stub is 0 bytes."""
    from pathlib import Path

    for candidate in paths:
        path = Path(candidate)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def _which(*names):
    import shutil

    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_git_bash(cfg: dict):
    paths = []
    if cfg.get("gitBash"):
        paths.append(os.path.expandvars(str(cfg["gitBash"])))
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    paths += [
        os.path.join(pf, "Git", "bin", "bash.exe"),
        os.path.join(pf, "Git", "usr", "bin", "bash.exe"),
        os.path.join(pf86, "Git", "bin", "bash.exe"),
        os.path.join(local, "Programs", "Git", "bin", "bash.exe"),
    ]
    return _first_real_file(paths)


def find_pwsh(cfg: dict):
    paths = []
    if cfg.get("powerShell7"):
        paths.append(os.path.expandvars(str(cfg["powerShell7"])))
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    # Fixed locations first: they cost one stat each, while a PATH search
    # walks every PATH entry.
    paths.append(os.path.join(pf, "PowerShell", "7", "pwsh.exe"))
    found = _first_real_file(paths)
    if found:
        return found
    discovered = _which("pwsh.exe", "pwsh")
    if discovered:
        paths.append(discovered)
    paths.append(os.path.join(local, "Microsoft", "WindowsApps", "pwsh.exe"))
    return _first_real_file(paths)


def find_cmd(cfg: dict):
    paths = []
    if cfg.get("commandPrompt"):
        paths.append(os.path.expandvars(str(cfg["commandPrompt"])))
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    paths.append(os.path.join(system_root, "System32", "cmd.exe"))
    found = _first_real_file(paths)
    if found:
        return found
    discovered = _which("cmd.exe", "cmd")
    return _first_real_file([discovered]) if discovered else None


def find_windows_powershell():
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    paths = [
        os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    ]
    discovered = _which("powershell.exe", "powershell")
    if discovered:
        paths.append(discovered)
    return _first_real_file(paths)


def parent_process_name() -> str | None:
    """Lower-case image name of the process that started this one (Windows)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        entry_pointer = ctypes.POINTER(ProcessEntry)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, entry_pointer]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, entry_pointer]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return None
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(ProcessEntry)
            names = {}
            parent = None
            me = os.getpid()
            more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                names[entry.th32ProcessID] = entry.szExeFile.lower()
                if entry.th32ProcessID == me:
                    parent = entry.th32ParentProcessID
                more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return names.get(parent)
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        return None


def host_is_powershell7() -> bool | None:
    """True when the PowerShell that launched us is 7+, False for 5.1, else None.

    Cursor starts hooks and Shell commands with the same PowerShell. None means
    the parent is not PowerShell (a wrapper, an IDE); callers then keep the
    conservative behaviour and start their own pwsh 7.
    """
    name = parent_process_name()
    if name == "pwsh.exe":
        return True
    if name == "powershell.exe":
        return False
    return None


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def code_mask(text: str) -> str:
    """Return same-length text with strings and comments replaced by spaces.

    Understands PowerShell here-strings and keeps `$(...)` subexpressions
    inside double quotes visible, because they hold code (this is how an
    agent embeds a bash `$(cat <<'EOF' ...)` message inside a quoted string).
    """
    mask = list(text)
    n = len(text)
    # stack of closers waiting for a `$(` to end: depth counters per nesting.
    parens: list[int] = []
    state = "code"
    i = 0

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if mask[k] not in "\r\n":
                mask[k] = " "

    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "`" and nxt:
                i += 2
                continue
            if c == "<" and nxt == "#":
                end = text.find("#>", i + 2)
                end = n if end < 0 else end + 2
                blank(i, end)
                i = end
                continue
            if c == "#" and (i == 0 or text[i - 1] in " \t\r\n;|&(){},"):
                end = text.find("\n", i)
                end = n if end < 0 else end
                blank(i, end)
                i = end
                continue
            if c == "@" and nxt in "'\"":
                j = i + 2
                while j < n and text[j] in " \t":
                    j += 1
                if j < n and text[j] in "\r\n":
                    terminator = "\n" + nxt + "@"
                    end = text.find(terminator, j)
                    end = n if end < 0 else end + len(terminator)
                    blank(i, end)
                    i = end
                    continue
            if c == "'":
                state = "single"
                mask[i] = " "
            elif c == '"':
                state = "double"
                mask[i] = " "
            elif parens:
                if c == "(":
                    parens[-1] += 1
                elif c == ")":
                    parens[-1] -= 1
                    if parens[-1] == 0:
                        parens.pop()
                        state = "double"
        elif state == "single":
            mask[i] = " " if c not in "\r\n" else c
            if c == "'" and nxt == "'":
                mask[i + 1] = " "
                i += 2
                continue
            if c == "'":
                state = "code"
        else:  # double
            if c == "`" and nxt:
                mask[i] = " "
                if nxt not in "\r\n":
                    mask[i + 1] = " "
                i += 2
                continue
            if c == "$" and nxt == "(":
                mask[i] = mask[i + 1] = " "
                parens.append(1)
                state = "code"
                i += 2
                continue
            if c == '"':
                mask[i] = " "
                state = "code"
            elif c not in "\r\n":
                mask[i] = " "
        i += 1
    return "".join(mask)


_POWERSHELL_STATEMENT = re.compile(
    r"(?m)^\s*\$[A-Za-z_]\w*\s*=(?!=)"  # `$x = 1` is not valid bash
    r"|(?:^|[;\n|(])\s*&\s*[$\"'(]"  # `& $exe`, `& 'C:\\tool.exe'`
)
_DRIVE_PATH = re.compile(r"\b[A-Za-z]:\\")


def _head_is_powershell(raw_head: str, masked_head: str) -> bool:
    """Does the text before a heredoc opener look like PowerShell?

    The raw text counts for $env: and cmdlet names because a PowerShell
    variable inside a string (`cd "$env:USERPROFILE\\x"`) is still PowerShell.
    """
    return bool(
        POWERSHELL_SYNTAX.search(raw_head)
        or _POWERSHELL_STATEMENT.search(masked_head)
        or _DRIVE_PATH.search(raw_head)
    )


def detect_syntax(command: str, cfg: dict):
    """Classify by syntax alone: (shell, reason) with shell in host|bash|cmd."""
    masked = code_mask(command)
    explicit = EXPLICIT_SHELL.match(masked)
    if explicit:
        return "host", f"explicit-shell:{explicit.group('shell').lower()}"
    opener = HEREDOC_OPENER.search(masked) if cfg.get("wrapHeredoc") else None
    if opener:
        # A heredoc opener outside any string is bash. Judge PowerShell syntax
        # only up to the opener's line: a commit message in the body often
        # mentions Get-Item or $env: and must not win. A PowerShell head
        # (`Set-Location x; & $py - <<'PY'`) stays in the host: PowerShell
        # rejects the whole command at parse time, whereas bash would run the
        # parts it can and fail on the rest.
        line_end = masked.find("\n", opener.end())
        cut = len(masked) if line_end < 0 else line_end
        if not _head_is_powershell(command[:cut], masked[:cut]) and not (
            unsafe_auto_route().search(masked)
        ):
            return "bash", "heredoc"
    if POWERSHELL_SYNTAX.search(masked):
        return "host", "powershell-syntax"
    if unsafe_auto_route().search(masked):
        return "host", "unsafe-command"
    if cfg.get("autoRouteBash"):
        for reason, pattern in bash_signatures():
            candidate = command if reason == "shebang" else masked
            if pattern.search(candidate):
                return "bash", reason
    if cfg.get("autoRouteCmd"):
        for reason, pattern in cmd_signatures():
            if pattern.search(masked):
                return "cmd", reason
    return "host", "native-or-powershell"


def classify_command(command: str, cfg: dict, *, bash, cmd) -> RouteDecision:
    """Choose a shell only when syntax identifies it with high confidence."""
    kind, reason = detect_syntax(command, cfg)
    if kind == "bash":
        if bash:
            return RouteDecision("git-bash", f"bash-syntax:{reason}")
        return RouteDecision("host", f"bash-unavailable:{reason}")
    if kind == "cmd":
        if cmd:
            return RouteDecision("cmd", f"cmd-syntax:{reason}")
        return RouteDecision("host", f"cmd-unavailable:{reason}")
    return RouteDecision("host", reason)


# --------------------------------------------------------------------------
# Rewrites that stay in the PowerShell host
# --------------------------------------------------------------------------

def _apply_masked_substitutions(text: str, pattern, replacement) -> str:
    mask = code_mask(text)
    edits = []
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

    def which_replacement(_match, old: str) -> str:
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

    def touch_replacement(_match, old: str):
        argument = old.split(None, 1)[1]
        if argument.startswith("-"):
            return None
        return f"New-Item -ItemType File -Force -LiteralPath {argument} | Out-Null"

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


def no_proxy_patterns(cfg: dict):
    patterns = []
    if cfg.get("useNoProxyEnv"):
        raw = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
        for part in raw.split(","):
            part = part.strip().strip(".")
            if not part or "/" in part or part.lower() in {"localhost", "::1"}:
                continue
            patterns.append(re.escape(part))
    patterns.extend(str(item) for item in (cfg.get("internalHostPatterns") or []))
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.I))
        except re.error:
            continue
    return compiled


def needs_direct(cmd: str, cfg: dict, host_pats) -> bool:
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
    return _apply_masked_substitutions(cmd, pattern, lambda _match, old: old + " -NoProxy")


# Cursor ends every Shell script with `exit`. PowerShell buffers an
# auto-sized table (for example `... | Select-Object Name`) until its
# pipeline ends, and `exit` discards the buffer along with everything printed
# after it. Running the command in a dot-sourced script block that pipes to
# Out-Host ends that pipeline first. The block keeps the caller's scope, so
# variables, aliases, the working directory and $LASTEXITCODE persist. Native
# output then flows through PowerShell instead of straight to the console:
# about +20 ms for 200 lines, +0.3 s for 3,000, +1.2 s for 20,000.
_STATEMENT = re.compile(r"[;\n]+")
_SELECT = re.compile(r"\|\s*(?:Select-Object|select)\b(?P<args>[^|]*)", re.I)
_SELECT_VALUE_PARAMS = {"-first", "-last", "-skip", "-skiplast", "-index"}
_SELECT_FLAG_PARAMS = {"-unique", "-wait"}
_PSOBJECT = re.compile(r"\[pscustomobject\]\s*@\{|New-Object\s+(?:-TypeName\s+)?PSObject", re.I)
_OBJECT_SINK = re.compile(
    r"\|\s*(?:Format-\w+|Out-\w+|Write-\w+|Set-Content|Add-Content|Export-\w+|"
    r"ConvertTo-\w+|Tee-Object|Select-String|Join-String)\b|"
    r"\|\s*&?\s*\S*\.exe\b|(?<![-<])>",
    re.I,
)
_SCRIPT_LEVEL = re.compile(r"(?im)^\s*(?:using\s+(?:namespace|module|assembly)\b|param\s*\()")
_ASSIGNMENT = re.compile(r"^\s*(?:\[[^\]]+\]\s*)*\$[\w:{}]+(?:\[[^\]]*\])?\s*(?:[-+*/%]|\?\?)?=")


def _select_property_count(args: str) -> int:
    """Properties named on a Select-Object stage; -1 for none, 99 for many."""
    tokens = re.findall(r"@\{.*?\}|[^\s,]+", args)
    count = 0
    skip_next = False
    for token in tokens:
        lower = token.lower()
        if skip_next:
            skip_next = False
            continue
        if lower in {"-expandproperty", "-excludeproperty"}:
            return 99
        if lower in _SELECT_VALUE_PARAMS:
            skip_next = True
            continue
        if lower in _SELECT_FLAG_PARAMS:
            continue
        if lower == "-property":
            continue
        if token.startswith("-"):
            continue
        if token == "*":
            return 99
        count += 1
    return count if count else -1


def _statement_buffers_table(statement: str) -> bool:
    if _ASSIGNMENT.match(statement) or _OBJECT_SINK.search(statement):
        return False
    for match in _SELECT.finditer(statement):
        count = _select_property_count(match.group("args"))
        if 1 <= count <= 4:
            return True
    return bool(_PSOBJECT.search(statement))


def needs_flush_guard(command: str) -> bool:
    """Would PowerShell lose this command's output when the script calls exit?"""
    masked = code_mask(command)
    if "|" not in masked and not _PSOBJECT.search(masked):
        return False
    # `using` and `param` are only legal at the top of a script, not in a block.
    if _SCRIPT_LEVEL.search(masked):
        return False
    # A pipe at the end or start of a line continues the pipeline.
    joined = re.sub(r"\|[ \t]*\r?\n[ \t]*", "| ", masked)
    joined = re.sub(r"\r?\n[ \t]*\|", " |", joined)
    return any(_statement_buffers_table(part) for part in _STATEMENT.split(joined))


def flush_guarded(command: str) -> str:
    return ". {\n" + command + "\n} | Out-Host"


# --------------------------------------------------------------------------
# Wrappers that start another shell
# --------------------------------------------------------------------------

def tmp_dir():
    from pathlib import Path

    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "agent-windows-shell" / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sweep_stale(directory, max_age_seconds: float = 86400.0) -> None:
    """Delete wrapper scripts a cancelled command left behind."""
    import time

    cutoff = time.time() - max_age_seconds
    try:
        for entry in os.scandir(directory):
            if entry.name.startswith("agent-") and entry.is_file():
                if entry.stat().st_mtime < cutoff:
                    os.unlink(entry.path)
    except OSError:
        pass


def _write_temp_script(suffix: str, body: str):
    import tempfile
    from pathlib import Path

    directory = tmp_dir()
    _sweep_stale(directory)
    fd, name = tempfile.mkstemp(prefix="agent-", suffix=suffix, dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def _ps_literal(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _wrapper(executable, args: str, script, env_prefix: str = "") -> str:
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


def git_bash_body(cmd: str) -> str:
    return (
        "export LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUTF8=1 "
        "PYTHONIOENCODING=utf-8 MSYS_NO_PATHCONV=1\n" + cmd + "\n"
    )


def wrap_git_bash(cmd: str, bash) -> str:
    script = _write_temp_script(".sh", git_bash_body(cmd))
    return _wrapper(bash, "--noprofile --norc", script, "$env:MSYS_NO_PATHCONV='1'; ")


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


def prepend_utf8(cmd: str, cfg: dict, *, force: bool = False) -> str:
    """Add UTF-8 setup. Only needed where the kit profile does not run."""
    if not (force or cfg.get("utf8Prefix")):
        return cmd
    if "[Console]::OutputEncoding" in cmd or cmd.lstrip().startswith("$utf8="):
        return cmd
    return UTF8_PREFIX + cmd


def wrap_pwsh(cmd: str, pwsh, cfg: dict) -> str:
    # The child starts with -NoProfile, so it always needs the UTF-8 setup.
    body = powershell_exit_body(prepend_utf8(cmd, cfg, force=True))
    script = _write_temp_script(".ps1", body)
    return _wrapper(pwsh, "-NoLogo -NoProfile -File", script)


def cmd_body(command: str) -> str:
    return "@echo off\r\nchcp 65001 >nul\r\n" + command + "\r\nexit /b %errorlevel%\r\n"


def wrap_cmd(command: str, cmd) -> str:
    script = _write_temp_script(".cmd", cmd_body(command))
    return _wrapper(cmd, "/d /s /c", script)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def plan_command(
    cmd: str, cfg: dict | None = None, *, flush_guard: bool = True
) -> CommandPlan:
    """Build one side-effect-free execution plan shared by hooks and the CLI."""
    if not cmd or not cmd.strip():
        return CommandPlan("host", "empty-command", cmd, None)
    cfg = cfg or load_config()
    kind, reason = detect_syntax(cmd, cfg)
    if kind == "bash":
        bash = find_git_bash(cfg)
        if bash:
            return CommandPlan("git-bash", f"bash-syntax:{reason}", cmd, bash)
        reason = f"bash-unavailable:{reason}"
    elif kind == "cmd":
        command_prompt = find_cmd(cfg)
        if command_prompt:
            return CommandPlan("cmd", f"cmd-syntax:{reason}", cmd, command_prompt)
        reason = f"cmd-unavailable:{reason}"

    changes = []
    updated = rewrite_safe_aliases(cmd)
    if updated != cmd:
        changes.append("safe-aliases")
    cmd = updated

    # `&&`, `||` and `-NoProxy` need PowerShell 7. A pwsh 7 host runs them
    # in place; only a 5.1 or unknown host needs a pwsh 7 child process.
    masked = code_mask(cmd)
    chain = bool(cfg.get("wrapAndAnd") and ("&&" in masked or "||" in masked))
    direct = needs_direct(cmd, cfg, no_proxy_patterns(cfg))
    host7 = host_is_powershell7() if (chain or direct) else None
    pwsh = None
    reasons = []
    if direct and (host7 or find_pwsh(cfg)):
        updated = inject_noproxy(cmd)
        if updated != cmd:
            changes.append("no-proxy")
            cmd = updated
            if host7 is not True:
                reasons.append("no-proxy")
    if chain and host7 is not True:
        reasons.insert(0, "pipeline-chain")
    if reasons:
        pwsh = find_pwsh(cfg)
        if pwsh:
            return CommandPlan(
                "powershell7", "powershell7:" + "+".join(reasons), cmd, pwsh, tuple(changes)
            )
        if "pipeline-chain" in reasons:
            return CommandPlan(
                "host", "powershell7-unavailable:pipeline-chain", cmd, None, tuple(changes)
            )
    if flush_guard and cfg.get("flushImplicitOutput") and needs_flush_guard(cmd):
        cmd = flush_guarded(cmd)
        changes.append("flush-guard")
    return CommandPlan("host", reason, cmd, None, tuple(changes))


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
    import subprocess

    cfg = cfg or load_config()
    # powershell_exit_body reads `$?`, which a flush guard would overwrite.
    plan = plan_command(cmd, cfg, flush_guard=False)
    env = None

    if plan.shell == "git-bash":
        script = _write_temp_script(".sh", git_bash_body(plan.command))
        argv = [str(plan.executable), "--noprofile", "--norc", str(script)]
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
            print("agent-windows-shell: PowerShell host not found", file=sys.stderr)
            return 127
        script = _write_temp_script(
            ".ps1", powershell_exit_body(prepend_utf8(plan.command, cfg, force=True))
        )
        argv = [str(host), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)]

    try:
        return subprocess.run(argv, check=False, env=env).returncode
    finally:
        script.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Hook protocol
# --------------------------------------------------------------------------

def extract_command(payload: dict):
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    if tool and tool.lower().split(".")[-1] not in {"shell", "bash", "powershell"}:
        return None, ""
    tool_input = payload.get("tool_input") or payload.get("toolInput")
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
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


def hook_response(original_payload: dict, updated: dict | None = None, new_cmd: str | None = None) -> dict:
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
    except ValueError:
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

    start = os.path.join(KIT_ROOT, "tests")
    suite = unittest.defaultTestLoader.discover(start, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


def cli_main(argv: list[str]) -> int:
    import argparse

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
        json.dump(explain_command(args.explain), sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    return run_command(args.run)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(cli_main(sys.argv[1:]))
    try:
        hook_main()
    except Exception:
        json.dump({"permission": "allow"}, sys.stdout)
        sys.exit(0)
