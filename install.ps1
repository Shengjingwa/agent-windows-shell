#Requires -Version 5.1
<#
.SYNOPSIS
  Install agent-windows-shell on this Windows user account.

.EXAMPLE
  .\install.ps1
  .\install.ps1 -Cursor -Profile -Skill -Env
  .\install.ps1 -Claude
  .\install.ps1 -DisableCursorAttribution
#>
[CmdletBinding()]
param(
    [switch]$Cursor,
    [switch]$Claude,
    [switch]$Profile,
    [switch]$Skill,
    [switch]$Env,
    [switch]$DisableCursorAttribution,
    [switch]$All
)

$ErrorActionPreference = 'Stop'
$KitRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Value
    )
    $parent = Split-Path -Parent $LiteralPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [IO.File]::WriteAllText(
        $LiteralPath,
        $Value,
        [Text.UTF8Encoding]::new($false)
    )
}

function Read-JsonObject {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        return [pscustomobject]@{}
    }
    $raw = Get-Content -LiteralPath $LiteralPath -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [pscustomobject]@{}
    }
    return $raw | ConvertFrom-Json
}

function Set-ObjectProperty {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        $Value
    )
    $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
}

function Merge-ManagedHook {
    param(
        [Parameter(Mandatory = $true)]$Hooks,
        [Parameter(Mandatory = $true)][string]$Event,
        [Parameter(Mandatory = $true)]$Entry,
        [Parameter(Mandatory = $true)][string]$ScriptPath
    )
    $merged = New-Object System.Collections.ArrayList
    $property = $Hooks.PSObject.Properties[$Event]
    if ($property) {
        foreach ($item in @($property.Value)) {
            if (-not ($item.command -and
                    [string]$item.command -like "*$ScriptPath*")) {
                [void]$merged.Add($item)
            }
        }
    }
    [void]$merged.Add($Entry)
    Set-ObjectProperty -Object $Hooks -Name $Event -Value @($merged)
}

function Find-Python {
    $cmds = @('python.exe', 'py.exe')
    foreach ($name in $cmds) {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c -and $c.Source) { return $c.Source }
    }
    $guess = @(
        "$env:LocalAppData\Programs\Python\Python313\python.exe",
        "$env:LocalAppData\Programs\Python\Python312\python.exe",
        "${env:ProgramFiles}\Python313\python.exe",
        "${env:ProgramFiles}\Python312\python.exe"
    )
    foreach ($p in $guess) {
        if (Test-Path $p) { return $p }
    }
    throw 'python.exe not found. Install Python 3 and add it to PATH.'
}

function Find-Pwsh {
    $c = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if ($c -and $c.Source -and (Test-Path $c.Source)) { return $c.Source }
    $p = "${env:ProgramFiles}\PowerShell\7\pwsh.exe"
    if (Test-Path $p) { return $p }
    return $null
}

function Find-GitBash {
    $guess = @(
        "${env:ProgramFiles}\Git\bin\bash.exe",
        "${env:ProgramFiles}\Git\usr\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "$env:LocalAppData\Programs\Git\bin\bash.exe"
    )
    foreach ($p in $guess) {
        if ((Test-Path $p) -and ((Get-Item $p).Length -gt 0)) { return $p }
    }
    return $null
}

function Find-Cmd {
    $command = Get-Command cmd.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -and (Test-Path $command.Source)) {
        return $command.Source
    }
    $path = Join-Path $env:SystemRoot 'System32\cmd.exe'
    if (Test-Path $path) { return $path }
    return $null
}

if ($All -or -not ($Cursor -or $Claude -or $Profile -or $Skill -or $Env -or $DisableCursorAttribution)) {
    $Cursor = $true
    $Profile = $true
    $Skill = $true
    $Env = $true
}

$python = Find-Python
$pwsh = Find-Pwsh
$gitBash = Find-GitBash
$commandPrompt = Find-Cmd

Write-Host "kit     $KitRoot"
Write-Host "python  $python"
Write-Host "pwsh    $(if ($pwsh) { $pwsh } else { '(not found)' })"
Write-Host "gitbash $(if ($gitBash) { $gitBash } else { '(not found)' })"
Write-Host "cmd     $(if ($commandPrompt) { $commandPrompt } else { '(not found)' })"

$homeCfg = Join-Path $env:USERPROFILE '.agent-windows-shell.json'
$cfg = Read-JsonObject -LiteralPath $homeCfg
$configDefaults = [ordered]@{
    kitRoot              = $KitRoot
    gitBash              = $gitBash
    powerShell7          = $pwsh
    commandPrompt        = $commandPrompt
    internalHostPatterns = @()
    useNoProxyEnv        = $true
    utf8Prefix           = $true
    wrapHeredoc          = $true
    wrapAndAnd           = $true
    autoRouteBash        = $true
    autoRouteCmd         = $true
    rfc1918NoProxy       = $true
}
foreach ($name in $configDefaults.Keys) {
    if ($name -in @('kitRoot', 'gitBash', 'powerShell7', 'commandPrompt') -or
        -not $cfg.PSObject.Properties[$name]) {
        Set-ObjectProperty -Object $cfg -Name $name -Value $configDefaults[$name]
    }
}
Write-Utf8NoBom -LiteralPath $homeCfg -Value ($cfg | ConvertTo-Json -Depth 10)
Write-Host "config  $homeCfg"

& $python (Join-Path $KitRoot 'src\rewrite_windows_shell.py') --test
if ($LASTEXITCODE -ne 0) { throw 'rewrite tests failed' }

if ($Env) {
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'User')
    [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'User')
    [Environment]::SetEnvironmentVariable('AGENT_WINDOWS_SHELL_ROOT', $KitRoot, 'User')
    Write-Host 'env     PYTHONUTF8, PYTHONIOENCODING, AGENT_WINDOWS_SHELL_ROOT (User)'
}

if ($Profile) {
    $profileDir = Join-Path $env:USERPROFILE 'Documents\PowerShell'
    if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Force -Path $profileDir | Out-Null }
    $userProfile = Join-Path $profileDir 'Microsoft.PowerShell_profile.ps1'
    $snippet = @'
# agent-windows-shell (do not edit; managed by install.ps1)
$root = $env:AGENT_WINDOWS_SHELL_ROOT
if (-not $root) {
    $cfg = Join-Path $env:USERPROFILE '.agent-windows-shell.json'
    if (Test-Path -LiteralPath $cfg) {
        $root = (Get-Content -LiteralPath $cfg -Raw -Encoding UTF8 | ConvertFrom-Json).kitRoot
    }
}
if ($root) {
    $kitProfile = Join-Path $root 'src\profile.ps1'
    if (Test-Path -LiteralPath $kitProfile) { . $kitProfile }
}
'@
    $existing = ''
    if (Test-Path $userProfile) {
        $existing = Get-Content -LiteralPath $userProfile -Raw -ErrorAction SilentlyContinue
    }
    if ($existing -match 'agent-windows-shell \(do not edit') {
        Write-Host "profile $userProfile (already wired)"
    } elseif ([string]::IsNullOrWhiteSpace($existing)) {
        Set-Content -LiteralPath $userProfile -Value $snippet.TrimStart() -Encoding UTF8
        Write-Host "profile $userProfile (written)"
    } else {
        Add-Content -LiteralPath $userProfile -Value $snippet -Encoding UTF8
        Write-Host "profile $userProfile (appended)"
    }
}

if ($Skill) {
    $skillSrc = Join-Path $KitRoot 'adapters\generic\SKILL.md'
    $skillDirs = @(
        (Join-Path $env:USERPROFILE '.cursor\skills\windows-agent-shell'),
        (Join-Path $env:USERPROFILE '.agents\skills\windows-agent-shell')
    )
    foreach ($d in $skillDirs) {
        $parent = Split-Path -Parent $d
        if (-not (Test-Path $parent)) { continue }
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        Copy-Item -LiteralPath $skillSrc -Destination (Join-Path $d 'SKILL.md') -Force
        Write-Host "skill   $d\SKILL.md"
    }
}

if ($Cursor) {
    $cursorDir = Join-Path $env:USERPROFILE '.cursor'
    $hooksDir = Join-Path $cursorDir 'hooks'
    New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null
    $rewrite = Join-Path $KitRoot 'src\rewrite_windows_shell.py'
    $session = Join-Path $KitRoot 'src\session_start.py'
    $hooksPath = Join-Path $cursorDir 'hooks.json'
    $hooks = Read-JsonObject -LiteralPath $hooksPath
    if (-not $hooks.PSObject.Properties['version']) {
        Set-ObjectProperty -Object $hooks -Name version -Value 1
    }
    if (-not $hooks.PSObject.Properties['hooks']) {
        Set-ObjectProperty -Object $hooks -Name hooks -Value ([pscustomobject]@{})
    }
    $sessionEntry = [pscustomobject]@{
        command = "& `"$python`" `"$session`""
        timeout = 10
    }
    $rewriteEntry = [pscustomobject]@{
        command = "& `"$python`" `"$rewrite`""
        matcher = 'Shell'
        timeout = 10
    }
    Merge-ManagedHook -Hooks $hooks.hooks -Event sessionStart `
        -Entry $sessionEntry -ScriptPath $session
    Merge-ManagedHook -Hooks $hooks.hooks -Event preToolUse `
        -Entry $rewriteEntry -ScriptPath $rewrite
    Write-Utf8NoBom -LiteralPath $hooksPath `
        -Value ($hooks | ConvertTo-Json -Depth 20)
    $ruleSrc = Join-Path $KitRoot 'adapters\cursor\windows-powershell-agent.mdc'
    $ruleDstDir = Join-Path $cursorDir 'rules'
    if (-not (Test-Path $ruleDstDir)) { New-Item -ItemType Directory -Force -Path $ruleDstDir | Out-Null }
    Copy-Item -LiteralPath $ruleSrc -Destination (Join-Path $ruleDstDir 'windows-powershell-agent.mdc') -Force
    Write-Host "cursor  $hooksPath"

    $settingsPath = Join-Path $env:APPDATA 'Cursor\User\settings.json'
    if (Test-Path (Split-Path $settingsPath)) {
        $settingsDir = Split-Path $settingsPath
        if (-not (Test-Path $settingsDir)) { New-Item -ItemType Directory -Force -Path $settingsDir | Out-Null }
        $overlay = @{
            'terminal.integrated.defaultProfile.windows' = 'PowerShell'
            'terminal.integrated.env.windows'           = @{
                PYTHONUTF8        = '1'
                PYTHONIOENCODING  = 'utf-8'
            }
        }
        if ($pwsh) {
            $overlay['terminal.integrated.automationProfile.windows'] = @{
                path = $pwsh
                args = @('-NoLogo')
            }
        }
        if (Test-Path $settingsPath) {
            $cur = Read-JsonObject -LiteralPath $settingsPath
            $map = @{}
            $cur.PSObject.Properties | ForEach-Object { $map[$_.Name] = $_.Value }
            $envMap = @{}
            if ($map['terminal.integrated.env.windows']) {
                $map['terminal.integrated.env.windows'].PSObject.Properties |
                    ForEach-Object { $envMap[$_.Name] = $_.Value }
            }
            foreach ($name in $overlay['terminal.integrated.env.windows'].Keys) {
                $envMap[$name] = $overlay['terminal.integrated.env.windows'][$name]
            }
            $overlay['terminal.integrated.env.windows'] = $envMap
            foreach ($k in $overlay.Keys) { $map[$k] = $overlay[$k] }
            Write-Utf8NoBom -LiteralPath $settingsPath `
                -Value ($map | ConvertTo-Json -Depth 20)
            Write-Host "cursor  $settingsPath (merged)"
        } else {
            Write-Utf8NoBom -LiteralPath $settingsPath `
                -Value ($overlay | ConvertTo-Json -Depth 20)
            Write-Host "cursor  $settingsPath (created)"
        }
    }
}

if ($Claude) {
    $claudeDir = Join-Path $env:USERPROFILE '.claude'
    if (-not (Test-Path $claudeDir)) { New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null }
    $rewrite = Join-Path $KitRoot 'src\rewrite_windows_shell.py'
    $settingsPath = Join-Path $claudeDir 'settings.json'
    $hookCmd = "& `"$python`" `"$rewrite`""
    $entry = @{
        matcher = 'Bash|PowerShell|Shell'
        hooks   = @(
            @{
                type    = 'command'
                command = $hookCmd
            }
        )
    }
    if (Test-Path $settingsPath) {
        Write-Host "claude  $settingsPath exists — merge PreToolUse manually; see adapters\claude\README.md"
        Write-Host "        command = $hookCmd"
    } else {
        $doc = @{
            hooks = @{
                PreToolUse = @($entry)
            }
        }
        Write-Utf8NoBom -LiteralPath $settingsPath `
            -Value ($doc | ConvertTo-Json -Depth 8)
        Write-Host "claude  $settingsPath"
    }
    $skillDir = Join-Path $claudeDir 'skills\windows-agent-shell'
    New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $KitRoot 'adapters\generic\SKILL.md') -Destination (Join-Path $skillDir 'SKILL.md') -Force
}

if ($DisableCursorAttribution) {
    $cli = Join-Path $env:USERPROFILE '.cursor\cli-config.json'
    if (Test-Path $cli) {
        $c = Get-Content -LiteralPath $cli -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $c.attribution) {
            $c | Add-Member -NotePropertyName attribution -NotePropertyValue ([pscustomobject]@{}) -Force
        }
        $c.attribution | Add-Member -NotePropertyName attributeCommitsToAgent -NotePropertyValue $false -Force
        $c.attribution | Add-Member -NotePropertyName attributePRsToAgent -NotePropertyValue $false -Force
        Write-Utf8NoBom -LiteralPath $cli `
            -Value ($c | ConvertTo-Json -Depth 20)
        Write-Host "cursor  attribution disabled in $cli"
        Write-Host '        IDE: Cursor Settings > Agents > Attribution (separate toggle)'
    } else {
        Write-Host 'cursor  no cli-config.json; skip attribution'
    }
}

Write-Host ''
Write-Host 'Reload the agent app (Cursor / Claude / Codex). New chats pick up hooks and skills.'
Write-Host 'Optional extra hosts: edit internalHostPatterns in %USERPROFILE%\.agent-windows-shell.json'
