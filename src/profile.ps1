# UTF-8 console, Python defaults and a PATH refresh. Works in Windows PowerShell 5.1 and PowerShell 7+.
# The block that install.ps1 adds to your profile dot-sources this file. A host
# started with -NoProfile skips it; set "utf8Prefix": true in
# ~/.agent-windows-shell.json to let the hook add the encoding setup per command.
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$ProgressPreference = 'SilentlyContinue'

# PowerShell 7 already reads and writes BOM-less UTF-8. 5.1 defaults to ANSI or UTF-16.
if ($PSVersionTable.PSVersion.Major -lt 6) {
    foreach ($cmdlet in 'Out-File', 'Set-Content', 'Add-Content', 'Get-Content') {
        $PSDefaultParameterValues["${cmdlet}:Encoding"] = 'UTF8'
    }
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# An editor started by a long-running launcher (or left open across installs)
# hands its shells the PATH from launch time, so tools installed later are
# missing. Append registry PATH entries this session lacks; existing order is
# kept. Cursor restores each chat's saved environment after the profile runs,
# so only chats started after an install see the new entries.
& {
    $have = @{}
    foreach ($dir in $env:Path -split ';') { if ($dir) { $have[$dir.TrimEnd('\')] = $true } }
    $missing = foreach ($scope in 'Machine', 'User') {
        foreach ($dir in [Environment]::GetEnvironmentVariable('Path', $scope) -split ';') {
            $dir = [Environment]::ExpandEnvironmentVariables($dir).TrimEnd('\')
            if ($dir -and -not $have[$dir]) { $have[$dir] = $true; $dir }
        }
    }
    if ($missing) { $env:Path = $env:Path.TrimEnd(';') + ';' + ($missing -join ';') }
}
