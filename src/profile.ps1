# Requires PowerShell 7+ for utf8NoBOM and $PSNativeCommandUseErrorActionPreference.
# Agents that spawn -NoProfile will not load this; the rewrite hook covers those.
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
chcp 65001 | Out-Null

$ProgressPreference = 'SilentlyContinue'

if ($PSVersionTable.PSVersion.Major -ge 6) {
    $enc = 'utf8NoBOM'
} else {
    $enc = 'UTF8'
}
$PSDefaultParameterValues['Out-File:Encoding'] = $enc
$PSDefaultParameterValues['Set-Content:Encoding'] = $enc
$PSDefaultParameterValues['Add-Content:Encoding'] = $enc
$PSDefaultParameterValues['Get-Content:Encoding'] = $enc

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $true
}
