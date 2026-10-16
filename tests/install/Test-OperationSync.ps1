#Requires -Version 7.0
<#
.SYNOPSIS
Test operation-name cloud-sync compatibility using isolated local fixtures.
.DESCRIPTION
FixtureRoot must be absent and outside SourceRoot. No OneDrive path, provider,
real project, registry, or global setting is used. Fixtures and logs are retained.
SourceRoot is a release payload containing skills/cloud-sync/scripts/sync_project.ps1.
#>
[CmdletBinding()]
param(
  [string]$SourceRoot=(Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
  [Parameter(Mandatory)][string]$FixtureRoot
)
$ErrorActionPreference='Stop'
$SourceRoot=[IO.Path]::GetFullPath($SourceRoot).TrimEnd('\','/')
$FixtureRoot=[IO.Path]::GetFullPath($FixtureRoot).TrimEnd('\','/')
if ($FixtureRoot -eq [IO.Path]::GetPathRoot($FixtureRoot).TrimEnd('\','/')) { throw 'FixtureRoot cannot be a drive root.' }
$sourcePrefix=$SourceRoot+[IO.Path]::DirectorySeparatorChar
$fixturePrefix=$FixtureRoot+[IO.Path]::DirectorySeparatorChar
if ($SourceRoot.Equals($FixtureRoot,[StringComparison]::OrdinalIgnoreCase) -or $sourcePrefix.StartsWith($fixturePrefix,[StringComparison]::OrdinalIgnoreCase) -or $fixturePrefix.StartsWith($sourcePrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'SourceRoot and FixtureRoot must not overlap.' }
if (Test-Path -LiteralPath $FixtureRoot) { throw 'FixtureRoot must be absent; existing fixtures are preserved.' }
$probe=Split-Path -Parent $FixtureRoot
while($probe) {
  $item=$null
  try { $item=Get-Item -LiteralPath $probe -Force -ErrorAction Stop } catch [System.Management.Automation.ItemNotFoundException] { }
  if($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'FixtureRoot cannot have a reparse ancestor.' }
  $parent=Split-Path -Parent $probe
  if($parent -eq $probe){break};$probe=$parent
}
foreach($cloudRoot in @($env:OneDrive,$env:OneDriveConsumer,$env:OneDriveCommercial)) {
  if(-not $cloudRoot){continue}
  $cloudPrefix=[IO.Path]::GetFullPath($cloudRoot).TrimEnd('\','/')+[IO.Path]::DirectorySeparatorChar
  if($fixturePrefix.StartsWith($cloudPrefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Fixtures must stay outside OneDrive.'}
}
$scriptPath=Join-Path $SourceRoot 'skills/cloud-sync/scripts/sync_project.ps1'
if(-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)){throw "Missing staged engine: $scriptPath"}
$scriptHash=(Get-FileHash -LiteralPath $scriptPath).Hash
$pwsh=(Get-Command pwsh -CommandType Application -ErrorAction Stop|Select-Object -First 1).Source
$utf8=[Text.UTF8Encoding]::new($false)
$checks=[Collections.Generic.List[object]]::new()
[IO.Directory]::CreateDirectory($FixtureRoot)|Out-Null
function Write-Text([string]$Path,[string]$Text){[IO.File]::WriteAllText($Path,$Text,$utf8)}
function Check([bool]$Condition,[string]$Label){$checks.Add(@{name=$Label;passed=$Condition});if(-not $Condition){throw "Assertion failed: $Label"}}
function Snapshot([string]$Path){
  (@(Get-ChildItem -LiteralPath $Path -Recurse -Force|Sort-Object FullName|ForEach-Object{
    $relative=[IO.Path]::GetRelativePath($Path,$_.FullName)
    if($_.PSIsContainer){'D:'+ $relative}else{'F:'+ $relative+':'+(Get-FileHash -LiteralPath $_.FullName).Hash}
  }) -join "`n")
}
function New-Case([string]$Id,[string]$Name,[string]$Kind='10.OPERATION'){
  $root=Join-Path $FixtureRoot $Id;$src=Join-Path $root 'source hub';$dst=Join-Path $root 'destination record'
  foreach($workspace in @($src,$dst)){foreach($role in @('01.PROJECT','10.OPERATION','12.MEMORY_RAM')){[IO.Directory]::CreateDirectory((Join-Path $workspace $role))|Out-Null}}
  $folder=Join-Path $src ($Kind+'/'+$Name)
  foreach($role in @('10.INPUT','20.PROCESS','30.OUTPUT')){[IO.Directory]::CreateDirectory((Join-Path $folder $role))|Out-Null}
  Write-Text (Join-Path $folder '10.INPUT/brief.txt') 'Synthetic source evidence.'
  Write-Text (Join-Path $folder '30.OUTPUT/result.txt') 'Synthetic result.'
  Write-Text (Join-Path $folder '20.PROCESS/RECIPE.md') "# Synthetic recipe`n- status: provisional`n- type1: publish`n- type2: blog`n`nUses the brief to produce ``30.OUTPUT/result.txt``.`n"
  return @{root=$root;src=$src;dst=$dst;name=$Name;folder=$folder;kind=$Kind;run=0}
}
function Add-Ram($Case,[string]$Name){Write-Text (Join-Path $Case.src ('12.MEMORY_RAM/'+$Name)) 'Synthetic RAM evidence.'}
function Run-Sync($Case,[string[]]$Extra=@()){
  $Case.run++
  $psi=[Diagnostics.ProcessStartInfo]::new($pwsh)
  $psi.UseShellExecute=$false;$psi.CreateNoWindow=$true;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true
  $psi.StandardOutputEncoding=$utf8;$psi.StandardErrorEncoding=$utf8
  $arguments=@{Project=$Case.name;SrcRoot=$Case.src;DstRoot=$Case.dst}
  for($i=0;$i -lt $Extra.Count;$i++){
    $key=$Extra[$i].TrimStart('-')
    if($key -in @('Apply','Hash')){$arguments[$key]=$true}else{$i++;$arguments[$key]=$Extra[$i]}
  }
  # Explicit UTF-8 prevents a host console code page from erasing Unicode in captured output.
  $psi.Environment['OPERATION_SYNC_TEST_SCRIPT']=$scriptPath
  $psi.Environment['OPERATION_SYNC_TEST_ARGUMENTS']=($arguments|ConvertTo-Json -Compress)
  $child='[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $invokeParameters=$env:OPERATION_SYNC_TEST_ARGUMENTS|ConvertFrom-Json -AsHashtable; & $env:OPERATION_SYNC_TEST_SCRIPT @invokeParameters; exit $LASTEXITCODE'
  $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($child))
  foreach($arg in @('-NoLogo','-NoProfile','-EncodedCommand',$encoded)){$psi.ArgumentList.Add($arg)}
  $process=[Diagnostics.Process]::Start($psi)
  $outTask=$process.StandardOutput.ReadToEndAsync();$errTask=$process.StandardError.ReadToEndAsync()
  if(-not $process.WaitForExit(60000)){$process.Kill($true);throw 'Synthetic sync child timed out.'}
  $result=@{exit=$process.ExitCode;text=$outTask.GetAwaiter().GetResult();error=$errTask.GetAwaiter().GetResult()};$process.Dispose()
  Write-Text (Join-Path $Case.root ("run_$($Case.run).stdout.txt")) $result.text
  Write-Text (Join-Path $Case.root ("run_$($Case.run).stderr.txt")) $result.error
  return $result
}
function Check-DryRun($Case,[string]$ExpectedRam,[string]$ExcludedRam,[string[]]$Extra=@()){
  $before=Snapshot $Case.src;$destBefore=Snapshot $Case.dst;$result=Run-Sync $Case $Extra
  Check ($result.exit -eq 0 -and $result.text.Contains('RESULT: DRY RUN, clean')) ($Case.name+' dry run succeeds')
  Check ($result.text.Contains('12.MEMORY_RAM/'+$ExpectedRam) -and -not $result.text.Contains('12.MEMORY_RAM/'+$ExcludedRam)) ($Case.name+' selects only the expected RAM prefix')
  Check ((Snapshot $Case.src) -ceq $before -and (Snapshot $Case.dst) -ceq $destBefore) ($Case.name+' dry run writes nothing')
}
$failure=$null
try{
  $c=New-Case 'canonical' 'publish_blog_weekly_digest_20260901_v0.01'
  $ram='20261017_publish_blog_weekly_digest_ram_v1.md';$wrong='20261017_publish_blog_weekly_digest_20260901_v0.01_ram_v1.md'
  Add-Ram $c $ram;Add-Ram $c $wrong;Check-DryRun $c $ram $wrong

  $c=New-Case 'explicit-slug' 'publish_blog_weekly_digest_20260901_v0.02'
  $ram='20261017_adopted_identity_ram_v1.md';$wrong='20261017_publish_blog_weekly_digest_ram_v1.md'
  Add-Ram $c $ram;Add-Ram $c $wrong;Check-DryRun $c $ram $wrong @('-Slug','adopted_identity')

  foreach($kind in @('01.PROJECT','10.OPERATION')){
    $c=New-Case ('legacy-'+$kind.Split('.')[1]) '20260901_publish_blog_weekly_digest' $kind
    $ram='20261017_publish_blog_weekly_digest_ram_v1.md';$wrong='20261017_other_identity_ram_v1.md'
    Add-Ram $c $ram;Add-Ram $c $wrong;Check-DryRun $c $ram $wrong
  }
  $c=New-Case 'project-suffix-unchanged' 'publish_blog_20260901_v0.01' '01.PROJECT'
  $ram='20261017_publish_blog_20260901_v0.01_ram_v1.md';$wrong='20261017_publish_blog_ram_v1.md'
  Add-Ram $c $ram;Add-Ram $c $wrong;Check-DryRun $c $ram $wrong

  foreach($name in @('publish_blog_20260230_v0.01','publish_blog_20260901_v01.01','publish_blog_20260901_v0.1','publish_blog_20260901_v0.00','publish_20250101_v0.01_20260901_v0.02')){
    $c=New-Case ('noncanonical-'+[guid]::NewGuid().ToString('N')) $name
    $ram='20261017_'+$name+'_ram_v1.md';$wrong='20261017_publish_blog_ram_v1.md'
    Add-Ram $c $ram;Add-Ram $c $wrong;Check-DryRun $c $ram $wrong
  }
  foreach($name in @('주간 재무_20260901_v0.01',('publish_blog_20260901_v'+('9'*40)+'.99'))){
    $c=New-Case ('canonical-extra-'+[guid]::NewGuid().ToString('N')) $name
    $semantic=if($name.StartsWith('주간')){'주간 재무'}else{'publish_blog'}
    $ram='20261017_'+$semantic+'_ram_v1.md';$wrong='20261017_unrelated_ram_v1.md'
    Add-Ram $c $ram;Add-Ram $c $wrong;Check-DryRun $c $ram $wrong
  }

  $c=New-Case 'older-destination' 'publish_blog_20260901_v1.00'
  $ram='20261017_publish_blog_ram_v1.md';Add-Ram $c $ram
  $oldName='PUBLISH_BLOG_20260901_v0.99';$oldFolder=Join-Path $c.dst ('10.OPERATION/'+$oldName)
  [IO.Directory]::CreateDirectory($oldFolder)|Out-Null;Write-Text (Join-Path $oldFolder 'keep.txt') 'Preserve the reviewed old counterpart.'
  [IO.File]::SetAttributes($oldFolder,([IO.File]::GetAttributes($oldFolder) -bor [IO.FileAttributes]::Hidden))
  $before=Snapshot $c.src;$destBefore=Snapshot $c.dst
  foreach($extra in @(@(),@('-Apply'),@('-Slug','adopted_identity'))){
    $r=Run-Sync $c $extra
    Check ($r.exit -eq 1 -and $r.text.Contains('Operation lineage already exists')) 'Different version, including hidden/case-different sibling, blocks implicit destination'
    Check ((Snapshot $c.src) -ceq $before -and (Snapshot $c.dst) -ceq $destBefore) 'Collision refusal preserves source/destination, including Apply'
  }
  $r=Run-Sync $c @('-DstName',$oldName)
  Check ($r.exit -eq 0 -and $r.text.Contains($oldFolder)) 'Explicit reviewed existing destination can be previewed'
  Check ((Snapshot $c.src) -ceq $before -and (Snapshot $c.dst) -ceq $destBefore) 'Explicit destination preview writes nothing'
  $inputHash=(Get-FileHash -LiteralPath (Join-Path $c.folder '10.INPUT/brief.txt')).Hash
  $outputHash=(Get-FileHash -LiteralPath (Join-Path $c.folder '30.OUTPUT/result.txt')).Hash
  $recipeHash=(Get-FileHash -LiteralPath (Join-Path $c.folder '20.PROCESS/RECIPE.md')).Hash
  $r=Run-Sync $c @('-DstName',$oldName,'-Apply','-Hash')
  Check ($r.exit -eq 0 -and $r.text.Contains('RESULT: OK')) 'Explicit reviewed existing destination applies to synthetic local data'
  Check (-not (Test-Path -LiteralPath (Join-Path $c.dst ('10.OPERATION/'+$c.name))) -and (Test-Path -LiteralPath (Join-Path $oldFolder 'keep.txt'))) 'Explicit override creates no parallel version and preserves existing files'
  Check ((Get-FileHash -LiteralPath (Join-Path $oldFolder '10.INPUT/brief.txt')).Hash -ceq $inputHash -and (Get-FileHash -LiteralPath (Join-Path $oldFolder '30.OUTPUT/result.txt')).Hash -ceq $outputHash) 'Synthetic destination input/output bytes match source'
  Check ((Get-FileHash -LiteralPath (Join-Path $c.folder '10.INPUT/brief.txt')).Hash -ceq $inputHash -and (Get-FileHash -LiteralPath (Join-Path $c.folder '30.OUTPUT/result.txt')).Hash -ceq $outputHash -and (Get-FileHash -LiteralPath (Join-Path $c.folder '20.PROCESS/RECIPE.md')).Hash -ceq $recipeHash) 'Apply preserves original input/output/recipe bytes'
  Check (Test-Path -LiteralPath (Join-Path $c.dst ('12.MEMORY_RAM/'+$ram))) 'Default canonical RAM prefix survives actual synthetic copy'

  $c=New-Case 'existing-current-target' 'publish_blog_20260901_v0.02'
  foreach($name in @($c.name,'publish_blog_20260901_v0.01')){[IO.Directory]::CreateDirectory((Join-Path $c.dst ('10.OPERATION/'+$name)))|Out-Null}
  $before=Snapshot $c.dst;$r=Run-Sync $c
  Check ($r.exit -eq 0 -and (Snapshot $c.dst) -ceq $before) 'Existing exact destination remains usable without creating a parallel folder'

  $c=New-Case 'different-lineage' 'publish_blog_20260901_v0.02'
  foreach($name in @('publish_blog_20260801_v0.01','other_blog_20260901_v0.01')){[IO.Directory]::CreateDirectory((Join-Path $c.dst ('10.OPERATION/'+$name)))|Out-Null}
  $before=Snapshot $c.dst;$r=Run-Sync $c
  Check ($r.exit -eq 0 -and (Snapshot $c.dst) -ceq $before) 'Different original dates or semantic names are not version collisions'
  $r=Run-Sync $c @('-DstName','../escape','-Apply')
  Check ($r.exit -eq 1 -and (Snapshot $c.dst) -ceq $before) 'Existing destination traversal refusal still applies'
  Check ((Get-FileHash -LiteralPath $scriptPath).Hash -ceq $scriptHash) 'Staged engine remains unchanged during tests'
}catch{$failure=$_.Exception.Message}
$receipt=[ordered]@{passed=($null -eq $failure);assertion_count=$checks.Count;failure=$failure;checks=$checks.ToArray();source_sha256=$scriptHash.ToLowerInvariant();fixture_root=$FixtureRoot;limitations='Local disposable fixtures only. No real OneDrive sync, folder conversion, registry modification, or provider execution.'}
Write-Text (Join-Path $FixtureRoot 'validation.json') ($receipt|ConvertTo-Json -Depth 6)
$receipt|ConvertTo-Json -Depth 6
if($failure){exit 1}
