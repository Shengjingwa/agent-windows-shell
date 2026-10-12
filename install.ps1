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

function Get-CanonicalJson {
    # JSON text with sorted keys, so two parsed values compare equal whatever
    # their key order or container type (hashtable or parsed object).
    param($Value)
    if ($null -eq $Value) { return 'null' }
    $isTable = $Value -is [System.Collections.IDictionary]
    if ($isTable -or $Value -is [pscustomobject]) {
        $names = if ($isTable) { @($Value.Keys) } else { @($Value.PSObject.Properties.Name) }
        $pairs = @()
        foreach ($name in ($names | Sort-Object)) {
            # Assign inside if/else: output through the pipeline would unwrap a one-item array.
            if ($isTable) { $child = $Value[$name] } else { $child = $Value.PSObject.Properties[$name].Value }
            $pairs += '{0}:{1}' -f (ConvertTo-Json -InputObject $name -Compress), (Get-CanonicalJson $child)
        }
        return '{' + ($pairs -join ',') + '}'
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        $items = @()
        foreach ($item in $Value) { $items += Get-CanonicalJson $item }
        return '[' + ($items -join ',') + ']'
    }
    return ConvertTo-Json -InputObject $Value -Compress
}

function Set-ObjectProperty {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        $Value
    )
    $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
}

function Test-RunsScript {
    param([string]$Command, [Parameter(Mandatory = $true)][string]$ScriptPath)
    if (-not $Command) { return $false }
    if ($Command.IndexOf($ScriptPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    # The same file under an 8.3 short path (C:\Users\ADMINI~1\...\REWRIT~1.PY).
    $target = [IO.Path]::GetFullPath($ScriptPath)
    foreach ($match in [regex]::Matches($Command, '"([^"]+)"')) {
        try {
            $path = $match.Groups[1].Value
            if ((Test-Path -LiteralPath $path -PathType Leaf) -and [IO.Path]::GetFullPath($path) -eq $target) {
                return $true
            }
        } catch { }
    }
    return $false
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
            if (-not (Test-RunsScript -Command ([string]$item.command) -ScriptPath $ScriptPath)) {
                [void]$merged.Add($item)
            }
        }
    }
    [void]$merged.Add($Entry)
    Set-ObjectProperty -Object $Hooks -Name $Event -Value @($merged)
}

function Remove-Link {
    # Deletes a junction or symlink without touching its target.
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    if ((Get-Item -LiteralPath $LiteralPath -Force) -is [IO.DirectoryInfo]) {
        [IO.Directory]::Delete($LiteralPath)
    } else {
        [IO.File]::Delete($LiteralPath)
    }
}

function Test-PlainJson {
    # PowerShell 7's ConvertFrom-Json accepts comments and trailing commas, and
    # a rewrite would drop them. 5.1's parser rejects them before we get here.
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text) -or $PSVersionTable.PSVersion.Major -lt 6) { return $true }
    try {
        [System.Text.Json.JsonDocument]::Parse($Text).Dispose()
        return $true
    } catch {
        return $false
    }
}

function Install-SkillLink {
    # Link, never copy: a copy goes stale the next time the kit changes.
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$LinkPath
    )
    $parent = Split-Path -Parent $LinkPath
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    if (Test-Path -LiteralPath $LinkPath) {
        $item = Get-Item -LiteralPath $LinkPath -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            if (@($item.Target) -contains $Source) {
                Write-Host "skill   $LinkPath (already linked)"
                return
            }
            Remove-Link -LiteralPath $LinkPath
        } else {
            $others = @(Get-ChildItem -LiteralPath $LinkPath -Force |
                    Where-Object { $_.Name -ne 'SKILL.md' })
            if ($others.Count -gt 0) {
                Write-Warning "$LinkPath holds files this installer did not create; left unchanged"
                return
            }
            # An old copy holds only SKILL.md. Never delete recursively here:
            # a recursive delete can follow a junction into the kit itself.
            $copy = Join-Path $LinkPath 'SKILL.md'
            if (Test-Path -LiteralPath $copy) { [IO.File]::Delete($copy) }
            [IO.Directory]::Delete($LinkPath)
        }
    }
    New-Item -ItemType Junction -Path $LinkPath -Target $Source | Out-Null
    Write-Host "skill   $LinkPath -> $Source"
}

function Find-Python {
    # The hook runs before every command, so point it at the interpreter itself:
    # the py.exe launcher adds a process hop to each call.
    $c = Get-Command python.exe -ErrorAction SilentlyContinue
    # The WindowsApps python.exe is a Store launcher, not an interpreter.
    if ($c -and $c.Source -and $c.Source -notmatch '\\WindowsApps\\') {
        return $c.Source
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher -and $launcher.Source) {
        # Under 5.1 with ErrorActionPreference Stop, any stderr line from py throws.
        try {
            $resolved = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
        } catch {
            $resolved = $null
        }
        if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-Path -LiteralPath $resolved)) {
            return [string]$resolved
        }
    }
    foreach ($version in '313', '312', '311') {
        foreach ($root in "$env:LocalAppData\Programs\Python", $env:ProgramFiles) {
            $candidate = Join-Path $root "Python$version\python.exe"
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    if ($launcher -and $launcher.Source) { return $launcher.Source }
    throw 'Python 3 not found. Install it from python.org, or install the py launcher.'
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

# Test before writing anything. The installer tests run this script with the
# variable already set, so they skip the suite they belong to.
if (-not $env:AGENT_WINDOWS_SHELL_SELFTEST) {
    $env:AGENT_WINDOWS_SHELL_SELFTEST = '1'
    try {
        & $python (Join-Path $KitRoot 'src\rewrite_windows_shell.py') --test
    } finally {
        Remove-Item Env:AGENT_WINDOWS_SHELL_SELFTEST
    }
    if ($LASTEXITCODE -ne 0) { throw 'rewrite tests failed' }
}

# pwsh finds $PROFILE under the Documents known folder, which OneDrive or a
# policy may move. A redirected USERPROFILE (a test sandbox) keeps its own.
$documents = [Environment]::GetFolderPath('MyDocuments', 'DoNotVerify')
if (-not $documents -or $env:USERPROFILE.TrimEnd('\') -ne [Environment]::GetFolderPath('UserProfile').TrimEnd('\')) {
    $documents = Join-Path $env:USERPROFILE 'Documents'
}
$userProfile = Join-Path $documents 'PowerShell\Microsoft.PowerShell_profile.ps1'
$profileBegin = '# >>> agent-windows-shell (managed by install.ps1; do not edit) >>>'
$profileEnd = '# <<< agent-windows-shell <<<'
# The first release wrote this unmarked block. Only an exact copy is removed:
# an edited one may hold the user's own code.
$legacyLines = @(
    '# agent-windows-shell (do not edit; managed by install.ps1)'
    '$root = $env:AGENT_WINDOWS_SHELL_ROOT'
    'if (-not $root) {'
    '$cfg = Join-Path $env:USERPROFILE ''.agent-windows-shell.json'''
    'if (Test-Path -LiteralPath $cfg) {'
    '$root = (Get-Content -LiteralPath $cfg -Raw -Encoding UTF8 | ConvertFrom-Json).kitRoot'
    '}'
    '}'
    'if ($root) {'
    '$kitProfile = Join-Path $root ''src\profile.ps1'''
    'if (Test-Path -LiteralPath $kitProfile) { . $kitProfile }'
    '}'
)
$legacyBlock = '(?m)^' + (($legacyLines | ForEach-Object { '[ \t]*' + [regex]::Escape($_) + '[ \t]*' }) -join '\r?\n') + '(?=\r?$)'
$legacyHeader = '(?m)^[ \t]*' + [regex]::Escape($legacyLines[0])
$profileText = ''
$profileReadable = $true
$profileBom = $false
if (Test-Path -LiteralPath $userProfile) {
    try {
        $bytes = [IO.File]::ReadAllBytes($userProfile)
        $profileBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
        $skip = if ($profileBom) { 3 } else { 0 }
        # Strict: a profile saved in a legacy code page must not be rewritten as UTF-8.
        $profileText = [Text.UTF8Encoding]::new($false, $true).GetString($bytes, $skip, $bytes.Length - $skip)
    } catch {
        $profileReadable = $false
    }
    # UTF-16 without a BOM decodes as valid UTF-8 full of NUL characters.
    if ($profileText.IndexOf([char]0) -ge 0) {
        $profileReadable = $false
        $profileText = ''
    }
}
$legacyEdited = [regex]::Matches($profileText, $legacyHeader).Count -ne [regex]::Matches($profileText, $legacyBlock).Count

if ($Profile -and -not $profileReadable) {
    Write-Warning "$userProfile is not UTF-8; left unchanged. Save it as UTF-8 and run again, or add the block from README.md by hand."
} elseif ($Profile -and $legacyEdited) {
    Write-Warning "$userProfile has an edited block that starts with '$($legacyLines[0])'; left unchanged. Delete that block by hand and run again."
} elseif ($Profile) {
    # A literal path keeps shell start-up cheap: no env lookup, no JSON parse.
    $kitProfile = Join-Path $KitRoot 'src\profile.ps1'
    $quotedProfile = "'" + $kitProfile.Replace("'", "''") + "'"
    $snippet = @(
        $profileBegin,
        "if ([IO.File]::Exists($quotedProfile)) { . $quotedProfile }",
        $profileEnd
    ) -join "`r`n"
    $managed = '(?s)' + [regex]::Escape($profileBegin) + '.*?' + [regex]::Escape($profileEnd)
    if ($profileText -cmatch $managed) {
        $updated = [regex]::Replace($profileText, $managed, { param($m) $snippet })
        $updated = [regex]::Replace($updated, $legacyBlock + '(?:\r?\n)?', '')
        $state = 'refreshed'
    } elseif ($profileText -cmatch $legacyBlock) {
        $updated = [regex]::Replace($profileText, $legacyBlock, { param($m) $snippet })
        $state = 'upgraded from the first release'
    } elseif ([string]::IsNullOrWhiteSpace($profileText)) {
        $updated = $snippet + "`r`n"
        $state = 'written'
    } else {
        $updated = $profileText.TrimEnd() + "`r`n`r`n" + $snippet + "`r`n"
        $state = 'appended'
    }
    $backup = $null
    if ($updated -cne $profileText) {
        if ($profileText -cmatch $legacyBlock) {
            $backup = "$userProfile.agent-windows-shell.bak"
            [IO.File]::Copy($userProfile, $backup, $true)
        }
        if ($profileBom) {
            [IO.File]::WriteAllText($userProfile, $updated, [Text.UTF8Encoding]::new($true))
        } else {
            Write-Utf8NoBom -LiteralPath $userProfile -Value $updated
        }
        $profileText = $updated
    } else {
        $state = 'unchanged'
    }
    Write-Host "profile $userProfile ($state)"
    if ($backup) { Write-Host "        previous profile saved as $backup" }
}
# Settings the managed profile block replaces stay in the config until that block exists.
$profileManaged = $profileText.Contains($profileBegin)

$homeCfg = Join-Path $env:USERPROFILE '.agent-windows-shell.json'
$cfg = Read-JsonObject -LiteralPath $homeCfg
# The config holds machine facts and the user's own overrides. Behaviour flags
# live as defaults in the code, so a changed default reaches every install.
$detected = [ordered]@{
    gitBash       = $gitBash
    powerShell7   = $pwsh
    commandPrompt = $commandPrompt
}
foreach ($name in $detected.Keys) {
    Set-ObjectProperty -Object $cfg -Name $name -Value $detected[$name]
}
# Older installers copied every default into this file. Drop those copies
# (and kitRoot, which only the first release's profile block read) so they
# stop pinning old behaviour.
$legacyDefaults = [ordered]@{
    useNoProxyEnv  = $true
    utf8Prefix     = $true
    wrapHeredoc    = $true
    wrapAndAnd     = $true
    autoRouteBash  = $true
    autoRouteCmd   = $true
    rfc1918NoProxy = $true
}
$removed = @()
foreach ($name in $legacyDefaults.Keys) {
    if ($name -eq 'utf8Prefix' -and -not $profileManaged) { continue }
    $property = $cfg.PSObject.Properties[$name]
    if ($property -and $property.Value -eq $legacyDefaults[$name]) {
        $cfg.PSObject.Properties.Remove($name)
        $removed += $name
    }
}
$patterns = $cfg.PSObject.Properties['internalHostPatterns']
if ($patterns -and @($patterns.Value).Count -eq 0) {
    $cfg.PSObject.Properties.Remove('internalHostPatterns')
    $removed += 'internalHostPatterns'
}
if ($profileManaged -and $cfg.PSObject.Properties['kitRoot']) {
    $cfg.PSObject.Properties.Remove('kitRoot')
    $removed += 'kitRoot'
}
Write-Utf8NoBom -LiteralPath $homeCfg -Value ($cfg | ConvertTo-Json -Depth 10)
Write-Host "config  $homeCfg"
if ($removed.Count -gt 0) {
    Write-Host "        dropped legacy defaults: $($removed -join ', ')"
}

if ($Env) {
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'User')
    [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'User')
    [Environment]::SetEnvironmentVariable('AGENT_WINDOWS_SHELL_ROOT', $KitRoot, 'User')
    Write-Host 'env     PYTHONUTF8, PYTHONIOENCODING, AGENT_WINDOWS_SHELL_ROOT (User)'
}

$skillSource = Join-Path $KitRoot 'skills\windows-agent-shell'

if ($Skill) {
    # Cursor reads personal skills from ~\.cursor\skills only; do not add copies elsewhere.
    Install-SkillLink -Source $skillSource `
        -LinkPath (Join-Path $env:USERPROFILE '.cursor\skills\windows-agent-shell')
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
    Write-Host "cursor  $hooksPath"
    # Without pwsh 7 the host is 5.1, and the hook adds the UTF-8 setup itself.
    if ($pwsh -and -not $profileManaged -and $profileText -cnotmatch $legacyHeader -and $cfg.utf8Prefix -ne $true) {
        Write-Warning "No agent-windows-shell block in $userProfile, so Cursor's shells keep the console code page and non-ASCII output can garble. Run install.ps1 -Profile, or set `"utf8Prefix`": true in $homeCfg."
    }
    # Cursor ignores ~\.cursor\rules; an earlier release copied a rule file there.
    $staleRule = Join-Path $cursorDir 'rules\windows-powershell-agent.mdc'
    if (Test-Path -LiteralPath $staleRule) {
        Remove-Item -LiteralPath $staleRule -Force
        Write-Host "cursor  removed unused $staleRule"
    }

    $settingsPath = Join-Path $env:APPDATA 'Cursor\User\settings.json'
    if (Test-Path (Split-Path $settingsPath)) {
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
        $settingsExist = Test-Path $settingsPath
        $cur = $null
        $plainSettings = $true
        if ($settingsExist) {
            try { $cur = Read-JsonObject -LiteralPath $settingsPath } catch { $cur = $null }
            $plainSettings = Test-PlainJson (Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8)
        }
        if ($settingsExist -and $null -eq $cur) {
            Write-Warning "$settingsPath is not plain JSON; left unchanged. Add adapters\cursor\settings.snippet.json by hand."
        } elseif ($settingsExist) {
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
            # settings.json is the user's own file (comments, ordering): rewrite
            # it only when a managed key actually changes.
            $pending = @($overlay.Keys | Where-Object {
                    -not $map.ContainsKey($_) -or
                    (Get-CanonicalJson $map[$_]) -ne (Get-CanonicalJson $overlay[$_])
                } | Sort-Object)
            if ($pending.Count -eq 0) {
                Write-Host "cursor  $settingsPath (unchanged)"
            } elseif (-not $plainSettings) {
                Write-Warning "$settingsPath has comments that a rewrite would drop; left unchanged. Add these keys by hand: $($pending -join ', ') (see adapters\cursor\settings.snippet.json)"
            } else {
                foreach ($k in $pending) { $map[$k] = $overlay[$k] }
                Write-Utf8NoBom -LiteralPath $settingsPath `
                    -Value ($map | ConvertTo-Json -Depth 20)
                Write-Host "cursor  $settingsPath (merged: $($pending -join ', '))"
            }
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
    # Exec form: Claude runs shell-form hooks in Git Bash, which cannot parse `& "..."`.
    # Its Bash tool already runs bash, so only the PowerShell tool is matched.
    $entry = [pscustomobject]@{
        matcher = 'PowerShell'
        hooks   = @(
            [pscustomobject]@{
                type    = 'command'
                command = $python
                args    = @($rewrite)
            }
        )
    }
    $settings = [pscustomobject]@{}
    if (Test-Path -LiteralPath $settingsPath) {
        try { $settings = Read-JsonObject -LiteralPath $settingsPath } catch { $settings = $null }
        if ($settings -isnot [pscustomobject] -or
            -not (Test-PlainJson (Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8)) -or
            ($settings.PSObject.Properties['hooks'] -and $settings.hooks -isnot [pscustomobject])) {
            $settings = $null
        }
    }
    if ($null -eq $settings) {
        Write-Warning "$settingsPath is not plain JSON; left unchanged. Merge PreToolUse by hand; see adapters\claude\README.md"
        Write-Host "        command = $python"
        Write-Host "        args    = [`"$rewrite`"]"
    } else {
        if (-not $settings.PSObject.Properties['hooks']) {
            Set-ObjectProperty -Object $settings -Name hooks -Value ([pscustomobject]@{})
        }
        # Replace this kit's hooks from earlier installs (the first release used
        # shell form, which fails in Git Bash) and keep every other hook.
        $groups = New-Object System.Collections.ArrayList
        $property = $settings.hooks.PSObject.Properties['PreToolUse']
        if ($property) {
            foreach ($group in @($property.Value)) {
                if ($group -isnot [pscustomobject] -or -not $group.PSObject.Properties['hooks']) {
                    [void]$groups.Add($group)
                    continue
                }
                $kept = @(@($group.hooks) | Where-Object {
                        ("$($_.command) $(@($_.args) -join ' ')").IndexOf(
                            'rewrite_windows_shell.py', [StringComparison]::OrdinalIgnoreCase) -lt 0
                    })
                if ($kept.Count -gt 0) {
                    Set-ObjectProperty -Object $group -Name hooks -Value $kept
                    [void]$groups.Add($group)
                }
            }
        }
        [void]$groups.Add($entry)
        Set-ObjectProperty -Object $settings.hooks -Name PreToolUse -Value @($groups)
        Write-Utf8NoBom -LiteralPath $settingsPath -Value ($settings | ConvertTo-Json -Depth 20)
        Write-Host "claude  $settingsPath"
    }
    Install-SkillLink -Source $skillSource `
        -LinkPath (Join-Path $claudeDir 'skills\windows-agent-shell')
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
