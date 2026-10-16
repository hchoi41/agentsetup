#Requires -Version 7.0
<#
.SYNOPSIS
Install the project-first agent setup into a local Windows folder.
.DESCRIPTION
Use -SourceRoot for an already downloaded or reviewed payload (no Git/network needed).
Otherwise the clean cache is cloned or updated from RepoUrl. Existing customizations
are preserved; -Force explicitly replaces conflicting files after backing them up.
User environment settings change only with -SetUserEnvironment.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Destination,
  [string]$RepoUrl = 'https://github.com/hchoi41/agentsetup.git',
  [string]$CacheRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AgentSetup\repo'),
  [string]$SourceRoot,
  [switch]$Force,
  [switch]$DryRun,
  [switch]$CopyAdapters,
  [switch]$SetUserEnvironment
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'This installer currently supports Windows only.' }
function Say([string]$Message) { Write-Host $Message }
function Full([string]$Path) {
  $absolute=[IO.Path]::GetFullPath($Path)
  if ($absolute.TrimEnd('\','/') -eq [IO.Path]::GetPathRoot($absolute).TrimEnd('\','/')) { throw 'Drive or share roots are not installation folders.' }
  return $absolute.TrimEnd('\','/')
}
function Within([string]$Path,[string]$Root) {
  return $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)
}
function Check-Local([string]$Path) {
  if ($Path -match '(?i)\\OneDrive(?: - [^\\]+)?(?:\\|$)') { throw 'Use a local folder outside OneDrive.' }
  foreach ($syncRoot in @($env:OneDrive,$env:OneDriveCommercial,$env:OneDriveConsumer)) {
    if ($syncRoot -and (Within $Path (Full $syncRoot))) { throw 'Use a local folder outside OneDrive.' }
  }
}
function Check-Chain([string]$Path) {
  $current=$Path
  while ($current) {
    $item=Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
    if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Reparse point in install path: $current" }
    $parent=Split-Path -Parent $current
    if ($parent -eq $current) { break }
    $current=$parent
  }
}
function Hash([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
$Destination=Full $Destination
Check-Local $Destination
Check-Chain $Destination
if (-not (Get-Command pwsh -ErrorAction SilentlyContinue)) { throw 'PowerShell 7 (pwsh) is required.' }
if (-not $SourceRoot) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is required for downloading; use -SourceRoot for an extracted payload.' }
  $CacheRoot=Full $CacheRoot
  Check-Local $CacheRoot
  Check-Chain $CacheRoot
  if ((Within $Destination $CacheRoot) -or (Within $CacheRoot $Destination)) { throw 'Destination and cache must be separate folders.' }
  if (Test-Path -LiteralPath (Join-Path $CacheRoot '.git')) {
    $origin=(& git -C $CacheRoot remote get-url origin)
    if ($LASTEXITCODE -ne 0 -or "$origin".Trim() -ne $RepoUrl) { throw 'Cache origin does not match RepoUrl.' }
    $dirty=@(& git -C $CacheRoot status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $dirty.Count) { throw 'Cache has local changes; preserve them and use a separate cache.' }
    if ($DryRun) { Say 'DRY RUN: would update the clean cache using git pull --ff-only.' }
    else {
      & git -C $CacheRoot pull --ff-only
      if ($LASTEXITCODE -ne 0) { throw 'Could not fast-forward the cache; no installation performed.' }
    }
  } elseif ($DryRun) {
    Say 'DRY RUN: would clone the repository, validate its payload, and install. Nothing written.'
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
Check-Chain $SourceRoot
if ((Within $Destination $SourceRoot) -or (Within $SourceRoot $Destination)) { throw 'Destination and source must be separate folders.' }
if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) { throw 'SourceRoot does not exist.' }
foreach ($legacy in @('01.INPUT','11.OUTPUT_ROUGH','03.OUTPUT_FINAL')) {
  if (Test-Path -LiteralPath (Join-Path $Destination $legacy)) { throw 'This is a legacy-layout workspace. Use its governed layout migration first; the installer does not migrate user work.' }
}

# Validate a complete, contained copy plan before creating destination files.
$newHub = -not ((Test-Path -LiteralPath (Join-Path $Destination 'AGENTS.md')) -or (Test-Path -LiteralPath (Join-Path $Destination '00.ABOUT/AGENTSETUP_INSTALL.json')))
$plan=[ordered]@{}
function Add-File([string]$From,[string]$To,[bool]$Preserve=$false) {
  $src=Join-Path $SourceRoot $From
  if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "Required payload file missing: $From" }
  Check-Chain $src
  if ($plan.Contains($To)) { throw "Duplicate installation target: $To" }
  $plan[$To]=@{Source=$src;Preserve=$Preserve}
}
Add-File 'template/AGENTS.md' 'AGENTS.md' $true
Add-File 'template/.gitignore' '.gitignore' $true
foreach ($doc in @('PROJECT_GOVERNANCE.md','CLAUDE.md','orchestration_protocol.md','claude_cowork_hdd_ingestion_protocol.md')) {
  Add-File ('governance/'+$doc) ('00.ABOUT/'+$doc) ($doc -eq 'CLAUDE.md')
}
Add-File 'AGENT_TARGETS.json' '00.ABOUT/AGENT_TARGETS.json' $true
Add-File 'START-HERE.md' '00.ABOUT/AGENT_CLI_ONBOARDING.md'
Add-File 'template/10.OPERATION/OPERATIONS_DASHBOARD.md' '10.OPERATION/OPERATIONS_DASHBOARD.md' $true
foreach ($tree in @('skills','scripts','orchestrate','tests')) {
  $root=Join-Path $SourceRoot $tree
  if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw "Required payload folder missing: $tree" }
  Check-Chain $root
  foreach ($entry in Get-ChildItem -LiteralPath $root -Force -Recurse) {
    if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point in payload: $($entry.Name)" }
    if ($entry.PSIsContainer) { continue }
    $rel=[IO.Path]::GetRelativePath($SourceRoot,$entry.FullName).Replace('\','/')
    if ($rel -match '(?i)(^|/)(__pycache__|\.pytest_cache|node_modules|\.venv)(/|$)|\.bak(?:_|$)|\.py[cod]$') { continue }
    Add-File $rel $rel
  }
}
foreach ($skill in @('start-project','project-complete','cloud-sync','operationalize','projectify')) {
  if (-not $plan.Contains('skills/'+$skill+'/SKILL.md')) { throw "Core skill missing: $skill" }
}
foreach ($required in @('scripts/operation_switch.ps1','scripts/deploy_new_project.ps1','scripts/sync_agent_adapters.ps1','scripts/adapter_templates/prompt-file.md','scripts/adapter_templates/self-contained.md','scripts/Setup-AgentCLIs.ps1','scripts/Assert-AgentCliSelection.ps1','skills/setup-agents/SKILL.md','scripts/operation_name.ps1','skills/operationalize/scripts/operation_name.ps1','skills/projectify/scripts/operation_name.ps1')) {
  if (-not $plan.Contains($required)) { throw "Required runtime file missing: $required" }
}
$seeds=[ordered]@{
  '00.ABOUT/PROJECT_INDEX.md'="| project_name | status | latest_ram_path | must_read | current_state_summary | deadline |`n| --- | --- | --- | --- | --- | --- |`n"
  '00.ABOUT/PROJECT_REGISTRY.json'="{`"version`":1,`"projects`":[]}`n"
  '13.MEMORY_HDD/ingest/ingest_manifest.jsonl'=''
  '15.WIKI/log.md'="# Wiki Log`n"
  'CLAUDE.md'=(@('@AGENTS.md','','@00.ABOUT/CLAUDE.md','') -join [Environment]::NewLine)
}
if ($newHub) {
  $seeds['00.ABOUT/AGENT_CLI_SETUP.json']="{`"schema_version`":1,`"selection_state`":`"pending`",`"selected_agents`":[],`"tools`":{},`"authentication`":`"not_checked`"}`n"
}
foreach ($rel in @($plan.Keys)+@($seeds.Keys)) {
  $dest=Join-Path $Destination $rel
  Check-Chain $dest
  if (Test-Path -LiteralPath $dest -PathType Container) { throw "File target is occupied by a directory: $rel" }
}
$ledgerPath=Join-Path $Destination '00.ABOUT/AGENTSETUP_INSTALL.json'
$prior=@{}
if (Test-Path -LiteralPath $ledgerPath) {
  Check-Chain $ledgerPath
  $old=Get-Content -LiteralPath $ledgerPath -Raw | ConvertFrom-Json -AsHashtable
  if ($old.schema -ne 1 -or $old.files -isnot [System.Collections.IDictionary]) { throw 'Unrecognized install ledger; preserve it and inspect before upgrading.' }
  $prior=$old.files
}

# A copied adapter ledger must never let regeneration act outside its output roots.
function Adapter-Path([string]$Relative) {
  if (-not $Relative -or [IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[\\/])\.\.([\\/]|$)|:') { throw 'Adapter paths must be relative and contained in this installation.' }
  $absolute=Full (Join-Path $Destination $Relative)
  if ($absolute -eq $Destination -or -not (Within $absolute $Destination)) { throw 'Adapter path escapes the installation.' }
  return $absolute
}
$targetsPath=Join-Path $Destination '00.ABOUT/AGENT_TARGETS.json'
$targetsOwned=(Test-Path -LiteralPath $targetsPath) -and $prior.ContainsKey('00.ABOUT/AGENT_TARGETS.json') -and $prior['00.ABOUT/AGENT_TARGETS.json'] -eq (Hash $targetsPath)
if ($Force -or $targetsOwned -or -not (Test-Path -LiteralPath $targetsPath)) { $targetsPath=$plan['00.ABOUT/AGENT_TARGETS.json'].Source }
$targets=Get-Content -LiteralPath $targetsPath -Raw | ConvertFrom-Json
Check-Chain (Adapter-Path $targets.canonical_skill_dir)
Check-Chain (Adapter-Path $targets.agents_md)
$adapterRoots=[Collections.Generic.List[string]]::new()
$adapterFiles=[Collections.Generic.List[string]]::new()
foreach ($target in $targets.targets) {
  if (-not $target.enabled) { continue }
  foreach ($relative in $target.install) {
    $outputRoot=Adapter-Path $relative
    Check-Chain $outputRoot
    $adapterRoots.Add($outputRoot)
  }
  if ($target.instructions_file) {
    $outputFile=Adapter-Path $target.instructions_file
    Check-Chain $outputFile
    $adapterFiles.Add($outputFile)
  }
  if ($target.mechanism -eq 'adapter') {
    $format=$targets.formats.($target.format)
    Check-Chain (Adapter-Path $format.template)
    if ($format.ext -notmatch '^\.[A-Za-z0-9.]+$') { throw 'Unsupported adapter filename extension.' }
  }
}
$adapterLedger=Join-Path $Destination 'scripts/.agent_adapters_ledger.json'
if (Test-Path -LiteralPath $adapterLedger) {
  Check-Chain $adapterLedger
  $adapterEntries=Get-Content -LiteralPath $adapterLedger -Raw | ConvertFrom-Json
  foreach ($entry in @($adapterEntries)) {
    if (-not $entry.path) { continue }
    $oldPath=Full ([string]$entry.path)
    $allowed=$adapterFiles.Contains($oldPath)
    foreach ($outputRoot in $adapterRoots) {
      if ($oldPath -ne $outputRoot -and (Within $oldPath $outputRoot)) { $allowed=$true }
    }
    if (-not $allowed) { throw 'Adapter ledger contains a path outside the configured adapter outputs; review it before upgrading.' }
    Check-Chain (Split-Path -Parent $oldPath)
    if ($entry.mode -eq 'generate') { Check-Chain $oldPath }
  }
}
if ($DryRun) {
  Say "DRY RUN: validated $($plan.Count) payload files; missing control files would be initialized; existing customizations preserved."
  Say 'DRY RUN: would rebuild agent adapters; no files or environment settings written.'
  return
}
$folders=@('00.ABOUT','00.ABOUT/must_read','01.PROJECT','02.INPUT_TEMPLATES','10.OPERATION','12.MEMORY_RAM','13.MEMORY_HDD/ingest','14.LESSONS_LEARNED','15.WIKI/wiki_template','workspaces')
foreach ($folder in $folders) { New-Item -ItemType Directory -Force -Path (Join-Path $Destination $folder) | Out-Null }
# Record the fresh-hub choice before copying AGENTS.md. A partial-install retry
# must not mistake a newly copied AGENTS.md for a pre-existing legacy hub.
if ($newHub) {
  $choicePath=Join-Path $Destination '00.ABOUT/AGENT_CLI_SETUP.json'
  if (-not (Test-Path -LiteralPath $choicePath)) {
    [IO.File]::WriteAllText($choicePath,$seeds['00.ABOUT/AGENT_CLI_SETUP.json'],[Text.UTF8Encoding]::new($false))
  }
}
$next=@{}; $kept=0; $written=0
$backupRoot=Join-Path $Destination ('13.MEMORY_HDD/agentsetup_install_'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
foreach ($rel in $plan.Keys) {
  $src=$plan[$rel].Source; $dest=Join-Path $Destination $rel; $wanted=Hash $src
  if (Test-Path -LiteralPath $dest) {
    $current=Hash $dest
    if ($current -eq $wanted) { $next[$rel]=$wanted; continue }
    $owned=$prior.ContainsKey($rel) -and $prior[$rel] -eq $current
    if (-not $Force -and -not $owned) {
      Say "KEEP local customization: $rel"; $kept++
      if ($prior.ContainsKey($rel)) { $next[$rel]=$prior[$rel] }
      continue
    }
    $backup=Join-Path $backupRoot $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
    Copy-Item -LiteralPath $dest -Destination $backup
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
  Copy-Item -LiteralPath $src -Destination $dest -Force
  if ((Hash $dest) -ne $wanted) { throw "Copy verification failed: $rel" }
  $next[$rel]=$wanted; $written++
}
foreach ($rel in $seeds.Keys) {
  $dest=Join-Path $Destination $rel
  if (-not (Test-Path -LiteralPath $dest)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    [IO.File]::WriteAllText($dest,$seeds[$rel],[Text.UTF8Encoding]::new($false))
  }
}
$adapterArgs=@('-NoProfile','-File',(Join-Path $Destination 'scripts/sync_agent_adapters.ps1'),'-WorkspaceRoot',$Destination)
if ($CopyAdapters) { $adapterArgs+='-CopyOnly' }
& pwsh @adapterArgs
if ($LASTEXITCODE -ne 0) { throw 'Adapter generation failed; installation is incomplete.' }
foreach ($skill in @('start-project','project-complete','cloud-sync','operationalize','projectify')) {
  foreach ($surface in @('skills','.claude/skills','.agents/skills','.opencode/skills')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Destination ($surface+'/'+$skill+'/SKILL.md')) -PathType Leaf)) { throw "Core skill is not addressable through $surface : $skill" }
  }
}
$record=@{schema=1;installed_utc=[DateTime]::UtcNow.ToString('o');files=$next}
[IO.File]::WriteAllText($ledgerPath,($record|ConvertTo-Json -Depth 5)+"`n",[Text.UTF8Encoding]::new($false))
if ($SetUserEnvironment) { [Environment]::SetEnvironmentVariable('AGENT_HUB_ROOT',$Destination,'User'); Say 'AGENT_HUB_ROOT set in the user environment.' }
Say "Installed project-first hub: $Destination ($written written; $kept local customizations kept)."
if ($kept) { Say 'Review preserved customizations against the new payload before using upgraded workflows.' }
Say 'Next: give 00.ABOUT/AGENT_CLI_ONBOARDING.md to the AI agent you already use. Choose one or several optional CLIs, or none.'
