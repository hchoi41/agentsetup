#Requires -Version 7.0
[CmdletBinding()]
param(
  [string]$SourceRoot=(Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
  [Parameter(Mandatory)][string]$FixtureRoot
)
$ErrorActionPreference='Stop'
$SourceRoot=[IO.Path]::GetFullPath($SourceRoot)
$FixtureRoot=[IO.Path]::GetFullPath($FixtureRoot)
if (Test-Path -LiteralPath $FixtureRoot) { throw 'Use a new, absent fixture directory. This test never cleans up an existing folder.' }
if ($FixtureRoot.StartsWith($SourceRoot.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Keep test artifacts outside the distributable repository.' }
New-Item -ItemType Directory -Path $FixtureRoot | Out-Null
$checks=[Collections.Generic.List[object]]::new()
$limitations=[Collections.Generic.List[string]]::new()
function Assert([bool]$Condition,[string]$Name) {
  if (-not $Condition) { throw "ASSERTION FAILED: $Name" }
  $checks.Add(@{name=$Name;passed=$true})
}
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }
function Run-Install([string]$Label,[string]$Target,[string]$Source,[string[]]$Extra=@(),[bool]$Success=$true) {
  $call=@('-NoProfile','-File',(Join-Path $SourceRoot 'Get-AgentSetup.ps1'),'-Destination',$Target,'-SourceRoot',$Source)+$Extra
  $output=& pwsh @call 2>&1
  $code=$LASTEXITCODE
  $output | Set-Content -LiteralPath (Join-Path $FixtureRoot ($Label+'.log')) -Encoding utf8
  Assert (($code -eq 0) -eq $Success) "$Label expected exit status (actual $code)"
}
$userEnvironmentBefore=[Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User')
$dest=Join-Path $FixtureRoot 'fresh hub with spaces'
Run-Install '01-dry-run' $dest $SourceRoot @('-DryRun')
Assert (-not (Test-Path -LiteralPath $dest)) 'dry run creates no destination'
Run-Install '02-clean-install' $dest $SourceRoot @('-CopyAdapters')
foreach ($folder in @('00.ABOUT/must_read','01.PROJECT','10.OPERATION','02.INPUT_TEMPLATES','12.MEMORY_RAM','13.MEMORY_HDD/ingest','14.LESSONS_LEARNED','15.WIKI/wiki_template','workspaces')) {
  Assert (Test-Path -LiteralPath (Join-Path $dest $folder) -PathType Container) "new folder $folder"
}
foreach ($old in @('01.INPUT','11.OUTPUT_ROUGH','03.OUTPUT_FINAL')) {
  Assert (-not (Test-Path -LiteralPath (Join-Path $dest $old))) "no legacy role $old"
}
Assert ((Hash (Join-Path $dest '00.ABOUT/PROJECT_GOVERNANCE.md')) -eq (Hash (Join-Path $SourceRoot 'governance/PROJECT_GOVERNANCE.md'))) 'governance matches payload bytes'
foreach ($skill in @('start-project','project-complete','cloud-sync','operationalize','projectify')) {
  $expected=Hash (Join-Path $SourceRoot ('skills/'+$skill+'/SKILL.md'))
  foreach ($surface in @('skills','.claude/skills','.agents/skills','.opencode/skills')) {
    Assert ((Hash (Join-Path $dest ($surface+'/'+$skill+'/SKILL.md'))) -eq $expected) "$skill addressable through $surface"
  }
  Assert (Test-Path -LiteralPath (Join-Path $dest ('.github/prompts/'+$skill+'.prompt.md'))) "$skill Copilot prompt exists"
  Assert (Test-Path -LiteralPath (Join-Path $dest ('microsoft_copilot/'+$skill+'.md'))) "$skill self-contained pack exists"
}
foreach ($file in @('scripts/operation_switch.ps1','scripts/deploy_new_project.ps1','skills/cloud-sync/scripts/sync_project.ps1','10.OPERATION/OPERATIONS_DASHBOARD.md','CLAUDE.md')) {
  Assert (Test-Path -LiteralPath (Join-Path $dest $file) -PathType Leaf) "runtime file $file"
}
$index=Get-Content -LiteralPath (Join-Path $dest '00.ABOUT/PROJECT_INDEX.md') -Raw
Assert ($index -match 'deadline') 'empty index contains deadline'
$registry=Get-Content -LiteralPath (Join-Path $dest '00.ABOUT/PROJECT_REGISTRY.json') -Raw | ConvertFrom-Json
Assert (@($registry.projects).Count -eq 0) 'new registry contains no personal projects'
$coreHash=Hash (Join-Path $dest 'skills/cloud-sync/scripts/sync_project.ps1')
Run-Install '03-repeat' $dest $SourceRoot @('-CopyAdapters')
Assert ((Hash (Join-Path $dest 'skills/cloud-sync/scripts/sync_project.ps1')) -eq $coreHash) 'repeat preserves runtime bytes'

# Model a prior installed stock version: it is unchanged relative to its ledger.
$installLedger=Join-Path $dest '00.ABOUT/AGENTSETUP_INSTALL.json'
$previous=Get-Content -LiteralPath $installLedger -Raw | ConvertFrom-Json -AsHashtable
foreach ($rel in @('AGENTS.md','.gitignore','00.ABOUT/CLAUDE.md','10.OPERATION/OPERATIONS_DASHBOARD.md')) {
  Add-Content -LiteralPath (Join-Path $dest $rel) -Value "`n# PRIOR STOCK VERSION" -Encoding utf8
  $previous.files[$rel]=(Hash (Join-Path $dest $rel)).ToLowerInvariant()
}
$previous | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $installLedger -Encoding utf8
Run-Install '03b-managed-stock-upgrade' $dest $SourceRoot @('-CopyAdapters')
foreach ($pair in @(@('AGENTS.md','template/AGENTS.md'),@('.gitignore','template/.gitignore'),@('00.ABOUT/CLAUDE.md','governance/CLAUDE.md'),@('10.OPERATION/OPERATIONS_DASHBOARD.md','template/10.OPERATION/OPERATIONS_DASHBOARD.md'))) {
  Assert ((Hash (Join-Path $dest $pair[0])) -eq (Hash (Join-Path $SourceRoot $pair[1]))) "managed stock upgrades $($pair[0])"
}

$custom=@('AGENTS.md','00.ABOUT/CLAUDE.md','scripts/deploy_new_project.ps1','10.OPERATION/OPERATIONS_DASHBOARD.md','00.ABOUT/PROJECT_INDEX.md')
$customHashes=@{}
foreach ($rel in $custom) {
  Add-Content -LiteralPath (Join-Path $dest $rel) -Value "`n# LOCAL CUSTOMIZATION TEST" -Encoding utf8
  $customHashes[$rel]=Hash (Join-Path $dest $rel)
}
$sentinel=Join-Path $dest 'workspaces/local-only.txt'
Set-Content -LiteralPath $sentinel -Value 'Local data stays here.' -Encoding utf8
$sentinelHash=Hash $sentinel
Run-Install '04-preserve-customization' $dest $SourceRoot @('-CopyAdapters')
foreach ($rel in $custom) { Assert ((Hash (Join-Path $dest $rel)) -eq $customHashes[$rel]) "customization preserved $rel" }
Assert ((Hash $sentinel) -eq $sentinelHash) 'unmanaged local data preserved'
Run-Install '05-explicit-force' $dest $SourceRoot @('-CopyAdapters','-Force')
Assert ((Hash (Join-Path $dest 'scripts/deploy_new_project.ps1')) -eq (Hash (Join-Path $SourceRoot 'scripts/deploy_new_project.ps1'))) 'explicit force restores selected payload'
$backups=@(Get-ChildItem -LiteralPath (Join-Path $dest '13.MEMORY_HDD') -Filter deploy_new_project.ps1 -File -Recurse)
Assert (@($backups | Where-Object {(Hash $_.FullName) -eq $customHashes['scripts/deploy_new_project.ps1']}).Count -eq 1) 'force retains exact customized original backup'
Assert ((Hash (Join-Path $dest '00.ABOUT/PROJECT_INDEX.md')) -eq $customHashes['00.ABOUT/PROJECT_INDEX.md']) 'force never resets local control data'

$legacy=Join-Path $FixtureRoot 'legacy hub'
New-Item -ItemType Directory -Path (Join-Path $legacy '01.INPUT') -Force | Out-Null
Run-Install '06-refuse-legacy' $legacy $SourceRoot @('-CopyAdapters') $false
Assert (-not (Test-Path -LiteralPath (Join-Path $legacy '01.PROJECT'))) 'legacy refusal creates no mixed layout'
Run-Install '07-refuse-overlap' $SourceRoot $SourceRoot @('-DryRun') $false
$oneDrive=Join-Path $FixtureRoot 'OneDrive - ExampleOrg/hub'
Run-Install '08-refuse-synced-target' $oneDrive $SourceRoot @('-DryRun') $false
Assert (-not (Test-Path -LiteralPath $oneDrive)) 'synced-target refusal writes nothing'

$incomplete=Join-Path $FixtureRoot 'incomplete payload'
New-Item -ItemType Directory -Path $incomplete | Out-Null
Copy-Item -LiteralPath (Join-Path $SourceRoot 'template') -Destination $incomplete -Recurse
$missingDest=Join-Path $FixtureRoot 'missing rejected'
Run-Install '09-refuse-incomplete' $missingDest $incomplete @('-CopyAdapters') $false
Assert (-not (Test-Path -LiteralPath $missingDest)) 'incomplete payload refused before destination writes'

Run-Install '11-refuse-drive-root' ([IO.Path]::GetPathRoot($FixtureRoot)) $SourceRoot @('-DryRun') $false
$outside=Join-Path $FixtureRoot 'outside adapter sentinel.txt'
Set-Content -LiteralPath $outside -Value 'Never delete this.' -Encoding utf8
$outsideHash=Hash $outside
$badLedgerDest=Join-Path $FixtureRoot 'bad ledger target'
New-Item -ItemType Directory -Path (Join-Path $badLedgerDest 'scripts') -Force | Out-Null
@(@{path=$outside;target='codex';mode='copy'}) | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $badLedgerDest 'scripts/.agent_adapters_ledger.json') -Encoding utf8
Run-Install '12-refuse-external-ledger' $badLedgerDest $SourceRoot @('-CopyAdapters') $false
Assert ((Hash $outside) -eq $outsideHash) 'external ledger target remains unchanged'
Assert (-not (Test-Path -LiteralPath (Join-Path $badLedgerDest '00.ABOUT'))) 'external ledger refused before install writes'
$badManifestDest=Join-Path $FixtureRoot 'bad adapter config'
New-Item -ItemType Directory -Path (Join-Path $badManifestDest '00.ABOUT') -Force | Out-Null
$badManifest=Get-Content -LiteralPath (Join-Path $SourceRoot 'AGENT_TARGETS.json') -Raw | ConvertFrom-Json
$badManifest.targets[0].install=@('../outside')
$badManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $badManifestDest '00.ABOUT/AGENT_TARGETS.json') -Encoding utf8
Run-Install '13-refuse-adapter-escape' $badManifestDest $SourceRoot @('-CopyAdapters') $false
Assert (-not (Test-Path -LiteralPath (Join-Path $badManifestDest 'AGENTS.md'))) 'adapter escape refused before install writes'

# Direct regeneration is also safe; it is a supported entrypoint without the installer.
$adapterLedgerPath=Join-Path $dest 'scripts/.agent_adapters_ledger.json'
$ledgerBytes=[IO.File]::ReadAllBytes($adapterLedgerPath)
$entries=Get-Content -LiteralPath $adapterLedgerPath -Raw | ConvertFrom-Json
@($entries)+@(@{path=$outside;target='codex';mode='copy'}) | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $adapterLedgerPath -Encoding utf8
$adapterOutput=& pwsh -NoProfile -File (Join-Path $dest 'scripts/sync_agent_adapters.ps1') -WorkspaceRoot $dest -CopyOnly 2>&1
$adapterCode=$LASTEXITCODE
$adapterOutput | Set-Content -LiteralPath (Join-Path $FixtureRoot '15-direct-adapter-ledger.log') -Encoding utf8
Assert ($adapterCode -ne 0 -and ($adapterOutput -join "`n") -match 'outside the configured adapter outputs') 'direct adapter refuses copied external ledger'
Assert ((Hash $outside) -eq $outsideHash) 'direct adapter leaves external target unchanged'
[IO.File]::WriteAllBytes($adapterLedgerPath,$ledgerBytes)
$junctionTarget=Join-Path $FixtureRoot 'junction refusal target'
New-Item -ItemType Directory -Path (Join-Path $junctionTarget '00.ABOUT') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $SourceRoot 'AGENT_TARGETS.json') -Destination (Join-Path $junctionTarget '00.ABOUT/AGENT_TARGETS.json')
$junctionCreated=$false
try { New-Item -ItemType Junction -Path (Join-Path $junctionTarget '.agents') -Target $dest -ErrorAction Stop | Out-Null; $junctionCreated=$true } catch {
  $limitations.Add('Synthetic adapter output-parent junction creation was unavailable; no elevation was requested.')
}
if ($junctionCreated) {
  $junctionOutput=& pwsh -NoProfile -File (Join-Path $dest 'scripts/sync_agent_adapters.ps1') -WorkspaceRoot $junctionTarget -CopyOnly 2>&1
  $junctionCode=$LASTEXITCODE
  $junctionOutput | Set-Content -LiteralPath (Join-Path $FixtureRoot '16-direct-adapter-junction.log') -Encoding utf8
  Assert ($junctionCode -ne 0 -and ($junctionOutput -join "`n") -match 'Reparse point in adapter path') 'direct adapter refuses redirected output parent'
  Assert (-not (Test-Path -LiteralPath (Join-Path $junctionTarget '.claude'))) 'junction refusal occurs before adapter writes'
}

$orchestrateOutput=& pwsh -NoProfile -File (Join-Path $dest 'orchestrate/Orchestrate.ps1') -HubRoot $dest -Config (Join-Path $dest 'orchestrate/configs/_template_code_job.json') -DryRun 2>&1
$orchestrateCode=$LASTEXITCODE
$orchestrateOutput | Set-Content -LiteralPath (Join-Path $FixtureRoot '17-orchestrate-dryrun.log') -Encoding utf8
Assert ($orchestrateCode -eq 0 -and ($orchestrateOutput -join "`n") -match 'DRY RUN') 'documented orchestrate path works with explicit installed hub root'
Assert (-not (Test-Path -LiteralPath (Join-Path $dest '_orchestrate'))) 'orchestrate dry run launches no jobs and writes no run directory'

$bootstrap7Target=Join-Path $FixtureRoot 'bootstrap7 dry run'
$bootstrap7Output=& pwsh -NoProfile -File (Join-Path $SourceRoot 'Bootstrap.ps1') -SourceRoot $SourceRoot -Destination $bootstrap7Target -DryRun 2>&1
$bootstrap7Code=$LASTEXITCODE
$bootstrap7Output | Set-Content -LiteralPath (Join-Path $FixtureRoot '14-bootstrap-ps7.log') -Encoding utf8
Assert ($bootstrap7Code -eq 0) 'PowerShell 7 bootstrap delegates successfully'
Assert (-not (Test-Path -LiteralPath $bootstrap7Target)) 'PowerShell 7 bootstrap dry run writes no destination'

$bootstrapTarget=Join-Path $FixtureRoot 'bootstrap dry run'
$bootstrapOutput=& powershell.exe -NoProfile -File (Join-Path $SourceRoot 'Bootstrap.ps1') -SourceRoot $SourceRoot -Destination $bootstrapTarget -DryRun 2>&1
$bootstrapCode=$LASTEXITCODE
$bootstrapOutput | Set-Content -LiteralPath (Join-Path $FixtureRoot '10-bootstrap-ps51.log') -Encoding utf8
if ($bootstrapCode -ne 0 -and ($bootstrapOutput -join "`n") -match 'running scripts is disabled|UnauthorizedAccess') {
  $limitations.Add('Windows PowerShell 5.1 wrapper execution is blocked by this host script-execution policy; no policy was changed. PowerShell 7 installer tests ran normally.')
} else { Assert ($bootstrapCode -eq 0) 'Windows PowerShell 5.1 bootstrap delegates to PowerShell 7' }
Assert (-not (Test-Path -LiteralPath $bootstrapTarget)) 'bootstrap dry run writes no destination'
Assert ([Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User') -eq $userEnvironmentBefore) 'all tests leave user environment unchanged'
$summary=@{status=if($limitations.Count){'PASS_WITH_RECORDED_LIMITATION'}else{'PASS'};checks=$checks;count=$checks.Count;limitations=$limitations;scope='Disposable local Windows install only. No company PC, account installation, network fetch, commit or publication tested.';fixture_root=$FixtureRoot}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $FixtureRoot 'validation.json') -Encoding utf8
Write-Host "PASS: $($checks.Count) checks. Evidence: $FixtureRoot"
