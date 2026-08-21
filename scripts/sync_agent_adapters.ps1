[CmdletBinding()]
param(
  [string]$WorkspaceRoot,
  [switch]$DryRun,
  [switch]$CopyOnly
)
$ErrorActionPreference = 'Stop'

# Resolve the workspace root from the script's own location. $PSScriptRoot is empty under some
# hosts (dot-sourcing, certain remote/MCP invocations), so fall back to $MyInvocation and only
# then to the CWD — and fail loudly rather than silently syncing into the wrong tree.
if (-not $WorkspaceRoot) {
  $scriptDir = $PSScriptRoot
  if (-not $scriptDir -and $MyInvocation.MyCommand.Path) {
    $scriptDir = Split-Path $MyInvocation.MyCommand.Path -Parent
  }
  $WorkspaceRoot = if ($scriptDir) { Split-Path $scriptDir -Parent } else { (Get-Location).Path }
}

function Write-Utf8NoBom([string]$path,[string]$content){
  $dir = Split-Path $path -Parent
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  [System.IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-NormalizedPath([string]$path) {
  return [System.IO.Path]::GetFullPath($path)
}

function Remove-Generated([string]$path){
  # Safe removal: if the path is a junction/symlink (reparse point), delete the LINK only.
  # NEVER use Remove-Item -Recurse on a junction — on Windows PowerShell 5.1 it can recurse
  # into the target and delete the canonical skills/ content.
  # Guard on Get-Item, not Test-Path: a BROKEN junction (target gone) can read as "absent" via
  # Test-Path on some PowerShell versions while still occupying the path, which then makes a later
  # New-Item -ItemType Junction fail. Get-Item still returns the reparse point so we can clear it.
  $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
  if ($item) {
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
      if ($item.PSIsContainer) { [System.IO.Directory]::Delete($item.FullName, $false) }
      else { [System.IO.File]::Delete($item.FullName) }
    } else {
      Remove-Item -LiteralPath $item.FullName -Recurse -Force
    }
    return
  }
  # Pathological: the entry occupies the path but Get-Item also failed — clear it non-recursively
  # (a junction delete never touches the target; no-op if the path is truly absent).
  try { [System.IO.Directory]::Delete($path, $false) } catch {}
}

$WorkspaceRoot = Get-NormalizedPath $WorkspaceRoot
$manifestPath = Join-Path $WorkspaceRoot '00.ABOUT/AGENT_TARGETS.json'
if (-not (Test-Path $manifestPath)) {
  throw "Workspace root '$WorkspaceRoot' has no 00.ABOUT/AGENT_TARGETS.json. Re-run with an explicit -WorkspaceRoot pointing at the hub."
}
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$skillsDir = Join-Path $WorkspaceRoot $manifest.canonical_skill_dir
$skills = Get-ChildItem $skillsDir -Directory -ErrorAction SilentlyContinue |
  Where-Object { Test-Path (Join-Path $_.FullName 'SKILL.md') } |
  Sort-Object Name

$ledgerPath = Join-Path $WorkspaceRoot 'scripts/.agent_adapters_ledger.json'
$oldLedger = @()
if (Test-Path $ledgerPath) {
  # Windows PowerShell 5.1's ConvertFrom-Json emits a JSON array as a SINGLE object, so piping it
  # straight into @(...) yields one Object[] element instead of N entries. Assign first, then wrap.
  # Without this, $_.path member-enumerates to a string[], the [string] cast space-joins it, and
  # Get-NormalizedPath throws NotSupportedException ("The given path's format is not supported").
  # Only reproduces once a ledger exists, which is why the first run succeeded and every run after failed.
  $parsedLedger = Get-Content $ledgerPath -Raw | ConvertFrom-Json
  $oldLedger = @($parsedLedger)
}
$oldPaths = @($oldLedger | Where-Object { $_.path } | ForEach-Object { Get-NormalizedPath ([string]$_.path) })
$newLedger = New-Object System.Collections.Generic.List[object]
$plannedPaths = New-Object System.Collections.Generic.List[string]
$plan = New-Object System.Collections.Generic.List[string]

function Get-SkillMeta($skillDir){
  $raw = Get-Content (Join-Path $skillDir.FullName 'SKILL.md') -Raw
  $name = $skillDir.Name
  $desc = ''
  if ($raw -match '(?m)^name:\s*(.+?)\s*$') { $name = $Matches[1].Trim().Trim('"') }
  if ($raw -match '(?m)^description:\s*(.+?)\s*$') { $desc = $Matches[1].Trim().Trim('"') }
  $body = $raw -replace '(?s)^---.*?---\s*',''
  [pscustomobject]@{ name=$name; description=$desc; body=$body; raw=$raw }
}

function Add-LedgerEntry([string]$path,[string]$target,[string]$mode) {
  $newLedger.Add([pscustomobject]@{
    path = (Get-NormalizedPath $path)
    target = $target
    mode = $mode
  })
}

function Test-LedgerOwned([string]$path) {
  $normalized = Get-NormalizedPath $path
  return $oldPaths -contains $normalized
}

function Set-Generated($path,$content,$target){
  if ((Test-Path $path) -and -not (Test-LedgerOwned $path)) {
    $plan.Add("SKIP (hand-authored, not in ledger): $path")
    return
  }
  $plannedPaths.Add((Get-NormalizedPath $path))
  if ($DryRun) {
    $plan.Add("WRITE: $path")
    return
  }
  Write-Utf8NoBom $path $content
  Add-LedgerEntry $path $target 'generate'
}

function Set-Link($link,$targetDir,$target){
  if ((Test-Path $link) -and -not (Test-LedgerOwned $link)) {
    $plan.Add("SKIP (exists, not in ledger): $link")
    return
  }
  $plannedPaths.Add((Get-NormalizedPath $link))
  if ($DryRun) {
    $plan.Add("LINK: $link -> $targetDir")
    return
  }
  Remove-Generated $link
  $parent = Split-Path $link -Parent
  if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
  $mode = 'junction'
  try {
    if ($CopyOnly) { throw 'copy-only' }
    New-Item -ItemType Junction -Path $link -Target $targetDir -ErrorAction Stop | Out-Null
  } catch {
    Copy-Item $targetDir $link -Recurse -Force
    $mode = 'copy'
  }
  Add-LedgerEntry $link $target $mode
}

foreach ($t in $manifest.targets) {
  if (-not $t.enabled) { continue }
  if ($t.mechanism -eq 'skill-native') {
    foreach ($inst in $t.install) {
      foreach ($s in $skills) {
        Set-Link (Join-Path $WorkspaceRoot (Join-Path $inst $s.Name)) $s.FullName $t.id
      }
    }
  } elseif ($t.mechanism -eq 'adapter') {
    $fmt = $manifest.formats.($t.format)
    $tpl = Get-Content (Join-Path $WorkspaceRoot $fmt.template) -Raw
    foreach ($s in $skills) {
      $m = Get-SkillMeta $s
      # YAML double-quoted frontmatter (pointer formats) needs \ and " escaped; full-content body uses raw.
      $descForTpl = if ($fmt.kind -eq 'pointer') { $m.description.Replace('\','\\').Replace('"','\"') } else { $m.description }
      $out = $tpl.Replace('{{skill_name}}',$m.name).Replace('{{skill_description}}',$descForTpl).Replace('{{skill_body}}',$m.body)
      foreach ($inst in $t.install) {
        $path = Join-Path $WorkspaceRoot (Join-Path $inst ($s.Name + $fmt.ext))
        Set-Generated $path $out $t.id
      }
    }
    if ($t.instructions_file) {
      $agents = Get-Content (Join-Path $WorkspaceRoot $manifest.agents_md) -Raw
      $banner = "<!-- Generated from AGENTS.md by sync_agent_adapters.ps1. .github/ is a local Copilot folder; nothing is published to github.com. -->`n`n"
      Set-Generated (Join-Path $WorkspaceRoot $t.instructions_file) ($banner + $agents) $t.id
    }
    if ($fmt.emit_readme) {
      $idx = ($skills | ForEach-Object { "- $($_.Name).md" }) -join "`n"
      $readme = "# Microsoft Copilot packs`nFull self-contained procedures. Attach in Copilot in Windows, or publish this folder to OneDrive for M365 grounding.`n`n$idx`n"
      Set-Generated (Join-Path $WorkspaceRoot (Join-Path $t.install[0] 'README.md')) $readme $t.id
    }
  }
}

foreach ($old in $oldLedger) {
  if (-not $old.path) { continue }
  $oldPath = Get-NormalizedPath ([string]$old.path)
  if ($plannedPaths -notcontains $oldPath) {
    # Guard via Get-Item, not Test-Path, so a dangling junction (de-listed renamed/removed skill)
    # is still cleaned on PowerShell versions where Test-Path under-reports broken reparse points.
    if ($DryRun) {
      if (Get-Item -LiteralPath $oldPath -Force -ErrorAction SilentlyContinue) { $plan.Add("REMOVE (de-listed): $oldPath") }
    } else {
      Remove-Generated $oldPath   # self-guards: clears dangling junctions, no-ops if truly absent
    }
  }
}

if ($DryRun) {
  Write-Host "=== adapter sync plan (dry-run) ===" -ForegroundColor Cyan
  $plan | ForEach-Object { Write-Host "  $_" }
  return
}

$sorted = $newLedger | Sort-Object path
Write-Utf8NoBom $ledgerPath (($sorted | ConvertTo-Json -Depth 5))
Write-Host ("adapter sync: {0} surfaces written." -f $sorted.Count) -ForegroundColor Green
