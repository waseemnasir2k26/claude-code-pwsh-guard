<#
.SYNOPSIS
    Install the claude-code-pwsh-guard hooks into a Claude Code settings.json.

.DESCRIPTION
    Merges two hook entries (PreToolUse on the Bash tool, and SessionStart) into
    the target settings file WITHOUT touching anything else in it.

    Safety contract:
      * The existing file is parsed first. If it is not valid JSON the script
        REFUSES to write anything and tells you where the backup is.
      * A timestamped backup is written before any change.
      * Existing hooks on the same events are preserved; the guard is appended.
      * Re-running is idempotent: an already-installed guard entry is replaced
        in place rather than duplicated.
      * The new file is written to a temp file and then moved over the target,
        so an interrupted run cannot leave a half-written settings.json.

.PARAMETER SettingsPath
    Settings file to modify. Defaults to $HOME\.claude\settings.json.

.PARAMETER PythonPath
    Python executable used to run the hooks. Defaults to the first "python" on
    PATH, then "py".

.PARAMETER Quiet
    Suppress the informational banner (used by the test suite).

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -SettingsPath .\.claude\settings.json
#>
[CmdletBinding()]
param(
    [string] $SettingsPath,
    [string] $PythonPath,
    [switch] $Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$MarkerKey = 'claude-code-pwsh-guard'

function Write-Info([string] $Message) {
    if (-not $Quiet) { Write-Host $Message }
}

function Resolve-SettingsPath([string] $Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return (Join-Path (Join-Path $HOME '.claude') 'settings.json')
    }
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path (Get-Location).Path $Path)
}

function Resolve-Python([string] $Preferred) {
    if ($Preferred) { return $Preferred }
    foreach ($candidate in @('python', 'py', 'python3')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $candidate }
    }
    throw 'No Python interpreter found on PATH. Install Python 3.9+ or pass -PythonPath.'
}

# Read the settings file. Returns a PSCustomObject.
# Throws on malformed JSON, after taking a backup.
function Read-Settings([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{}
    }
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [pscustomobject]@{}
    }
    try {
        $parsed = $raw | ConvertFrom-Json
    } catch {
        $backup = New-Backup $Path
        throw "REFUSING TO WRITE: $Path is not valid JSON. Nothing was changed. A copy was saved to $backup. Fix the JSON by hand, then re-run this installer. Parser said: $($_.Exception.Message)"
    }
    if ($null -eq $parsed) { return [pscustomobject]@{} }
    if ($parsed -is [array]) {
        $backup = New-Backup $Path
        throw "REFUSING TO WRITE: $Path contains a JSON array, not an object. Nothing was changed. A copy was saved to $backup."
    }
    return $parsed
}

function New-Backup([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
    $backup = "$Path.pwsh-guard-backup-$stamp"
    $i = 1
    while (Test-Path -LiteralPath $backup) {
        $backup = "$Path.pwsh-guard-backup-$stamp-$i"
        $i++
    }
    Copy-Item -LiteralPath $Path -Destination $backup -Force
    return $backup
}

function Get-Prop($Object, [string] $Name) {
    if ($null -eq $Object) { return $null }
    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function Set-Prop($Object, [string] $Name, $Value) {
    if ($Object.PSObject.Properties[$Name]) {
        $Object.PSObject.Properties[$Name].Value = $Value
    } else {
        $Object | Add-Member -MemberType NoteProperty -Name $Name -Value $Value
    }
}

# Build the guard's hook-group object for one event.
function New-GuardGroup([string] $Matcher, [string] $Script, [string] $Python, [string] $Root) {
    $handler = [pscustomobject]@{
        type            = 'command'
        command         = $Python
        args            = @('-X', 'utf8', (Join-Path (Join-Path $Root 'hooks') $Script))
        timeout         = 15
        statusMessage   = 'pwsh-guard'
        pwshGuardMarker = $MarkerKey
    }
    return [pscustomobject]@{
        matcher = $Matcher
        hooks   = @($handler)
    }
}

# Does this hook group belong to us?
function Test-IsGuardGroup($Group) {
    $hooks = Get-Prop $Group 'hooks'
    if ($null -eq $hooks) { return $false }
    foreach ($h in @($hooks)) {
        if ((Get-Prop $h 'pwshGuardMarker') -eq $MarkerKey) { return $true }
    }
    return $false
}

# Merge one guard group into settings.hooks.<Event>, preserving everything else.
function Merge-GuardGroup($Settings, [string] $EventName, $Group) {
    $hooksRoot = Get-Prop $Settings 'hooks'
    if ($null -eq $hooksRoot) {
        $hooksRoot = [pscustomobject]@{}
        Set-Prop $Settings 'hooks' $hooksRoot
    }
    if ($hooksRoot -isnot [psobject] -or $hooksRoot -is [array]) {
        throw "REFUSING TO WRITE: settings 'hooks' is not an object. Nothing was changed."
    }

    $existing = Get-Prop $hooksRoot $EventName
    $list = New-Object System.Collections.ArrayList
    if ($null -ne $existing) {
        foreach ($g in @($existing)) {
            if (Test-IsGuardGroup $g) { continue }   # drop our own previous entry
            [void] $list.Add($g)
        }
    }
    [void] $list.Add($Group)
    Set-Prop $hooksRoot $EventName @($list.ToArray())
}

function Write-SettingsAtomic($Settings, [string] $Path) {
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $json = $Settings | ConvertTo-Json -Depth 100
    $tmp = "$Path.pwsh-guard-tmp-$([System.Guid]::NewGuid().ToString('N'))"
    # UTF-8 without BOM: Claude Code reads strict JSON and a BOM upsets some parsers.
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $json, $enc)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

# ---------------------------------------------------------------------------

$target = Resolve-SettingsPath $SettingsPath
$python = Resolve-Python $PythonPath

Write-Info "claude-code-pwsh-guard installer"
Write-Info "  repo:     $RepoRoot"
Write-Info "  settings: $target"
Write-Info "  python:   $python"

$settings = Read-Settings $target

$backup = New-Backup $target
if ($backup) { Write-Info "  backup:   $backup" }

Merge-GuardGroup $settings 'PreToolUse'   (New-GuardGroup 'Bash'    'pwsh_guard.py'       $python $RepoRoot)
Merge-GuardGroup $settings 'SessionStart' (New-GuardGroup 'startup' 'session_env_note.py' $python $RepoRoot)

Write-SettingsAtomic $settings $target

# Prove the file we just wrote still parses.
try {
    Get-Content -LiteralPath $target -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null
} catch {
    if ($backup) { Copy-Item -LiteralPath $backup -Destination $target -Force }
    throw "Wrote invalid JSON - restored from backup. This is a bug, please report it. $($_.Exception.Message)"
}

Write-Info ''
Write-Info 'Installed. Restart Claude Code (or run /hooks) to load the guard.'
Write-Info 'Uninstall with: .\uninstall.ps1'
