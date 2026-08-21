<#
.SYNOPSIS
  One-command install of the agent setup on a fresh machine.

.DESCRIPTION
  Step zero of the consumer pipeline. Checks prerequisites, clones (or updates) the
  public repo into a local cache OUTSIDE OneDrive, then delegates the actual install
  to Get-AgentSetup.ps1 from the freshly synced repo (run under pwsh 7+).

      GitHub  ->  cache repo (default C:\000.agentsetup_repo)  ->  your new hub (-Destination)

  Runs from Windows PowerShell 5.1 or pwsh. Every step is re-runnable (idempotent).

.EXAMPLE
  # brand-new machine, straight from the web:
  #   irm https://raw.githubusercontent.com/hchoi41/agentsetup/main/Bootstrap.ps1 -OutFile "$env:TEMP\Bootstrap.ps1"
  #   & "$env:TEMP\Bootstrap.ps1" -Destination C:\000.newhub -DryRun
  #   & "$env:TEMP\Bootstrap.ps1" -Destination C:\000.newhub
  # or from a local copy of the repo:
  .\Bootstrap.ps1 -Destination C:\000.newhub -DryRun
  .\Bootstrap.ps1 -Destination C:\000.newhub

.NOTES
  Author: Claude, 2026-08-20. Max-approved; closes payload_manifest open_items O4.
  Guards (2026-08-20 incident lesson): refuses OneDrive paths for both -Destination and
  -CacheRoot — runtime hubs and git working copies live OUTSIDE OneDrive by design,
  because sync churn corrupts .git and floods OneDrive storage.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Destination,
  [string]$RepoUrl   = 'https://github.com/hchoi41/agentsetup.git',
  [string]$CacheRoot = 'C:\000.agentsetup_repo',
  [switch]$Force,
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Step([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 0. PREFLIGHT
Step '0. Preflight'
$missing = @()
foreach ($c in @('git', 'pwsh')) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { Say "  ok   $c" 'DarkGreen' }
  else { Say "  FAIL $c (required)" 'Red'; $missing += $c }
}
if ($missing.Count -gt 0) {
  Say '  install hints:  winget install Git.Git   |   winget install Microsoft.PowerShell' 'Yellow'
  throw "missing required tool(s): $($missing -join ', ')"
}

# OneDrive guard: hubs and git working copies never live inside OneDrive.
foreach ($pair in @(@('Destination', $Destination), @('CacheRoot', $CacheRoot))) {
  $full = [System.IO.Path]::GetFullPath($pair[1])
  if ($full -match '(?i)\\OneDrive(\\|$)') {
    throw "BOOTSTRAP GUARD: -$($pair[0]) '$full' is under OneDrive. Runtime hubs and git working copies live OUTSIDE OneDrive (sync churn corrupts them). Pick a local path like C:\000.myhub."
  }
}
Say "  destination   $Destination"
Say "  cache repo    $CacheRoot"

# ---------------------------------------------------------------- 1. GET THE REPO
Step '1. Get the repo'
if (Test-Path -LiteralPath (Join-Path $CacheRoot '.git')) {
  if ($DryRun) { Say '  -DryRun: would fetch + reset the cache to origin/main' 'Yellow' }
  else {
    Push-Location $CacheRoot
    try {
      git fetch origin 2>&1 | ForEach-Object { Say "  $_" }
      if ($LASTEXITCODE -ne 0) { throw "git fetch failed ($LASTEXITCODE)" }
      git rev-parse --verify --quiet origin/main *> $null
      if ($LASTEXITCODE -eq 0) {
        git reset --hard origin/main 2>&1 | ForEach-Object { Say "  $_" }
        if ($LASTEXITCODE -ne 0) { throw "git reset --hard origin/main failed ($LASTEXITCODE)" }
        Say '  cache synced to origin/main (local edits discarded - the cache is a read-only consumer copy)' 'Green'
      } else {
        Say '  origin/main not found on the remote - keeping local state' 'Yellow'
      }
    } finally { Pop-Location }
  }
} else {
  if ($DryRun) { Say "  -DryRun: would clone $RepoUrl -> $CacheRoot" 'Yellow' }
  else {
    New-Item -ItemType Directory -Force -Path (Split-Path $CacheRoot -Parent) | Out-Null
    git clone $RepoUrl $CacheRoot
    if ($LASTEXITCODE -ne 0) { throw "clone failed ($LASTEXITCODE)" }
  }
}

# ---------------------------------------------------------------- 2. DELEGATE THE INSTALL
Step '2. Install via Get-AgentSetup.ps1 (under pwsh)'
$installer = Join-Path $CacheRoot 'Get-AgentSetup.ps1'
if (-not (Test-Path -LiteralPath $installer)) {
  if ($DryRun) {
    Say "  -DryRun: installer not on disk yet (clone was skipped); a real run would execute $installer" 'Yellow'
    Say "`nDryRun complete." 'Cyan'
    return
  }
  throw "Get-AgentSetup.ps1 not found at $installer - the repo clone looks incomplete"
}
$gasArgs = @('-NoProfile', '-File', $installer, '-Destination', $Destination, '-RepoUrl', $RepoUrl, '-CacheRoot', $CacheRoot)
if ($Force)  { $gasArgs += '-Force' }
if ($DryRun) { $gasArgs += '-DryRun' }
& pwsh @gasArgs
if ($LASTEXITCODE -ne 0) { throw "Get-AgentSetup.ps1 returned $LASTEXITCODE - inspect its output above" }
Say "`nBootstrap complete. Hub at $Destination" 'Cyan'
