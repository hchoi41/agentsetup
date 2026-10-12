<#
.SYNOPSIS
  The switchboard for 10.OPERATION: list the operations, check a switch, flip it, add or remove a row.

.DESCRIPTION
  10.OPERATION/OPERATIONS_DASHBOARD.md holds one table row per operation:

      | Operation | Switch | Cadence | Runner | Notes |

  The first cell is the operation's folder name; the second is its switch.
  A run may go ahead only when exactly one row names the operation and its switch says ON.

    status                  list every operation; flag rows without a folder and folders without a row
    check  <operation>      the gate every run calls first (see exit codes)
    on     <operation>      set the switch to ON
    off    <operation>      set the switch to OFF
    add    <operation>      append a row (-Cadence, -Runner, -Notes, -State ON|OFF; the switch defaults to OFF)
                            -IfMissing: do nothing when the row is already there
    remove <operation>      delete the row

  on/off change only the switch cell; add/remove change only their own line. Every other character of the file
  is kept, including its line endings and its byte-order mark. A dashboard that is not UTF-8 text is never
  rewritten. Runs must NOT write to the dashboard: it is hand-edited and synced, and automated writers create
  conflict copies.

  Exit codes
    0   done. For check: the switch is ON - go ahead.
    3   check: the switch is OFF. A deliberate stop: end the run quietly.
    4   check: cannot tell - no dashboard, no row, two rows, an unreadable file, a switch that is neither
        ON nor OFF. Do not run, and let the scheduler show a failed run so that someone looks.
    1   error (every action except check).

.EXAMPLE
  # The gate: first lines of a scheduled PowerShell script.
  $global:LASTEXITCODE = 4
  & 'D:\workspace\scripts\operation_switch.ps1' check 20260901_refresh_finance_calendar
  if ($LASTEXITCODE -eq 3) { exit 0 }                # switched OFF: stop quietly
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }    # cannot tell: stop loudly

.EXAMPLE
  .\operation_switch.ps1 status
  .\operation_switch.ps1 off 20260901_refresh_finance_calendar
  .\operation_switch.ps1 add 20261010_publish_blog_weekly_digest -Cadence 'weekly, Mon 08:00' -Runner 'desk PC, Task Scheduler' -State ON
#>
[CmdletBinding(PositionalBinding = $false)]
param(
  [Parameter(Mandatory = $true, Position = 0)] [ValidateSet('status', 'check', 'on', 'off', 'add', 'remove')] [string]$Action,
  [Parameter(Position = 1)] [string]$Operation,
  [string]$Root,                                     # workspace root; default: the parent of this script's folder
  [string]$Cadence = '',
  [string]$Runner = '',
  [string]$Notes = '',
  [ValidateSet('ON', 'OFF')] [string]$State = 'OFF',   # for add; not named -Switch because $switch is reserved inside a switch statement
  [switch]$IfMissing                                 # for add
)

$ErrorActionPreference = 'Stop'

function Stop-Unknown { param([string]$Msg) Write-Host "DO NOT RUN (cannot tell): $Msg"; exit 4 }
function Fail { param([string]$Msg)
  if ($Action -eq 'check') { Stop-Unknown $Msg }
  Write-Host "ERROR: $Msg" -ForegroundColor Red
  exit 1
}
function Same { param([string]$A, [string]$B) return [string]::Equals($A, $B, [System.StringComparison]::OrdinalIgnoreCase) }
function Clean-Cell { param([string]$Text) return ($Text.Trim() -replace '^[`*]+|[`*]+$', '').Trim() }
function Safe-Cell  { param([string]$Text) return ((($Text -replace '\r?\n', ' ') -replace '(?<!\\)\|', '\|').Trim()) }
function Split-Row  { param([string]$Line)
  $body = $Line.Trim()
  if ($body.StartsWith('|')) { $body = $body.Substring(1) }
  if ($body.EndsWith('|') -and -not $body.EndsWith('\|')) { $body = $body.Substring(0, $body.Length - 1) }
  return , @([regex]::Split($body, '(?<!\\)\|') | ForEach-Object { $_.Trim() })
}

try {
  if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
  $Root  = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Root)
  $OpDir = Join-Path $Root '10.OPERATION'
  $Dash  = Join-Path $OpDir 'OPERATIONS_DASHBOARD.md'

  $Template = @(
    '# Operations Dashboard', '',
    'One row per operation in this folder. **Switch** is the on/off switch: a run goes ahead only when its row says `ON`. Anything else (`OFF`, no row, a typo) and the run stops before it starts.', '',
    'Flip a switch by editing the cell, or with `scripts/operation_switch.ps1 on|off <operation>`. For the summary, run `scripts/operation_switch.ps1 status`.', '',
    '| Operation | Switch | Cadence | Runner | Notes |',
    '| --- | --- | --- | --- | --- |'
  ) -join "`n"

  # Reads the dashboard into lines (terminators kept) and the rows of its operations table.
  # The operations table is the first table whose header starts with: | Operation | Switch |
  function Read-Dashboard {
    if (-not [System.IO.File]::Exists($Dash)) { return $null }
    $bytes = [System.IO.File]::ReadAllBytes($Dash)
    $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $skip = 0; if ($bom) { $skip = 3 }
    $strict = New-Object System.Text.UTF8Encoding($false, $true)
    try { $text = $strict.GetString($bytes, $skip, $bytes.Length - $skip) }
    catch { throw "the dashboard is not UTF-8 text - it may have been saved in another encoding: $Dash" }
    $lines = [regex]::Split($text, '(?<=\n)')
    $rows  = New-Object System.Collections.Generic.List[object]
    $header = -1; $last = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
      $t = $lines[$i].Trim()
      if (-not $t.StartsWith('|')) { if ($header -ge 0) { break } else { continue } }    # the table ends at the first other line
      $cells = Split-Row $lines[$i]
      if ($header -lt 0) {
        if ($cells.Count -ge 2 -and (Same (Clean-Cell $cells[0]) 'Operation') -and (Same (Clean-Cell $cells[1]) 'Switch')) { $header = $i; $last = $i }
        continue
      }
      $last = $i
      if (@($cells | Where-Object { $_ -notmatch '^:?-+:?$' }).Count -eq 0) { continue }   # separator row
      $name = Clean-Cell $cells[0]
      if (-not $name) { continue }
      $sw = ''; if ($cells.Count -gt 1) { $sw = (Clean-Cell $cells[1]).ToUpperInvariant() }
      $rows.Add([pscustomobject]@{ Index = $i; Name = $name; Switch = $sw; Cells = $cells })
    }
    $nl = "`n"; if ($text.Contains("`r`n")) { $nl = "`r`n" }
    return [pscustomobject]@{ Lines = $lines; Rows = $rows; Header = $header; LastTableLine = $last; Newline = $nl; Bom = $bom }
  }

  function Write-Dashboard { param([string[]]$Lines, [bool]$Bom)
    $tmp = "$Dash.tmp"
    [System.IO.File]::WriteAllText($tmp, ($Lines -join ''), (New-Object System.Text.UTF8Encoding($Bom)))
    if ([System.IO.File]::Exists($Dash)) {
      try { [System.IO.File]::Replace($tmp, $Dash, [NullString]::Value) }
      catch { [System.IO.File]::Copy($tmp, $Dash, $true); [System.IO.File]::Delete($tmp) }
    } else {
      [System.IO.File]::Move($tmp, $Dash)
    }
  }

  function Find-Rows { param($Board, [string]$Name) return , @($Board.Rows | Where-Object { Same $_.Name $Name }) }

  # Replaces the text of the switch cell and nothing else on the line.
  function Set-SwitchCell { param([string]$Line, [string]$Value)
    $eol = ''; if ($Line -match '(\r?\n)$') { $eol = $Matches[1] }
    $body = $Line.Substring(0, $Line.Length - $eol.Length)
    $pipes = @([regex]::Matches($body, '(?<!\\)\|') | ForEach-Object { $_.Index })
    $first = 0; if (-not $body.TrimStart().StartsWith('|')) { $first = -1 }    # a row written without its opening pipe
    $open = $first + 1                                                       # index in $pipes of the pipe that opens the switch cell
    if ($open -lt 0 -or $open -ge $pipes.Count) { return $null }
    $start = $pipes[$open] + 1
    $end = $body.Length; if ($open + 1 -lt $pipes.Count) { $end = $pipes[$open + 1] }
    $old = $body.Substring($start, $end - $start)
    $lead = ' '; $trail = ' '
    if ($old.Trim().Length -gt 0) {
      $lead = $old.Substring(0, $old.Length - $old.TrimStart().Length)
      $trail = $old.Substring($old.TrimEnd().Length)
    } elseif ($end -eq $body.Length) { $trail = '' }
    return $body.Substring(0, $start) + $lead + $Value + $trail + $body.Substring($end) + $eol
  }

  if ($Action -ne 'status' -and [string]::IsNullOrWhiteSpace($Operation)) { Fail "'$Action' needs an operation name" }
  if ($Operation) { $Operation = $Operation.Trim().TrimEnd('\', '/') }

  if ($Action -eq 'check') {
    $board = Read-Dashboard
    if (-not $board) { Stop-Unknown "dashboard not found: $Dash" }
    if ($board.Header -lt 0) { Stop-Unknown "the dashboard has no table that starts with | Operation | Switch |" }
    $hit = Find-Rows $board $Operation
    if ($hit.Count -eq 0) { Stop-Unknown "'$Operation' has no row on the dashboard" }
    if ($hit.Count -gt 1) { Stop-Unknown "'$Operation' has $($hit.Count) rows on the dashboard" }
    if ($hit[0].Switch -eq 'ON')  { Write-Host "ON: $Operation"; exit 0 }
    if ($hit[0].Switch -eq 'OFF') { Write-Host "OFF: $Operation - do not run"; exit 3 }
    Stop-Unknown "the switch of '$Operation' reads '$($hit[0].Switch)'; it must be ON or OFF"
  }

  if ($Action -eq 'status') {
    $board = Read-Dashboard
    if (-not $board) { Write-Host "No dashboard at $Dash"; exit 0 }
    $folders = @()
    if (Test-Path -LiteralPath $OpDir -PathType Container) { $folders = @(Get-ChildItem -LiteralPath $OpDir -Directory | ForEach-Object { $_.Name }) }
    $issues = New-Object System.Collections.Generic.List[string]
    if ($board.Header -lt 0) { $issues.Add('the dashboard has no table that starts with | Operation | Switch |') }
    $out = foreach ($r in $board.Rows) {
      $dir = Join-Path $OpDir $r.Name
      $has = Test-Path -LiteralPath $dir -PathType Container
      $last = ''
      $outDir = Join-Path $dir '30.OUTPUT'
      if ($has -and (Test-Path -LiteralPath $outDir -PathType Container)) {
        $newest = Get-ChildItem -LiteralPath $outDir -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($newest) { $last = $newest.LastWriteTime.ToString('yyyy-MM-dd') }
      }
      if (-not $has) { $issues.Add("row without a folder: $($r.Name)") }
      if ($r.Switch -ne 'ON' -and $r.Switch -ne 'OFF') { $issues.Add("switch reads '$($r.Switch)' - its runs will stop with 'cannot tell': $($r.Name)") }
      if ((Find-Rows $board $r.Name).Count -gt 1) { $issues.Add("more than one row - its runs will stop with 'cannot tell': $($r.Name)") }
      $cad = ''; if ($r.Cells.Count -gt 2) { $cad = $r.Cells[2] }
      $run = ''; if ($r.Cells.Count -gt 3) { $run = $r.Cells[3] }
      $shown = '?'; if ($r.Switch -eq 'ON' -or $r.Switch -eq 'OFF') { $shown = $r.Switch }
      [pscustomobject]@{ Operation = $r.Name; Switch = $shown; Cadence = $cad; Runner = $run; 'Last output' = $last }
    }
    foreach ($f in $folders) { if ((Find-Rows $board $f).Count -eq 0) { $issues.Add("folder without a row - it never runs: $f") } }
    if ($out) { $out | Format-Table -AutoSize | Out-String -Width 220 | Write-Host }
    $on  = @($board.Rows | Where-Object { $_.Switch -eq 'ON' }).Count
    $off = @($board.Rows | Where-Object { $_.Switch -eq 'OFF' }).Count
    Write-Host ("{0} operation(s): {1} ON, {2} OFF, {3} unreadable" -f $board.Rows.Count, $on, $off, ($board.Rows.Count - $on - $off))
    foreach ($i in ($issues | Select-Object -Unique)) { Write-Host "  ! $i" -ForegroundColor Yellow }
    exit 0
  }

  # ---- on / off / add / remove: one writer at a time, and the file is read again inside the lock
  if ($Action -eq 'add') {
    if ($Operation -match '[<>:"/\\|?*`\x00-\x1f]' -or $Operation.EndsWith('.')) { Fail "not usable as a folder name: '$Operation'" }
  }
  $sha = [System.Security.Cryptography.SHA1]::Create()
  $key = [System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Dash.ToLowerInvariant()))).Replace('-', '').Substring(0, 16)
  $mutex = New-Object System.Threading.Mutex($false, "Global\operation_switch_$key")
  $held = $false
  try {
    try { $held = $mutex.WaitOne(15000) } catch [System.Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { Fail 'another operation_switch run is writing the dashboard; try again' }
    $board = Read-Dashboard

    if ($Action -eq 'on' -or $Action -eq 'off') {
      if (-not $board) { Fail "dashboard not found: $Dash" }
      $hit = Find-Rows $board $Operation
      if ($hit.Count -ne 1) { Fail "'$Operation' has $($hit.Count) rows on the dashboard; exactly one is needed" }
      $want = $Action.ToUpperInvariant()
      $row  = $hit[0]
      if ($row.Switch -eq $want) { Write-Host "$Operation is already $want"; exit 0 }
      $new = Set-SwitchCell $board.Lines[$row.Index] $want
      if ($null -eq $new) { Fail "the row for '$Operation' has no Switch cell" }
      $board.Lines[$row.Index] = $new
      Write-Dashboard $board.Lines $board.Bom
      Write-Host "$Operation : $($row.Switch) -> $want"
      exit 0
    }

    if ($Action -eq 'add') {
      if (-not $board) {
        [void][System.IO.Directory]::CreateDirectory($OpDir)
        [System.IO.File]::WriteAllText($Dash, ($Template + "`n"), (New-Object System.Text.UTF8Encoding($false)))
        $board = Read-Dashboard
        Write-Host "created $Dash"
      }
      if ((Find-Rows $board $Operation).Count -gt 0) {
        if ($IfMissing) { Write-Host "$Operation already has a row; left as it is"; exit 0 }
        Fail "'$Operation' already has a row on the dashboard"
      }
      if ($board.Header -lt 0) { Fail "the dashboard has no table that starts with | Operation | Switch | : $Dash" }
      $line  = '| ' + ((@($Operation, $State, $Cadence, $Runner, $Notes) | ForEach-Object { Safe-Cell $_ }) -join ' | ') + ' |'
      $lines = New-Object System.Collections.Generic.List[string]
      for ($i = 0; $i -lt $board.Lines.Count; $i++) {
        $l = $board.Lines[$i]
        if ($i -eq $board.LastTableLine) {
          if ($l -notmatch '\n$') { $l = $l + $board.Newline }       # the table ended at end-of-file without a newline
          $lines.Add($l); $lines.Add($line + $board.Newline)
        } else { $lines.Add($l) }
      }
      Write-Dashboard $lines.ToArray() $board.Bom
      Write-Host "added: $Operation (switch $State)"
      if (-not (Test-Path -LiteralPath (Join-Path $OpDir $Operation) -PathType Container)) { Write-Host "note: there is no folder 10.OPERATION/$Operation yet" -ForegroundColor Yellow }
      exit 0
    }

    if ($Action -eq 'remove') {
      if (-not $board) { Fail "dashboard not found: $Dash" }
      $hit = Find-Rows $board $Operation
      if ($hit.Count -ne 1) { Fail "'$Operation' has $($hit.Count) rows on the dashboard; exactly one is needed" }
      $lines = New-Object System.Collections.Generic.List[string]
      for ($i = 0; $i -lt $board.Lines.Count; $i++) { if ($i -ne $hit[0].Index) { $lines.Add($board.Lines[$i]) } }
      Write-Dashboard $lines.ToArray() $board.Bom
      Write-Host "removed: $Operation (was switched '$($hit[0].Switch)')"
      exit 0
    }
  }
  finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
  }
}
catch {
  Fail $_.Exception.Message
}
