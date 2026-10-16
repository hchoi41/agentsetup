#Requires -Version 7.0
<# Tests pure naming proposals in disposable synthetic fixtures; retains all logs. #>
[CmdletBinding()]
param(
    [string]$HelperPath = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'scripts/operation_name.ps1'),
    [Parameter(Mandatory)][string]$FixtureRoot
)
$ErrorActionPreference='Stop'
$HelperPath=[IO.Path]::GetFullPath($HelperPath)
$FixtureRoot=[IO.Path]::GetFullPath($FixtureRoot).TrimEnd('\','/')
$helperDirectory=Split-Path -Parent $HelperPath
if($FixtureRoot.Equals($helperDirectory,[StringComparison]::OrdinalIgnoreCase) -or $FixtureRoot.StartsWith($helperDirectory+'\',[StringComparison]::OrdinalIgnoreCase) -or $helperDirectory.StartsWith($FixtureRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Helper directory and FixtureRoot must not overlap.'}
if(Test-Path -LiteralPath $FixtureRoot){throw 'FixtureRoot must be new and absent.'}
$sourceHash=(Get-FileHash -LiteralPath $HelperPath).Hash
$pwsh=(Get-Command pwsh -CommandType Application -ErrorAction Stop|Select-Object -First 1).Source
$utf8=[Text.UTF8Encoding]::new($false)
$cases=[Collections.Generic.List[object]]::new()
function Valid([string]$Name,[hashtable]$Parameters,[string]$Target,[hashtable]$Fields=@{}){$cases.Add(@{name=$Name;parameters=$Parameters;target=$Target;fields=$Fields;valid=$true})}
function Invalid([string]$Name,[hashtable]$Parameters){$cases.Add(@{name=$Name;parameters=$Parameters;valid=$false})}

Valid 'initial operation' @{Action='ToOperation';FolderName='20261017_daily_review'} 'daily_review_20261017_v0.01' @{creation_date='20261017';version='0.01';semantic_name='daily_review';legacy=$false;previous_operation_folder=$null}
Valid 'leap date preserved' @{Action='ToOperation';FolderName='20240229_daily_review'} 'daily_review_20240229_v0.01'
Valid 'Unicode and internal spaces preserved' @{Action='ToOperation';FolderName='20261017_한국어 daily_review'} '한국어 daily_review_20261017_v0.01'
Valid 'semantic embedded date preserved' @{Action='ToOperation';FolderName='20261017_foo_20200101_bar'} 'foo_20200101_bar_20261017_v0.01'
Valid 'maximum generated component length allowed' @{Action='ToOperation';FolderName=('20261017_'+('x'*240))} (('x'*240)+'_20261017_v0.01')
Valid 'semantic leading date roundtrip' @{Action='ToProject';FolderName='20200101_notes_20261017_v0.01'} '20261017_20200101_notes'
foreach($pair in @(@('0.01','0.02'),@('0.09','0.10'),@('0.99','1.00'),@('1.99','2.00'),@('9.99','10.00'),@('9999999999999999999999.99','10000000000000000000000.00'))){
    Valid ('integer increment '+$pair[0]) @{Action='NextVersion';FolderName=('daily_review_20261017_v'+$pair[0])} ('daily_review_20261017_v'+$pair[1]) @{version=$pair[1];previous_operation_folder=('daily_review_20261017_v'+$pair[0])}
}
Valid 'operation returns to project with lineage' @{Action='ToProject';FolderName='daily_review_20240229_v2.10'} '20240229_daily_review' @{creation_date='20240229';version='2.10';previous_operation_folder='daily_review_20240229_v2.10';legacy=$false}
Valid 'legacy date-first remains unchanged' @{Action='ToProject';FolderName='20240229_daily_review'} '20240229_daily_review' @{creation_date='20240229';version=$null;previous_operation_folder=$null;legacy=$true}
Valid 'roundtrip preserves operation version' @{Action='ToOperation';FolderName='20240229_daily_review';PreviousOperationFolderName='daily_review_20240229_v2.10'} 'daily_review_20240229_v2.10' @{version='2.10';previous_operation_folder='daily_review_20240229_v2.10'}
Valid 'retry with same lineage stays unchanged' @{Action='ToOperation';FolderName='20240229_daily_review';PreviousOperationFolderName='daily_review_20240229_v2.10'} 'daily_review_20240229_v2.10'
Valid 'explicit revision after roundtrip' @{Action='ToOperation';FolderName='20240229_daily_review';PreviousOperationFolderName='daily_review_20240229_v0.99';IncrementVersion=$true} 'daily_review_20240229_v1.00'
foreach($date in @('20260229','20241301','20240001','20240431','00000101','20241000','2024101','２０２４０２２９')){Invalid ('invalid date '+$date) @{Action='ToOperation';FolderName=($date+'_daily_review')}}
foreach($version in @('0.00','01.01','00.01','0.1','1.0','1.000','-1.01','+1.01','1e2.01','1,01','١.01')){Invalid ('noncanonical version '+$version) @{Action='NextVersion';FolderName=('daily_review_20261017_v'+$version)}}
Invalid 'uppercase version marker' @{Action='NextVersion';FolderName='daily_review_20261017_V0.01'}
Invalid 'malformed operation cannot become legacy project' @{Action='ToProject';FolderName='20200101_notes_20261017_v0.1'}
foreach($name in @('','.','..','20261017_','_20261017_v0.01',' 20261017_daily','20261017_daily ','.20261017_daily','20261017_daily.','20261017_ daily','20261017_daily_20261017_v0.01','20261017_daily_20261017_v0.1','20261017_daily_20261017_V0.01','C:\20261017_daily','\\server\share\20261017_daily','../20261017_daily','20261017_a\b','20261017_a/b','20261017_a:b','20261017_a<b','20261017_a>b','20261017_a|b','20261017_a?b','20261017_a*b','20261017_a"b','20261017_CON','20261017_con.txt','20261017_COM1.foo','20261017_LPT².txt','20261017_CONIN$','20261017_CON .txt')){
    Invalid ('unsafe or malformed leaf '+$name) @{Action='ToOperation';FolderName=$name}
}
foreach($control in @([char]0,[char]10,[char]13,[char]127,[char]0x85)){Invalid ('control character '+[int]$control) @{Action='ToOperation';FolderName=('20261017_daily'+$control+'review')}}
Invalid 'oversized input leaf' @{Action='ToOperation';FolderName=('20261017_'+('x'*247))}
Invalid 'valid input but generated target too long' @{Action='ToOperation';FolderName=('20261017_'+('x'*241))}
Invalid 'UTF16 surrogate pair counts as two units' @{Action='ToOperation';FolderName=('20261017_'+('x'*239)+'😀')}
Invalid 'increment would exceed Windows component limit' @{Action='NextVersion';FolderName=(('x'*240)+'_20261017_v9.99')}
Invalid 'prior semantic mismatch' @{Action='ToOperation';FolderName='20261017_daily_review';PreviousOperationFolderName='weekly_review_20261017_v0.01'}
Invalid 'prior creation date mismatch' @{Action='ToOperation';FolderName='20261017_daily_review';PreviousOperationFolderName='daily_review_20261016_v0.01'}
Invalid 'case mismatch is not silently renamed' @{Action='ToOperation';FolderName='20261017_daily_review';PreviousOperationFolderName='Daily_review_20261017_v0.01'}
Invalid 'prior path rejected' @{Action='ToOperation';FolderName='20261017_daily_review';PreviousOperationFolderName='C:\daily_review_20261017_v0.01'}
Invalid 'explicit empty prior rejected' @{Action='ToOperation';FolderName='20261017_daily_review';PreviousOperationFolderName=''}
Invalid 'legacy unversioned prior is not canonical version lineage' @{Action='ToOperation';FolderName='20261017_daily_review';PreviousOperationFolderName='20261017_daily_review'}
Invalid 'prior only allowed on ToOperation' @{Action='ToProject';FolderName='daily_review_20261017_v0.01';PreviousOperationFolderName='daily_review_20261017_v0.01'}
Invalid 'increment requires prior lineage' @{Action='ToOperation';FolderName='20261017_daily_review';IncrementVersion=$true}
Invalid 'increment only allowed on ToOperation' @{Action='NextVersion';FolderName='daily_review_20261017_v0.01';IncrementVersion=$true}
Invalid 'unknown action' @{Action='Rename';FolderName='20261017_daily_review'}
Invalid 'missing action' @{FolderName='20261017_daily_review'}
Invalid 'missing folder name' @{Action='ToOperation'}

[IO.Directory]::CreateDirectory($FixtureRoot)|Out-Null
$workspace=Join-Path $FixtureRoot 'unchanged_workspace'
$logs=Join-Path $FixtureRoot 'logs'
[IO.Directory]::CreateDirectory($workspace)|Out-Null
[IO.Directory]::CreateDirectory($logs)|Out-Null
foreach($folder in @('20261017_daily_review','daily_review_20261017_v0.01','.hidden_history')){
    $directory=Join-Path $workspace $folder
    [IO.Directory]::CreateDirectory($directory)|Out-Null
    [IO.File]::WriteAllText((Join-Path $directory 'sentinel.txt'),'must remain unchanged',$utf8)
}
function Snapshot{
    return (@(Get-ChildItem -LiteralPath $workspace -Recurse -Force|Sort-Object FullName|ForEach-Object{
        $relative=[IO.Path]::GetRelativePath($workspace,$_.FullName)
        if($_.PSIsContainer){'D|'+$relative}else{'F|'+$relative+'|'+(Get-FileHash -LiteralPath $_.FullName).Hash+'|'+$_.LastWriteTimeUtc.Ticks}
    }) -join [Environment]::NewLine)
}
$worker=Join-Path $FixtureRoot 'Invoke-Case.ps1'
[IO.File]::WriteAllText($worker,@'
param([string]$HelperPath,[string]$CasePath)
$ErrorActionPreference='Stop'
$inputCase=Get-Content -LiteralPath $CasePath -Raw|ConvertFrom-Json
$parameters=@{}
foreach($property in $inputCase.PSObject.Properties){$parameters[$property.Name]=$property.Value}
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
& $HelperPath @parameters
exit $LASTEXITCODE
'@,$utf8)
$before=Snapshot
$results=[Collections.Generic.List[object]]::new()
$index=0
foreach($case in $cases){
    $index++
    $prefix='case_'+$index.ToString('D3')
    $casePath=Join-Path $logs ($prefix+'.json')
    [IO.File]::WriteAllText($casePath,($case.parameters|ConvertTo-Json -Depth 4),$utf8)
    $psi=[Diagnostics.ProcessStartInfo]::new($pwsh)
    $psi.UseShellExecute=$false;$psi.CreateNoWindow=$true;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true;$psi.WorkingDirectory=$workspace
    $psi.StandardOutputEncoding=$utf8;$psi.StandardErrorEncoding=$utf8
    foreach($argument in @('-NoLogo','-NoProfile','-NonInteractive','-File',$worker,'-HelperPath',$HelperPath,'-CasePath',$casePath)){$psi.ArgumentList.Add($argument)}
    $process=[Diagnostics.Process]::Start($psi)
    $outTask=$process.StandardOutput.ReadToEndAsync();$errTask=$process.StandardError.ReadToEndAsync()
    if(-not $process.WaitForExit(10000)){$process.Kill($true);throw 'Pure naming helper unexpectedly exceeded ten seconds.'}
    $stdout=$outTask.GetAwaiter().GetResult();$stderr=$errTask.GetAwaiter().GetResult();$exitCode=$process.ExitCode;$process.Dispose()
    [IO.File]::WriteAllText((Join-Path $logs ($prefix+'.stdout.json')),$stdout,$utf8)
    [IO.File]::WriteAllText((Join-Path $logs ($prefix+'.stderr.txt')),$stderr,$utf8)
    $report=$null;$issues=[Collections.Generic.List[string]]::new()
    try{$report=$stdout|ConvertFrom-Json -AsHashtable}catch{$issues.Add('Output was not structured JSON.')}
    if($case.valid){
        if($exitCode -ne 0 -or $report.ok -ne $true -or $report.target -cne $case.target){$issues.Add('Successful transformation or target mismatch.')}
        foreach($key in $case.fields.Keys){if($report[$key] -cne $case.fields[$key]){$issues.Add('Field mismatch: '+$key)}}
        if($report.previous_operation_folder -and (-not $report.lineage.operation_version -or $report.lineage.operation_folder_name -cne $report.previous_operation_folder)){$issues.Add('Previous operation lineage is incomplete.')}
    }else{
        if($exitCode -eq 0 -or $report.ok -ne $false -or $null -ne $report.target -or -not $report.error){$issues.Add('Invalid input was not cleanly refused.')}
    }
    if((Snapshot) -cne $before){$issues.Add('Filesystem side effect detected.')}
    $results.Add(@{name=$case.name;passed=($issues.Count -eq 0);issues=$issues.ToArray();exit_code=$exitCode})
}
$sourceUnchanged=(Get-FileHash -LiteralPath $HelperPath).Hash -ceq $sourceHash
$passed=(@($results|Where-Object{-not $_.passed}).Count -eq 0 -and $sourceUnchanged)
$receipt=[ordered]@{passed=$passed;case_count=$cases.Count;results=$results.ToArray();source_unchanged=$sourceUnchanged;workspace_unchanged=((Snapshot) -ceq $before);helper_sha256=$sourceHash.ToLowerInvariant();host_version=$PSVersionTable.PSVersion.ToString();fixture_root=$FixtureRoot;limits='PowerShell7 behavior tested with synthetic names only. No real folders converted. PowerShell5.1 execution is not claimed.'}
[IO.File]::WriteAllText((Join-Path $FixtureRoot 'validation.json'),($receipt|ConvertTo-Json -Depth 8),$utf8)
$receipt|ConvertTo-Json -Depth 8
if(-not $passed){exit 1}
