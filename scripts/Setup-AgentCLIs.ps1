#Requires -Version 7.0
<#
.SYNOPSIS
Inventory, plan, or explicitly install selected agent CLIs. Never signs in.
.DESCRIPTION
No Choose: read-only inventory. Choose without Apply: read-only plan.
Choose none with Apply disables orchestration selection without uninstalling anything.
Apply means the displayed package/dependency plan has been explicitly approved.
Node 22+ is this helper's shared npm policy, not every vendor's individual minimum.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$HubRoot,
  [string]$Choose,
  [switch]$Apply
)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$catalog=[ordered]@{
  codex=@{command='codex';package='@openai/codex';source='https://developers.openai.com/codex/cli'}
  claude=@{command='claude';package='@anthropic-ai/claude-code';source='https://code.claude.com/docs/en/setup'}
  copilot=@{command='copilot';package='@github/copilot';source='https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli'}
  gemini=@{command='gemini';package='@google/gemini-cli';source='https://geminicli.com/docs/get-started/installation/'}
  opencode=@{command='opencode';package='opencode-ai';source='https://opencode.ai/docs/'}
}
function Emit($Value) { $Value | ConvertTo-Json -Depth 12 -Compress | Write-Output }
function Full([string]$Path) { return [IO.Path]::GetFullPath($Path).TrimEnd('\','/') }
function Check-Chain([string]$Path) {
  $current=Full $Path
  while ($current) {
    $entry=$null
    try { $entry=Get-Item -LiteralPath $current -Force -ErrorAction Stop }
    catch [System.Management.Automation.ItemNotFoundException] { }
    if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Reparse path refused: $current" }
    $parent=Split-Path -Parent $current
    if ($parent -eq $current) { break }
    $current=$parent
  }
}
function Find-Tool([string]$Name) {
  $found=Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($found) { return $found.Source }
  return $null
}
function Node-Version([string]$Command) {
  if (-not $Command) { return $null }
  $text=(& $Command --version 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -eq 0 -and $text -match '^v?(\d+)\.(\d+)\.(\d+)') { return [version]::new([int]$Matches[1],[int]$Matches[2],[int]$Matches[3]) }
  return $null
}
function Verify-Cli([string]$Name) {
  $command=Find-Tool $Name
  if (-not $command) { return $false }
  try { $discard=& $command --version 2>&1; return ($LASTEXITCODE -eq 0) }
  catch { return $false }
}
function Read-NpmPrefix([string]$Command) {
  if (-not $Command) { return $null }
  $prefix=(& $Command config get prefix 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or -not [IO.Path]::IsPathFullyQualified($prefix) -or $prefix -match '[\r\n]') { throw 'npm global prefix could not be read as an absolute local path; repair npm before installation.' }
  if ($prefix.StartsWith('\\')) { throw 'A network npm prefix is unsupported by this Windows setup helper.' }
  Check-Chain $prefix
  if ((Test-Path -LiteralPath $prefix) -and -not (Test-Path -LiteralPath $prefix -PathType Container)) { throw 'npm global prefix is occupied by a file.' }
  return [ordered]@{path=$prefix;configuration_changed=$false;writability='not_verified';note='No write probe is performed. The existing npm prefix is retained; if host permissions deny installation, this helper stops without changing npm configuration or elevating npm.'}
}
function Invoke-Planned($Item) {
  $command=Find-Tool $Item.executable
  if (-not $command) { return @{exit_code=-1;status='command_unavailable'} }
  try {
    $arguments=@($Item.arguments)
    $discard=& $command @arguments 2>&1
    $code=$LASTEXITCODE
    return @{exit_code=$code;status=if($code -eq 0){'completed'}else{'failed'}}
  } catch { return @{exit_code=-1;status='launch_failed'} }
}
function Refresh-ProcessPath {
  # Only this helper process changes. Existing PATH entries (including test mocks) retain priority.
  $additional=@(
    [Environment]::GetEnvironmentVariable('PATH','User'),
    [Environment]::GetEnvironmentVariable('PATH','Machine'),
    $(if($env:APPDATA){Join-Path $env:APPDATA 'npm'})
  ) | Where-Object {$_}
  $env:PATH=(@($env:PATH)+$additional) -join [IO.Path]::PathSeparator
}
function Save-State($State) {
  Check-Chain $statePath
  Check-Chain $history
  if (Test-Path -LiteralPath $statePath -PathType Container) { throw 'Setup state path is occupied by a directory.' }
  if ($priorBytes) {
    $now=[IO.File]::ReadAllBytes($statePath)
    if ([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($now)) -cne $priorHash) { throw 'Setup state changed during this run; refusing replacement.' }
    $backupDir=Join-Path $history ('agent_cli_setup_'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')+'_'+[Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($backupDir) | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $backupDir 'AGENT_CLI_SETUP.json'),$priorBytes)
  } elseif (Test-Path -LiteralPath $statePath) { throw 'Setup state appeared during this run; refusing replacement.' }
  $temp=Join-Path $about ('.agent_cli_setup_'+[Guid]::NewGuid().ToString('N')+'.tmp')
  try {
    [IO.File]::WriteAllText($temp,($State|ConvertTo-Json -Depth 10)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temp,$statePath,$true)
  } finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) } }
}
$report=$null
try {
  if (-not $IsWindows) { throw 'This setup helper currently supports Windows only.' }
  $HubRoot=Full $HubRoot
  if (-not $HubRoot -or $HubRoot -eq [IO.Path]::GetPathRoot($HubRoot).TrimEnd('\','/')) { throw 'An installed hub directory is required.' }
  Check-Chain $HubRoot
  $about=Join-Path $HubRoot '00.ABOUT'
  $history=Join-Path $HubRoot '13.MEMORY_HDD'
  $statePath=Join-Path $about 'AGENT_CLI_SETUP.json'
  foreach ($path in @($about,$history,$statePath)) { Check-Chain $path }
  if (-not (Test-Path -LiteralPath $about -PathType Container) -or -not (Test-Path -LiteralPath (Join-Path $HubRoot 'AGENTS.md') -PathType Leaf)) { throw 'HubRoot must be an installed hub with AGENTS.md and 00.ABOUT.' }
  if ((Test-Path -LiteralPath $history) -and -not (Test-Path -LiteralPath $history -PathType Container)) { throw 'History path is occupied by a file.' }
  if (Test-Path -LiteralPath $statePath -PathType Container) { throw 'Setup state path is occupied by a directory.' }
  $priorBytes=$null;$priorHash=$null;$prior=$null
  if (Test-Path -LiteralPath $statePath) {
    $priorBytes=[IO.File]::ReadAllBytes($statePath)
    $priorHash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($priorBytes))
    $raw=[Text.Encoding]::UTF8.GetString($priorBytes).TrimStart([char]0xFEFF)
    $document=[Text.Json.JsonDocument]::Parse($raw)
    try {
      if ($document.RootElement.ValueKind -ne [Text.Json.JsonValueKind]::Object) { throw 'Setup state must be a JSON object.' }
      $keys=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
      foreach ($property in $document.RootElement.EnumerateObject()) { if (-not $keys.Add($property.Name)) { throw 'Duplicate setup state property.' } }
    } finally { $document.Dispose() }
    $prior=$raw | ConvertFrom-Json -AsHashtable
    foreach ($key in @('schema_version','selection_state','selected_agents','tools','authentication')) { if (-not ($prior.Keys -ccontains $key)) { throw "Missing setup state property: $key" } }
    if (($prior.schema_version -isnot [int] -and $prior.schema_version -isnot [long]) -or $prior.schema_version -ne 1 -or $prior.selection_state -isnot [string] -or $prior.selection_state -cnotin @('pending','none','selected') -or $prior.selected_agents -isnot [array] -or $prior.tools -isnot [Collections.IDictionary] -or $prior.authentication -isnot [string] -or $prior.authentication -cne 'not_checked') { throw 'Malformed existing CLI setup state; preserve and review it before continuing.' }
    $ids=@($prior.selected_agents)
    if (@($ids|Select-Object -Unique).Count -ne $ids.Count -or @($ids|Where-Object {$_ -isnot [string] -or $_ -cnotin $catalog.Keys}).Count -or ($prior.selection_state -eq 'selected' -and -not $ids.Count) -or ($prior.selection_state -ne 'selected' -and $ids.Count)) { throw 'Malformed existing CLI selection; preserve and review it before continuing.' }
  }
  $explicit=$PSBoundParameters.ContainsKey('Choose')
  if ($Apply -and -not $explicit) { throw 'Apply requires an explicit Choose value: one or more agent IDs, or none.' }
  $selected=@()
  if ($explicit) {
    if ([string]::IsNullOrWhiteSpace($Choose)) { throw 'Choose cannot be empty.' }
    $selected=@($Choose.Split(',')|ForEach-Object {$_.Trim().ToLowerInvariant()})
    if (@($selected|Where-Object {-not $_}).Count -or @($selected|Select-Object -Unique).Count -ne $selected.Count -or @($selected|Where-Object {$_ -notin (@($catalog.Keys)+'none')}).Count -or ('none' -in $selected -and $selected.Count -ne 1)) { throw 'Invalid Choose value. Use unique codex,claude,copilot,gemini,opencode IDs, or none alone.' }
  }
  if ($explicit -and $selected[0] -eq 'none') {
    $disabled=[ordered]@{schema_version=1;selection_state='none';selected_agents=@();tools=@{};authentication='not_checked';ready_for_orchestration=$false;updated_utc=[DateTime]::UtcNow.ToString('o')}
    if ($Apply) { Save-State $disabled }
    Emit ([ordered]@{mode=if($Apply){'applied'}else{'plan'};status='disabled';selection=$disabled;commands=@();state_written=[bool]$Apply;note='No external command, uninstall, login or orchestration is performed.'})
    exit 0
  }
  $inventory=[ordered]@{}
  foreach ($id in $catalog.Keys) {
    $inventory[$id]=[ordered]@{command=$catalog[$id].command;command_present=[bool](Find-Tool $catalog[$id].command);version_verified=$false;authentication='not_checked'}
  }
  if (-not $explicit) {
    Emit ([ordered]@{mode='inventory';status=if($prior){'selection_'+$prior.selection_state}else{'selection_unset'};current_selection=if($prior){@{selection_state=$prior.selection_state;selected_agents=@($prior.selected_agents)}}else{@{selection_state='unset';selected_agents=@()}};state_written=$false;tools=$inventory;choices=@($catalog.Keys)+@('none');instructions='Choose one or more IDs. Run with -Choose first to review exact dependencies and commands; add -Apply only after approving that concrete plan. Installed does not mean authenticated.'})
    exit 0
  }
  $commands=[Collections.Generic.List[object]]::new()
  $blocked=[Collections.Generic.List[string]]::new()
  $missing=@($selected|Where-Object {-not $inventory[$_].command_present})
  if ('gemini' -in $missing -and [Environment]::OSVersion.Version.Build -lt 26100) { $blocked.Add('Gemini currently documents Windows 11 24H2 or later (build 26100+).') }
  $nodeCommand=$null;$nodeVersion=$null;$npmCommand=$null;$npmPrefix=$null
  if ($missing.Count) {
    $nodeCommand=Find-Tool 'node'
    $nodeVersion=Node-Version $nodeCommand
    $npmCommand=Find-Tool 'npm'
    if ($nodeCommand -and -not $nodeVersion) { $blocked.Add('Existing node version could not be determined; no automatic dependency replacement is planned.') }
    if (-not $nodeCommand -or ($nodeVersion -and $nodeVersion.Major -lt 22)) {
      $commands.Add([ordered]@{kind='dependency';id='node';executable='winget';arguments=@('install','--id','OpenJS.NodeJS.LTS','--exact','--source','winget','--accept-package-agreements','--accept-source-agreements','--disable-interactivity');reason='Shared helper policy: Node22+ and npm for selected missing npm CLIs.';may_require_administrator_approval=$true})
      if (-not (Find-Tool 'winget')) { $blocked.Add('WinGet is unavailable; the exact Node dependency command cannot run. Obtain the prerequisite through the approved software channel, then rerun this plan.') }
    } elseif (-not $npmCommand) { $blocked.Add('Node exists but npm is missing. Repair that Node/npm installation before any selected CLI installation.') }
    if ($npmCommand) { try { $npmPrefix=Read-NpmPrefix $npmCommand } catch { $blocked.Add($_.Exception.Message) } }
    foreach ($id in $missing) { $commands.Add([ordered]@{kind='agent';id=$id;executable='npm';arguments=@('install','--global','--no-audit','--no-fund',$catalog[$id].package);source=$catalog[$id].source}) }
  }
  $report=[ordered]@{mode=if($Apply){'apply'}else{'plan'};status=if($blocked.Count){'blocked'}else{'planned'};selected_agents=@($selected);tools=$inventory;commands=@($commands.ToArray());blocked_reasons=@($blocked.ToArray());state_written=$false;authentication='not_checked';ready_for_orchestration=$false;dependency_policy='Node22+ shared npm requirement. Node package may request Windows administrator approval; no RunAs, execution-policy change or automatic scope fallback. Git is optional workflow support and is never automatically installed.';npm_prefix=$npmPrefix;git_available=[bool](Find-Tool 'git');approval='Apply represents explicit approval of this concrete selected package/dependency plan. No login/provider configuration is performed.'}
  if (-not $Apply) { Emit $report; if($blocked.Count){exit 2};exit 0 }
  $toolState=[ordered]@{}
  foreach ($id in $selected) { $toolState[$id]=[ordered]@{command=$catalog[$id].command;command_present=$inventory[$id].command_present;installed=$false;version_verified=$false;installation_status=if($inventory[$id].command_present){'already_installed_skipped'}else{'not_started'};authentication='not_checked'} }
  $outcomes=[Collections.Generic.List[object]]::new()
  if (-not $blocked.Count) {
    foreach ($id in $selected) {
      if ($inventory[$id].command_present) {
        $verified=Verify-Cli $catalog[$id].command
        $toolState[$id].version_verified=$verified
        $toolState[$id].installed=$verified
        if (-not $verified) { $blocked.Add("Existing $id command failed --version verification; it was not updated or replaced.") }
      }
    }
  }
  if (-not $blocked.Count) {
    foreach ($item in $commands) {
      $result=Invoke-Planned $item
      $outcomes.Add([ordered]@{id=$item.id;kind=$item.kind;status=$result.status;exit_code=$result.exit_code})
      if ($result.exit_code -ne 0) {
        $blocked.Add("Installation failed for $($item.id), exit $($result.exit_code). No remaining install commands were started; check host policy or package-manager diagnostics.")
        if ($item.kind -eq 'agent') { $toolState[$item.id].installation_status='failed' }
        break
      }
      Refresh-ProcessPath
      if ($item.kind -eq 'dependency') {
        $version=Node-Version (Find-Tool 'node')
        if (-not $version -or $version.Major -lt 22 -or -not (Find-Tool 'npm')) { $blocked.Add('Node dependency command completed, but Node22+/npm are not addressable. Open a fresh terminal and rerun; no agent installs were started.');break }
        try { $report.npm_prefix=Read-NpmPrefix (Find-Tool 'npm') } catch { $blocked.Add($_.Exception.Message);break }
      } else {
        $present=[bool](Find-Tool $catalog[$item.id].command)
        $verified=$present -and (Verify-Cli $catalog[$item.id].command)
        $toolState[$item.id].command_present=$present
        $toolState[$item.id].version_verified=$verified
        $toolState[$item.id].installed=$verified
        $toolState[$item.id].installation_status=if($verified){'installed_verified'}elseif($present){'version_check_failed'}else{'installed_not_on_path'}
        if (-not $verified) { $blocked.Add("Package command completed for $($item.id), but its CLI did not pass --version verification. Open a fresh terminal and inspect the installation; remaining commands were stopped.");break }
      }
    }
  }
  $state=[ordered]@{schema_version=1;selection_state='selected';selected_agents=@($selected);tools=$toolState;authentication='not_checked';ready_for_orchestration=$false;installation_state=if($blocked.Count){'blocked'}else{'installed'};updated_utc=[DateTime]::UtcNow.ToString('o')}
  Save-State $state
  $report.status=if($blocked.Count){'blocked'}else{'installed_authentication_not_checked'}
  $report.state_written=$true
  $report.blocked_reasons=@($blocked.ToArray())
  $report.tools=$toolState
  $report.outcomes=@($outcomes.ToArray())
  Emit $report
  if ($blocked.Count) { exit 2 }
  exit 0
} catch {
  Emit ([ordered]@{mode='refused';status='error';state_written=$false;error=$_.Exception.Message;authentication='not_checked';ready_for_orchestration=$false})
  exit 1
}
