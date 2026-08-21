<#
  Orchestrate.ps1 — generalized multi-LLM "tiki-taka" conductor for the GITLESS hub.

  Generalizes Orchestrate-Build.ps1: the ROLES stay locked, the GATE is pluggable,
  and the JOB (targets, tasks, gate, models) is defined by a CONFIG FILE — so the
  same proven loop drives CODE or a DOCUMENT.

  Locked roles (config.roles may override model/effort, not the choreography):
    Codex   (5.6 Sol @ xhigh)  — MAKER: writes/fixes the work product; and RED-TEAM (review #1).
    Copilot (auto)             — REVIEWER #2 + tie-break input + end-of-run bug tracker.
    Claude  (Opus 5 @ extra)   — CONDUCTOR (this script) + REFEREE (tie-break, fires only on disagreement).
    Gemini  (3.7 Flash @ high) — optional swap-in for any reviewer/referee role via config.roles.

  Pluggable gate (deterministic layer) — config.gate.checks[] or task.gate.checks[]:
    { "type":"pytest", "path":"tests\\memory" }          -> python -m pytest <path> -q
    { "type":"command","command":"pwsh -File ... " }      -> any native command; exit 0 = pass
    (no checks)                                            -> no deterministic gate; quality rests on the triad
  Pass = ALL checks exit 0. The review triad (judgment layer) is unchanged from the build conductor.

  Progress + issue tracking (per run dir under _orchestrate\):
    _status.json   — LIVE machine-readable state (percent, current task/step, per-task verdicts, issues)
    _run.log       — timestamped append-only feed (tail it to watch progress)
    ERRORS.log     — every issue, with the exact artifact path to investigate
    RUN_SUMMARY.md — human-readable end-of-run (or end-of-failure) index
    Write-Progress — a live bar too, when run interactively (no-op when backgrounded)

  Usage:
    .\Orchestrate.ps1 -Config orchestrate\configs\design_doc_retrofit.json -Preflight   # check CLIs, run nothing
    .\Orchestrate.ps1 -Config orchestrate\configs\design_doc_retrofit.json -DryRun       # print the resolved plan
    .\Orchestrate.ps1 -Config orchestrate\configs\design_doc_retrofit.json               # run (spends Codex/Copilot credits)
    .\Orchestrate.ps1 -Config ... -Task A                                                # run a single task by id
#>
param(
  [Parameter(Mandatory)][string]$Config,
  [string]$Task,
  [int]$MaxFix = -1,
  [string]$HubRoot,
  [switch]$DryRun,
  [switch]$Preflight
)
$ErrorActionPreference = 'Stop'

# ---------- load config ----------
if (-not (Test-Path $Config)) { throw "Config not found: $Config" }
$cfg = Get-Content $Config -Raw | ConvertFrom-Json
# Hub root, most-specific wins: config.hub > -HubRoot > $env:AGENT_HUB_ROOT > this script's own folder.
# No machine-specific default: the engine is portable and resolves its hub at run time.
$Hub = if     ($cfg.hub)              { "$($cfg.hub)" }
       elseif ($HubRoot)              { "$HubRoot" }
       elseif ($env:AGENT_HUB_ROOT)   { "$env:AGENT_HUB_ROOT" }
       else                           { Split-Path -Parent $PSCommandPath }
if (-not (Test-Path -LiteralPath $Hub)) { throw "Hub root not found: $Hub  (set config.hub, -HubRoot, or `$env:AGENT_HUB_ROOT)" }
$workProduct = if ($cfg.workProduct) { "$($cfg.workProduct)" } else { 'code' }
if ($MaxFix -lt 0) { $MaxFix = if ($null -ne $cfg.maxFix) { [int]$cfg.maxFix } else { 2 } }

function Resolve-Role($name, $defCli, $defModel, $defEffort) {
  $r = $cfg.roles.$name
  [pscustomobject]@{
    cli    = if ($r -and $r.cli)    { "$($r.cli)" }    else { $defCli }
    model  = if ($r -and $r.model)  { "$($r.model)" }  else { $defModel }
    effort = if ($r -and $r.effort) { "$($r.effort)" } else { $defEffort }
  }
}
$Maker     = Resolve-Role 'maker'     'codex'   'gpt-5.6-sol'   'xhigh'
$Reviewer1 = Resolve-Role 'reviewer1' 'codex'   'gpt-5.6-sol'   'xhigh'
$Reviewer2 = Resolve-Role 'reviewer2' 'copilot' 'auto'          ''
$Referee   = Resolve-Role 'referee'   'claude'  'claude-opus-5' 'extra'

$RunRoot   = "$Hub\_orchestrate\$($cfg.name)_$(Get-Date -f yyyyMMdd_HHmmss)"
$JsonRule  = 'When verdict is "changes", classify the SINGLE most-severe BLOCKER: "none"=a normal in-scope fix the maker can apply; "unresolved"=reviewers cannot converge but it is not clearly scope/design/achievability; "scope_change"=needs a meaningful change to the task scope (cannot be fixed within the current scope); "design_change"=the plan/design does not work for the real-world goal and must be redesigned; "unachievable"=the desired output cannot be achieved as specified (contradiction or impossible requirement). End your reply with EXACTLY ONE fenced json code block (```json ... ```), nothing after it: {"verdict":"approve|changes","blocker":"none|unresolved|scope_change|design_change|unachievable","must_fix":["..."],"notes":"..."}'
$ScopeRule = 'SCOPE: judge ONLY against THIS task''s own acceptance criteria + the rubric. Anything assigned to a LATER task (or a mechanism not chosen) is OUT OF SCOPE — do NOT request it. If this task''s criteria are met and the gate is green, the verdict is approve.'

# ---------- escalation policy (chain of command) ----------
# mode 'auto' = full delegation, never ping the human (commander on holiday). mode 'tiered' = escalate when a
# blocker's severity >= the configured rank. Cumulative: a lower rank also catches everything more severe.
$EscMode    = if ($cfg.escalation -and $cfg.escalation.mode)  { "$($cfg.escalation.mode)" }  else { 'auto' }
$EscLevel   = if ($cfg.escalation -and $cfg.escalation.level) { "$($cfg.escalation.level)" } else { 'general' }
$EscMinIter = if ($cfg.escalation -and $cfg.escalation.minIterationsForDesign) { [int]$cfg.escalation.minIterationsForDesign } else { 3 }
$EscSev       = @{ none = 0; unresolved = 1; scope_change = 2; design_change = 3; unachievable = 4 }
$EscRank      = @{ unresolved = 'General / Officer'; scope_change = 'Chief of Staff'; design_change = 'Secretary of Defense'; unachievable = 'Commander-in-Chief' }
$EscThreshold = @{ auto = 99; general = 1; chief_of_staff = 2; secretary = 3; commander = 4 }

# ---------- vendor adapters (flags verified: codex-cli 0.142.4 / claude 2.1.191 / copilot 1.0.65; gemini-cli unverified on this machine — file-based prompt, no --effort support) ----------
function Invoke-Maker([string]$prompt, [string]$out) {     # WRITES files
  switch ($Maker.cli) {
    'codex'  { $prompt | codex -C $Hub exec --dangerously-bypass-approvals-and-sandbox -m $Maker.model -c "model_reasoning_effort=$($Maker.effort)" -o $out -;
               if (Test-Path $out) { Get-Content $out -Raw } else { '' } }
    'copilot'{ $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; copilot -p "Read the file at '$pf' and follow its instructions exactly. You may edit files in the workspace as needed for the requested task. Output a concise summary of files changed." --allow-all-tools --allow-all-paths --model $Maker.model *> $out; Remove-Item $pf -Force -ErrorAction SilentlyContinue;
               if (Test-Path $out) { Get-Content $out -Raw } else { '' } }
    'gemini' { $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; gemini -p "Read the file at '$pf' and follow its instructions exactly. You may edit files in the workspace as needed for the requested task. Output a concise summary of files changed." -m $Maker.model --output-format text *> $out; Remove-Item $pf -Force -ErrorAction SilentlyContinue;
               if (Test-Path $out) { Get-Content $out -Raw } else { '' } }
    default  { throw "maker cli '$($Maker.cli)' not supported" }
  }
}
# Review CLIs use a REAL file redirection ('*> $tf'); Copilot additionally gets its prompt via a FILE
# (it takes -p as an ARG, and a large review prompt blows the ~32KB Windows command-line limit — Codex/Claude
# take stdin so they are immune). Each call is wrapped so one CLI's failure degrades to a referee call,
# not a crash.
function Read-Captured($tf) {
  $txt = Get-Content $tf -Raw -ErrorAction SilentlyContinue
  Remove-Item $tf -Force -ErrorAction SilentlyContinue
  if ($txt) { $txt } else { '' }
}
function Invoke-Redteam([string]$prompt) {                 # review #1 — analyze only
  $tf = [System.IO.Path]::GetTempFileName()
  try {
    switch ($Reviewer1.cli) {
      'codex'  { $prompt | codex -C $Hub exec --dangerously-bypass-approvals-and-sandbox -m $Reviewer1.model -c "model_reasoning_effort=$($Reviewer1.effort)" - *> $tf }
      'claude' { $prompt | claude -p --model $Reviewer1.model --effort $Reviewer1.effort --output-format text --max-turns 6 *> $tf }
      'copilot'{ $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; copilot -p "Read the file at '$pf' and follow its instructions exactly. Output only the required fenced JSON verdict block." --allow-all-tools --allow-all-paths --model $Reviewer1.model *> $tf; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
      'gemini' { $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; gemini -p "Read the file at '$pf' and follow its instructions exactly. Output only the required fenced JSON verdict block." -m $Reviewer1.model --output-format text *> $tf; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
      default  { throw "reviewer1 cli '$($Reviewer1.cli)' not supported" }
    }
  } catch { "ADAPTER ERROR: $_" | Out-File -FilePath $tf -Append }
  Read-Captured $tf
}
function Invoke-Reviewer2([string]$prompt) {               # review #2 / bug tracker
  $tf = [System.IO.Path]::GetTempFileName()
  try {
    switch ($Reviewer2.cli) {
      'copilot'{ $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; copilot -p "Read the file at '$pf' and follow its instructions exactly. Output only the required fenced JSON verdict block." --allow-all-tools --allow-all-paths --model $Reviewer2.model *> $tf; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
      'claude' { $prompt | claude -p --model $Reviewer2.model --effort $Reviewer2.effort --output-format text --max-turns 6 *> $tf }
      'codex'  { $prompt | codex -C $Hub exec --dangerously-bypass-approvals-and-sandbox -m $Reviewer2.model -c "model_reasoning_effort=$($Reviewer2.effort)" - *> $tf }
      'gemini' { $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; gemini -p "Read the file at '$pf' and follow its instructions exactly. Output only the required fenced JSON verdict block." -m $Reviewer2.model --output-format text *> $tf; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
      default  { throw "reviewer2 cli '$($Reviewer2.cli)' not supported" }
    }
  } catch { "ADAPTER ERROR: $_" | Out-File -FilePath $tf -Append }
  Read-Captured $tf
}
function Invoke-Referee([string]$prompt) {                 # tie-break (deep)
  $tf = [System.IO.Path]::GetTempFileName()
  try {
    switch ($Referee.cli) {
      'claude' { $prompt | claude -p --model $Referee.model --effort $Referee.effort --output-format text --max-turns 12 *> $tf }
      'codex'  { $prompt | codex -C $Hub exec --dangerously-bypass-approvals-and-sandbox -m $Referee.model -c "model_reasoning_effort=$($Referee.effort)" - *> $tf }
      'copilot'{ $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; copilot -p "Read the file at '$pf' and follow its instructions exactly. Output only the required fenced JSON verdict block." --allow-all-tools --allow-all-paths --model $Referee.model *> $tf; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
      'gemini' { $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; gemini -p "Read the file at '$pf' and follow its instructions exactly. Output only the required fenced JSON verdict block." -m $Referee.model --output-format text *> $tf; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
      default  { throw "referee cli '$($Referee.cli)' not supported" }
    }
  }
  catch { "ADAPTER ERROR: $_" | Out-File -FilePath $tf -Append }
  Read-Captured $tf
}

function Repair-VerdictJson([string]$json) {
  if (-not $json) { return $json }
  $r = $json
  $r = $r -replace '([{\[,:]\s*)\\(?=")', '$1'
  $r = $r -replace '\\(?="\s*[\]\},:])', ''
  $r
}
function Convert-VerdictJson([string]$json) {
  $attempts = @("$json".Trim())
  $repaired = Repair-VerdictJson $attempts[0]
  if ($repaired -and $repaired -ne $attempts[0]) { $attempts += $repaired }
  foreach ($attempt in $attempts) {
    try {
      $o = $attempt | ConvertFrom-Json
      if ($o.PSObject.Properties.Name -contains 'verdict') { return $o }
    } catch {}
  }
  $null
}
function Read-Json([string]$text) {
  $candidates = @()
  foreach ($fence in [regex]::Matches($text, '(?s)```(?:json)?\s*(\{.*?\})\s*```')) { $candidates += $fence.Groups[1].Value }
  foreach ($obj in [regex]::Matches($text, '(?s)\{(?:[^{}]|\{[^{}]*\})*\}')) { $candidates += $obj.Value }
  for ($i = $candidates.Count - 1; $i -ge 0; $i--) {
    $o = Convert-VerdictJson $candidates[$i]
    if ($null -ne $o) { return $o }
  }
  [pscustomobject]@{ verdict='changes'; must_fix=@('reviewer returned no parseable JSON'); notes="$text" }
}

function Get-Snapshot {
  $items = foreach ($p in @($cfg.artifacts)) {
    $full = if ([System.IO.Path]::IsPathRooted("$p")) { "$p" } else { Join-Path $Hub "$p" }
    Get-ChildItem $full -Recurse -File -ErrorAction SilentlyContinue
  }
  ($items | Sort-Object FullName -Unique | ForEach-Object { "### $($_.FullName)`n$(Get-Content $_.FullName -Raw)" }) -join "`n`n"
}

function Resolve-Instruction($task) {
  if ($task.instruction) { return "$($task.instruction)" }
  if ($cfg.taskInstructionTemplate) {
    return ("$($cfg.taskInstructionTemplate)" -replace '\{id\}', "$($task.id)" -replace '\{title\}', "$($task.title)")
  }
  return "Complete task $($task.id)."
}

function Invoke-Gate($td, $round, $task) {
  $checks = @()
  if ($task.gate -and $task.gate.checks) { $checks = @($task.gate.checks) }
  elseif ($cfg.gate -and $cfg.gate.checks) { $checks = @($cfg.gate.checks) }
  if ($checks.Count -eq 0) { return [pscustomobject]@{ pass = $true; output = '(no deterministic gate; quality rests on the review triad + rubric)' } }
  $allPass = $true; $log = ''
  $i = 0
  foreach ($chk in $checks) {
    $i++
    $cmd = if ($chk.type -eq 'pytest') { "python -m pytest `"$(Join-Path $Hub "$($chk.path)")`" -q" } else { "$($chk.command)" }
    $cf  = "$td\gate_${round}_$i.txt"
    $code = 0
    try { Invoke-Expression $cmd *> $cf; $code = $LASTEXITCODE } catch { $code = 1; "$_" | Set-Content $cf }
    if ($null -eq $code) { $code = 0 }
    if ($code -ne 0) { $allPass = $false }
    $log += "[$cmd] exit=$code`n" + (Get-Content $cf -Raw -ErrorAction SilentlyContinue) + "`n"
  }
  [pscustomobject]@{ pass = $allPass; output = $log }
}

function Test-Cli([string]$name) {
  try { $v = (& $name --version 2>&1 | Select-Object -First 1); [pscustomobject]@{ cli = $name; ok = $true; info = "$v" } }
  catch { [pscustomobject]@{ cli = $name; ok = $false; info = 'NOT FOUND on PATH' } }
}
if ($Preflight) {
  Write-Host "Preflight — CLIs for job '$($cfg.name)':" -ForegroundColor Cyan
  @($Maker.cli, $Reviewer1.cli, $Reviewer2.cli, $Referee.cli, 'python') | Select-Object -Unique |
    ForEach-Object { Test-Cli $_ } | Format-Table -Auto | Out-String | Write-Host
  return
}

# ---------- progress + issue tracking ----------
$script:Prog = $null
function Now { (Get-Date).ToString('HH:mm:ss') }
function Save-Status {
  if (-not $script:Prog) { return }
  $script:Prog.updatedAt = (Get-Date).ToString('s')
  try { $script:Prog | ConvertTo-Json -Depth 6 | Set-Content "$RunRoot\_status.json" -Encoding UTF8 } catch {}
}
function Write-Log([string]$msg, [string]$color = 'Gray') {
  $line = "[$(Now)] $msg"
  Write-Host $line -ForegroundColor $color
  try { Add-Content -LiteralPath "$RunRoot\_run.log" -Value $line } catch {}
}
function Init-Tracking {
  $script:Prog = [ordered]@{
    job = "$($cfg.name)"; runId = (Split-Path $RunRoot -Leaf); runDir = $RunRoot
    state = 'running'; startedAt = (Get-Date).ToString('s'); updatedAt = (Get-Date).ToString('s')
    total = $tasks.Count; doneTasks = 0; percent = 0
    current = [ordered]@{ task = ''; step = 'starting'; round = 0 }
    tasks = [ordered]@{}; issues = @()
  }
  foreach ($t in $tasks) { $script:Prog.tasks["$($t.id)"] = [ordered]@{ title = "$($t.title)"; state = 'pending'; round = 0; gate = ''; review1 = ''; review2 = ''; final = '' } }
  Save-Status
}
function Set-Phase([string]$id, [string]$step, [double]$frac, [int]$round = 0) {
  if (-not $script:Prog) { return }
  $idx = @($tasks.id).IndexOf($id); if ($idx -lt 0) { $idx = 0 }
  $pct = [int]([Math]::Min(100, (($idx + $frac) / [Math]::Max(1, $script:Prog.total)) * 100))
  $script:Prog.percent = $pct
  $script:Prog.current.task = $id; $script:Prog.current.step = $step; $script:Prog.current.round = $round
  Save-Status
  try { Write-Progress -Activity "Job $($cfg.name) ($pct%)" -Status "TASK $id - $step" -PercentComplete $pct } catch {}
}
function Add-Issue([string]$id, [string]$step, [string]$message, [string]$artifact) {
  if ($script:Prog) { $script:Prog.issues += , ([ordered]@{ task = $id; step = $step; message = $message; artifact = $artifact; at = (Get-Date).ToString('s') }); Save-Status }
  try { Add-Content -LiteralPath "$RunRoot\ERRORS.log" -Value "[$(Now)] TASK $id - $step - $message - track: $artifact" } catch {}
  Write-Host "  WARN: $message -> track: $artifact" -ForegroundColor Yellow
}
function Is-AdapterFail($v) { (@($v.must_fix) -contains 'reviewer returned no parseable JSON') -or ("$($v.notes)" -match 'ADAPTER ERROR') }
function Write-Summary([string]$state) {
  if (-not $script:Prog) { return }
  $script:Prog.state = $state; Save-Status
  $L = @()
  $L += "# Run summary - $($cfg.name)"
  $L += ""
  $L += "| field | value |"
  $L += "|---|---|"
  $L += "| run | $($script:Prog.runId) |"
  $L += "| state | **$state** |"
  $L += "| approved | $($script:Prog.doneTasks)/$($script:Prog.total) |"
  $L += "| run dir | ``$RunRoot`` |"
  $L += "| live tracking | ``_status.json`` (state) / ``_run.log`` (feed) / ``ERRORS.log`` (issues) |"
  $L += ""
  $L += "## Tasks"
  $L += "| id | title | state | gate | review1 | review2 | final | dir |"
  $L += "|---|---|---|---|---|---|---|---|"
  foreach ($k in $script:Prog.tasks.Keys) { $t = $script:Prog.tasks[$k]; $L += "| $k | $($t.title) | $($t.state) | $($t.gate) | $($t.review1) | $($t.review2) | $($t.final) | ``task_$k\`` |" }
  if (@($script:Prog.issues).Count) {
    $L += ""; $L += "## Issues - track here"
    foreach ($i in $script:Prog.issues) { $L += "- **TASK $($i.task)** - $($i.step) - $($i.message) -> ``$($i.artifact)``" }
  } else { $L += ""; $L += "_No issues recorded._" }
  try { ($L -join "`n") | Set-Content "$RunRoot\RUN_SUMMARY.md" -Encoding UTF8 } catch {}
}

# ---------- escalation decision + human-facing escalation file ----------
function Resolve-Escalation([string]$blocker, [int]$iterations, [bool]$atExhaustion) {
  if ($EscMode -eq 'auto') { return [pscustomobject]@{ escalate = $false; rank = ''; tier = $blocker } }
  $sev = $EscSev[$blocker]; if ($null -eq $sev) { $sev = 1 }
  $thr = $EscThreshold[$EscLevel]; if ($null -eq $thr) { $thr = 1 }
  if ($sev -lt $thr) { return [pscustomobject]@{ escalate = $false; rank = ''; tier = $blocker } }
  $fire = switch ($blocker) {
    'unachievable'  { $true }                        # impossible goal -> escalate immediately
    'scope_change'  { $true }                        # cannot be fixed within scope -> escalate
    'design_change' { $iterations -ge $EscMinIter }  # only after >= N agent iterations (plan proven not to work)
    'unresolved'    { $atExhaustion }                # only once the fix loop is exhausted
    default         { $false }
  }
  [pscustomobject]@{ escalate = [bool]$fire; rank = $EscRank[$blocker]; tier = $blocker }
}
function Write-Escalation([string]$id, [string]$rank, [string]$tier, [string]$reason, $mustfix, [string]$td) {
  $L = @()
  $L += "# ESCALATION — human decision required"
  $L += ""
  $L += "| field | value |"; $L += "|---|---|"
  $L += "| escalate to | **$rank** |"
  $L += "| blocker tier | ``$tier`` |"
  $L += "| job · task | $($cfg.name) · $id |"
  $L += "| run dir | ``$RunRoot`` |"
  $L += ""
  $L += "## Why this stopped"
  $L += "The agent loop could not resolve TASK **$id** within its scope/iterations and classified the blocker as **$tier**. This needs a human decision, not another automated fix."
  $L += ""
  $L += "## Your call"
  switch ($tier) {
    'unachievable'  { $L += "- The desired output appears **not achievable as specified**. Decide: change the goal, relax a requirement, or abandon." }
    'design_change' { $L += "- The plan **does not work for the real-world goal** after $EscMinIter+ agent iterations. Decide: approve a redesign / new approach, or change the target." }
    'scope_change'  { $L += "- Resolving this needs a **meaningful scope change**. Decide: expand/redefine the task scope, or accept current scope and defer the rest." }
    default         { $L += "- The agents (including the Claude referee) **could not converge**. Decide the tie-break, or provide the missing context." }
  }
  $L += ""
  $L += "## What the agents flagged"
  foreach ($m in @($mustfix | Where-Object { $_ })) { $L += "- $m" }
  if ($reason) { $L += ""; $L += "**Referee note:** $reason" }
  $L += ""
  $L += "## Where to look"
  $L += "- Task artifacts: ``task_$id\`` (make / gate / review JSON / referee)"
  $L += "- Feed ``_run.log`` · Issues ``ERRORS.log`` · State ``_status.json`` · Summary ``RUN_SUMMARY.md``"
  try { ($L -join "`n") | Set-Content "$RunRoot\ESCALATION.md" -Encoding UTF8 } catch {}
  Add-Issue $id 'escalation' "ESCALATE to $rank ($tier)" "$RunRoot\ESCALATION.md"
}

# ---------- resolve tasks ----------
$tasks = @($cfg.tasks)
if ($Task) { $tasks = $tasks | Where-Object { "$($_.id)" -eq $Task } }
if ($tasks.Count -eq 0) { throw "No tasks to run (check -Task '$Task' against config.tasks ids)." }

# ---------- dry run: print the plan, touch nothing ----------
if ($DryRun) {
  Write-Host "Job '$($cfg.name)' [$workProduct] - $($tasks.Count) task(s) - DRY RUN" -ForegroundColor DarkGray
  Write-Host "Roles: maker=$($Maker.cli)/$($Maker.model) · review1=$($Reviewer1.cli) · review2=$($Reviewer2.cli) · referee=$($Referee.cli) · maxFix=$MaxFix" -ForegroundColor DarkGray
  Write-Host "Escalation: mode=$EscMode$(if ($EscMode -ne 'auto') { " · escalate at >= $EscLevel" } else { ' · full delegation (no human ping)' })" -ForegroundColor DarkGray
  foreach ($tk in $tasks) {
    $tid = "$($tk.id)"; $title = "$($tk.title)"; $instruction = Resolve-Instruction $tk
    Write-Host "=== TASK $tid — $title ===" -ForegroundColor Cyan
    Write-Host "[dry-run] make($($Maker.cli)) -> gate -> redteam($($Reviewer1.cli)) + review($($Reviewer2.cli)) -> referee($($Referee.cli)) on tie -> fix(max $MaxFix)"
    $gc = @(); if ($tk.gate.checks) { $gc = @($tk.gate.checks) } elseif ($cfg.gate.checks) { $gc = @($cfg.gate.checks) }
    $gateDesc = if ($gc.Count) { ($gc | ForEach-Object { if ($_.type -eq 'pytest') { "pytest:$($_.path)" } else { 'cmd' } }) -join ', ' } else { 'triad-only' }
    Write-Host "  gate: $gateDesc"
    Write-Host "  instruction: $instruction"
  }
  return
}

# ---------- the run ----------
New-Item -ItemType Directory -Force $RunRoot | Out-Null
Init-Tracking
Write-Log "Job '$($cfg.name)' [$workProduct] · $($tasks.Count) task(s) · roles: $($Maker.cli)/$($Reviewer1.cli)/$($Reviewer2.cli)/$($Referee.cli) · maxFix=$MaxFix" 'DarkGray'
Write-Log "TRACK HERE -> $RunRoot  (live: _status.json · _run.log · ERRORS.log · RUN_SUMMARY.md)" 'Cyan'
Write-Log "Escalation: mode=$EscMode$(if ($EscMode -ne 'auto') { " · escalate at >= $EscLevel (minIterDesign=$EscMinIter)" } else { ' · full delegation (no human ping)' })" 'DarkGray'

$ti = 0
foreach ($tk in $tasks) {
  $ti++
  $tid = "$($tk.id)"; $title = "$($tk.title)"
  $instruction = Resolve-Instruction $tk
  $script:Prog.tasks[$tid].state = 'running'
  Set-Phase $tid 'start' 0.0
  Write-Log "[$ti/$($tasks.Count)] TASK $tid - $title" 'Cyan'

  $td = "$RunRoot\task_$tid"; New-Item -ItemType Directory -Force $td | Out-Null

  # 1) MAKE — Codex (skipped on an idempotent re-run when the deterministic gate already passes)
  $skipMake = $false
  if ($cfg.skipMakeIfGatePasses) {
    $pre = Invoke-Gate $td 'pre' $tk
    if ($pre.pass) { $skipMake = $true; Write-Log "  TASK $tid · make: skipped (gate already green)" 'DarkGray' }
  }
  if (-not $skipMake) {
    Set-Phase $tid 'make' 0.2
    Write-Log "  TASK $tid · make ($($Maker.cli)) ..." 'Gray'
    $makePrompt = @"
Read the brief: $($cfg.briefRef)
TASK $tid — $title
$instruction

Constraints: do ONLY what THIS task specifies; change nothing it does not call for; do not do work assigned to other tasks. The orchestrator runs the gate (you need not). Do NOT run git. Finish by listing the files you changed.
"@
    [void](Invoke-Maker $makePrompt "$td\01_make.md")
  }

  for ($r = 0; $r -le $MaxFix; $r++) {
    # 2) DETERMINISTIC GATE
    Set-Phase $tid 'gate' 0.4 $r
    $g = Invoke-Gate $td $r $tk
    $gate = if ($g.pass) { 'PASS' } else { 'FAIL' }
    $script:Prog.tasks[$tid].gate = $gate; $script:Prog.tasks[$tid].round = $r; Save-Status
    Write-Log "  TASK $tid · gate(round $r): $gate" $(if ($g.pass) { 'Green' } else { 'Yellow' })
    $ctx = @"
TASK $tid — $title
Brief: $($cfg.briefRef)
Deterministic gate: $gate
--- GATE OUTPUT ---
$($g.output)
--- ARTIFACTS UNDER REVIEW ---
$(Get-Snapshot)
"@

    # 3) REVIEW #1 (Codex red-team) + REVIEW #2 (Copilot)
    Set-Phase $tid 'review' 0.7 $r
    $c1 = Read-Json (Invoke-Redteam "You are a REVIEWER — do NOT edit files; analyze only, then output the JSON verdict. Adversarially red-team TASK $tid against its acceptance criteria and this RUBRIC: $($cfg.rubric) $ScopeRule`n$ctx`n$JsonRule")
    $c1 | ConvertTo-Json -Depth 6 | Set-Content "$td\review1_$r.json"
    $c2 = Read-Json (Invoke-Reviewer2 "You are REVIEWER #2. Review TASK $tid against its acceptance criteria and this RUBRIC: $($cfg.rubric) Do NOT edit files. $ScopeRule`n$ctx`n$JsonRule")
    $c2 | ConvertTo-Json -Depth 6 | Set-Content "$td\review2_$r.json"
    $script:Prog.tasks[$tid].review1 = "$($c1.verdict)"; $script:Prog.tasks[$tid].review2 = "$($c2.verdict)"; Save-Status
    if (Is-AdapterFail $c1) { Add-Issue $tid "review1($($Reviewer1.cli))" "reviewer #1 output unparseable / adapter error" "$td\review1_$r.json" }
    if (Is-AdapterFail $c2) { Add-Issue $tid "review2($($Reviewer2.cli))" "reviewer #2 output unparseable / adapter error" "$td\review2_$r.json" }
    Write-Log "  TASK $tid · review: $($Reviewer1.cli)=$($c1.verdict) $($Reviewer2.cli)=$($c2.verdict)" 'Gray'

    # 4) CONSENSUS or REFEREE (Claude — only on disagreement)
    if ($c1.verdict -eq $c2.verdict) {
      $final = $c1; $mustfix = @($c1.must_fix + $c2.must_fix | Where-Object { $_ } | Select-Object -Unique)
    } else {
      Set-Phase $tid 'referee' 0.8 $r
      Write-Log "  TASK $tid · TIE ($($c1.verdict) vs $($c2.verdict)) -> $($Referee.cli) referees" 'Yellow'
      $final = Read-Json (Invoke-Referee "Two reviewers disagree on TASK $tid. REVIEW1=$($c1 | ConvertTo-Json -Compress). REVIEW2=$($c2 | ConvertTo-Json -Compress). RUBRIC: $($cfg.rubric) $ScopeRule`n$ctx`nMake the blocking call as tie-breaker. $JsonRule")
      $final | ConvertTo-Json -Depth 6 | Set-Content "$td\referee_$r.json"
      $mustfix = @($final.must_fix | Where-Object { $_ })
    }

    if ($g.pass -and $final.verdict -eq 'approve') {
      "APPROVED at round $r" | Set-Content "$td\STATUS.txt"
      $script:Prog.tasks[$tid].state = 'approved'; $script:Prog.tasks[$tid].final = 'approve'; $script:Prog.doneTasks = [int]$script:Prog.doneTasks + 1
      Set-Phase $tid 'done' 1.0 $r
      Write-Log "  TASK $tid · APPROVED (round $r)  [$($script:Prog.percent)%]" 'Green'
      break
    }
    # ---- not approved: classify the blocker + decide escalation (chain of command) ----
    $blk = "$($final.blocker)"
    if (-not $EscSev.ContainsKey($blk)) { $blk = if ("$($final.verdict)" -eq 'changes') { 'unresolved' } else { 'none' } }
    $esc = Resolve-Escalation $blk ($r + 1) ($r -eq $MaxFix)
    if ($esc.escalate) {
      "ESCALATED to $($esc.rank) [$blk] at round $r" | Set-Content "$td\STATUS.txt"
      $script:Prog.tasks[$tid].state = "escalated:$blk"; $script:Prog.tasks[$tid].final = "$($final.verdict)"; Save-Status
      Write-Escalation $tid $esc.rank $blk "$($final.notes)" $mustfix $td
      Write-Summary 'escalated'
      Write-Log "ESCALATE -> $($esc.rank) [$blk] at TASK $tid -- decision needed: $RunRoot\ESCALATION.md" 'Red'
      exit 3
    }
    if ($r -eq $MaxFix) {
      "NEEDS HUMAN (gate=$($g.pass), verdict=$($final.verdict), blocker=$blk)" | Set-Content "$td\STATUS.txt"
      $script:Prog.tasks[$tid].state = 'unresolved'; $script:Prog.tasks[$tid].final = "$($final.verdict)"; Save-Status
      Add-Issue $tid 'unresolved' "not approved after $MaxFix fix-rounds (gate=$($g.pass), verdict=$($final.verdict), blocker=$blk)" "$td\"
      Invoke-Reviewer2 "Produce a short prioritized markdown bug list from these open items for TASK ${tid}: $($mustfix -join '; ')" | Set-Content "$RunRoot\BUGS.md"
      Write-Summary 'failed'
      Write-Log "HALTED at TASK $tid - track: $RunRoot\RUN_SUMMARY.md (+ task_$tid\, ERRORS.log)" 'Red'
      exit 1
    }
    # 5) FIX — Codex applies only the must-fix items, then we re-gate
    Set-Phase $tid 'fix' 0.5 $r
    Write-Log "  TASK $tid · fix(round $r) -> $($mustfix.Count) item(s) [blocker=$blk]" 'Yellow'
    [void](Invoke-Maker "TASK ${tid}: apply ONLY these must-fix items, change nothing else, and do not add other-task work. The orchestrator re-runs the gate. Do NOT run git. Must-fix: $($mustfix -join '; ')" "$td\fix_$r.md")
  }
}

# 6) BUG TRACKER — Copilot, at the end
$reviews = (Get-ChildItem $RunRoot -Recurse -Filter 'review*_*.json' -ErrorAction SilentlyContinue | Get-Content -Raw) -join "`n"
if ($reviews) { Invoke-Reviewer2 "From these per-task reviews, produce a prioritized open-bug / follow-up list as markdown, grouped by task with severity: $reviews" | Set-Content "$RunRoot\BUGS.md" }
Write-Summary 'completed'
try { Write-Progress -Activity "Job $($cfg.name)" -Completed } catch {}
Write-Log "DONE · $($script:Prog.doneTasks)/$($script:Prog.total) approved · summary: $RunRoot\RUN_SUMMARY.md" 'Green'
