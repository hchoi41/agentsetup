#Requires -Version 7.0
<#
.SYNOPSIS
Exercise CLI selection with isolated fake executables. Installs no software.
.DESCRIPTION
FixtureRoot must not exist. Fixtures and logs are retained, including on failure.
Every child starts with an isolated PATH. Mock npm/winget remain first after PATH
refresh, and create deny stubs before refresh can expose any host agent executable.
#>
[CmdletBinding()]
param(
  [string]$SourceRoot=(Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
  [Parameter(Mandatory)][string]$FixtureRoot
)
$ErrorActionPreference='Stop'
$checks=[Collections.Generic.List[object]]::new()
$pwsh=(Get-Command pwsh -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$SourceRoot=[IO.Path]::GetFullPath($SourceRoot).TrimEnd('\','/')
$helper=Join-Path $SourceRoot 'scripts/Setup-AgentCLIs.ps1'
$FixtureRoot=[IO.Path]::GetFullPath($FixtureRoot).TrimEnd('\','/')
if ($FixtureRoot.Equals($SourceRoot,[StringComparison]::OrdinalIgnoreCase) -or $FixtureRoot.StartsWith($SourceRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or $SourceRoot.StartsWith($FixtureRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'SourceRoot and FixtureRoot must not overlap.' }
if (Test-Path -LiteralPath $FixtureRoot) { throw 'FixtureRoot must be a new absent directory; no existing fixture is overwritten.' }
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "Missing helper: $helper" }
$sourceHash=(Get-FileHash -LiteralPath $helper -Algorithm SHA256).Hash
$userHubBefore=[Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User')
$processHubBefore=$env:AGENT_HUB_ROOT
[IO.Directory]::CreateDirectory($FixtureRoot) | Out-Null
$utf8=[Text.UTF8Encoding]::new($false)
function Check([bool]$Condition,[string]$Name) {
  $checks.Add(@{name=$Name;passed=$Condition})
  if (-not $Condition) { throw "Assertion failed: $Name" }
}
function Write-Text([string]$Path,[string]$Text) { [IO.File]::WriteAllText($Path,$Text,$utf8) }
function Snapshot([string]$Root) {
  return (@(Get-ChildItem -LiteralPath $Root -Recurse -Force -File | Sort-Object FullName | ForEach-Object { [IO.Path]::GetRelativePath($Root,$_.FullName)+':'+(Get-FileHash -LiteralPath $_.FullName).Hash }) -join "`n")
}
function Write-Stub([string]$Bin,[string]$Name) {
  $cmd='@echo off'+"`r`n"+'"'+$pwsh+'" -NoLogo -NoProfile -File "'+$mockScript+'" '+$Name+' %*'+"`r`n"+'exit /b %errorlevel%'+"`r`n"
  Write-Text (Join-Path $Bin ($Name+'.cmd')) $cmd
}
$mockScript=Join-Path $FixtureRoot 'Mock-Executable.ps1'
Write-Text $mockScript @'
$ErrorActionPreference='Stop'
$tool=$args[0]
$callArgs=@($args | Select-Object -Skip 1)
$root=$env:AGENT_CLI_TEST_CASE
$config=Get-Content -LiteralPath (Join-Path $root 'config.json') -Raw | ConvertFrom-Json -AsHashtable
@{tool=$tool;arguments=$callArgs} | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $root 'calls.jsonl') -Encoding utf8
function Stub($name) {
  $text='@echo off'+"`r`n"+'"'+$env:AGENT_CLI_TEST_PWSH+'" -NoLogo -NoProfile -File "'+$PSCommandPath+'" '+$name+' %*'+"`r`n"+'exit /b %errorlevel%'+"`r`n"
  [IO.File]::WriteAllText((Join-Path $root ('bin/'+$name+'.cmd')),$text,[Text.UTF8Encoding]::new($false))
}
function Install-Stubs {
  # Each absent agent gets a failing --version stub, so host PATH can never pass.
  foreach ($name in @('codex','claude','copilot','gemini','opencode','node','npm','winget')) { Stub $name }
}
if ($tool -eq 'node') {
  if (($callArgs -join ' ') -ne '--version') { exit 91 }
  if (-not $config.node_ready) { exit 92 }
  Write-Output 'v22.9.0';exit 0
}
if ($tool -eq 'npm') {
  if (($callArgs -join ' ') -eq 'config get prefix') {
    if ($config.prefix_fail) { exit 17 }
    Write-Output (Join-Path $root 'npm-prefix');exit 0
  }
  if ($callArgs.Count -ne 5 -or ($callArgs[0..3] -join ' ') -cne 'install --global --no-audit --no-fund') { exit 93 }
  $mapping=@{'@openai/codex'='codex';'@anthropic-ai/claude-code'='claude';'@github/copilot'='copilot';'@google/gemini-cli'='gemini';'opencode-ai'='opencode'}
  if (-not $mapping.ContainsKey($callArgs[4])) { exit 94 }
  Install-Stubs
  $id=$mapping[$callArgs[4]]
  if ($config.fail_agent -eq $id) { exit 42 }
  $config.installed=@($config.installed)+@($id)
  $config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'config.json') -Encoding utf8
  exit 0
}
if ($tool -eq 'winget') {
  $expected='install --id OpenJS.NodeJS.LTS --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity'
  if (($callArgs -join ' ') -cne $expected) { exit 95 }
  if ($config.fail_dependency) { exit 43 }
  Install-Stubs
  $config.node_ready=$true
  $config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'config.json') -Encoding utf8
  exit 0
}
if ($tool -in @('codex','claude','copilot','gemini','opencode')) {
  if (($callArgs -join ' ') -cne '--version') { exit 96 }
  if ($tool -notin $config.installed -or $config.fail_version -eq $tool) { exit 44 }
  Write-Output ($tool+' 1.0.0');exit 0
}
exit 97
'@
function New-Case([string]$Name,[hashtable]$Options=@{},[switch]$NoAbout) {
  $root=Join-Path $FixtureRoot $Name
  $hub=Join-Path $root 'hub with spaces'
  $bin=Join-Path $root 'bin'
  [IO.Directory]::CreateDirectory($hub) | Out-Null
  [IO.Directory]::CreateDirectory($bin) | Out-Null
  if (-not $NoAbout) { [IO.Directory]::CreateDirectory((Join-Path $hub '00.ABOUT')) | Out-Null }
  Write-Text (Join-Path $hub 'AGENTS.md') '# Synthetic installed hub'
  $config=@{node_ready=$true;installed=@();fail_agent='';fail_version='';fail_dependency=$false;prefix_fail=$false}
  foreach ($key in $Options.Keys) { $config[$key]=$Options[$key] }
  Write-Text (Join-Path $root 'config.json') ($config|ConvertTo-Json -Depth 5)
  if ($config.node_ready) { Write-Stub $bin 'node';Write-Stub $bin 'npm' }
  if (-not $config.no_winget) { Write-Stub $bin 'winget' }
  foreach ($id in $config.installed) { Write-Stub $bin $id }
  return @{root=$root;hub=$hub;bin=$bin;state=(Join-Path $hub '00.ABOUT/AGENT_CLI_SETUP.json');run=0}
}
function Calls($Case) {
  $path=Join-Path $Case.root 'calls.jsonl'
  if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path | ForEach-Object {$_|ConvertFrom-Json -AsHashtable} }
}
function Installs($Case) { @(Calls $Case | Where-Object { $_.arguments.Count -and $_.arguments[0] -eq 'install' }) }
function Run-Setup($Case,[string[]]$Arguments=@()) {
  $Case.run++
  $psi=[Diagnostics.ProcessStartInfo]::new($pwsh)
  $psi.UseShellExecute=$false;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true;$psi.CreateNoWindow=$true
  foreach ($arg in @('-NoLogo','-NoProfile','-File',$helper,'-HubRoot',$Case.hub)+$Arguments) { $psi.ArgumentList.Add($arg) }
  # Child-only isolation. Later PATH refresh appends host values AFTER these mocks.
  $psi.Environment['PATH']=$Case.bin+[IO.Path]::PathSeparator+(Join-Path $env:SystemRoot 'System32')
  $psi.Environment['APPDATA']=Join-Path $Case.root 'appdata'
  $psi.Environment['AGENT_CLI_TEST_CASE']=$Case.root
  $psi.Environment['AGENT_CLI_TEST_PWSH']=$pwsh
  $p=[Diagnostics.Process]::Start($psi)
  $stdoutTask=$p.StandardOutput.ReadToEndAsync();$stderrTask=$p.StandardError.ReadToEndAsync()
  if (-not $p.WaitForExit(45000)) { $p.Kill($true);throw "Mock-only child timed out: $($Case.root)" }
  $stdout=$stdoutTask.GetAwaiter().GetResult();$stderr=$stderrTask.GetAwaiter().GetResult();$code=$p.ExitCode;$p.Dispose()
  Write-Text (Join-Path $Case.root ("run_$($Case.run).stdout.json")) $stdout
  Write-Text (Join-Path $Case.root ("run_$($Case.run).stderr.txt")) $stderr
  return @{exit=$code;report=($stdout.Trim()|ConvertFrom-Json -AsHashtable)}
}
$failure=$null
try {
  $c=New-Case 'inventory-plan'
  $before=Snapshot $c.hub
  $r=Run-Setup $c
  Check ($r.exit -eq 0 -and $r.report.status -eq 'selection_unset') 'Inventory reports unset current selection'
  Check ((Snapshot $c.hub) -ceq $before -and @(Calls $c).Count -eq 0) 'Inventory creates no files and invokes no external command'
  Check (-not $r.report.tools.codex.command_present -and -not $r.report.tools.codex.version_verified) 'Inventory distinguishes command presence from verified installation'
  $r=Run-Setup $c @('-Choose','codex')
  Check ($r.exit -eq 0 -and $r.report.commands.Count -eq 1 -and $r.report.commands[0].arguments[-1] -ceq '@openai/codex') 'Concrete plan contains only exact selected package'
  Check ((Snapshot $c.hub) -ceq $before -and @(Installs $c).Count -eq 0) 'Plan does not write hub or run installation'
  Check ($r.report.npm_prefix.writability -eq 'not_verified' -and -not $r.report.npm_prefix.configuration_changed) 'Plan discloses unproved prefix writability without changing npm'

  $c=New-Case 'none'
  $prior='{"schema_version":1,"selection_state":"pending","selected_agents":[],"tools":{},"authentication":"not_checked"}'
  Write-Text $c.state $prior
  $r=Run-Setup $c @('-Choose','none','-Apply')
  $state=Get-Content -LiteralPath $c.state -Raw|ConvertFrom-Json -AsHashtable
  Check ($r.exit -eq 0 -and $state.selection_state -eq 'none' -and $state.selected_agents.Count -eq 0) 'None persists disabled valid state'
  Check (@(Calls $c).Count -eq 0) 'None invokes zero external commands'
  $backups=@(Get-ChildItem -LiteralPath (Join-Path $c.hub '13.MEMORY_HDD') -Recurse -File)
  Check ($backups.Count -eq 1 -and [IO.File]::ReadAllText($backups[0].FullName) -ceq $prior) 'Previous state is preserved byte-for-byte in history'
  $r=Run-Setup $c
  Check ($r.report.status -eq 'selection_none' -and $r.report.current_selection.selection_state -eq 'none') 'Inventory retains prior none selection without reprompting'

  $c=New-Case 'one'
  $r=Run-Setup $c @('-Choose','codex','-Apply')
  $installs=@(Installs $c)
  Check ($r.exit -eq 0 -and $installs.Count -eq 1 -and $installs[0].arguments[-1] -ceq '@openai/codex') 'One selection installs only Codex'
  Check ($r.report.tools.codex.version_verified -and $r.report.tools.codex.installed) 'Successful install passes selected CLI --version'
  Check ($r.report.authentication -eq 'not_checked' -and -not $r.report.ready_for_orchestration) 'Installed is never represented as authenticated or orchestration-ready'
  $r=Run-Setup $c
  Check ($r.report.status -eq 'selection_selected' -and ($r.report.current_selection.selected_agents -join ',') -eq 'codex') 'Inventory reports persisted selected IDs'

  $c=New-Case 'many'
  $r=Run-Setup $c @('-Choose','codex,claude,copilot,opencode','-Apply')
  $packages=@(Installs $c | ForEach-Object {$_.arguments[-1]})
  Check ($r.exit -eq 0 -and ($packages -join ',') -ceq '@openai/codex,@anthropic-ai/claude-code,@github/copilot,opencode-ai') 'Multiple selection installs only requested official packages in plan order'
  Check (@($r.report.tools.Values | Where-Object {-not $_.version_verified}).Count -eq 0) 'Every installed selected CLI passes version verification'

  $c=New-Case 'already-installed' @{installed=@('codex')}
  $r=Run-Setup $c @('-Choose','codex','-Apply')
  Check ($r.exit -eq 0 -and @(Installs $c).Count -eq 0 -and $r.report.tools.codex.installation_status -eq 'already_installed_skipped') 'Existing selected CLI is verified and never updated'

  foreach ($choice in @('unknown','codex,none','none,none','codex,codex','codex,',' ')) {
    $c=New-Case ('invalid-'+[guid]::NewGuid().ToString('N'))
    $before=Snapshot $c.hub;$r=Run-Setup $c @('-Choose',$choice,'-Apply')
    Check ($r.exit -ne 0 -and (Snapshot $c.hub) -ceq $before -and @(Calls $c).Count -eq 0) ("Invalid choice refused without side effects: '$choice'")
  }
  $c=New-Case 'apply-without-choice';$before=Snapshot $c.hub;$r=Run-Setup $c @('-Apply')
  Check ($r.exit -ne 0 -and (Snapshot $c.hub) -ceq $before -and @(Calls $c).Count -eq 0) 'Apply requires explicit choice'

  $c=New-Case 'failed-install' @{fail_agent='codex'}
  $r=Run-Setup $c @('-Choose','codex,claude','-Apply')
  $state=Get-Content -LiteralPath $c.state -Raw|ConvertFrom-Json -AsHashtable
  Check ($r.exit -eq 2 -and @(Installs $c).Count -eq 1 -and $state.selection_state -eq 'selected' -and ($state.selected_agents -join ',') -eq 'codex,claude') 'Failed installation retains chosen IDs and stops remaining commands'
  Check ($state.installation_state -eq 'blocked' -and -not $state.ready_for_orchestration -and -not $state.tools.codex.installed -and $state.authentication -eq 'not_checked') 'Failed install remains blocked and unauthenticated'

  $c=New-Case 'version-failure' @{fail_version='codex'}
  $r=Run-Setup $c @('-Choose','codex,claude','-Apply')
  Check ($r.exit -eq 2 -and @(Installs $c).Count -eq 1 -and $r.report.tools.codex.installation_status -eq 'version_check_failed') 'Exit-zero package with broken CLI is blocked before next installation'

  $c=New-Case 'dependency' @{node_ready=$false}
  $r=Run-Setup $c @('-Choose','codex')
  Check ($r.exit -eq 0 -and $r.report.commands[0].id -eq 'node' -and $r.report.commands[0].may_require_administrator_approval -and @(Installs $c).Count -eq 0) 'Missing Node plan discloses exact dependency and possible administrator approval'
  $r=Run-Setup $c @('-Choose','codex','-Apply')
  $installs=@(Installs $c)
  Check ($r.exit -eq 0 -and $installs.Count -eq 2 -and $installs[0].tool -eq 'winget' -and $installs[0].arguments[2] -ceq 'OpenJS.NodeJS.LTS' -and $installs[1].tool -eq 'npm') 'Only planned Node dependency runs before selected package'

  $c=New-Case 'dependency-denied' @{node_ready=$false;fail_dependency=$true}
  $r=Run-Setup $c @('-Choose','codex','-Apply')
  Check ($r.exit -eq 2 -and @(Installs $c).Count -eq 1 -and $r.report.outcomes[0].exit_code -eq 43 -and $r.report.state_written) 'Denied dependency stops before npm and retains valid selection'

  $c=New-Case 'no-package-manager' @{node_ready=$false;no_winget=$true}
  $r=Run-Setup $c @('-Choose','codex','-Apply')
  Check ($r.exit -eq 2 -and @(Calls $c).Count -eq 0 -and $r.report.state_written) 'Unavailable prerequisite blocks every install before starting'

  $c=New-Case 'prefix-failure' @{prefix_fail=$true}
  $r=Run-Setup $c @('-Choose','codex','-Apply')
  Check ($r.exit -eq 2 -and @(Installs $c).Count -eq 0) 'Unreadable npm prefix blocks installation without changing configuration'

  $malformed=@(
    '{"schema_version":"1","selection_state":"none","selected_agents":[],"tools":{},"authentication":"not_checked"}',
    '{"schema_version":1,"schema_version":1,"selection_state":"none","selected_agents":[],"tools":{},"authentication":"not_checked"}',
    '{"schema_version":1,"selection_state":"NONE","selected_agents":[],"tools":{},"authentication":"not_checked"}',
    '{"schema_version":1,"selection_state":"selected","selected_agents":["CODEX"],"tools":{},"authentication":"not_checked"}',
    '{"schema_version":1,"selection_state":"selected","selected_agents":"codex","tools":{},"authentication":"not_checked"}',
    '{"schema_version":1,"selection_state":"selected","selected_agents":["codex","codex"],"tools":{},"authentication":"not_checked"}',
    '{"schema_version":1,"selection_state":"pending","selected_agents":["codex"],"tools":{},"authentication":"not_checked"}',
    '{"schema_version":1,"selection_state":"selected","selected_agents":[],"tools":{},"authentication":"not_checked"}'
  )
  $malformed+=@('{"schema_version":1,"selection_state":["selected"],"selected_agents":["codex"],"tools":{},"authentication":"not_checked"}','{"schema_version":1,"selection_state":"selected","selected_agents":["codex"],"tools":{},"authentication":["not_checked"]}')
  $i=0
  foreach ($raw in $malformed) {
    $c=New-Case ('malformed-'+(++$i));Write-Text $c.state $raw;$before=Snapshot $c.hub
    $r=Run-Setup $c @('-Choose','codex','-Apply')
    Check ($r.exit -eq 1 -and (Snapshot $c.hub) -ceq $before -and @(Calls $c).Count -eq 0) ("Malformed prior state $i is preserved and blocks external commands")
  }
  $c=New-Case 'occupied-state';[IO.Directory]::CreateDirectory($c.state)|Out-Null
  $r=Run-Setup $c @('-Choose','none','-Apply')
  Check ($r.exit -eq 1 -and (Test-Path -LiteralPath $c.state -PathType Container) -and @(Calls $c).Count -eq 0) 'Occupied state folder is refused and preserved'

  $c=New-Case 'reparse-about' -NoAbout
  $outside=Join-Path $c.root 'outside';[IO.Directory]::CreateDirectory($outside)|Out-Null
  New-Item -ItemType Junction -Path (Join-Path $c.hub '00.ABOUT') -Target $outside | Out-Null
  $r=Run-Setup $c @('-Choose','none','-Apply')
  Check ($r.exit -eq 1 -and @(Get-ChildItem -LiteralPath $outside -Force).Count -eq 0 -and @(Calls $c).Count -eq 0) 'Reparse ancestor is refused without writing through junction'

  Check ((Get-FileHash -LiteralPath $helper).Hash -ceq $sourceHash) 'Helper source remains unchanged'
  Check ([Environment]::GetEnvironmentVariable('AGENT_HUB_ROOT','User') -ceq $userHubBefore -and $env:AGENT_HUB_ROOT -ceq $processHubBefore) 'User and caller process hub environment remain unchanged'
  $allCalls=@(Get-ChildItem -LiteralPath $FixtureRoot -Filter calls.jsonl -Recurse -File | ForEach-Object {Get-Content -LiteralPath $_.FullName|ForEach-Object {$_|ConvertFrom-Json -AsHashtable}})
  Check (@($allCalls|Where-Object {$_.arguments -contains 'login' -or $_.arguments -contains 'auth'}).Count -eq 0) 'No authentication or login command is invoked'
} catch { $failure=$_.Exception.Message }
$receipt=[ordered]@{passed=($null -eq $failure);checks=$checks.ToArray();assertion_count=$checks.Count;failure=$failure;helper_sha256=$sourceHash.ToLowerInvariant();fixture_root=$FixtureRoot;real_software_installed=$false;limitations='Mock package managers only; real download, host policy, admin prompts, authentication and company PC remain untested.'}
Write-Text (Join-Path $FixtureRoot 'validation.json') ($receipt|ConvertTo-Json -Depth 8)
$receipt|ConvertTo-Json -Depth 8
if ($failure) { exit 1 }
