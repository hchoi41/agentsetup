#Requires -Version 7.0
<#
.SYNOPSIS
Test selection enforcement without real agents, authentication or installations.
.DESCRIPTION
Creates an absent fixture root, retains every fixture and log, and runs byte-exact
copies of the guard and both engines. Child PATH contains only synthetic CLI
commands and Windows/PowerShell system folders. Never cleans up fixtures.
#>
[CmdletBinding()]
param(
  [string]$SourceRoot=(Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
  [Parameter(Mandatory)][string]$FixtureRoot
)
$ErrorActionPreference='Stop'
if (-not $IsWindows) { throw 'These entrypoint tests require Windows and PowerShell 7.' }
$SourceRoot=[IO.Path]::GetFullPath($SourceRoot)
$FixtureRoot=[IO.Path]::GetFullPath($FixtureRoot)
if (Test-Path -LiteralPath $FixtureRoot) { throw 'Use a new, absent fixture directory. Existing fixtures are never removed.' }
if ($FixtureRoot.Equals($SourceRoot,[StringComparison]::OrdinalIgnoreCase) -or
    $FixtureRoot.StartsWith($SourceRoot.TrimEnd('\','/')+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
  throw 'Keep test artifacts outside the distributable source.'
}
# A parent hub's real choice must not contaminate synthetic legacy/allow cases.
$ancestor=[IO.Directory]::GetParent($FixtureRoot)
while ($ancestor) {
  if (Test-Path -LiteralPath (Join-Path $ancestor.FullName '00.ABOUT/AGENT_CLI_SETUP.json')) {
    throw 'Choose a fixture location with no existing ancestor AGENT_CLI_SETUP.json.'
  }
  $ancestor=$ancestor.Parent
}
$sourceFiles=@('scripts/Assert-AgentCliSelection.ps1','orchestrate/Orchestrate.ps1','orchestrate/Orchestrate2.ps1')
$sourceManifest=@(foreach ($relative in $sourceFiles) {
  $path=Join-Path $SourceRoot $relative
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required source missing: $path" }
  [ordered]@{relative_path=$relative;path=$path;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
})
[void][IO.Directory]::CreateDirectory($FixtureRoot)
$logs=Join-Path $FixtureRoot 'logs'
[void][IO.Directory]::CreateDirectory($logs)
$checks=[Collections.Generic.List[object]]::new()
$utf8=[Text.UTF8Encoding]::new($false)
$failure=$null
$pwsh=Join-Path $PSHOME 'pwsh.exe'
$guard=Join-Path $SourceRoot 'scripts/Assert-AgentCliSelection.ps1'
$environmentBefore=@{path=$env:PATH;agent_hub_root=$env:AGENT_HUB_ROOT;user_agent_hub_root=[Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User')}
function Assert-Test([bool]$Condition,[string]$Name) {
  $checks.Add([ordered]@{name=$Name;passed=$Condition})
  if (-not $Condition) { throw "ASSERTION FAILED: $Name" }
}
function Write-Text([string]$Path,[string]$Text) {
  [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path))
  [IO.File]::WriteAllText($Path,$Text,$utf8)
}
function Write-State([string]$Root,[AllowNull()][object]$Json) {
  if ($null -ne $Json) { Write-Text (Join-Path $Root '00.ABOUT/AGENT_CLI_SETUP.json') $Json }
}
function Get-TreeSnapshot([string]$Root) {
  $entries=@(Get-ChildItem -LiteralPath $Root -Force -Recurse | Sort-Object FullName | ForEach-Object {
    $_.Refresh()
    [ordered]@{
      path=[IO.Path]::GetRelativePath($Root,$_.FullName)
      directory=$_.PSIsContainer
      attributes=[int64]$_.Attributes
      last_write_utc=$_.LastWriteTimeUtc.ToString('o')
      length=if ($_.PSIsContainer) {$null} else {$_.Length}
      sha256=if ($_.PSIsContainer) {$null} else {(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    }
  })
  ConvertTo-Json -InputObject $entries -Depth 5 -Compress
}
function Invoke-GuardCase {
  param([string]$Name,[AllowNull()][object]$InstallState,[AllowNull()][object]$HubState,
    [string[]]$Required=@('codex'),[bool]$Allowed=$false,[string]$AncestorState,
    [switch]$StateDirectory)
  $case=Join-Path $FixtureRoot ('guard/'+$Name)
  $installation=Join-Path $case 'installation'
  $hub=Join-Path $case 'external hub'
  [void][IO.Directory]::CreateDirectory($installation)
  [void][IO.Directory]::CreateDirectory($hub)
  Write-State $installation $InstallState
  Write-State $hub $HubState
  if ($AncestorState) { Write-State $case $AncestorState }
  if ($StateDirectory) { [void][IO.Directory]::CreateDirectory((Join-Path $installation '00.ABOUT/AGENT_CLI_SETUP.json')) }
  $before=Get-TreeSnapshot $case
  $actualAllowed=$true
  $guardOutput=@()
  $errorText=''
  try { $guardOutput=@(& $guard -InstallationRoot $installation -HubRoot $hub -RequiredClis $Required) }
  catch { $actualAllowed=$false; $errorText=$_.Exception.Message }
  $after=Get-TreeSnapshot $case
  Write-Text (Join-Path $logs ($Name+'.before-tree.json')) $before
  Write-Text (Join-Path $logs ($Name+'.after-tree.json')) $after
  Write-Text (Join-Path $logs ($Name+'.guard.log')) ("allowed=$actualAllowed`noutput_count=$($guardOutput.Count)`n$errorText")
  Assert-Test ($actualAllowed -eq $Allowed) "$Name expected guard decision"
  Assert-Test ($guardOutput.Count -eq 0) "$Name guard emits no success output"
  Assert-Test ($after -ceq $before) "$Name guard leaves fixture bytes and metadata unchanged"
}
function Invoke-Child([string]$Name,[string]$Script,[string[]]$Arguments,[string]$WorkingDirectory) {
  $callMarker=Join-Path $logs ($Name+'.fake-cli-calls.txt')
  $start=[Diagnostics.ProcessStartInfo]::new()
  $start.FileName=$pwsh
  $start.UseShellExecute=$false
  $start.CreateNoWindow=$true
  $start.RedirectStandardOutput=$true
  $start.RedirectStandardError=$true
  $start.WorkingDirectory=$WorkingDirectory
  foreach ($argument in @('-NoLogo','-NoProfile','-NonInteractive','-File',$Script)+$Arguments) { [void]$start.ArgumentList.Add($argument) }
  $start.Environment['PATH']=$fakeBin+';'+$PSHOME+';'+(Join-Path $env:SystemRoot 'System32')
  $start.Environment['PATHEXT']='.COM;.EXE;.BAT;.CMD'
  $start.Environment['AGENT_SELECTION_TEST_MARKER']=$callMarker
  [void]$start.Environment.Remove('AGENT_HUB_ROOT')
  $process=[Diagnostics.Process]::new()
  $process.StartInfo=$start
  try {
    [void]$process.Start()
    $stdoutTask=$process.StandardOutput.ReadToEndAsync()
    $stderrTask=$process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(45000)) {
      $process.Kill($true)
      $process.WaitForExit()
      throw "Synthetic child timed out: $Name"
    }
    $stdout=$stdoutTask.GetAwaiter().GetResult()
    $stderr=$stderrTask.GetAwaiter().GetResult()
    $code=$process.ExitCode
  } finally { $process.Dispose() }
  Write-Text (Join-Path $logs ($Name+'.stdout.log')) $stdout
  Write-Text (Join-Path $logs ($Name+'.stderr.log')) $stderr
  [pscustomobject]@{code=$code;stdout=$stdout;stderr=$stderr;call_marker=$callMarker}
}
function Invoke-EngineCase {
  param([string]$Name,[ValidateSet('v1','v2')][string]$Engine,
    [AllowNull()][object]$InstallState,[AllowNull()][object]$HubState,
    [ValidateSet('dryrun','preflight','run','capabilities')][string]$Mode='dryrun',
    [string]$RoleCli='codex',[bool]$Allowed=$false,[switch]$MissingGuard,[switch]$DefaultRoles)
  $case=Join-Path $FixtureRoot ('engines/'+$Name)
  $installation=Join-Path $case 'installation'
  $hub=Join-Path $case 'external configured hub'
  [void][IO.Directory]::CreateDirectory($installation)
  [void][IO.Directory]::CreateDirectory($hub)
  foreach ($source in $sourceManifest) {
    if ($MissingGuard -and $source.relative_path -like 'scripts/*') { continue }
    $target=Join-Path $installation $source.relative_path
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
    [IO.File]::Copy($source.path,$target,$false)
    Assert-Test ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $source.sha256) "$Name uses exact $($source.relative_path)"
  }
  Write-State $installation $InstallState
  Write-State $hub $HubState
  $runRoot=Join-Path $hub 'precreated run root'
  [void][IO.Directory]::CreateDirectory($runRoot)
  $token='synthetic-selection-test-token'
  Write-Text (Join-Path $runRoot '.orchestrate-runroot.json') (@{name=$Name;runRootToken=$token}|ConvertTo-Json)
  $receipt=Join-Path $case 'synthetic-consent.json'
  Write-Text $receipt (@{runRootToken=$token}|ConvertTo-Json)
  $roles=[ordered]@{}
  foreach ($seat in @('maker','reviewer1','reviewer2','referee')) {
    $roles[$seat]=@{cli=$RoleCli;model='fixture-model';effort='medium';maxTurns=1}
  }
  $toolPaths=[ordered]@{}
  foreach ($cli in @('codex','claude','copilot','gemini','opencode')) { $toolPaths[$cli]=Join-Path $fakeBin ($cli+'.cmd') }
  $config=[ordered]@{
    name=$Name;hub=$hub;workProduct='document';maxFix=0;roles=$roles
    runRoot=$runRoot;runRootToken=$token;toolPaths=$toolPaths
    artifacts=@('fixture-artifact');gate=@{checks=@()}
    tasks=@(@{id='one';title='Synthetic selection test';instruction='Fixture only. Do not contact any service.';gate=@{checks=@()}})
  }
  if ($DefaultRoles) { $config.Remove('roles') }
  $configPath=Join-Path $case 'job.json'
  Write-Text $configPath ($config|ConvertTo-Json -Depth 8)
  $enginePath=Join-Path $installation ('orchestrate/'+$(if ($Engine -eq 'v1') {'Orchestrate.ps1'} else {'Orchestrate2.ps1'}))
  $arguments=@('-Config',$configPath,'-HubRoot',$installation)
  switch ($Mode) {
    'dryrun' { $arguments+=@('-DryRun'); if ($Engine -eq 'v2') { $arguments+=@('-OutputFormat','Json') } }
    'preflight' { $arguments+=@('-Preflight') }
    'run' { if ($Engine -eq 'v2') { $arguments+=@('-ConsentReceipt',$receipt,'-OutputFormat','Json') } }
    'capabilities' { $arguments=@('-Capabilities') }
  }
  $before=Get-TreeSnapshot $case
  $result=Invoke-Child $Name $enginePath $arguments $case
  $after=Get-TreeSnapshot $case
  Write-Text (Join-Path $logs ($Name+'.before-tree.json')) $before
  Write-Text (Join-Path $logs ($Name+'.after-tree.json')) $after
  Assert-Test (($result.code -eq 0) -eq $Allowed) "$Name expected engine exit (actual $($result.code))"
  if (-not $Allowed) {
    Assert-Test (($result.stdout+$result.stderr) -match 'Agent CLI selection|Job requires CLI') "$Name stops at selection gate rather than another config error"
    if ($Engine -eq 'v2') { Assert-Test ($result.code -eq 2) "$Name uses v2 config-invalid exit code" }
  } elseif ($Mode -eq 'capabilities') {
    $capabilities=$result.stdout|ConvertFrom-Json
    Assert-Test ($capabilities.engineContract -ceq 'orchestrate/v2') "$Name returns capabilities without a config"
  } elseif ($Mode -eq 'dryrun') {
    Assert-Test (($result.stdout -match 'DRY RUN') -or ($result.stdout -match 'orchestrate2-dryrun/v1')) "$Name reaches actual engine preview"
  }
  if ($Allowed -and $Mode -eq 'preflight') {
    Assert-Test (Test-Path -LiteralPath $result.call_marker) "$Name proves fake PATH commands are reachable"
    $calls=@(Get-Content -LiteralPath $result.call_marker)
    Assert-Test (($calls -contains 'codex') -and ($calls -contains 'python')) "$Name probes only synthetic CLI commands"
  } else {
    Assert-Test (-not (Test-Path -LiteralPath $result.call_marker)) "$Name starts no CLI process"
  }
  Assert-Test ($after -ceq $before) "$Name writes no engine artifacts and changes no fixture files"
  Assert-Test (-not (Test-Path -LiteralPath (Join-Path $hub '_orchestrate'))) "$Name creates no legacy run directory"
}
try {
  $pending='{"schema_version":1,"selection_state":"pending","selected_agents":[]}'
  $none='{"schema_version":1,"selection_state":"none","selected_agents":[]}'
  $codex='{"schema_version":1,"selection_state":"selected","selected_agents":["codex"]}'
  $claude='{"schema_version":1,"selection_state":"selected","selected_agents":["claude"]}'
  $superset='{"schema_version":1,"selection_state":"selected","selected_agents":["codex","claude","copilot","gemini","opencode"],"authentication":"not_checked"}'
  Invoke-GuardCase 'legacy-absent' $null $null -Allowed $true
  Invoke-GuardCase 'pending' $pending $null
  Invoke-GuardCase 'none' $none $null
  Invoke-GuardCase 'selected-subset' $superset $null -Required @('codex','gemini') -Allowed $true
  Invoke-GuardCase 'unselected-role' $codex $null -Required @('copilot')
  Invoke-GuardCase 'consistent-two-roots' $superset $codex -Allowed $true
  Invoke-GuardCase 'conflicting-two-roots' $codex $claude
  Invoke-GuardCase 'installation-none-external-hub-no-state' $none $null
  Invoke-GuardCase 'installation-none-hub-selected' $none $codex
  Invoke-GuardCase 'hub-none-installation-selected' $codex $none
  Invoke-GuardCase 'ancestor-none' $null $null -AncestorState $none
  Invoke-GuardCase 'ancestor-selection' $codex $null -AncestorState $superset -Allowed $true
  Invoke-GuardCase 'state-is-directory' $null $null -StateDirectory
  $invalid=[ordered]@{
    'malformed-json'='{'
    'json-null'='null'
    'json-array'='[]'
    'missing-version'='{"selection_state":"selected","selected_agents":["codex"]}'
    'wrong-version'='{"schema_version":2,"selection_state":"selected","selected_agents":["codex"]}'
    'string-version'='{"schema_version":"1","selection_state":"selected","selected_agents":["codex"]}'
    'float-version'='{"schema_version":1.0,"selection_state":"selected","selected_agents":["codex"]}'
    'bool-version'='{"schema_version":true,"selection_state":"selected","selected_agents":["codex"]}'
    'wrong-key-case'='{"Schema_Version":1,"selection_state":"selected","selected_agents":["codex"]}'
    'wrong-state-type'='{"schema_version":1,"selection_state":true,"selected_agents":["codex"]}'
    'wrong-state-case'='{"schema_version":1,"selection_state":"SELECTED","selected_agents":["codex"]}'
    'unknown-state'='{"schema_version":1,"selection_state":"ready","selected_agents":["codex"]}'
    'agents-string'='{"schema_version":1,"selection_state":"selected","selected_agents":"codex"}'
    'agents-null'='{"schema_version":1,"selection_state":"selected","selected_agents":null}'
    'agents-object'='{"schema_version":1,"selection_state":"selected","selected_agents":{"codex":true}}'
    'agent-number'='{"schema_version":1,"selection_state":"selected","selected_agents":[1]}'
    'agent-null'='{"schema_version":1,"selection_state":"selected","selected_agents":[null]}'
    'unknown-agent'='{"schema_version":1,"selection_state":"selected","selected_agents":["unknown"]}'
    'wrong-agent-case'='{"schema_version":1,"selection_state":"selected","selected_agents":["Codex"]}'
    'duplicate-agent'='{"schema_version":1,"selection_state":"selected","selected_agents":["codex","codex"]}'
    'selected-empty'='{"schema_version":1,"selection_state":"selected","selected_agents":[]}'
    'none-with-agent'='{"schema_version":1,"selection_state":"none","selected_agents":["codex"]}'
    'pending-with-agent'='{"schema_version":1,"selection_state":"pending","selected_agents":["codex"]}'
    'duplicate-key'='{"schema_version":1,"schema_version":1,"selection_state":"selected","selected_agents":["codex"]}'
    'duplicate-key-case'='{"schema_version":1,"SCHEMA_VERSION":1,"selection_state":"selected","selected_agents":["codex"]}'
  }
  foreach ($item in $invalid.GetEnumerator()) { Invoke-GuardCase $item.Key $item.Value $null }

  $fakeBin=Join-Path $FixtureRoot 'fake-bin'
  [void][IO.Directory]::CreateDirectory($fakeBin)
  # These commands cannot contact a model: they only append their own name and exit.
  $fakeCommand="@echo off`r`nif not defined AGENT_SELECTION_TEST_MARKER exit /b 98`r`n>>`"%AGENT_SELECTION_TEST_MARKER%`" echo %~n0`r`necho %~n0 synthetic-version-0.0`r`nexit /b 0`r`n"
  foreach ($cli in @('codex','claude','copilot','gemini','opencode','python')) { Write-Text (Join-Path $fakeBin ($cli+'.cmd')) $fakeCommand }
  foreach ($engine in @('v1','v2')) {
    Invoke-EngineCase "$engine-pending-preview" $engine $pending $null
    Invoke-EngineCase "$engine-none-preview-external-hub" $engine $none $null
    Invoke-EngineCase "$engine-none-real-run" $engine $none $null -Mode run
    Invoke-EngineCase "$engine-selected-preview" $engine $codex $null -Allowed $true
    Invoke-EngineCase "$engine-legacy-preview" $engine $null $null -Allowed $true
    Invoke-EngineCase "$engine-unselected-role" $engine $codex $null -RoleCli copilot
    Invoke-EngineCase "$engine-default-role-not-selected" $engine $codex $null -DefaultRoles
    Invoke-EngineCase "$engine-conflicting-hub" $engine $codex $claude
    Invoke-EngineCase "$engine-malformed-state" $engine '{' $null
    Invoke-EngineCase "$engine-missing-guard-state-present" $engine $none $null -MissingGuard
    Invoke-EngineCase "$engine-missing-guard-legacy" $engine $null $null -MissingGuard -Allowed $true
  }
  Invoke-EngineCase 'v1-pending-preflight' v1 $pending $null -Mode preflight
  Invoke-EngineCase 'v1-none-preflight' v1 $none $null -Mode preflight
  Invoke-EngineCase 'v1-selected-fake-preflight' v1 $codex $null -Mode preflight -Allowed $true
  Invoke-EngineCase 'v2-none-capabilities' v2 $none $null -Mode capabilities -Allowed $true
  foreach ($source in $sourceManifest) {
    Assert-Test ((Get-FileHash -LiteralPath $source.path -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $source.sha256) "source unchanged: $($source.relative_path)"
  }
  Assert-Test ($env:PATH -ceq $environmentBefore.path) 'parent process PATH unchanged'
  Assert-Test ($env:AGENT_HUB_ROOT -ceq $environmentBefore.agent_hub_root) 'parent process hub environment unchanged'
  Assert-Test ([Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User') -ceq $environmentBefore.user_agent_hub_root) 'user hub environment unchanged'
} catch {
  $failure=$_
  Write-Text (Join-Path $logs 'failure.log') ($_.ToString()+"`n"+$_.ScriptStackTrace)
} finally {
  $summary=[ordered]@{
    status=if ($failure) {'FAIL'} else {'PASS'}
    generated_utc=[DateTime]::UtcNow.ToString('o')
    source_root=$SourceRoot;fixture_root=$FixtureRoot
    scope='Selection guard and exact copied engine entrypoints only. Synthetic CLI processes; no software installation, login, model request, or fixture deletion.'
    count=$checks.Count;checks=@($checks.ToArray());source_manifest=$sourceManifest
    failure=if ($failure) {$failure.Exception.Message} else {$null}
  }
  Write-Text (Join-Path $FixtureRoot 'validation.json') ($summary|ConvertTo-Json -Depth 8)
}
if ($failure) { throw $failure }
Write-Host "PASS: $($checks.Count) checks. Retained evidence: $FixtureRoot"
