<#
  Check-Additive.ps1 — deterministic "documentation-only" gate for retrofit jobs.

  PASS (exit 0) iff the change from -Base to -New is purely ADDITIVE: every meaningful
  line of the protected region of Base still appears (whitespace-normalized) somewhere
  in New. A deleted or reworded base line => FAIL. This mechanically catches design
  drift (e.g. a renumbered/reworded locked decision) without any LLM judgment.

  -IgnoreBefore : a regex marking where the protected region BEGINS in Base. Lines above
                  the first match are exempt (that is the title/metadata header, which
                  legitimately changes on a version bump). Default: first Markdown H2 ('^##\s').
  -RequireId    : optional tokens that MUST be present verbatim in New (belt-and-suspenders).

  Relocation-safe: moving a block (e.g. a changelog to an appendix) keeps its lines, so it passes.
  Addition-safe : new lines in New are ignored; only Base lines are required to survive.

  Exit: 0 = additive OK · 1 = non-additive (deletions/rewording) · 2 = a file was missing.
#>
param(
  [Parameter(Mandatory)][string]$Base,
  [Parameter(Mandatory)][string]$New,
  [string]$IgnoreBefore = '^##\s',
  [string[]]$RequireId = @(),
  [string[]]$AllowMissing = @()   # base lines matching any of these regexes MAY change/disappear (known cosmetic edits, e.g. a version-footer)
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Base)) { Write-Host "BASE not found: $Base"; exit 2 }
if (-not (Test-Path -LiteralPath $New))  { Write-Host "NEW not found: $New";  exit 2 }

function Norm($s) { ($s -replace '\s+', ' ').Trim() }

$newSet = @{}
foreach ($l in Get-Content -LiteralPath $New) { $n = Norm $l; if ($n) { $newSet[$n] = $true } }

$baseAll = @(Get-Content -LiteralPath $Base)
$start = 0
if ($IgnoreBefore) {
  for ($k = 0; $k -lt $baseAll.Count; $k++) { if ($baseAll[$k] -match $IgnoreBefore) { $start = $k; break } }
}

$missing = New-Object System.Collections.Generic.List[string]
for ($k = $start; $k -lt $baseAll.Count; $k++) {
  $n = Norm $baseAll[$k]; if (-not $n) { continue }
  $exempt = $false
  foreach ($pat in $AllowMissing) { if ("$pat" -and ($baseAll[$k] -match $pat)) { $exempt = $true; break } }
  if ($exempt) { continue }
  if (-not $newSet.ContainsKey($n)) { $missing.Add($baseAll[$k]) }
}

$idMissing = @()
foreach ($id in $RequireId) { if (-not (Select-String -LiteralPath $New -SimpleMatch $id -Quiet)) { $idMissing += $id } }

if ($missing.Count -eq 0 -and $idMissing.Count -eq 0) {
  Write-Host "ADDITIVE OK — every protected base line (from /$IgnoreBefore/ onward) preserved in NEW; required IDs present."
  exit 0
}
if ($missing.Count) {
  Write-Host "NON-ADDITIVE — $($missing.Count) protected base line(s) deleted or reworded in NEW:"
  $missing | Select-Object -First 40 | ForEach-Object { Write-Host "  - $_" }
}
if ($idMissing.Count) { Write-Host "MISSING REQUIRED IDs: $($idMissing -join ', ')" }
exit 1
