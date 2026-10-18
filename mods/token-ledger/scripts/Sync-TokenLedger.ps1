<#
.SYNOPSIS
  Copies token-ledger's shared records into your private data repository and pushes them.

.DESCRIPTION
  Run it yourself when you want the records backed up on GitHub. The mod never runs git;
  this script is the only step that sends records anywhere, and only to the repository
  you clone into -RepoDir (it refuses a repository that GitHub reports as public).

  The shared folder holds trimmed records only (no paths, commands or error text):
  <ShareDir>/<machine>/<session>.jsonl. They are copied to <RepoDir>/token-ledger/.

.EXAMPLE
  pwsh -NoProfile -File .\Sync-TokenLedger.ps1 -ShareDir "$env:OneDrive\AgentHub\token-ledger-share" -RepoDir "$env:LOCALAPPDATA\AgentSetup\agentsetup_token_optimizer_data"
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [Parameter(Mandatory)][string]$ShareDir,
  [Parameter(Mandatory)][string]$RepoDir,
  [string]$Message = "token-ledger: sync $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ShareDir -PathType Container)) { throw "Shared folder not found: $ShareDir" }
if (-not (Test-Path -LiteralPath (Join-Path $RepoDir '.git') -PathType Container)) { throw "Not a git clone: $RepoDir (clone your private data repository there first)" }

$remote = (& git -C $RepoDir remote get-url origin).Trim()
if ($remote -match 'github\.com[:/](.+?)(\.git)?$' -and (Get-Command gh -ErrorAction SilentlyContinue)) {
  $visibility = (& gh repo view $Matches[1] --json visibility -q .visibility 2>$null)
  if ($visibility -and $visibility -ne 'PRIVATE') { throw "Refusing to push records to a $visibility repository: $remote" }
}

& git -C $RepoDir pull --ff-only --quiet
$target = Join-Path $RepoDir 'token-ledger'
$copied = 0
foreach ($file in Get-ChildItem -LiteralPath $ShareDir -Filter '*.jsonl' -Recurse -File) {
  $rel = [IO.Path]::GetRelativePath($ShareDir, $file.FullName)
  $dest = Join-Path $target $rel
  New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
  if (-not (Test-Path -LiteralPath $dest) -or (Get-Item -LiteralPath $dest).LastWriteTimeUtc -lt $file.LastWriteTimeUtc) {
    Copy-Item -LiteralPath $file.FullName -Destination $dest -Force
    $copied++
  }
}
& git -C $RepoDir add -- token-ledger
& git -C $RepoDir diff --cached --quiet
if ($LASTEXITCODE -eq 0) { Write-Host "No new records ($copied files checked)."; return }
if ($PSCmdlet.ShouldProcess($remote, "commit and push $copied record files")) {
  & git -C $RepoDir commit --quiet -m $Message
  & git -C $RepoDir push --quiet
  Write-Host "Pushed $copied record files to $remote."
}
