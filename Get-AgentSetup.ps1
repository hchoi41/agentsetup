<#
.SYNOPSIS
  Install the latest agent setup from GitHub onto this machine.

.DESCRIPTION
  Tier 3 -> new machine. Clones (or pulls) https://github.com/hchoi41/agentsetup,
  lays the v5 scaffold down at -Destination, then rebuilds the agent surfaces that
  git cannot carry (.claude / .agents / .opencode / .github / microsoft_copilot are
  JUNCTIONS - they are recreated locally by scripts/sync_agent_adapters.ps1).

.EXAMPLE
  .\Get-AgentSetup.ps1 -Destination C:\000.thinkfirst_together_local -DryRun
  .\Get-AgentSetup.ps1 -Destination C:\000.newhub

.NOTES
  Status: UNVALIDATED beyond AST parse. Run -DryRun first. Author: Claude, 2026-08-18.
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
$hard = @('git', 'pwsh'); $soft = @('python', 'node', 'codex', 'claude', 'copilot')
$missing = @()
foreach ($c in $hard) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { Say "  ok   $c" 'DarkGreen' } else { Say "  FAIL $c (required)" 'Red'; $missing += $c }
}
foreach ($c in $soft) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { Say "  ok   $c" 'DarkGreen' } else { Say "  warn $c (optional - needed to RUN Orchestrate, not to install)" 'Yellow' }
}
if ($missing.Count -gt 0) { throw "missing required tool(s): $($missing -join ', ')" }

# ---------------------------------------------------------------- 1. FETCH
Step '1. Fetch latest from GitHub'
if (Test-Path -LiteralPath (Join-Path $CacheRoot '.git')) {
  Push-Location $CacheRoot
  if (-not $DryRun) { git pull --ff-only 2>&1 | ForEach-Object { Say "  $_" } } else { Say '  -DryRun: would git pull' 'Yellow' }
  Pop-Location
} else {
  if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Split-Path $CacheRoot -Parent) | Out-Null
    git clone $RepoUrl $CacheRoot
    if ($LASTEXITCODE -ne 0) { throw "clone failed ($LASTEXITCODE)" }
  } else { Say "  -DryRun: would clone $RepoUrl -> $CacheRoot" 'Yellow' }
}
if (-not $DryRun) {
  Push-Location $CacheRoot; $head = git log --oneline -1; Pop-Location
  Say "  at: $head" 'Green'
}

# ---------------------------------------------------------------- 2. SCAFFOLD
Step '2. Scaffold the hub'
$folders = @('00.ABOUT','01.INPUT','02.INPUT_TEMPLATES','03.OUTPUT_FINAL','11.OUTPUT_ROUGH',
             '12.MEMORY_RAM','13.MEMORY_HDD','14.LESSONS_LEARNED','15.WIKI')
foreach ($f in $folders) {
  $p = Join-Path $Destination $f
  if (Test-Path -LiteralPath $p) { Say "  skip $f (exists)" 'DarkGray' }
  else { if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $p | Out-Null }; Say "  make $f" }
}

# ---------------------------------------------------------------- 3. LAY DOWN PAYLOAD
Step '3. Lay down payload'
$map = @(
  @{ from = 'governance\PROJECT_GOVERNANCE.md';               to = '00.ABOUT\PROJECT_GOVERNANCE.md';               merge = $false }
  @{ from = 'governance\CLAUDE.md';                           to = '00.ABOUT\CLAUDE.md';                           merge = $true  }
  @{ from = 'governance\orchestration_protocol.md';           to = '00.ABOUT\orchestration_protocol.md';           merge = $false }
  @{ from = 'governance\claude_cowork_hdd_ingestion_protocol.md'; to = '00.ABOUT\claude_cowork_hdd_ingestion_protocol.md'; merge = $false }
  @{ from = 'AGENT_TARGETS.json';                             to = '00.ABOUT\AGENT_TARGETS.json';                  merge = $false }
  @{ from = 'template\AGENTS.md';                             to = 'AGENTS.md';                                    merge = $true  }
)
foreach ($m in $map) {
  $s = Join-Path $CacheRoot $m.from; $d = Join-Path $Destination $m.to
  if (-not (Test-Path -LiteralPath $s)) { Say "  MISSING in repo: $($m.from)" 'Red'; continue }
  if ((Test-Path -LiteralPath $d) -and $m.merge -and -not $Force) {
    Say "  KEEP $($m.to)  (merge-not-overwrite; use -Force to replace)" 'Yellow'; continue
  }
  if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Split-Path $d -Parent) | Out-Null
    Copy-Item -LiteralPath $s -Destination $d -Force
  }
  Say "  put  $($m.to)"
}
foreach ($tree in @('skills', 'scripts', 'orchestrate', 'tests')) {
  $s = Join-Path $CacheRoot $tree; $d = Join-Path $Destination $tree
  if (-not (Test-Path -LiteralPath $s)) { Say "  MISSING in repo: $tree\" 'Red'; continue }
  if (-not $DryRun) { Copy-Item -LiteralPath $s -Destination $d -Recurse -Force }
  Say "  put  $tree\"
}

# ---------------------------------------------------------------- 4. REBUILD JUNCTIONS
Step '4. Rebuild agent surfaces (what git cannot carry)'
$sync = Join-Path $Destination 'scripts\sync_agent_adapters.ps1'
if (-not (Test-Path -LiteralPath $sync)) { throw "sync_agent_adapters.ps1 missing at $sync - the install is incomplete" }
if ($DryRun) { Say "  -DryRun: would run $sync -WorkspaceRoot $Destination" 'Yellow' }
else {
  & pwsh -NoProfile -File $sync -WorkspaceRoot $Destination
  if ($LASTEXITCODE -ne 0) { Say "  adapter sync returned $LASTEXITCODE - inspect before using the hub" 'Red' }
  else { Say '  adapters rebuilt' 'Green' }
}

# ---------------------------------------------------------------- 5. VERIFY
Step '5. Verify'
if (-not $DryRun) {
  foreach ($surface in @('.claude', '.agents', '.opencode')) {
    $p = Join-Path $Destination $surface
    $i = Get-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
    if ($i) { Say ("  ok   {0,-14} present" -f $surface) 'DarkGreen' } else { Say ("  warn {0,-14} absent" -f $surface) 'Yellow' }
  }
  $env:AGENT_HUB_ROOT = $Destination
  [Environment]::SetEnvironmentVariable('AGENT_HUB_ROOT', $Destination, 'User')
  Say "  AGENT_HUB_ROOT set to $Destination (user scope)" 'Green'
}
Say "`nDone. Hub at $Destination" 'Cyan'
