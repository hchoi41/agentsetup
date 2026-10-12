<#
.SYNOPSIS
Download the agent setup and install a local Windows hub with PowerShell 7.
.DESCRIPTION
Runs in Windows PowerShell 5.1 or PowerShell 7. It never resets a dirty cache.
Use -SourceRoot with an extracted ZIP to install without Git or network access.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Destination,
  [string]$RepoUrl='https://github.com/hchoi41/agentsetup.git',
  [string]$CacheRoot=(Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AgentSetup\repo'),
  [string]$SourceRoot,
  [switch]$Force,
  [switch]$DryRun,
  [switch]$CopyAdapters,
  [switch]$SetUserEnvironment
)
$ErrorActionPreference='Stop'
if (-not (Get-Command pwsh -ErrorAction SilentlyContinue)) { throw 'Install PowerShell 7+ (pwsh) through your approved software channel first.' }
function Full([string]$Path) {
  $absolute=[IO.Path]::GetFullPath($Path)
  if ($absolute.TrimEnd('\','/') -eq [IO.Path]::GetPathRoot($absolute).TrimEnd('\','/')) { throw 'Drive or share roots are not installation folders.' }
  $absolute.TrimEnd('\','/')
}
function Within([string]$Path,[string]$Root) { $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase) }
function Check-Local([string]$Path) {
  if ($Path -match '(?i)\\OneDrive(?: - [^\\]+)?(?:\\|$)') { throw 'Use folders outside OneDrive.' }
  foreach ($syncRoot in @($env:OneDrive,$env:OneDriveCommercial,$env:OneDriveConsumer)) {
    if ($syncRoot -and (Within $Path (Full $syncRoot))) { throw 'Use folders outside OneDrive.' }
  }
  $current=$Path
  while ($current) {
    $item=Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
    if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Reparse point in install path: $current" }
    $parent=Split-Path -Parent $current
    if ($parent -eq $current) { break }
    $current=$parent
  }
}
$Destination=Full $Destination
Check-Local $Destination
if (-not $SourceRoot) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Install Git first, or download the repository ZIP and use -SourceRoot.' }
  $CacheRoot=Full $CacheRoot
  Check-Local $CacheRoot
  if ((Within $Destination $CacheRoot) -or (Within $CacheRoot $Destination)) { throw 'Destination and cache must be separate folders.' }
  if (Test-Path -LiteralPath (Join-Path $CacheRoot '.git')) {
    $origin=(& git -C $CacheRoot remote get-url origin)
    if ($LASTEXITCODE -ne 0 -or "$origin".Trim() -ne $RepoUrl) { throw 'Cache origin does not match RepoUrl.' }
    $dirty=@(& git -C $CacheRoot status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $dirty.Count) { throw 'Cache has local changes; preserve them and use a separate cache.' }
    if (-not $DryRun) {
      & git -C $CacheRoot pull --ff-only
      if ($LASTEXITCODE -ne 0) { throw 'Cache update failed; no installation performed.' }
    } else { Write-Host 'DRY RUN: would update the clean cache with git pull --ff-only.' }
  } elseif ($DryRun) {
    Write-Host 'DRY RUN: would clone and validate the repository, then install. Nothing written.'
    return
  } else {
    if ((Test-Path -LiteralPath $CacheRoot) -and @(Get-ChildItem -LiteralPath $CacheRoot -Force).Count) { throw 'Cache folder is nonempty and is not a Git checkout.' }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CacheRoot) | Out-Null
    & git clone $RepoUrl $CacheRoot
    if ($LASTEXITCODE -ne 0) { throw 'Repository clone failed.' }
  }
  $SourceRoot=$CacheRoot
}
$SourceRoot=Full $SourceRoot
Check-Local $SourceRoot
if ((Within $Destination $SourceRoot) -or (Within $SourceRoot $Destination)) { throw 'Destination and source must be separate folders.' }
$installer=Join-Path $SourceRoot 'Get-AgentSetup.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'SourceRoot has no Get-AgentSetup.ps1; use the repository root.' }
$argsForInstaller=@('-NoProfile','-File',$installer,'-Destination',$Destination,'-SourceRoot',$SourceRoot)
if ($Force) { $argsForInstaller+='-Force' }
if ($DryRun) { $argsForInstaller+='-DryRun' }
if ($CopyAdapters) { $argsForInstaller+='-CopyAdapters' }
if ($SetUserEnvironment) { $argsForInstaller+='-SetUserEnvironment' }
& pwsh @argsForInstaller
if ($LASTEXITCODE -ne 0) { throw "Installer failed with exit code $LASTEXITCODE." }
