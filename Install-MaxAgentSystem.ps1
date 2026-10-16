#Requires -Version 7.0
<#
.SYNOPSIS
Create a new Max Agent System hub from an extracted release ZIP.
.DESCRIPTION
No existing Max Agent System folders, cache, OneDrive setup or Git installation
are needed. The default target is %LOCALAPPDATA%\AgentSetup\hub. Missing parent
folders are created by the bundled installer after validation. Existing nonempty
targets are refused; use Get-AgentSetup.ps1 for an existing installation.
Copied agent adapters are the default. No user-global setting or account changes.
.EXAMPLE
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1 -DryRun
.EXAMPLE
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1
.EXAMPLE
pwsh -NoProfile -File .\Install-MaxAgentSystem.ps1 -Destination 'D:\Work\Max Agent System'
#>
[CmdletBinding()]
param(
  [string]$Destination,
  [switch]$DryRun,
  [switch]$UseJunctions
)
$ErrorActionPreference='Stop'
if (-not $IsWindows) { throw 'This installer supports Windows only.' }
if ($PSBoundParameters.ContainsKey('Destination')) {
  if ([string]::IsNullOrWhiteSpace($Destination)) { throw 'Destination cannot be empty.' }
} else {
  if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA is unavailable. Supply -Destination with a new local hub path.'
  }
  $Destination=Join-Path $env:LOCALAPPDATA 'AgentSetup\hub'
}
$Destination=[IO.Path]::GetFullPath($Destination).TrimEnd('\','/')
if ($Destination -eq [IO.Path]::GetPathRoot($Destination).TrimEnd('\','/')) {
  throw 'Choose a new hub folder, not a drive or share root.'
}
$installer=Join-Path $PSScriptRoot 'Get-AgentSetup.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
  throw 'Extract the complete release ZIP first; Get-AgentSetup.ps1 must be beside this script.'
}
$existing=$null
try { $existing=Get-Item -LiteralPath $Destination -Force -ErrorAction Stop }
catch [System.Management.Automation.ItemNotFoundException] { }
if ($existing) {
  if (-not $existing.PSIsContainer) { throw 'Destination is an existing file. Choose a new hub folder.' }
  if ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Destination must be a regular local folder, not a link or junction.'
  }
  if (@(Get-ChildItem -LiteralPath $Destination -Force -ErrorAction Stop).Count) {
    throw 'Destination is not empty. Clean installation will not overwrite it. Use Get-AgentSetup.ps1 for an existing installation, or choose another folder.'
  }
}

# One installation engine owns payload validation, scaffold creation and adapters.
# Pass arguments directly; no composed shell command or global configuration change.
$installerArgs=@('-NoProfile','-File',$installer,'-SourceRoot',$PSScriptRoot,'-Destination',$Destination)
if (-not $UseJunctions) { $installerArgs+='-CopyAdapters' }
if ($DryRun) { $installerArgs+='-DryRun' }
Write-Host "Clean installation target: $Destination"
& pwsh @installerArgs
if ($LASTEXITCODE -ne 0) { throw "Clean installation failed (exit $LASTEXITCODE). See the installer message above." }
if ($DryRun) {
  Write-Host 'Preview complete. Rerun without -DryRun to create the hub and its missing parent folders.'
} else {
  Write-Host "Max Agent System installed: $Destination"
  Write-Host 'Open that folder in your current AI agent and fill in its AGENTS.md identity.'
  Write-Host 'First, give your current AI the prompt in 00.ABOUT/AGENT_CLI_ONBOARDING.md to choose optional agent CLIs (one, several, or none).'
}
