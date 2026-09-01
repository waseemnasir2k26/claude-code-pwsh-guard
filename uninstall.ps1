<#
.SYNOPSIS
    Remove the claude-code-pwsh-guard hooks from a Claude Code settings.json.

.DESCRIPTION
    Removes only the hook groups this installer added, identified by the
    pwshGuardMarker field on the handler. Every other hook and setting is left
    exactly as it was. Empty event keys and an empty "hooks" object are cleaned
    up so the file does not accumulate dead structure.

    Same safety contract as install.ps1: refuse on malformed JSON, back up
    first, write atomically, re-parse afterwards.

.PARAMETER SettingsPath
    Settings file to modify. Defaults to $HOME\.claude\settings.json.

.PARAMETER Quiet
    Suppress the informational banner (used by the test suite).
#>
[CmdletBinding()]
param(
    [string] $SettingsPath,
    [switch] $Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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

function Test-IsGuardGroup($Group) {
    $hooks = Get-Prop $Group 'hooks'
    if ($null -eq $hooks) { return $false }
    foreach ($h in @($hooks)) {
        if ((Get-Prop $h 'pwshGuardMarker') -eq $MarkerKey) { return $true }
    }
    return $false
}

function Write-SettingsAtomic($Settings, [string] $Path) {
    $json = $Settings | ConvertTo-Json -Depth 100
    $tmp = "$Path.pwsh-guard-tmp-$([System.Guid]::NewGuid().ToString('N'))"
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $json, $enc)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

# ---------------------------------------------------------------------------

$target = Resolve-SettingsPath $SettingsPath

if (-not (Test-Path -LiteralPath $target)) {
    Write-Info "Nothing to do: $target does not exist."
    exit 0
}

$raw = Get-Content -LiteralPath $target -Raw -Encoding UTF8
try {
    $settings = $raw | ConvertFrom-Json
} catch {
    $backup = New-Backup $target
    throw "REFUSING TO WRITE: $target is not valid JSON. Nothing was changed. A copy was saved to $backup."
}

$hooksRoot = Get-Prop $settings 'hooks'
if ($null -eq $hooksRoot) {
    Write-Info 'Nothing to do: no hooks configured.'
    exit 0
}

$backup = New-Backup $target
Write-Info "  backup:   $backup"

$removed = 0
foreach ($eventName in @($hooksRoot.PSObject.Properties | ForEach-Object { $_.Name })) {
    $groups = Get-Prop $hooksRoot $eventName
    if ($null -eq $groups) { continue }
    $kept = New-Object System.Collections.ArrayList
    foreach ($g in @($groups)) {
        if (Test-IsGuardGroup $g) { $removed++; continue }
        [void] $kept.Add($g)
    }
    if ($kept.Count -eq 0) {
        $hooksRoot.PSObject.Properties.Remove($eventName)
    } else {
        $hooksRoot.PSObject.Properties[$eventName].Value = @($kept.ToArray())
    }
}

if (@($hooksRoot.PSObject.Properties).Count -eq 0) {
    $settings.PSObject.Properties.Remove('hooks')
}

Write-SettingsAtomic $settings $target

try {
    Get-Content -LiteralPath $target -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null
} catch {
    Copy-Item -LiteralPath $backup -Destination $target -Force
    throw "Wrote invalid JSON - restored from backup. This is a bug, please report it."
}

Write-Info ''
Write-Info "Removed $removed pwsh-guard hook group(s). Restart Claude Code to apply."
