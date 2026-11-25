<#
rule-of-thumb-model: one Excel job (design section 12). Owner: CORE-X.

Run by lib/excel.py only:
  powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File excel_job.ps1 -Job <job.json> -Result <result.json>

What it does:
  1. Reads the job JSON (written by excel.py; the operations are already checked and normalised there).
  2. Starts a new Excel instance (New-Object -ComObject Excel.Application), finds its process id from the
     window handle and writes it to the job's pid file at once.
  3. If that process id was running before the job started, the instance is not ours (for example Max's own
     Excel): the job releases its reference, does not close that Excel, and reports it.
  4. Opens the workbook (links not updated, macros off), runs the operations in order, saves when asked.
     pq_attach and pq_refresh set PreserveFormatting on and AdjustColumnWidth off on every query table they
     touch and, when the op holds 'formats', apply each raw table's look to its whole loaded range afterwards.
     After a save, excel.py removes the folder path and the user name Excel writes into the file.
  5. Closes the workbook, quits, releases the COM objects, collects garbage; if the process it started is
     still alive after 5 seconds, it stops that process id only.
  6. Writes the result JSON (always, also after a failure).

Exit codes: 0 every operation ran; 1 an operation failed (nothing saved); 3 Excel could not be started;
4 the job file could not be read.
#>
param(
    [Parameter(Mandatory = $true)][string]$Job,
    [Parameter(Mandatory = $true)][string]$Result
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Off

Add-Type -AssemblyName System.Web.Extensions
Add-Type -AssemblyName Microsoft.VisualBasic
$ser = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$ser.MaxJsonLength = [int]::MaxValue
$ser.RecursionLimit = 1000

$clock = [System.Diagnostics.Stopwatch]::StartNew()
$res = @{
    rotm_excel_result = 1
    ok                = $false
    pid               = $null
    attached          = $false
    killed            = $false
    start_failed      = $false
    excel_version     = $null
    saved             = $null
    read_only         = $null
    steps             = (New-Object System.Collections.ArrayList)
    errors            = (New-Object System.Collections.ArrayList)
    notes             = (New-Object System.Collections.ArrayList)
    elapsed_s         = 0
}

function To-Json($v, [int]$depth = 0) {
    # Explicit JSON writer: PowerShell wraps values in PSObject, which JavaScriptSerializer cannot serialise.
    # Leaves (strings, numbers) are escaped by JavaScriptSerializer; anything else becomes its text.
    if ($null -eq $v) { return 'null' }
    $b = $v
    if ($v -is [System.Management.Automation.PSObject]) { $b = $v.PSObject.BaseObject }
    if ($null -eq $b) { return 'null' }
    if ($b -is [string]) { return $ser.Serialize([string]$b) }
    if ($b -is [bool]) { if ($b) { return 'true' } else { return 'false' } }
    if ($b -is [int] -or $b -is [long] -or $b -is [int16] -or $b -is [byte] -or $b -is [uint32]) { return ([string][long]$b) }
    if ($b -is [double] -or $b -is [single] -or $b -is [decimal]) {
        $d = [double]$b
        if ([double]::IsNaN($d) -or [double]::IsInfinity($d)) { return 'null' }
        return $d.ToString('R', [System.Globalization.CultureInfo]::InvariantCulture)
    }
    if ($depth -gt 60) { return $ser.Serialize([string]$b) }
    if ($b -is [System.Collections.IDictionary]) {
        $parts = New-Object System.Collections.Generic.List[string]
        foreach ($k in $b.Keys) { $parts.Add(($ser.Serialize([string]$k) + ':' + (To-Json $b[$k] ($depth + 1)))) }
        return '{' + ($parts -join ',') + '}'
    }
    if ($b -is [System.Collections.IEnumerable]) {
        $parts = New-Object System.Collections.Generic.List[string]
        foreach ($x in $b) { $parts.Add((To-Json $x ($depth + 1))) }
        return '[' + ($parts -join ',') + ']'
    }
    return $ser.Serialize([string]$b)
}

function Write-ResultFile {
    $res.elapsed_s = [Math]::Round($clock.Elapsed.TotalSeconds, 3)
    $text = To-Json $res
    [System.IO.File]::WriteAllText($Result, $text, (New-Object System.Text.UTF8Encoding $false))
}

function Add-Err([string]$text) { [void]$res.errors.Add($text) }
function Add-Note([string]$text) { [void]$res.notes.Add($text) }

function Get-Opt($dict, [string]$key, $default) {
    if ($null -ne $dict -and $dict.ContainsKey($key) -and $null -ne $dict[$key]) { return $dict[$key] }
    return $default
}

function Short-Message($err) {
    $m = $err.Exception.Message
    if ($err.Exception.InnerException) { $m = $err.Exception.InnerException.Message }
    if ($null -eq $m) { $m = "$err" }
    return ($m -replace '\s+', ' ').Trim()
}

# ----------------------------------------------------------------------------- job file
try {
    $jobText = [System.IO.File]::ReadAllText($Job, [System.Text.Encoding]::UTF8)
    $j = $ser.DeserializeObject($jobText)
} catch {
    Add-Err ("the job file could not be read: " + (Short-Message $_))
    Write-ResultFile
    exit 4
}

$workbookPath = [string]$j['workbook']
$readOnly = [bool](Get-Opt $j 'read_only' $false)
$doSave = [bool](Get-Opt $j 'save' $false)
$saveAs = Get-Opt $j 'save_as' $null
$fileFormat = [int](Get-Opt $j 'file_format' 51)
$pidFile = [string]$j['pid_file']
$progId = [string](Get-Opt $j 'progid' 'Excel.Application')
$treatAsForeign = [bool](Get-Opt $j 'treat_as_foreign' $false)
# Arrays are read with List-Of, not Get-Opt: PowerShell unrolls an array returned from a function.
function List-Of($dict, [string]$key) {
    if ($null -ne $dict -and $dict.ContainsKey($key) -and $null -ne $dict[$key]) { return , @($dict[$key]) }
    return , @()
}
$ops = List-Of $j 'operations'
$before = New-Object System.Collections.Generic.HashSet[int]
foreach ($p in (List-Of $j 'before_pids')) { [void]$before.Add([int]$p) }
foreach ($p in @(Get-Process -Name EXCEL -ErrorAction SilentlyContinue)) { [void]$before.Add([int]$p.Id) }

$res.read_only = $readOnly

# ----------------------------------------------------------------------------- Excel error values
# Range.Value2 returns an error cell as an Int32: 0x800A0000 + the xlErr code (design 12, find_errors).
$ErrBase = -2146828288
$ErrText = @{
    2000 = '#NULL!'; 2007 = '#DIV/0!'; 2015 = '#VALUE!'; 2023 = '#REF!'; 2029 = '#NAME?'; 2036 = '#NUM!'
    2042 = '#N/A'; 2043 = '#GETTING_DATA'; 2045 = '#SPILL!'; 2046 = '#CONNECT!'; 2047 = '#BLOCKED!'
    2048 = '#UNKNOWN!'; 2049 = '#FIELD!'; 2050 = '#CALC!'
}

function Convert-Value($v) {
    # One cell value as a JSON-able object: number, text, bool, null, or @{error = '#DIV/0!'}.
    if ($null -eq $v) { return $null }
    if ($v -is [int]) {
        $code = $v - $ErrBase
        if ($ErrText.ContainsKey($code)) { return @{ error = $ErrText[$code] } }
        return @{ error = ('#ERROR(' + $code + ')') }
    }
    if ($v -is [double] -or $v -is [string] -or $v -is [bool]) { return $v }
    if ($v -is [decimal] -or $v -is [single] -or $v -is [long]) { return [double]$v }
    if ($v -is [datetime]) { return $v.ToOADate() }
    return [string]$v
}

function Convert-RangeValue($v) {
    # A multi-cell Value2 is object[,] (1-based): return a list of rows.
    if ($null -ne $v -and $v -is [System.Array] -and $v.Rank -eq 2) {
        $rows = New-Object System.Collections.ArrayList
        $r0 = $v.GetLowerBound(0); $r1 = $v.GetUpperBound(0)
        $c0 = $v.GetLowerBound(1); $c1 = $v.GetUpperBound(1)
        for ($r = $r0; $r -le $r1; $r++) {
            $row = New-Object System.Collections.ArrayList
            for ($c = $c0; $c -le $c1; $c++) { [void]$row.Add((Convert-Value $v[$r, $c])) }
            [void]$rows.Add($row)
        }
        return , $rows
    }
    return (Convert-Value $v)
}

function To-BGR([string]$hex) {
    # "RRGGBB" -> Excel colour number (blue * 65536 + green * 256 + red).
    $r = [Convert]::ToInt32($hex.Substring(0, 2), 16)
    $g = [Convert]::ToInt32($hex.Substring(2, 2), 16)
    $b = [Convert]::ToInt32($hex.Substring(4, 2), 16)
    return ($b * 65536 + $g * 256 + $r)
}

function Get-Sheet([string]$name) {
    if ([string]::IsNullOrEmpty($name)) { return $script:wb.Worksheets.Item(1) }
    foreach ($s in $script:wb.Worksheets) { if ($s.Name -eq $name) { return $s } }
    throw "the workbook has no worksheet named '$name'"
}

function Clean-Address([string]$a) { return $a.Replace('$', '') }

# ----------------------------------------------------------------------------- operations
# Each Do-* function fills the hashtable $step. Callers use `$null = Do-X ...` so nothing leaks into output.

function Do-Recalc($op, $step) {
    $mode = [string](Get-Opt $op 'mode' 'full_rebuild')
    try { $script:app.CalculateUntilAsyncQueriesDone() } catch { }
    switch ($mode) {
        'full_rebuild' { $script:app.CalculateFullRebuild() }
        'full' { $script:app.CalculateFull() }
        default { $script:app.Calculate() }
    }
    try { $script:app.CalculateUntilAsyncQueriesDone() } catch { }
    $step.mode = $mode
    $cm = [int]$script:app.Calculation
    $step.calc_mode = switch ($cm) { -4105 { 'automatic' } -4135 { 'manual' } 2 { 'semiautomatic' } default { [string]$cm } }
    $step.calc_state = [int]$script:app.CalculationState   # 0 = done
}

function Do-Read($op, $step) {
    $values = New-Object System.Collections.ArrayList
    foreach ($ref in (List-Of $op 'refs')) {
        $item = @{ ref = $ref['ref'] }
        try {
            $ws = Get-Sheet ([string]$ref['sheet'])
            $rng = $ws.Range([string]$ref['address'])
            $item.sheet = $ws.Name
            $item.address = Clean-Address $rng.Address()
            $item.value = (Convert-RangeValue $rng.Value2)
            if ($rng.Count -eq 1) { $item.text = [string]$rng.Text }
        } catch {
            $item.problem = (Short-Message $_)
        }
        [void]$values.Add($item)
    }
    $step.values = $values
}

function Do-Errors($op, $step) {
    $limit = [int](Get-Opt $op 'limit' 5000)
    $only = $null
    if ($op.ContainsKey('sheets') -and $null -ne $op['sheets']) { $only = List-Of $op 'sheets' }
    $cells = New-Object System.Collections.ArrayList
    $total = 0
    foreach ($ws in $script:wb.Worksheets) {
        if ($null -ne $only -and -not ($only -contains $ws.Name)) { continue }
        foreach ($kind in @(-4123, 2)) {        # xlCellTypeFormulas, xlCellTypeConstants
            $found = $null
            try { $found = $ws.UsedRange.SpecialCells($kind, 16) } catch { $found = $null }   # 16 = xlErrors
            if ($null -eq $found) { continue }
            $total += [int64]$found.CountLarge
            foreach ($area in $found.Areas) {
                foreach ($c in $area.Cells) {
                    if ($cells.Count -ge $limit) { break }
                    $v = Convert-Value $c.Value2
                    $text = if ($v -is [hashtable]) { $v.error } else { [string]$c.Text }
                    [void]$cells.Add(@($ws.Name, (Clean-Address $c.Address()), $text))
                }
                if ($cells.Count -ge $limit) { break }
            }
        }
    }
    # v2 (A-aera-5): the circular reference Excel reports on each sheet after the recalculation (one cell per
    # sheet); its formula has no value. Python adds the cells the saved file holds without a value.
    foreach ($ws in $script:wb.Worksheets) {
        if ($null -ne $only -and -not ($only -contains $ws.Name)) { continue }
        $cr = $null
        try { $cr = $ws.CircularReference } catch { $cr = $null }
        if ($null -eq $cr) { continue }
        $addr = Clean-Address $cr.Address()
        $seen = $false
        foreach ($c in $cells) { if ($c[0] -eq $ws.Name -and $c[1] -eq $addr) { $seen = $true } }
        if ($seen) { continue }
        $total += 1
        if ($cells.Count -lt $limit) { [void]$cells.Add(@($ws.Name, $addr, 'no value (circular reference)')) }
    }
    $step.cells = $cells
    $step.count = $total
    $step.truncated = ($total -gt $cells.Count)
}

function Do-Inventory($op, $step) {
    $sheets = New-Object System.Collections.ArrayList
    foreach ($sh in $script:wb.Sheets) {
        $kind = [Microsoft.VisualBasic.Information]::TypeName($sh)
        $info = @{ name = $sh.Name; kind = $kind; visible = [int]$sh.Visible }
        if ($kind -eq 'Worksheet') {
            $info.used = Clean-Address $sh.UsedRange.Address()
            $los = New-Object System.Collections.ArrayList
            foreach ($lo in $sh.ListObjects) {
                $q = $false
                $conn = $null
                try { $qt = $lo.QueryTable; if ($null -ne $qt) { $q = $true; $conn = $qt.WorkbookConnection.Name } } catch { }
                [void]$los.Add(@{ name = $lo.Name; range = (Clean-Address $lo.Range.Address()); rows = [int]$lo.ListRows.Count; source_type = [int]$lo.SourceType; query = $q; connection = $conn })
            }
            $info.tables = $los
            $info.charts = [int]$sh.ChartObjects().Count
            $info.shapes = [int]$sh.Shapes.Count
            $info.format_conditions = [int]$sh.Cells.FormatConditions.Count
            $dv = 0
            try { $dv = [int64]$sh.Cells.SpecialCells(-4174).CountLarge } catch { $dv = 0 }   # xlCellTypeAllValidation
            $info.validation_cells = $dv
            $info.query_tables = [int]$sh.QueryTables.Count
            $pts = 0
            try { $pts = [int]$sh.PivotTables().Count } catch { }
            $info.pivot_tables = $pts
        }
        [void]$sheets.Add($info)
    }
    $names = New-Object System.Collections.ArrayList
    # Names starting with _xlfn. are Excel's own (for example _xlfn.SINGLE) and are not saved in the file.
    foreach ($n in $script:wb.Names) { if (-not ([string]$n.Name).StartsWith('_xlfn.')) { [void]$names.Add($n.Name) } }
    $queries = New-Object System.Collections.ArrayList
    # v4 (C-sol-1): query_formulas holds each query's name and its M text (formula null when Excel cannot give it),
    # so build can carry a query attached by hand over a rebuild.
    $formulas = New-Object System.Collections.ArrayList
    try {
        foreach ($q in $script:wb.Queries) {
            [void]$queries.Add($q.Name)
            $f = $null
            try { $f = [string]$q.Formula } catch { $f = $null }
            [void]$formulas.Add(@{ name = [string]$q.Name; formula = $f })
        }
    } catch { }
    $conns = New-Object System.Collections.ArrayList
    foreach ($c in $script:wb.Connections) { [void]$conns.Add($c.Name) }
    $step.sheets = $sheets
    $step.names = $names
    $step.queries = $queries
    $step.query_formulas = $formulas
    $step.connections = $conns
    $step.has_vba = [bool]$script:wb.HasVBProject
}

function Apply-Look($r, $spec) {
    # One look (font, fill, number_format, border, align; normalised by excel.py) onto one range.
    $font = Get-Opt $spec 'font' $null
    $hasFill = $spec.ContainsKey('fill')
    $fill = $spec['fill']
    $nf = Get-Opt $spec 'number_format' $null
    $border = Get-Opt $spec 'border' $null
    $align = Get-Opt $spec 'align' $null
    if ($null -ne $font) {
        if ($font.ContainsKey('name') -and $null -ne $font['name']) { $r.Font.Name = [string]$font['name'] }
        if ($font.ContainsKey('size') -and $null -ne $font['size']) { $r.Font.Size = [double]$font['size'] }
        if ($font.ContainsKey('bold') -and $null -ne $font['bold']) { $r.Font.Bold = [bool]$font['bold'] }
        if ($font.ContainsKey('italic') -and $null -ne $font['italic']) { $r.Font.Italic = [bool]$font['italic'] }
        if ($font.ContainsKey('underline') -and $null -ne $font['underline']) {
            $r.Font.Underline = $(if ([bool]$font['underline']) { 2 } else { -4142 })   # xlUnderlineStyleSingle / None
        }
        if ($font.ContainsKey('color')) {
            if ($null -eq $font['color']) { $r.Font.ColorIndex = -4105 } else { $r.Font.Color = (To-BGR ([string]$font['color'])) }
        }
    }
    if ($hasFill) {
        if ($null -eq $fill) { $r.Interior.Pattern = -4142 } else { $r.Interior.Pattern = 1; $r.Interior.Color = (To-BGR ([string]$fill)) }
    }
    if ($null -ne $nf) { $r.NumberFormat = [string]$nf }
    if ($null -ne $border) {
        foreach ($side in $border.Keys) {
            $bspec = $border[$side]
            $first = $true
            foreach ($idx in (List-Of $bspec 'indices')) {
                # The first index is the outer edge; the second (inside lines) does not exist on a
                # one-row or one-column range, so its failure is ignored.
                try {
                    $b = $r.Borders.Item([int]$idx)
                    if ([int]$bspec['line'] -eq -4142) { $b.LineStyle = -4142 }
                    else {
                        $b.LineStyle = [int]$bspec['line']
                        $b.Weight = [int]$bspec['weight']
                        if ($null -ne $bspec['color']) { $b.Color = (To-BGR ([string]$bspec['color'])) }
                    }
                } catch { if ($first) { throw } }
                $first = $false
            }
        }
    }
    if ($null -ne $align) {
        if ($align.ContainsKey('horizontal') -and $null -ne $align['horizontal']) { $r.HorizontalAlignment = [int]$align['horizontal'] }
        if ($align.ContainsKey('vertical') -and $null -ne $align['vertical']) { $r.VerticalAlignment = [int]$align['vertical'] }
        if ($align.ContainsKey('wrap') -and $null -ne $align['wrap']) { $r.WrapText = [bool]$align['wrap'] }
        if ($align.ContainsKey('indent') -and $null -ne $align['indent']) { $r.IndentLevel = [int]$align['indent'] }
    }
}

function Do-Style($op, $step) {
    $ws = Get-Sheet ([string]$op['sheet'])
    $n = 0
    foreach ($addr in (List-Of $op 'addresses')) {
        $r = $ws.Range([string]$addr)
        Apply-Look $r $op
        $n++
    }
    $step.sheet = $ws.Name
    $step.address_groups = $n
}

function Set-QueryTableKeep($qt) {
    # v2 (A-aera-8): Excel keeps the cell formats on refresh and does not change the column widths.
    try { $qt.PreserveFormatting = $true } catch { }
    try { $qt.AdjustColumnWidth = $false } catch { }
}

function Apply-RawFormats($lo, $fmt, $item) {
    # v2 (A-aera-8): the raw table's look after a load or a refresh. $fmt = @{ columns = [look per column, in
    # table order]; header = look }. Each column's look goes onto its whole loaded data range (DataBodyRange),
    # the header look onto the header row.
    $cols = List-Of $fmt 'columns'
    $n = [int]$lo.ListColumns.Count
    $done = 0
    $limit = [Math]::Min($n, $cols.Count)
    for ($i = 1; $i -le $limit; $i++) {
        $spec = $cols[$i - 1]
        if ($null -eq $spec) { continue }
        $body = $null
        try { $body = $lo.ListColumns.Item($i).DataBodyRange } catch { $body = $null }
        if ($null -eq $body) { continue }
        try { Apply-Look $body $spec } catch { throw ("the formats of column " + $i + " of " + $lo.Name + " could not be applied: " + (Short-Message $_)) }
        $done++
    }
    $hdr = Get-Opt $fmt 'header' $null
    if ($null -ne $hdr) {
        $h = $null
        try { $h = $lo.HeaderRowRange } catch { $h = $null }
        if ($null -ne $h) {
            try { Apply-Look $h $hdr } catch { throw ("the header look of " + $lo.Name + " could not be applied: " + (Short-Message $_)) }
        }
    }
    $item.formatted_columns = $done
    $item.formatted_rows = 0
    try { $item.formatted_rows = [int]$lo.ListRows.Count } catch { }
}

function Get-RawFormat($op, [string]$sheetName) {
    $fmts = Get-Opt $op 'formats' $null
    if ($null -eq $fmts -or -not ($fmts -is [System.Collections.IDictionary])) { return $null }
    if ($fmts.ContainsKey($sheetName)) { return $fmts[$sheetName] }
    return $null
}

function Do-Formula($op, $step) {
    $ws = Get-Sheet ([string]$op['sheet'])
    $r = $ws.Range([string]$op['cell'])
    $f = [string]$op['formula']
    try {
        if ([bool](Get-Opt $op 'dynamic' $false)) { $r.Formula2 = $f } else { $r.Formula = $f }
    } catch {
        throw ("Excel refused the formula for " + $ws.Name + "!" + $op['cell'] + ": " + $f + " (" + (Short-Message $_) + ")")
    }
    $step.cell = $ws.Name + '!' + (Clean-Address $r.Address())
}

function Do-Value($op, $step) {
    $ws = Get-Sheet ([string]$op['sheet'])
    $r = $ws.Range([string]$op['cell'])
    $v = $op['value']
    if ($null -eq $v) { [void]$r.ClearContents() }
    elseif ([bool](Get-Opt $op 'as_text' $false)) { $r.Value2 = ("'" + [string]$v) }
    elseif ($v -is [decimal]) { $r.Value2 = [double]$v }
    else { $r.Value2 = $v }
    $step.cell = $ws.Name + '!' + (Clean-Address $r.Address())
}

function Do-FillDown($op, $step) {
    $ws = Get-Sheet ([string]$op['sheet'])
    $from = [int]$op['from_row']; $to = [int]$op['to_row']
    $c1 = [int]$op['first_col']; $c2 = [int]$op['last_col']
    $rng = $ws.Range($ws.Cells.Item($from, $c1), $ws.Cells.Item($to, $c2))
    [void]$rng.FillDown()
    $step.range = $ws.Name + '!' + (Clean-Address $rng.Address())
}

function Do-AddSheet($op, $step) {
    $name = [string]$op['name']
    foreach ($s in $script:wb.Sheets) { if ($s.Name -eq $name) { throw "a sheet named '$name' exists already" } }
    $afterName = Get-Opt $op 'after' $null
    if ($null -eq $afterName) { $after = $script:wb.Sheets.Item($script:wb.Sheets.Count) } else { $after = $script:wb.Sheets.Item([string]$afterName) }
    $new = $script:wb.Worksheets.Add([Type]::Missing, $after)
    $new.Name = $name
    $step.name = $new.Name
}

function Do-Clear($op, $step) {
    $ws = Get-Sheet ([string]$op['sheet'])
    $what = [string](Get-Opt $op 'what' 'contents')
    foreach ($addr in (List-Of $op 'addresses')) {
        $r = $ws.Range([string]$addr)
        switch ($what) {
            'all' { [void]$r.Clear() }
            'formats' { [void]$r.ClearFormats() }
            default { [void]$r.ClearContents() }
        }
    }
    $step.sheet = $ws.Name
    $step.what = $what
}

function Do-Render($op, $step) {
    # One PNG per sheet: Range.CopyPicture (as shown on screen) pasted into a temporary chart object,
    # then Chart.Export. The workbook is not saved by a render job.
    $outDir = [string]$op['out_dir']
    $maxRows = [int](Get-Opt $op 'max_rows' 200)
    $maxCols = [int](Get-Opt $op 'max_cols' 40)
    $only = $null
    if ($op.ContainsKey('sheets') -and $null -ne $op['sheets']) { $only = List-Of $op 'sheets' }
    $files = New-Object System.Collections.ArrayList
    $script:app.ScreenUpdating = $true
    # The list is taken first: moving a chart sheet (below) changes the Sheets collection.
    $all = New-Object System.Collections.ArrayList
    foreach ($sh in $script:wb.Sheets) { [void]$all.Add($sh) }
    $tmpSheet = $null
    $i = 0
    foreach ($sh in $all) {
        $i++
        if ($null -ne $only -and -not ($only -contains $sh.Name)) { continue }
        $kind = [Microsoft.VisualBasic.Information]::TypeName($sh)
        $file = Join-Path $outDir ('sheet_' + $i + '.png')
        $item = @{ sheet = [string]$sh.Name; index = $i; kind = $kind; file = $file }
        try {
            if ([int]$sh.Visible -ne -1) { $sh.Visible = -1; $item.was_hidden = $true }
            if ($kind -eq 'Chart') {
                # Measured on this PC (2026-11-25): a chart sheet has size 0 x 0 in a hidden instance and its
                # Export writes an empty file. Moved onto a temporary worksheet as a 720 x 450 pt chart object,
                # it exports. The workbook is not saved, so the move is not kept.
                if ($null -eq $tmpSheet) {
                    $tmpSheet = $script:wb.Worksheets.Add()
                    $tmpSheet.Name = 'rotm_render_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
                }
                $ch = $sh.Location(2, $tmpSheet.Name)      # xlLocationAsObject
                $co = $ch.Parent
                $co.Width = 720; $co.Height = 450
                [void]$tmpSheet.Activate()
                [void]$co.Activate()
                [void]$ch.Export($file, 'PNG')
                $item.width_pt = 720
                $item.height_pt = 450
            } else {
                $ur = $sh.UsedRange
                $lastRow = [int]($ur.Row + $ur.Rows.Count - 1)
                $lastCol = [int]($ur.Column + $ur.Columns.Count - 1)
                foreach ($shp in $sh.Shapes) {          # charts and shapes do not extend the used range
                    try {
                        $br = $shp.BottomRightCell
                        if ([int]$br.Row -gt $lastRow) { $lastRow = [int]$br.Row }
                        if ([int]$br.Column -gt $lastCol) { $lastCol = [int]$br.Column }
                    } catch { }
                }
                $rows = [Math]::Min($lastRow, $maxRows)
                $cols = [Math]::Min($lastCol, $maxCols)
                $rng = $sh.Range($sh.Cells.Item(1, 1), $sh.Cells.Item($rows, $cols))
                $item.range = Clean-Address $rng.Address()
                $item.last_row = $lastRow
                $item.last_col = $lastCol
                $item.truncated = ($lastRow -gt $rows -or $lastCol -gt $cols)
                $w = [double]$rng.Width; $h = [double]$rng.Height
                if ($w -lt 1) { $w = 48 }
                if ($h -lt 1) { $h = 15 }
                # Measured on this PC (2026-11-25): xlPicture fails or pastes blank in a hidden instance;
                # xlBitmap pastes the sheet as shown when the sheet and the chart object are activated first.
                [void]$sh.Activate()
                $copied = $false
                for ($try = 1; $try -le 3 -and -not $copied; $try++) {
                    try { [void]$rng.CopyPicture(1, 2); $copied = $true }   # xlScreen, xlBitmap
                    catch { Start-Sleep -Milliseconds (300 * $try) }
                }
                if (-not $copied) { throw "Range.CopyPicture failed three times (the clipboard may be in use)" }
                # Seen on this PC (2026-11-25, integrator): a tall bitmap pasted at once can export blank;
                # a short wait lets the clipboard picture settle. Python re-renders a sheet whose PNG is blank.
                Start-Sleep -Milliseconds 200
                $co = $sh.ChartObjects().Add(0, 0, $w, $h)
                try {
                    try { $co.Chart.ChartArea.Format.Line.Visible = 0 } catch { }
                    [void]$co.Activate()
                    [void]$co.Chart.Paste()
                    Start-Sleep -Milliseconds 100
                    [void]$co.Chart.Export($file, 'PNG')
                } finally {
                    [void]$co.Delete()
                }
                $item.width_pt = $w
                $item.height_pt = $h
            }
            $item.ok = (Test-Path -LiteralPath $file)
            if (-not $item.ok) { $item.problem = 'Chart.Export wrote no file' }
        } catch {
            $item.ok = $false
            $item.problem = (Short-Message $_)
        }
        [void]$files.Add($item)
    }
    try { [void]$script:app.CutCopyMode } catch { }
    try { $script:app.CutCopyMode = $false } catch { }
    $step.files = $files
    $bad = @($files | Where-Object { -not $_.ok })
    if ($bad.Count -gt 0) { $step.failed = $bad.Count }
}

function Find-QueryTable($ws, [string]$queryName) {
    foreach ($lo in $ws.ListObjects) {
        try {
            $qt = $lo.QueryTable
            if ($null -ne $qt) {
                $conn = [string]$qt.Connection
                if ($conn -match ('Location="?' + [regex]::Escape($queryName) + '"?(;|$)')) { return $lo }
            }
        } catch { }
    }
    return $null
}

function Do-PqAttach($op, $step) {
    $ws = Get-Sheet ([string]$op['sheet'])
    $tableName = [string]$op['table_name']
    $queryName = [string]$op['query_name']
    $m = [string]$op['m_text']
    $q = $null
    foreach ($x in $script:wb.Queries) { if ($x.Name -eq $queryName) { $q = $x } }
    if ($null -ne $q) { $q.Formula = $m; $step.query = 'updated' } else { [void]$script:wb.Queries.Add($queryName, $m); $step.query = 'added' }
    $lo = Find-QueryTable $ws $queryName
    if ($null -eq $lo) {
        $unlisted = New-Object System.Collections.ArrayList
        foreach ($old in @($ws.ListObjects)) { [void]$unlisted.Add($old.Name); [void]$old.Unlist() }
        $step.unlisted = $unlisted
        [void]$ws.UsedRange.ClearContents()        # values go; cell formats stay for the query rows
        $loc = $queryName
        if ($queryName -match '[^A-Za-z0-9_]') { $loc = '"' + $queryName + '"' }
        $conn = 'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=' + $loc + ';Extended Properties=""'
        $lo = $ws.ListObjects.Add(0, $conn, [Type]::Missing, 1, $ws.Range('A1'))   # xlSrcExternal, xlYes
        $qt = $lo.QueryTable
        $qt.CommandType = 2                          # xlCmdSql
        $qt.CommandText = 'SELECT * FROM [' + $queryName + ']'
        $qt.RowNumbers = $false
        $qt.FillAdjacentFormulas = $false
        $qt.PreserveFormatting = $true
        $qt.RefreshOnFileOpen = $false
        $qt.BackgroundQuery = $false
        $qt.RefreshStyle = 1                         # xlInsertDeleteCells
        $qt.SavePassword = $false
        $qt.SaveData = $true
        $qt.AdjustColumnWidth = $false
        $qt.RefreshPeriod = 0
        $qt.PreserveColumnInfo = $true
        try { $qt.WorkbookConnection.Name = 'Query - ' + $queryName } catch { }
        $lo.Name = $tableName
        try { $lo.TableStyle = '' } catch { }
        $step.table = 'added'
    } else {
        $lo.Name = $tableName
        $step.table = 'kept'
    }
    $qt = $lo.QueryTable
    $qt.BackgroundQuery = $false
    Set-QueryTableKeep $qt
    try { [void]$qt.Refresh($false) } catch { throw ("the query " + $queryName + " did not refresh: " + (Short-Message $_)) }
    try { $script:app.CalculateUntilAsyncQueriesDone() } catch { }
    $fmt = Get-RawFormat $op $ws.Name
    if ($null -ne $fmt) { Apply-RawFormats $lo $fmt $step }
    $step.sheet = $ws.Name
    $step.table_name = $lo.Name
    $step.range = Clean-Address $lo.Range.Address()
    $step.rows = [int]$lo.ListRows.Count
}

function Do-PqRefresh($op, $step) {
    $waitS = [double](Get-Opt $op 'wait_s' 300)
    $tables = New-Object System.Collections.ArrayList
    $done = New-Object System.Collections.Generic.HashSet[string]
    $failed = 0
    foreach ($ws in $script:wb.Worksheets) {
        foreach ($lo in $ws.ListObjects) {
            $qt = $null
            try { $qt = $lo.QueryTable } catch { $qt = $null }
            if ($null -eq $qt) { continue }
            $item = @{ sheet = $ws.Name; table = $lo.Name }
            try {
                $qt.BackgroundQuery = $false
                Set-QueryTableKeep $qt
                try { [void]$done.Add([string]$qt.WorkbookConnection.Name) } catch { }
                [void]$qt.Refresh($false)
                $item.ok = $true
            } catch {
                $item.ok = $false
                $item.problem = (Short-Message $_)
                $failed++
            }
            $item.rows = [int]$lo.ListRows.Count
            $item.range = Clean-Address $lo.Range.Address()
            [void]$tables.Add($item)
        }
        foreach ($qt in $ws.QueryTables) {
            $item = @{ sheet = $ws.Name; table = $qt.Name }
            try { $qt.BackgroundQuery = $false; Set-QueryTableKeep $qt; [void]$qt.Refresh($false); $item.ok = $true } catch { $item.ok = $false; $item.problem = (Short-Message $_); $failed++ }
            [void]$tables.Add($item)
        }
    }
    $others = New-Object System.Collections.ArrayList
    foreach ($c in $script:wb.Connections) {
        if ($done.Contains([string]$c.Name)) { continue }
        $item = @{ connection = $c.Name }
        try { try { $c.OLEDBConnection.BackgroundQuery = $false } catch { }; [void]$c.Refresh(); $item.ok = $true } catch { $item.ok = $false; $item.problem = (Short-Message $_); $failed++ }
        [void]$others.Add($item)
    }
    try { $script:app.CalculateUntilAsyncQueriesDone() } catch { }
    $deadline = (Get-Date).AddSeconds($waitS)
    $still = $true
    while ($still) {
        $still = $false
        foreach ($ws in $script:wb.Worksheets) {
            foreach ($lo in $ws.ListObjects) { try { if ($lo.QueryTable.Refreshing) { $still = $true } } catch { } }
        }
        if ($still) {
            if ((Get-Date) -gt $deadline) { throw ("a query was still refreshing after " + $waitS + " s") }
            Start-Sleep -Milliseconds 250
        }
    }
    # v2 (A-aera-8): the raw tables' formats again on their whole loaded range (more rows, fewer rows)
    foreach ($item in $tables) {
        if (-not $item.ok -or -not $item.ContainsKey('rows')) { continue }
        $fmt = Get-RawFormat $op ([string]$item.sheet)
        if ($null -eq $fmt) { continue }
        $lo = $null
        try { $lo = (Get-Sheet ([string]$item.sheet)).ListObjects.Item([string]$item.table) } catch { $lo = $null }
        if ($null -ne $lo) { Apply-RawFormats $lo $fmt $item }
    }
    $step.tables = $tables
    $step.connections = $others
    $step.failed = $failed
    if ($failed -gt 0) { throw ("" + $failed + " query refresh(es) failed; see the step details") }
}

function Invoke-Op($op, $step) {
    switch ([string]$op['op']) {
        'recalc' { $null = Do-Recalc $op $step }
        'read' { $null = Do-Read $op $step }
        'errors' { $null = Do-Errors $op $step }
        'inventory' { $null = Do-Inventory $op $step }
        'style' { $null = Do-Style $op $step }
        'formula' { $null = Do-Formula $op $step }
        'value' { $null = Do-Value $op $step }
        'fill_down' { $null = Do-FillDown $op $step }
        'add_sheet' { $null = Do-AddSheet $op $step }
        'clear' { $null = Do-Clear $op $step }
        'render' { $null = Do-Render $op $step }
        'pq_attach' { $null = Do-PqAttach $op $step }
        'pq_refresh' { $null = Do-PqRefresh $op $step }
        'sleep' { Start-Sleep -Seconds ([double](Get-Opt $op 'seconds' 1)) }
        default { throw ("unknown operation '" + $op['op'] + "'") }
    }
}

# ----------------------------------------------------------------------------- start Excel
Add-Type -Namespace RotmExcel -Name Win32 -MemberDefinition @'
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr hWnd, out uint lpdwProcessId);
'@

$app = $null
$wbs = $null
$wb = $null
$xpid = $null
$exitCode = 0

try {
    $app = New-Object -ComObject $progId
} catch {
    $res.start_failed = $true
    Add-Err ("Excel could not be started (" + $progId + "): " + (Short-Message $_))
    Write-ResultFile
    exit 3
}

try {
    try {
        [uint32]$found = 0
        [void][RotmExcel.Win32]::GetWindowThreadProcessId([System.IntPtr][int64]$app.Hwnd, [ref]$found)
        if ($found -gt 0) { $xpid = [int]$found }
    } catch { Add-Note ("the Excel process id could not be read: " + (Short-Message $_)) }
    if ($null -ne $xpid) {
        [System.IO.File]::WriteAllText($pidFile, [string]$xpid, (New-Object System.Text.UTF8Encoding $false))
        $res.pid = $xpid
    }
    if ($treatAsForeign -or ($null -ne $xpid -and $before.Contains($xpid))) {
        $res.attached = $true
        Add-Err ("attached to an Excel instance this job did not start (PID " + $xpid + "); it was left running and nothing was done")
    } else {
        try { $res.excel_version = [string]$app.Version } catch { }
        foreach ($setting in @('Visible', 'DisplayAlerts', 'ScreenUpdating', 'EnableEvents', 'AskToUpdateLinks')) {
            try { $app.$setting = $false } catch { Add-Note ("could not set Application." + $setting + ": " + (Short-Message $_)) }
        }
        try { $app.AutomationSecurity = 3 } catch { Add-Note ("could not turn macros off: " + (Short-Message $_)) }   # msoAutomationSecurityForceDisable

        $wbs = $app.Workbooks
        # A wrong open password makes an encrypted file fail instead of showing a prompt. A write-reservation
        # password is not passed: Excel refuses a dummy one even on a file that has none (measured 2026-11-25).
        $noPw = 'rotm-no-password'
        try {
            $wb = $wbs.Open($workbookPath, 0, $readOnly, [Type]::Missing, $noPw, [Type]::Missing, $true, [Type]::Missing, [Type]::Missing, $false, $false, [Type]::Missing, $false)
        } catch {
            throw ("Excel could not open " + $workbookPath + ": " + (Short-Message $_))
        }
        if (-not $readOnly -and $wb.ReadOnly) {
            throw ("Excel opened " + $workbookPath + " read-only: the file is open in another program or is read-only. Close it there and run again.")
        }
        $index = 0
        foreach ($op in $ops) {
            $index++
            $step = @{ op = [string]$op['op']; index = $index; ok = $false }
            [void]$res.steps.Add($step)
            $t0 = $clock.Elapsed.TotalMilliseconds
            try {
                Invoke-Op $op $step
                $step.ok = $true
            } catch {
                $step.problem = (Short-Message $_)
                Add-Err ("operation " + $index + " (" + $op['op'] + ") failed: " + $step.problem)
            }
            $step.ms = [int]($clock.Elapsed.TotalMilliseconds - $t0)
            if (-not $step.ok) { break }
        }
        if ($res.errors.Count -eq 0) {
            if ($null -ne $saveAs) {
                $app.DisplayAlerts = $false
                $wb.SaveAs([string]$saveAs, $fileFormat, [Type]::Missing, [Type]::Missing, $false, $false, 1, 2)
                $res.saved = [string]$saveAs
            } elseif ($doSave) {
                $wb.Save()
                $res.saved = $workbookPath
            }
            $res.ok = $true
        } elseif ($null -ne $saveAs -or $doSave) {
            Add-Note "nothing was saved because an operation failed"
        }
    }
} catch {
    Add-Err (Short-Message $_)
} finally {
    if (-not $res.attached) {
        if ($null -ne $wb) { try { $wb.Close($false) } catch { } }
        if ($null -ne $app) { try { $app.Quit() } catch { } }
    }
    foreach ($o in @($wb, $wbs, $app)) {
        if ($null -ne $o) { try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($o) } catch { } }
    }
    $wb = $null; $wbs = $null; $app = $null
    # Caught COM errors keep error records in $Error that can hold references to Excel objects.
    $Error.Clear()
    [GC]::Collect(); [GC]::WaitForPendingFinalizers(); [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    if (-not $res.attached -and $null -ne $xpid) {
        $gone = $false
        for ($w = 0; $w -lt 50 -and -not $gone; $w++) {
            if ($null -eq (Get-Process -Id $xpid -ErrorAction SilentlyContinue)) { $gone = $true }
            else {
                if ($w % 5 -eq 4) { [GC]::Collect(); [GC]::WaitForPendingFinalizers() }
                Start-Sleep -Milliseconds 100
            }
        }
        if (-not $gone) {
            try { Stop-Process -Id $xpid -Force -ErrorAction Stop; $res.killed = $true; Add-Note ("Excel PID " + $xpid + " was still running 5 s after Quit and was stopped") } catch { Add-Note ("Excel PID " + $xpid + " could not be stopped: " + (Short-Message $_)) }
        }
    }
    if ($res.errors.Count -gt 0) { $res.ok = $false; $exitCode = 1 }
    Write-ResultFile
}
exit $exitCode
