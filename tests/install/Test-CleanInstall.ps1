#Requires -Version 7.0
[CmdletBinding()]
param(
  [string]$SourceRoot=(Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
  [Parameter(Mandatory)][string]$FixtureRoot
)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
if (-not $IsWindows) { throw 'Windows and PowerShell 7 are required.' }
$SourceRoot=[IO.Path]::GetFullPath($SourceRoot).TrimEnd('\','/')
$FixtureRoot=[IO.Path]::GetFullPath($FixtureRoot).TrimEnd('\','/')
function Within([string]$Path,[string]$Root) {
  return $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)
}
if ((Within $FixtureRoot $SourceRoot) -or (Within $SourceRoot $FixtureRoot)) { throw 'Keep the fixture and release source in separate, non-overlapping folders.' }
if ($FixtureRoot -eq [IO.Path]::GetPathRoot($FixtureRoot).TrimEnd('\','/')) { throw 'Do not use a drive/share root as a fixture.' }
if (Get-Item -LiteralPath $FixtureRoot -Force -ErrorAction SilentlyContinue) { throw 'FixtureRoot must be new and absent. Existing folders are never removed.' }
$wrapper=Join-Path $SourceRoot 'Install-MaxAgentSystem.ps1'
$installer=Join-Path $SourceRoot 'Get-AgentSetup.ps1'
$bootstrap=Join-Path $SourceRoot 'Bootstrap.ps1'
foreach ($script in @($wrapper,$installer,$bootstrap)) {
  if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw "Required release script missing: $script" }
}
$pwsh=(Get-Command pwsh -ErrorAction Stop).Source
$checks=[Collections.Generic.List[object]]::new()
$runs=[Collections.Generic.List[object]]::new()
$userEnvironmentBefore=[Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User')
$processLocalAppDataBefore=$env:LOCALAPPDATA
$started=[DateTime]::UtcNow.ToString('o')
$status='RUNNING'
$failure=$null
New-Item -ItemType Directory -Path $FixtureRoot | Out-Null
function Hash([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Assert([bool]$Condition,[string]$Name) {
  $checks.Add([ordered]@{name=$Name;passed=$Condition})
  if (-not $Condition) { throw "ASSERTION FAILED: $Name" }
}
function Tree-Hashes([string]$Path) {
  $map=[ordered]@{}
  foreach ($file in Get-ChildItem -LiteralPath $Path -File -Recurse -Force | Sort-Object FullName) {
    $map[[IO.Path]::GetRelativePath($Path,$file.FullName)]=Hash $file.FullName
  }
  return $map
}
function Same-Hashes($Before,$After) {
  if ($Before.Count -ne $After.Count) { return $false }
  foreach ($key in $Before.Keys) {
    if (-not $After.Contains($key) -or $Before[$key] -cne $After[$key]) { return $false }
  }
  return $true
}
function Run-Child([string]$Label,[string]$Script,[string[]]$Arguments=@(),[bool]$Success=$true) {
  $log=Join-Path $FixtureRoot ($Label+'.log')
  $output=& $pwsh -NoProfile -File $Script @Arguments 2>&1
  $code=$LASTEXITCODE
  $output | Out-String -Width 400 | Set-Content -LiteralPath $log -Encoding utf8
  $runs.Add([ordered]@{label=$Label;script=$Script;arguments=$Arguments;exit_code=$code;log=$log})
  Assert (($code -eq 0) -eq $Success) "$Label expected exit status (actual $code); see $log"
  Assert ([Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User') -ceq $userEnvironmentBefore) "$Label leaves user AGENT_HUB_ROOT unchanged"
}
function Check-CleanInstall([string]$Destination,[string]$Label) {
  foreach ($role in @('00.ABOUT','00.ABOUT/must_read','01.PROJECT','02.INPUT_TEMPLATES','10.OPERATION','12.MEMORY_RAM','13.MEMORY_HDD/ingest','14.LESSONS_LEARNED','15.WIKI/wiki_template','workspaces')) {
    Assert (Test-Path -LiteralPath (Join-Path $Destination $role) -PathType Container) "$Label creates $role"
  }
  Assert (@('01.INPUT','11.OUTPUT_ROUGH','03.OUTPUT_FINAL' | Where-Object {Test-Path -LiteralPath (Join-Path $Destination $_)}).Count -eq 0) "$Label has no legacy role roots"
  $registry=Get-Content -LiteralPath (Join-Path $Destination '00.ABOUT/PROJECT_REGISTRY.json') -Raw | ConvertFrom-Json
  Assert (@($registry.projects).Count -eq 0) "$Label starts with an empty registry"
  foreach ($skill in @('start-project','project-complete','cloud-sync','operationalize','projectify')) {
    $expected=Hash (Join-Path $SourceRoot ('skills/'+$skill+'/SKILL.md'))
    Assert ((Hash (Join-Path $Destination ('skills/'+$skill+'/SKILL.md'))) -ceq $expected) "$Label canonical $skill matches source"
    foreach ($surface in @('.claude/skills','.agents/skills','.opencode/skills')) {
      $dir=Join-Path $Destination ($surface+'/'+$skill)
      $item=Get-Item -LiteralPath $dir -Force
      Assert (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) "$Label defaults to copied $surface/$skill"
      Assert ((Hash (Join-Path $dir 'SKILL.md')) -ceq $expected) "$Label adapter $surface/$skill matches canonical"
    }
  }
  Assert (Test-Path -LiteralPath (Join-Path $Destination '10.OPERATION/OPERATIONS_DASHBOARD.md')) "$Label includes operations dashboard"
}
try {
  # Missing parents and spaces are intentional; DryRun must not create any of them.
  $nestedParent=Join-Path $FixtureRoot 'missing parent with spaces'
  $fresh=Join-Path $nestedParent 'second missing parent/fresh hub'
  Run-Child '01-wrapper-dry-run-absent-parents' $wrapper @('-Destination',$fresh,'-DryRun')
  Assert (-not (Test-Path -LiteralPath $nestedParent)) 'wrapper DryRun creates neither destination nor absent parents'
  Run-Child '02-wrapper-clean-absent-parents' $wrapper @('-Destination',$fresh)
  Check-CleanInstall $fresh 'explicit clean'

  # The fresh-only entry point must refuse repeat use without touching installed bytes.
  $beforeRepeat=Tree-Hashes $fresh
  Run-Child '03-wrapper-repeat-refuses' $wrapper @('-Destination',$fresh) $false
  Assert (Same-Hashes $beforeRepeat (Tree-Hashes $fresh)) 'wrapper repeat refusal preserves every installed file'
  Run-Child '04-existing-installer-upgrade-continues' $installer @('-Destination',$fresh,'-SourceRoot',$SourceRoot,'-CopyAdapters')
  Assert ((Hash (Join-Path $fresh 'skills/start-project/SKILL.md')) -ceq (Hash (Join-Path $SourceRoot 'skills/start-project/SKILL.md'))) 'existing installer upgrade still keeps canonical skill accessible'

  $empty=Join-Path $FixtureRoot 'existing empty folder'
  New-Item -ItemType Directory -Path $empty | Out-Null
  Run-Child '05-wrapper-existing-empty-allowed' $wrapper @('-Destination',$empty)
  Assert (Test-Path -LiteralPath (Join-Path $empty '00.ABOUT/AGENTSETUP_INSTALL.json')) 'existing empty destination receives installation ledger'

  foreach ($case in @('visible nonempty','hidden only')) {
    $dest=Join-Path $FixtureRoot $case
    New-Item -ItemType Directory -Path $dest | Out-Null
    $sentinel=Join-Path $dest 'keep.txt'
    [IO.File]::WriteAllText($sentinel,"KEEP $case",[Text.UTF8Encoding]::new($false))
    if ($case -eq 'hidden only') { (Get-Item -LiteralPath $sentinel).Attributes=[IO.FileAttributes]::Hidden }
    $before=Hash $sentinel
    Run-Child ('06-refuse-'+$case.Replace(' ','-')) $wrapper @('-Destination',$dest) $false
    Assert ((Hash $sentinel) -ceq $before) "$case refusal preserves sentinel bytes"
    Assert (@(Get-ChildItem -LiteralPath $dest -Force).Count -eq 1) "$case refusal adds no installed files"
  }
  $fileDest=Join-Path $FixtureRoot 'occupied destination.txt'
  [IO.File]::WriteAllText($fileDest,'KEEP file target',[Text.UTF8Encoding]::new($false))
  $fileHash=Hash $fileDest
  Run-Child '07-refuse-file-destination' $wrapper @('-Destination',$fileDest) $false
  Assert ((Hash $fileDest) -ceq $fileHash) 'file-destination refusal preserves exact bytes'

  # Source overlap is a DryRun refusal; the source payload must remain untouched.
  $overlap=Join-Path $SourceRoot ('clean-install-overlap-probe-'+[Guid]::NewGuid().ToString('N'))
  Assert (-not (Test-Path -LiteralPath $overlap)) 'source-overlap probe starts absent'
  Run-Child '08-refuse-source-overlap' $wrapper @('-Destination',$overlap,'-DryRun') $false
  Assert (-not (Test-Path -LiteralPath $overlap)) 'source-overlap refusal creates nothing in payload'

  # Isolate the default destination in a CHILD process only, and restore its env value.
  $defaultHarness=Join-Path $FixtureRoot 'Invoke-IsolatedDefault.ps1'
  @'
#Requires -Version 7.0
param([string]$Wrapper,[string]$LocalAppData,[string]$AuditPath)
$ErrorActionPreference='Stop'
$prior=[Environment]::GetEnvironmentVariable('LOCALAPPDATA','Process')
$code=0
try {
  [Environment]::SetEnvironmentVariable('LOCALAPPDATA',$LocalAppData,'Process')
  & $Wrapper
  if (-not $?) { $code=1 }
} catch {
  Write-Host $_.Exception.Message
  $code=1
} finally {
  [Environment]::SetEnvironmentVariable('LOCALAPPDATA',$prior,'Process')
  [ordered]@{before=$prior;after=[Environment]::GetEnvironmentVariable('LOCALAPPDATA','Process');isolated=$LocalAppData;restored=([Environment]::GetEnvironmentVariable('LOCALAPPDATA','Process') -ceq $prior)} | ConvertTo-Json | Set-Content -LiteralPath $AuditPath -Encoding utf8
}
exit $code
'@ | Set-Content -LiteralPath $defaultHarness -Encoding utf8
  $isolatedLocal=Join-Path $FixtureRoot 'isolated LOCALAPPDATA with spaces'
  $audit=Join-Path $FixtureRoot 'default_process_environment.json'
  Run-Child '09-wrapper-default-destination' $defaultHarness @('-Wrapper',$wrapper,'-LocalAppData',$isolatedLocal,'-AuditPath',$audit)
  $default=Join-Path $isolatedLocal 'AgentSetup/hub'
  Check-CleanInstall $default 'isolated default'
  $environmentAudit=Get-Content -LiteralPath $audit -Raw | ConvertFrom-Json
  Assert ($environmentAudit.restored -and $environmentAudit.before -ceq $environmentAudit.after) 'child restores process-only LOCALAPPDATA'
  Assert ($env:LOCALAPPDATA -ceq $processLocalAppDataBefore) 'parent process LOCALAPPDATA remains unchanged'

  # Regression: Bootstrap with an extracted SourceRoot must actually apply to absent parents.
  $bootstrapParent=Join-Path $FixtureRoot 'bootstrap absent parent'
  $bootstrapDest=Join-Path $bootstrapParent 'nested/hub with spaces'
  Assert (-not (Test-Path -LiteralPath $bootstrapParent)) 'Bootstrap parent starts absent'
  Run-Child '10-bootstrap-sourceroot-actual-apply' $bootstrap @('-SourceRoot',$SourceRoot,'-Destination',$bootstrapDest,'-CopyAdapters')
  Check-CleanInstall $bootstrapDest 'Bootstrap SourceRoot'
  Assert ([Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User') -ceq $userEnvironmentBefore) 'all tests leave user AGENT_HUB_ROOT unchanged'
  $status='PASS'
} catch {
  $status='FAIL'
  $failure=[ordered]@{message=$_.Exception.Message;fully_qualified_error_id=$_.FullyQualifiedErrorId;script_stack_trace=$_.ScriptStackTrace}
  Write-Host ('FAILED: '+$failure.message) -ForegroundColor Red
} finally {
  [ordered]@{
    status=$status;started_utc=$started;finished_utc=[DateTime]::UtcNow.ToString('o')
    source_root=$SourceRoot;fixture_root=$FixtureRoot;count=$checks.Count
    checks=@($checks.ToArray());child_runs=@($runs.ToArray());failure=$failure
    user_environment_before=$userEnvironmentBefore
    user_environment_after=[Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User')
    scope='Disposable local clean-install wrapper and Bootstrap regression only. No actual company PC, Git/network, accounts, SetUserEnvironment, Force, workflow execution or cleanup.'
  } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $FixtureRoot 'validation.json') -Encoding utf8
  Write-Host ('Receipt: '+(Join-Path $FixtureRoot 'validation.json'))
}
if ($status -ne 'PASS') { exit 1 }
Write-Host ('PASS: '+$checks.Count+' focused checks; fixtures and logs retained.')
