<#
.SYNOPSIS
  Publish the curated agent-setup payload from OneDrive to https://github.com/hchoi41/agentsetup

.DESCRIPTION
  Tier 2 -> Tier 3 of the promotion pipeline:
      hub (local)  ->  980.agents_setup (OneDrive)  ->  agentsetup_repo (git)  ->  GitHub

  Mirrors only the paths named in payload_manifest.json into a git working copy that lives
  OUTSIDE OneDrive, runs a secret/personal-data gate, then commits and pushes.

  THE SCAN GATE IS NOT OPTIONAL. The GitHub repo is PUBLIC. If the gate finds anything,
  this script refuses to commit. Use -SkipScan only if you have manually reviewed every hit.

.PARAMETER Message
  Commit message. Convention: {type}({scope}): {summary}
  types: feat | fix | docs | chore | refactor | plan | research
  e.g. "feat(skills): add doc-wash-nonfiction skill"

.EXAMPLE
  .\Publish-AgentSetup.ps1 -Message "feat(skills): add doc-wash-nonfiction" -DryRun
  .\Publish-AgentSetup.ps1 -Message "feat(skills): add doc-wash-nonfiction"

.NOTES
  Status: UNVALIDATED beyond AST parse. Run -DryRun first. Author: Claude, 2026-08-18.
  2026-08-20: Added step 0.5 WIPE-TARGET GUARD (runs before clone/pull) after the deletion incident — the destructive
  mirror step now hard-stops if $RepoRoot is (or is inside) the OneDrive source, any hub,
  or any OneDrive path, or lacks a .git working copy. Guard added by Claude (Cowork), Max-approved.
  2026-08-20b (post-QA-5c): pull replaced by exit-checked fetch + reset --hard origin/main (the
  mirror is disposable, local changes there are discarded by design); -DryRun is now fully
  offline and scans the PAYLOAD SOURCES instead of the stale mirror; guard additionally verifies
  the mirror's origin URL; path:hub is exempted for the four self-describing tooling files
  (secrets/PII/path:user remain universal); $AgentsSetup default derives from $env:OneDrive.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Message,
  [string]$Manifest    = "$PSScriptRoot\payload_manifest.json",
  [string]$RepoRoot    = 'C:\000.agentsetup_repo',
  [string]$RepoUrl     = 'https://github.com/hchoi41/agentsetup.git',
  [string]$AgentsSetup = (Join-Path $env:OneDrive '980.agents_setup'),
  [string]$Hub         = 'C:\000.thinkfirst_together_local',
  [string]$Playground  = 'C:\000.playground_local',
  [string]$StagedDir   = "$PSScriptRoot",
  [switch]$SkipScan,
  [switch]$NoPush,
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Step([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 0. PREFLIGHT
Step '0. Preflight'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git not on PATH' }
if (-not (Test-Path -LiteralPath $Manifest))              { throw "manifest not found: $Manifest" }
$mf = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json

$roots = @{
  'AGENTS_SETUP' = $AgentsSetup
  'HUB'          = $Hub
  'PLAYGROUND'   = $Playground
  'STAGED'       = $StagedDir
}
foreach ($k in $roots.Keys) {
  if (-not (Test-Path -LiteralPath $roots[$k])) { throw "root '$k' not found: $($roots[$k])" }
  Say ("  root {0,-13} {1}" -f $k, $roots[$k])
}
$name = git config --global user.name; $mail = git config --global user.email
if (-not $name -or -not $mail) { throw 'git identity not set (git config --global user.name / user.email)' }
Say "  identity      $name <$mail>"

# ------------------------------------------------------- 0.5 WIPE-TARGET GUARD (2026-08-20)
# Incident 2026-08-20: ~20 tracked files vanished from the OneDrive source while the publish
# pipeline was in play (restored via git). Step 2 below deletes EVERYTHING except .git under
# $RepoRoot, so this gate HARD-STOPS unless $RepoRoot is the intended disposable mirror
# OUTSIDE OneDrive. It runs BEFORE step 1 so even clone/pull never touches a wrong target,
# and it replaces the old warn-only check, which did not stop anything.
Step '0.5 Wipe-target guard'
$repoFull = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
$protectedRoots = @($AgentsSetup, $Hub, $Playground, $env:OneDrive) | Where-Object { $_ }
foreach ($p in $protectedRoots) {
  $pFull = [System.IO.Path]::GetFullPath($p).TrimEnd('\')
  if (($repoFull -eq $pFull) -or $repoFull.StartsWith("$pFull\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "WIPE GUARD: RepoRoot '$repoFull' is (or is inside) protected source '$pFull'. Step 2 would delete its contents. Refusing to run - use the disposable mirror outside OneDrive (default C:\000.agentsetup_repo)."
  }
}
if ($repoFull -match '(?i)\\OneDrive(\\|$)') {
  throw "WIPE GUARD: RepoRoot '$repoFull' is under a OneDrive path. Sync can corrupt .git and the wipe step must never touch synced folders. Refusing to run."
}
if ((Test-Path -LiteralPath $repoFull) -and -not (Test-Path -LiteralPath (Join-Path $repoFull '.git'))) {
  throw "WIPE GUARD: RepoRoot '$repoFull' exists but has no .git working copy. Refusing to wipe a non-repo directory."
}
if (Test-Path -LiteralPath (Join-Path $repoFull '.git')) {
  $originUrl = (git -C $repoFull remote get-url origin 2>$null)
  if ($LASTEXITCODE -eq 0 -and $originUrl -and ("$originUrl".Trim() -ne $RepoUrl)) {
    throw "WIPE GUARD: RepoRoot '$repoFull' origin is '$originUrl' but expected '$RepoUrl'. Refusing to touch a different repository."
  }
}
Say "  guard passed: wipe target = $repoFull" 'Green'

# ---------------------------------------------------------------- 1. CLONE OR PULL
Step '1. Working copy'
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.git'))) {
  Say "  no working copy at $RepoRoot - cloning" 'Yellow'
  if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Split-Path $RepoRoot -Parent) | Out-Null
    git clone $RepoUrl $RepoRoot
    if ($LASTEXITCODE -ne 0) { throw "clone failed ($LASTEXITCODE)" }
    Push-Location $RepoRoot; git symbolic-ref HEAD refs/heads/main; Pop-Location
  }
} else {
  if ($DryRun) {
    Say '  -DryRun: skipping fetch/reset (mirror untouched; no network)' 'Yellow'
  } else {
    Push-Location $RepoRoot
    try {
      git fetch origin 2>&1 | ForEach-Object { Say "  $_" }
      if ($LASTEXITCODE -ne 0) { throw "git fetch failed ($LASTEXITCODE)" }
      git rev-parse --verify --quiet origin/main *> $null
      if ($LASTEXITCODE -eq 0) {
        git reset --hard origin/main 2>&1 | ForEach-Object { Say "  $_" }
        if ($LASTEXITCODE -ne 0) { throw "git reset --hard origin/main failed ($LASTEXITCODE)" }
        Say '  mirror synced to origin/main (local changes discarded - the mirror is disposable by design)' 'Green'
      } else {
        Say '  origin/main not found (empty remote) - keeping local state' 'Yellow'
      }
    } finally { Pop-Location }
  }
}
# ---------------------------------------------------------------- 2. MIRROR PAYLOAD
Step '2. Mirror payload'
if (-not $DryRun) {
  Get-ChildItem -LiteralPath $RepoRoot -Force |
    Where-Object { $_.Name -ne '.git' } |
    Remove-Item -Recurse -Force
}
$excl = @('__pycache__', '.pytest_cache', 'node_modules', '.venv', 'desktop.ini', 'Thumbs.db')
function Test-Excluded([string]$p) {
  foreach ($e in $excl) { if ($p -like "*\$e\*" -or $p -like "*\$e") { return $true } }
  if ($p -match '\.py[cod]$') { return $true }
  if ((Split-Path $p -Leaf) -like '~$*') { return $true }
  if ((Split-Path $p -Leaf) -like '*.bak_*') { return $true }  # rollback backups stay private (added 2026-08-20)
  if ((Split-Path $p -Leaf) -in @('run_migration_networking_relationships.ps1', 'migrate_v1_to_v2.ps1')) { return $true }
  return $false
}
$copied = 0; $skipped = 0
$payloadFiles = [System.Collections.Generic.List[string]]::new()
foreach ($item in $mf.include) {
  $parts = "$($item.src)".Split('/', 2)
  if (-not $roots.ContainsKey($parts[0])) { Say "  ?? unknown root in '$($item.src)'" 'Red'; continue }
  $src = if ($parts.Count -gt 1) { Join-Path $roots[$parts[0]] ($parts[1] -replace '/', '\') } else { $roots[$parts[0]] }
  $dst = Join-Path $RepoRoot ("$($item.dest)" -replace '/', '\')
  if (-not (Test-Path -LiteralPath $src)) { Say "  MISSING  $src" 'Red'; continue }

  if ($item.mode -eq 'file') {
    if (Test-Excluded $src) { $skipped++; continue }
    $payloadFiles.Add($src)
    if (-not $DryRun) {
      New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
      Copy-Item -LiteralPath $src -Destination $dst -Force
    }
    $copied++
  } else {
    foreach ($f in Get-ChildItem -LiteralPath $src -Recurse -File) {
      if (Test-Excluded $f.FullName) { $skipped++; continue }
      $payloadFiles.Add($f.FullName)
      $rel = $f.FullName.Substring($src.Length).TrimStart('\')
      $t   = Join-Path $dst $rel
      if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path (Split-Path $t -Parent) | Out-Null
        Copy-Item -LiteralPath $f.FullName -Destination $t -Force
      }
      $copied++
    }
  }
}
Say ("  {0} file(s) mirrored, {1} skipped by exclude rules" -f $copied, $skipped) 'Green'

# ---------------------------------------------------------------- 3. SCAN GATE
Step '3. Secret / personal-data gate  (repo is PUBLIC)'
if ($SkipScan) {
  Say '  SKIPPED by -SkipScan. You are asserting you reviewed this manually.' 'Red'
} else {
  $patterns = [ordered]@{
    # secret:key is built by concatenation so the pattern text cannot match its own source
    # now that this script publishes to the public repo (self-hit found in 2026-08-20 QA).
    'secret:key'    = ('sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|github' + '_pat_|AKIA[0-9A-Z]{16}|xox[baprs]-|-----BEGIN [A-Z ]*PRIVATE KEY')
    'secret:assign' = '(?i)\b(api[_-]?key|secret|passwd|password|access[_-]?token|client[_-]?secret)\s*[:=]\s*["'']?[A-Za-z0-9_\-\.]{12,}'
    'pii:email'     = '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
    'pii:krrn'      = '\b\d{6}\s*-\s*\d{7}\b'
    'pii:phone'     = '\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b'
    'path:user'     = 'C:\\Users\\[A-Za-z0-9._-]+'
    'path:hub'      = 'C:\\000\.'
  }
  # The self-describing tooling files document the hub system by design (install paths,
  # manifest roots, script defaults) - exempt them from path:hub ONLY. Secrets, PII, and
  # path:user stay universal. (2026-08-20b, after QA-5c; Bootstrap.ps1 added same day.)
  $pathHubExempt = @('README.md', 'repo_README.md', 'payload_manifest.json', 'Publish-AgentSetup.ps1', 'Get-AgentSetup.ps1', 'Bootstrap.ps1')
  if ($DryRun) {
    Say '  (DryRun scans the PAYLOAD SOURCE files; a real run scans the mirrored tree)' 'Yellow'
    $scanFiles = $payloadFiles | ForEach-Object { Get-Item -LiteralPath $_ }
  } else {
    $scanFiles = Get-ChildItem -LiteralPath $RepoRoot -Recurse -File |
      Where-Object { $_.FullName -notmatch '\\\.git\\' }
  }
  $scanFiles = $scanFiles | Where-Object { $_.Extension -notin @('.png','.jpg','.jpeg','.gif','.pdf','.zip','.xlsx','.pyc','.svg','.drawio') }
  $fail = 0
  foreach ($k in $patterns.Keys) {
    $targets = if ($k -eq 'path:hub') { $scanFiles | Where-Object { $_.Name -notin $pathHubExempt } } else { $scanFiles }
    $hits = $targets | Select-String -Pattern $patterns[$k] -AllMatches
    $n = ($hits | Measure-Object).Count
    if ($n -gt 0) {
      $fail += $n
      Say ("  FAIL {0,-14} {1} hit(s)" -f $k, $n) 'Red'
      $hits | Select-Object -First 5 | ForEach-Object {
        Say ("        {0}:{1}" -f $_.Path.Replace($RepoRoot,''), $_.LineNumber) 'DarkGray'
      }
    } else {
      Say ("  ok   {0,-14} clean" -f $k) 'DarkGreen'
    }
  }
  if ($fail -gt 0) {
    throw "GATE FAILED: $fail hit(s). Nothing was committed. Fix the files or re-run with -SkipScan if every hit is a reviewed false positive."
  }
  Say '  GATE PASSED' 'Green'
}

# ---------------------------------------------------------------- 4. COMMIT + PUSH
Step '4. Commit and push'
if ($DryRun) { Say '  -DryRun: stopping before git add/commit/push.' 'Yellow'; return }
Push-Location $RepoRoot
try {
  git add -A
  $pending = git status --porcelain
  if (-not $pending) { Say '  nothing changed - no commit made.' 'Yellow'; return }
  Say ("  {0} path(s) staged" -f ($pending | Measure-Object).Count)
  git commit -m $Message
  if ($LASTEXITCODE -ne 0) { throw "commit failed ($LASTEXITCODE)" }
  if ($NoPush) { Say '  -NoPush: committed locally, not pushed.' 'Yellow'; return }
  git push -u origin main
  if ($LASTEXITCODE -ne 0) { throw "push failed ($LASTEXITCODE) - check gh auth status / credential helper" }
  Say '  pushed to origin/main' 'Green'
} finally { Pop-Location }
