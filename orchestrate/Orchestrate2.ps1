<#
  Orchestrate2.ps1 — engine v2 of the multi-LLM "tiki-taka" conductor. (orchestrate/v2)

  A NEW FILE beside the proven v1 (Orchestrate.ps1, untouched). Same loop:
    MAKE -> GATE -> RED-TEAM + REVIEW -> REFEREE on tie -> APPROVE or FIX (<= maxFix) -> BUGS.md
  Six contracted changes vs v1 (design v8 §4):
    1. Structured gates: gate.checks[] = { tool: <absolute exe>, argv: [strings] },
       run via native argument arrays. Invoke-Expression and string commands REMOVED.
    2. Pre-created run root: config.runRoot (+ runRootToken) made by the launcher with a
       marker file; the engine verifies and uses it, never creates or renames run roots.
    3. Strict adapter outcomes: native nonzero exit, empty output, or unparseable verdict
       stops the run with a named reason BEFORE the next seat (exit 1).
    4. Severity contract: verdicts carry severity critical|major|minor|nit; the gate action
       keys on the highest severity (critical/major block; minor/nit are advisory).
    5. Escalation one-pager: ESCALATION.md always answers what / since when / suspected
       cause / blast radius / recommended action ("never wake the human empty-handed").
    6. JSON interfaces: -Capabilities prints the contract; -DryRun -OutputFormat Json
       prints the resolved plan for the launcher's consent screen.
  Exit codes: 0 completed · 1 failed · 2 config invalid (nothing written) · 3 escalated.

  Launcher jobs call this engine exclusively. Legacy configs keep running on v1.
#>
param(
  [string]$Config,
  [string]$Task,
  [int]$MaxFix = -1,
  [string]$HubRoot,
  [string]$ConsentReceipt,
  [switch]$DryRun,
  [switch]$Capabilities,
  [ValidateSet('Text','Json')][string]$OutputFormat = 'Text'
)
$ErrorActionPreference = 'Stop'

# ---------- change 6a: capabilities (config-free) ----------
if ($Capabilities) {
  [ordered]@{ engineContract = 'orchestrate/v2'; gates = 'argv'; runRoot = 'precreated' } |
    ConvertTo-Json -Compress | Write-Output
  exit 0
}

function Fail-Config([string]$msg) {
  if ($OutputFormat -eq 'Json') {
    $err = [ordered]@{ schemaVersion = 'orchestrate-error/v1'; code = 'config-invalid'; message = $msg } | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($err)
  } else { [Console]::Error.WriteLine("CONFIG INVALID: $msg") }
  exit 2
}

# ---------- load + validate config (exit 2 before any write) ----------
if (-not $Config) { Fail-Config '-Config is required (or use -Capabilities)' }
if (-not (Test-Path -LiteralPath $Config)) { Fail-Config "config not found: $Config" }
try { $cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json } catch { Fail-Config "config is not valid JSON: $_" }

$Hub = if     ($cfg.hub)            { "$($cfg.hub)" }
       elseif ($HubRoot)            { "$HubRoot" }
       elseif ($env:AGENT_HUB_ROOT) { "$env:AGENT_HUB_ROOT" }
       else                         { Split-Path -Parent $PSCommandPath }
if (-not (Test-Path -LiteralPath $Hub)) { Fail-Config "hub root not found: $Hub" }
if (-not $cfg.name) { Fail-Config 'config.name is required' }
$workProduct = if ($cfg.workProduct) { "$($cfg.workProduct)" } else { 'code' }
if ($MaxFix -lt 0) { $MaxFix = if ($null -ne $cfg.maxFix) { [int]$cfg.maxFix } else { 2 } }
if ($MaxFix -lt 0 -or $MaxFix -gt 5) { Fail-Config 'maxFix must be 0..5' }

# ---------- change 2: pre-created run root with marker ----------
if (-not $cfg.runRoot)      { Fail-Config 'config.runRoot is required (pre-created by the launcher)' }
if (-not $cfg.runRootToken) { Fail-Config 'config.runRootToken is required' }
$RunRoot = "$($cfg.runRoot)"
if (-not [System.IO.Path]::IsPathRooted($RunRoot)) { Fail-Config "runRoot must be absolute: $RunRoot" }
if (-not (Test-Path -LiteralPath $RunRoot -PathType Container)) { Fail-Config "runRoot does not exist: $RunRoot" }
$markerPath = Join-Path $RunRoot '.orchestrate-runroot.json'
if (-not (Test-Path -LiteralPath $markerPath)) { Fail-Config "runRoot marker missing: $markerPath" }
try { $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json } catch { Fail-Config "runRoot marker unreadable: $_" }
if ("$($marker.name)" -ne "$($cfg.name)")               { Fail-Config "runRoot marker name mismatch ('$($marker.name)' vs '$($cfg.name)')" }
if ("$($marker.runRootToken)" -ne "$($cfg.runRootToken)") { Fail-Config 'runRoot marker token mismatch' }

# ---------- roles (v1 defaults, with the invalid claude effort fixed to 'max') ----------
$ValidClaudeEfforts = @('low','medium','high','xhigh','max')
# maxTurns became a per-seat input on 2026-09-01 (it was hardcoded 6/6/12 at the call sites).
# Rationale: turn budget is a real compute lever for the claude adapter, and the launcher must be
# able to pin every seat to its ceiling rather than silently capping it.
function Resolve-Role($name, $defCli, $defModel, $defEffort, $defMaxTurns) {
  $r = $cfg.roles.$name
  $mt = if ($r -and $r.maxTurns) { [int]$r.maxTurns } else { [int]$defMaxTurns }
  if ($mt -lt 1 -or $mt -gt 100) { Fail-Config "$name maxTurns must be 1..100 (got $mt)" }
  # contextWindow -> claude's --autocompact (auto | 100k..1m). Validated here so a bad value is
  # a config-time refusal, never a silently-ignored flag. Empty/absent = do not pass the flag.
  $cw = if ($r -and $r.contextWindow) { "$($r.contextWindow)" } else { '' }
  if ($cw -and $cw -ne 'auto') {
    if ($cw -notmatch '^(?i)(\d+)(k|m)?$') { Fail-Config "$name contextWindow must be 'auto' or a token count like 200k / 1m (got '$cw')" }
    $n = [int64]$Matches[1]
    switch ("$($Matches[2])".ToLower()) { 'k' { $n *= 1000 } 'm' { $n *= 1000000 } }
    if ($n -lt 100000 -or $n -gt 1000000) { Fail-Config "$name contextWindow must be between 100k and 1m tokens (got '$cw')" }
  }
  [pscustomobject]@{
    cli           = if ($r -and $r.cli)    { "$($r.cli)" }    else { $defCli }
    model         = if ($r -and $r.model)  { "$($r.model)" }  else { $defModel }
    effort        = if ($r -and $r.effort) { "$($r.effort)" } else { $defEffort }
    maxTurns      = $mt
    contextWindow = $cw
  }
}
$Maker     = Resolve-Role 'maker'     'codex'   'gpt-5.6-sol'   'ultra'  6
$Reviewer1 = Resolve-Role 'reviewer1' 'codex'   'gpt-5.6-sol'   'ultra'  6
$Reviewer2 = Resolve-Role 'reviewer2' 'copilot' 'auto'          ''       6
$Referee   = Resolve-Role 'referee'   'claude'  'claude-opus-5' 'max'   12
foreach ($pair in @(@('maker',$Maker),@('reviewer1',$Reviewer1),@('reviewer2',$Reviewer2),@('referee',$Referee))) {
  $seat = $pair[0]; $role = $pair[1]
  if ($role.cli -notin @('codex','claude','copilot','gemini')) { Fail-Config "$seat cli '$($role.cli)' not supported" }
  if ($seat -eq 'maker' -and $role.cli -eq 'claude') { Fail-Config 'claude is not a supported maker seat' }
  if ($role.cli -eq 'claude' -and $role.effort -and $role.effort -notin $ValidClaudeEfforts) {
    Fail-Config "$seat claude effort '$($role.effort)' invalid (valid: $($ValidClaudeEfforts -join ', '))"
  }
}

# executable binding (build-review P0-1): every vendor invocation uses the consented absolute
# path from config.toolPaths — never a bare name resolved from the current PATH.
$ToolExe = @{}
foreach ($cli in @(@($Maker.cli, $Reviewer1.cli, $Reviewer2.cli, $Referee.cli) | Sort-Object -Unique)) {
  $exe = if ($cfg.toolPaths) { "$($cfg.toolPaths.$cli)" } else { '' }
  if (-not $exe) { Fail-Config "config.toolPaths.$cli is required (consented absolute executable path)" }
  if (-not [System.IO.Path]::IsPathRooted($exe)) { Fail-Config "toolPaths.$cli must be absolute: $exe" }
  if (-not (Test-Path -LiteralPath $exe)) { Fail-Config "toolPaths.$cli not found: $exe" }
  $ToolExe[$cli] = $exe
}

# consent capability (build-review P0-2, partial): a real run requires the single-use receipt
# written by the helper's confirm-run phase, bound to this config's runRootToken.
if (-not $DryRun -and -not $Capabilities) {
  if (-not $ConsentReceipt) { Fail-Config 'a real run requires -ConsentReceipt (written by the launcher confirm-run phase)' }
  if (-not (Test-Path -LiteralPath $ConsentReceipt)) { Fail-Config "consent receipt not found: $ConsentReceipt" }
  try { $receipt = Get-Content -LiteralPath $ConsentReceipt -Raw | ConvertFrom-Json } catch { Fail-Config "consent receipt unreadable: $_" }
  if ("$($receipt.runRootToken)" -ne "$($cfg.runRootToken)") { Fail-Config 'consent receipt does not match this run (runRootToken mismatch)' }
}

# ---------- change 1: structured gate validation ----------
function Get-TaskChecks($task) {
  if ($task.gate -and $task.gate.checks) { return @($task.gate.checks) }
  if ($cfg.gate -and $cfg.gate.checks)   { return @($cfg.gate.checks) }
  @()
}
$tasks = @($cfg.tasks)
if (-not $tasks -or $tasks.Count -lt 1) { Fail-Config 'config.tasks must have at least one task' }
if ($Task) { $tasks = @($tasks | Where-Object { "$($_.id)" -eq $Task }) }
if ($tasks.Count -eq 0) { Fail-Config "no tasks match -Task '$Task'" }
$seenIds = @{}
foreach ($tk in $tasks) {
  if (-not "$($tk.id)".Trim())    { Fail-Config 'every task needs a non-empty id' }
  if (-not "$($tk.title)".Trim()) { Fail-Config "task $($tk.id) needs a non-empty title" }
  if ($seenIds.ContainsKey("$($tk.id)")) { Fail-Config "duplicate task id: $($tk.id)" }
  $seenIds["$($tk.id)"] = $true
  foreach ($chk in (Get-TaskChecks $tk)) {
    if ($chk.PSObject.Properties.Name -contains 'type' -or $chk.PSObject.Properties.Name -contains 'command') {
      Fail-Config "task $($tk.id): legacy gate check (type/command) is not supported by orchestrate/v2 — use {tool, argv}"
    }
    if (-not $chk.tool)                                    { Fail-Config "task $($tk.id): gate check missing tool" }
    if (-not [System.IO.Path]::IsPathRooted("$($chk.tool)")) { Fail-Config "task $($tk.id): gate tool must be an absolute path: $($chk.tool)" }
    if (-not (Test-Path -LiteralPath "$($chk.tool)"))        { Fail-Config "task $($tk.id): gate tool not found: $($chk.tool)" }
    if ($null -eq $chk.argv)                               { Fail-Config "task $($tk.id): gate check missing argv array" }
  }
}
if (-not $cfg.artifacts -or @($cfg.artifacts).Count -lt 1) { Fail-Config 'config.artifacts must list at least one root' }

function Resolve-Instruction($task) {
  if ($task.instruction) { return "$($task.instruction)" }
  if ($cfg.taskInstructionTemplate) {
    return ("$($cfg.taskInstructionTemplate)" -replace '\{id\}', "$($task.id)" -replace '\{title\}', "$($task.title)")
  }
  Fail-Config "task $($task.id) has no instruction and no taskInstructionTemplate exists"
}
foreach ($tk in $tasks) { [void](Resolve-Instruction $tk) }

# ---------- change 4: severity contract in the verdict rule ----------
$JsonRule  = 'When verdict is "changes", set "severity" to the SINGLE most severe finding: "critical" (broken/unsafe/wrong result), "major" (acceptance criteria not met), "minor" (works, should improve), "nit" (style only). Also classify the most-severe BLOCKER: "none"=a normal in-scope fix; "unresolved"=reviewers cannot converge; "scope_change"=needs a scope change; "design_change"=the plan does not work and must be redesigned; "unachievable"=impossible as specified. End your reply with EXACTLY ONE fenced json code block (```json ... ```), nothing after it: {"verdict":"approve|changes","severity":"critical|major|minor|nit","blocker":"none|unresolved|scope_change|design_change|unachievable","must_fix":["..."],"notes":"..."}'
$ScopeRule = 'SCOPE: judge ONLY against THIS task''s own acceptance criteria + the rubric. Anything assigned to a LATER task (or a mechanism not chosen) is OUT OF SCOPE — do NOT request it. If this task''s criteria are met and the gate is green, the verdict is approve.'
$SevRank   = @{ critical = 4; major = 3; minor = 2; nit = 1 }

# ---------- escalation policy (v1, unchanged) ----------
$EscMode    = if ($cfg.escalation -and $cfg.escalation.mode)  { "$($cfg.escalation.mode)" }  else { 'auto' }
$EscLevel   = if ($cfg.escalation -and $cfg.escalation.level) { "$($cfg.escalation.level)" } else { 'general' }
$EscMinIter = if ($cfg.escalation -and $cfg.escalation.minIterationsForDesign) { [int]$cfg.escalation.minIterationsForDesign } else { 3 }
$EscSev       = @{ none = 0; unresolved = 1; scope_change = 2; design_change = 3; unachievable = 4 }
$EscRank      = @{ unresolved = 'General / Officer'; scope_change = 'Chief of Staff'; design_change = 'Secretary of Defense'; unachievable = 'Commander-in-Chief' }
$EscThreshold = @{ auto = 99; general = 1; chief_of_staff = 2; secretary = 3; commander = 4 }

# ---------- change 3: adapters return {text, code}; outcomes are asserted ----------
function Invoke-Maker([string]$prompt, [string]$out) {     # WRITES files — inside the workspace ($Hub = workspace root)
  $code = 0
  # O16 (2026-08-29): every vendor is spawned with the WORKSPACE as its working directory.
  # Previously only codex was confined (via -C); the copilot and gemini arms had no workspace
  # flag at all and copilot additionally passed --allow-all-paths, so a maker on those seats
  # would have run in the engine's own cwd with path verification disabled — the same class as
  # build-review P0-1. cwd-confinement works for every adapter regardless of flag support;
  # -C is kept (codex) and added (copilot, verified present in CLI 1.0.80) as defence in depth.
  Push-Location -LiteralPath $Hub
  try {
    switch ($Maker.cli) {
      'codex'  { $prompt | & $ToolExe['codex'] -C $Hub exec --dangerously-bypass-approvals-and-sandbox -m $Maker.model -c "model_reasoning_effort=$($Maker.effort)" -o $out -; $code = $LASTEXITCODE }
      'copilot'{ $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; & $ToolExe['copilot'] -C $Hub -p "Read the file at '$pf' and follow its instructions exactly. You may edit files in the workspace as needed for the requested task. Output a concise summary of files changed." --allow-all-tools --add-dir $Hub --model $Maker.model *> $out; $code = $LASTEXITCODE; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
      'gemini' { $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; & $ToolExe['gemini'] -p "Read the file at '$pf' and follow its instructions exactly. You may edit files in the workspace as needed for the requested task. Output a concise summary of files changed." -m $Maker.model --output-format text *> $out; $code = $LASTEXITCODE; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
    }
  } finally { Pop-Location }
  $txt = if (Test-Path -LiteralPath $out) { Get-Content -LiteralPath $out -Raw -ErrorAction SilentlyContinue } else { '' }
  [pscustomobject]@{ text = "$txt"; code = [int]$code }
}
function Read-Captured($tf) {
  $txt = Get-Content -LiteralPath $tf -Raw -ErrorAction SilentlyContinue
  Remove-Item $tf -Force -ErrorAction SilentlyContinue
  if ($txt) { "$txt" } else { '' }
}
function Invoke-ReviewCli($role, [string]$prompt, [int]$maxTurns) {
  $tf = [System.IO.Path]::GetTempFileName(); $code = 0
  # O16: reviewers do not write, but they DO read the workspace — confine them the same way.
  Push-Location -LiteralPath $Hub
  try {
    switch ($role.cli) {
      'codex'  { $prompt | & $ToolExe['codex'] -C $Hub exec --dangerously-bypass-approvals-and-sandbox -m $role.model -c "model_reasoning_effort=$($role.effort)" - *> $tf; $code = $LASTEXITCODE }
      'claude' {
        # --autocompact is only passed when the seat declares one, so an unset value keeps the
        # CLI's own default rather than this engine inventing a window size.
        $cArgs = @('-p','--model',$role.model,'--effort',$role.effort,'--output-format','text','--max-turns',$maxTurns)
        if ($role.contextWindow) { $cArgs += @('--autocompact', "$($role.contextWindow)") }
        $prompt | & $ToolExe['claude'] @cArgs *> $tf; $code = $LASTEXITCODE
      }
      'copilot'{ $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; & $ToolExe['copilot'] -C $Hub -p "Read the file at '$pf' and follow its instructions exactly. Output only the required fenced JSON verdict block." --allow-all-tools --add-dir $Hub --model $role.model *> $tf; $code = $LASTEXITCODE; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
      'gemini' { $pf=[System.IO.Path]::GetTempFileName(); Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8; & $ToolExe['gemini'] -p "Read the file at '$pf' and follow its instructions exactly. Output only the required fenced JSON verdict block." -m $role.model --output-format text *> $tf; $code = $LASTEXITCODE; Remove-Item $pf -Force -ErrorAction SilentlyContinue }
    }
  } catch { "ADAPTER ERROR: $_" | Out-File -FilePath $tf -Append; $code = 1 }
  finally { Pop-Location }
  [pscustomobject]@{ text = (Read-Captured $tf); code = [int]$code }
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
    if ($null -ne $o) { return Test-Verdict $o }
  }
  $null   # v2: unparseable is an ADAPTER FAILURE, not a synthetic 'changes' verdict
}
function Test-Verdict($o) {
  # schema + semantics validation (build-review P1-3): a malformed verdict is an adapter failure,
  # and an 'approve' that simultaneously carries a blocking classification is rejected.
  if ($null -eq $o) { return $null }
  if ("$($o.verdict)" -notin @('approve','changes')) { return $null }
  if ($o.PSObject.Properties.Name -contains 'severity' -and "$($o.severity)" -and "$($o.severity)" -notin @('critical','major','minor','nit')) { return $null }
  if ($o.PSObject.Properties.Name -contains 'blocker' -and "$($o.blocker)" -and "$($o.blocker)" -notin @('none','unresolved','scope_change','design_change','unachievable')) { return $null }
  if ("$($o.verdict)" -eq 'approve' -and ("$($o.severity)" -in @('critical','major') -or ("$($o.blocker)" -and "$($o.blocker)" -ne 'none'))) { return $null }
  $o
}

function Get-Snapshot {
  $items = foreach ($p in @($cfg.artifacts)) {
    $full = if ([System.IO.Path]::IsPathRooted("$p")) { "$p" } else { Join-Path $Hub "$p" }
    Get-ChildItem $full -Recurse -File -ErrorAction SilentlyContinue
  }
  ($items | Sort-Object FullName -Unique | ForEach-Object { "### $($_.FullName)`n$(Get-Content $_.FullName -Raw)" }) -join "`n`n"
}

# ---------- change 1: native-argv gate execution ----------
function Invoke-Gate($td, $round, $task) {
  # external wrapper anchor (build-review P1-2): the engine (itself consent-hash-bound) verifies
  # the gate wrapper's bytes before every gate execution — the wrapper no longer vouches for itself.
  if ($cfg.gate -and $cfg.gate.wrapperPath) {
    if (-not (Test-Path -LiteralPath "$($cfg.gate.wrapperPath)")) {
      return [pscustomobject]@{ pass = $false; trust = $true; output = "GATE WRAPPER MISSING: $($cfg.gate.wrapperPath)" }
    }
    $wh = (Get-FileHash -LiteralPath "$($cfg.gate.wrapperPath)" -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($wh -ne "$($cfg.gate.wrapperSha256)".ToLowerInvariant()) {
      return [pscustomobject]@{ pass = $false; trust = $true; output = 'GATE WRAPPER TRUST FAILURE: wrapper bytes changed since consent' }
    }
  }
  $checks = Get-TaskChecks $task
  if ($checks.Count -eq 0) { return [pscustomobject]@{ pass = $true; output = '(no deterministic gate; quality rests on the review triad + rubric)' } }
  $allPass = $true; $log = ''
  $i = 0
  foreach ($chk in $checks) {
    $i++
    $tool = "$($chk.tool)"; $argv = @($chk.argv | ForEach-Object { "$_" })
    $cf = Join-Path $td "gate_${round}_$i.txt"
    $code = 1
    try { & $tool @argv *> $cf; $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 } }
    catch { "$_" | Set-Content -LiteralPath $cf; $code = 1 }
    if ($code -ne 0) { $allPass = $false }
    $log += "[$tool $($argv -join ' ')] exit=$code`n" + (Get-Content -LiteralPath $cf -Raw -ErrorAction SilentlyContinue) + "`n"
  }
  [pscustomobject]@{ pass = $allPass; output = $log }
}

# ---------- change 6b: dry run (Text or Json), touches nothing ----------
function Get-Sha256([string]$text) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try { ([System.BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))) -replace '-', '').ToLowerInvariant() }
  finally { $sha.Dispose() }
}
if ($DryRun) {
  if ($OutputFormat -eq 'Json') {
    $plan = [ordered]@{
      schemaVersion = 'orchestrate2-dryrun/v1'
      engineContract = 'orchestrate/v2'
      name = "$($cfg.name)"; hub = $Hub; runRoot = $RunRoot; workProduct = $workProduct
      maxFix = $MaxFix
      escalation = [ordered]@{ mode = $EscMode; level = $EscLevel }
      roles = @(
        [ordered]@{ seat='maker';     cli=$Maker.cli;     model=$Maker.model;     effort=$Maker.effort },
        [ordered]@{ seat='reviewer1'; cli=$Reviewer1.cli; model=$Reviewer1.model; effort=$Reviewer1.effort },
        [ordered]@{ seat='reviewer2'; cli=$Reviewer2.cli; model=$Reviewer2.model; effort=$Reviewer2.effort },
        [ordered]@{ seat='referee';   cli=$Referee.cli;   model=$Referee.model;   effort=$Referee.effort }
      )
      tasks = @(foreach ($tk in $tasks) {
        [ordered]@{ id = "$($tk.id)"; title = "$($tk.title)"; instructionSha256 = (Get-Sha256 (Resolve-Instruction $tk)) }
      })
      gates = @(foreach ($tk in $tasks) {
        $ci = 0
        foreach ($chk in (Get-TaskChecks $tk)) {
          $ci++
          [ordered]@{ taskId = "$($tk.id)"; checkOrdinal = $ci; tool = "$($chk.tool)"
                      argvSha256 = (Get-Sha256 ("$($chk.tool)`n" + (@($chk.argv) -join "`n"))) }
        }
      })
    }
    $plan | ConvertTo-Json -Depth 8 | Write-Output
  } else {
    Write-Host "Job '$($cfg.name)' [$workProduct] - $($tasks.Count) task(s) - DRY RUN (orchestrate/v2)" -ForegroundColor DarkGray
    Write-Host "Roles: maker=$($Maker.cli)/$($Maker.model) · review1=$($Reviewer1.cli) · review2=$($Reviewer2.cli) · referee=$($Referee.cli) · maxFix=$MaxFix" -ForegroundColor DarkGray
    foreach ($tk in $tasks) {
      Write-Host "=== TASK $($tk.id) — $($tk.title) ===" -ForegroundColor Cyan
      $gc = Get-TaskChecks $tk
      $gateDesc = if ($gc.Count) { ($gc | ForEach-Object { Split-Path "$($_.tool)" -Leaf }) -join ', ' } else { 'triad-only' }
      Write-Host "  gate: $gateDesc"
      Write-Host "  instruction: $(Resolve-Instruction $tk)"
    }
  }
  exit 0
}

# ---------- progress + issue tracking (v1, run root now pre-created) ----------
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
    engineContract = 'orchestrate/v2'
    state = 'running'; startedAt = (Get-Date).ToString('s'); updatedAt = (Get-Date).ToString('s')
    total = $tasks.Count; doneTasks = 0; percent = 0
    current = [ordered]@{ task = ''; step = 'starting'; round = 0 }
    tasks = [ordered]@{}; issues = @(); advisories = @()
  }
  foreach ($t in $tasks) { $script:Prog.tasks["$($t.id)"] = [ordered]@{ title = "$($t.title)"; state = 'pending'; round = 0; gate = ''; review1 = ''; review2 = ''; severity = ''; final = '' } }
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
function Write-Summary([string]$state) {
  if (-not $script:Prog) { return }
  $script:Prog.state = $state; Save-Status
  $L = @()
  $L += "# Run summary - $($cfg.name)"
  $L += ""
  $L += "| field | value |"
  $L += "|---|---|"
  $L += "| run | $($script:Prog.runId) |"
  $L += "| engine | orchestrate/v2 |"
  $L += "| state | **$state** |"
  $L += "| approved | $($script:Prog.doneTasks)/$($script:Prog.total) |"
  $L += "| run dir | ``$RunRoot`` |"
  $L += ""
  $L += "## Tasks"
  $L += "| id | title | state | gate | review1 | review2 | severity | final |"
  $L += "|---|---|---|---|---|---|---|---|"
  foreach ($k in $script:Prog.tasks.Keys) { $t = $script:Prog.tasks[$k]; $L += "| $k | $($t.title) | $($t.state) | $($t.gate) | $($t.review1) | $($t.review2) | $($t.severity) | $($t.final) |" }
  if (@($script:Prog.advisories).Count) {
    $L += ""; $L += "## Advisory items (minor/nit — non-blocking)"
    foreach ($a in $script:Prog.advisories) { $L += "- **TASK $($a.task)**: $($a.item)" }
  }
  if (@($script:Prog.issues).Count) {
    $L += ""; $L += "## Issues - track here"
    foreach ($i in $script:Prog.issues) { $L += "- **TASK $($i.task)** - $($i.step) - $($i.message) -> ``$($i.artifact)``" }
  } else { $L += ""; $L += "_No issues recorded._" }
  try { ($L -join "`n") | Set-Content "$RunRoot\RUN_SUMMARY.md" -Encoding UTF8 } catch {}
}

# ---------- change 3: seat failure = named stop, never a fake completion ----------
function Fail-Run([string]$id, [string]$seat, [string]$reason, [string]$artifact) {
  Add-Issue $id $seat $reason $artifact
  if ($script:Prog -and $script:Prog.tasks.Contains($id)) { $script:Prog.tasks[$id].state = "failed:$seat" }
  Write-Summary 'failed'
  Write-Log "FAILED at TASK $id · $seat · $reason" 'Red'
  exit 1
}

# ---------- change 5: escalation one-pager ----------
function Write-Escalation([string]$id, [string]$rank, [string]$tier, [string]$reason, $mustfix, [int]$round) {
  $recommended = switch ($tier) {
    'unachievable'  { 'Change the goal, relax a requirement, or abandon this task.' }
    'design_change' { 'Approve a redesign / new approach, or change the target.' }
    'scope_change'  { 'Expand or redefine the task scope, or accept current scope and defer the rest.' }
    default         { 'Decide the tie-break yourself, or provide the missing context and re-run.' }
  }
  $cause = if ($reason) { $reason } elseif (@($mustfix).Count) { "$(@($mustfix)[0])" } else { 'reviewers could not converge' }
  $L = @()
  $L += "# ESCALATION — human decision required"
  $L += ""
  $L += "| field | value |"; $L += "|---|---|"
  $L += "| **what** | TASK $id of job ``$($cfg.name)`` blocked as **$tier** |"
  $L += "| **since when** | round $round of $MaxFix fix-rounds · $(Get-Date -Format 's') |"
  $L += "| **suspected cause** | $cause |"
  $L += "| **blast radius** | this task's artifacts only (workspace-isolated); nothing merged to live sources |"
  $L += "| **recommended action** | $recommended |"
  $L += "| escalate to | **$rank** |"
  $L += "| run dir | ``$RunRoot`` |"
  $L += ""
  $L += "## What the agents flagged"
  foreach ($m in @($mustfix | Where-Object { $_ })) { $L += "- $m" }
  $L += ""
  $L += "## Where to look"
  $L += "- Task artifacts: ``task_$id\`` · feed ``_run.log`` · issues ``ERRORS.log`` · state ``_status.json``"
  try { ($L -join "`n") | Set-Content "$RunRoot\ESCALATION.md" -Encoding UTF8 } catch {}
  Add-Issue $id 'escalation' "ESCALATE to $rank ($tier)" "$RunRoot\ESCALATION.md"
}
function Resolve-Escalation([string]$blocker, [int]$iterations, [bool]$atExhaustion) {
  if ($EscMode -eq 'auto') { return [pscustomobject]@{ escalate = $false; rank = ''; tier = $blocker } }
  $sev = $EscSev[$blocker]; if ($null -eq $sev) { $sev = 1 }
  $thr = $EscThreshold[$EscLevel]; if ($null -eq $thr) { $thr = 1 }
  if ($sev -lt $thr) { return [pscustomobject]@{ escalate = $false; rank = ''; tier = $blocker } }
  $fire = switch ($blocker) {
    'unachievable'  { $true }
    'scope_change'  { $true }
    'design_change' { $iterations -ge $EscMinIter }
    'unresolved'    { $atExhaustion }
    default         { $false }
  }
  [pscustomobject]@{ escalate = [bool]$fire; rank = $EscRank[$blocker]; tier = $blocker }
}

# ---------- the run ----------
Init-Tracking
Write-Log "Job '$($cfg.name)' [$workProduct] · $($tasks.Count) task(s) · engine v2 · roles: $($Maker.cli)/$($Reviewer1.cli)/$($Reviewer2.cli)/$($Referee.cli) · maxFix=$MaxFix" 'DarkGray'
Write-Log "TRACK HERE -> $RunRoot" 'Cyan'

$ti = 0
foreach ($tk in $tasks) {
  $ti++
  $tid = "$($tk.id)"; $title = "$($tk.title)"
  $instruction = Resolve-Instruction $tk
  $script:Prog.tasks[$tid].state = 'running'
  Set-Phase $tid 'start' 0.0
  Write-Log "[$ti/$($tasks.Count)] TASK $tid - $title" 'Cyan'

  $td = "$RunRoot\task_$tid"; New-Item -ItemType Directory -Force $td | Out-Null

  # 1) MAKE
  $skipMake = $false
  if ($cfg.skipMakeIfGatePasses) {
    $pre = Invoke-Gate $td 'pre' $tk
    if ($pre.trust) { Fail-Run $tid 'gate-wrapper' "$($pre.output)" "$td\" }
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
    $mk = Invoke-Maker $makePrompt "$td\01_make.md"
    if ($mk.code -ne 0)              { Fail-Run $tid "maker($($Maker.cli))" "native exit $($mk.code)" "$td\01_make.md" }
    if (-not "$($mk.text)".Trim())   { Fail-Run $tid "maker($($Maker.cli))" 'empty output (a dead CLI cannot count as done)' "$td\01_make.md" }
  }

  for ($r = 0; $r -le $MaxFix; $r++) {
    # 2) DETERMINISTIC GATE (native argv)
    Set-Phase $tid 'gate' 0.4 $r
    $g = Invoke-Gate $td $r $tk
    if ($g.trust) { Fail-Run $tid 'gate-wrapper' "$($g.output)" "$td\" }
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

    # 3) REVIEW #1 + REVIEW #2 (strict outcomes)
    Set-Phase $tid 'review' 0.7 $r
    $r1 = Invoke-ReviewCli $Reviewer1 "You are a REVIEWER — do NOT edit files; analyze only, then output the JSON verdict. Adversarially red-team TASK $tid against its acceptance criteria and this RUBRIC: $($cfg.rubric) $ScopeRule`n$ctx`n$JsonRule" $($Reviewer1.maxTurns)
    if ($r1.code -ne 0)            { Fail-Run $tid "review1($($Reviewer1.cli))" "native exit $($r1.code)" "$td\review1_$r.json" }
    if (-not "$($r1.text)".Trim()) { Fail-Run $tid "review1($($Reviewer1.cli))" 'empty output' "$td\review1_$r.json" }
    $c1 = Read-Json $r1.text
    if ($null -eq $c1)             { "$($r1.text)" | Set-Content "$td\review1_$r.raw.txt"; Fail-Run $tid "review1($($Reviewer1.cli))" 'no parseable verdict JSON' "$td\review1_$r.raw.txt" }
    $c1 | ConvertTo-Json -Depth 6 | Set-Content "$td\review1_$r.json"

    $r2 = Invoke-ReviewCli $Reviewer2 "You are REVIEWER #2. Review TASK $tid against its acceptance criteria and this RUBRIC: $($cfg.rubric) Do NOT edit files. $ScopeRule`n$ctx`n$JsonRule" $($Reviewer2.maxTurns)
    if ($r2.code -ne 0)            { Fail-Run $tid "review2($($Reviewer2.cli))" "native exit $($r2.code)" "$td\review2_$r.json" }
    if (-not "$($r2.text)".Trim()) { Fail-Run $tid "review2($($Reviewer2.cli))" 'empty output' "$td\review2_$r.json" }
    $c2 = Read-Json $r2.text
    if ($null -eq $c2)             { "$($r2.text)" | Set-Content "$td\review2_$r.raw.txt"; Fail-Run $tid "review2($($Reviewer2.cli))" 'no parseable verdict JSON' "$td\review2_$r.raw.txt" }
    $c2 | ConvertTo-Json -Depth 6 | Set-Content "$td\review2_$r.json"

    $script:Prog.tasks[$tid].review1 = "$($c1.verdict)"; $script:Prog.tasks[$tid].review2 = "$($c2.verdict)"; Save-Status
    Write-Log "  TASK $tid · review: $($Reviewer1.cli)=$($c1.verdict) $($Reviewer2.cli)=$($c2.verdict)" 'Gray'

    # 4) CONSENSUS or REFEREE
    if ($c1.verdict -eq $c2.verdict) {
      $final = $c1; $mustfix = @($c1.must_fix + $c2.must_fix | Where-Object { $_ } | Select-Object -Unique)
      $sevCandidates = @("$($c1.severity)", "$($c2.severity)") | Where-Object { $SevRank.ContainsKey($_) }
      $finalSev = if ($sevCandidates) { ($sevCandidates | Sort-Object { $SevRank[$_] } -Descending)[0] } else { '' }
      # deterministic blocker merge (build-review P1-3): the stronger reviewer's blocker survives
      $blkCandidates = @("$($c1.blocker)", "$($c2.blocker)") | Where-Object { $EscSev.ContainsKey($_) }
      $mergedBlocker = if ($blkCandidates) { ($blkCandidates | Sort-Object { $EscSev[$_] } -Descending)[0] } else { "$($final.blocker)" }
    } else {
      Set-Phase $tid 'referee' 0.8 $r
      Write-Log "  TASK $tid · TIE ($($c1.verdict) vs $($c2.verdict)) -> $($Referee.cli) referees" 'Yellow'
      $rf = Invoke-ReviewCli $Referee "Two reviewers disagree on TASK $tid. REVIEW1=$($c1 | ConvertTo-Json -Compress). REVIEW2=$($c2 | ConvertTo-Json -Compress). RUBRIC: $($cfg.rubric) $ScopeRule`n$ctx`nMake the blocking call as tie-breaker. $JsonRule" $($Referee.maxTurns)
      if ($rf.code -ne 0)            { Fail-Run $tid "referee($($Referee.cli))" "native exit $($rf.code)" "$td\referee_$r.json" }
      if (-not "$($rf.text)".Trim()) { Fail-Run $tid "referee($($Referee.cli))" 'empty output' "$td\referee_$r.json" }
      $final = Read-Json $rf.text
      if ($null -eq $final)          { "$($rf.text)" | Set-Content "$td\referee_$r.raw.txt"; Fail-Run $tid "referee($($Referee.cli))" 'no parseable verdict JSON' "$td\referee_$r.raw.txt" }
      $final | ConvertTo-Json -Depth 6 | Set-Content "$td\referee_$r.json"
      $mustfix = @($final.must_fix | Where-Object { $_ })
      $finalSev = "$($final.severity)"
      $mergedBlocker = "$($final.blocker)"
    }
    if (-not $SevRank.ContainsKey($finalSev)) { $finalSev = if ("$($final.verdict)" -eq 'changes') { 'major' } else { '' } }
    $script:Prog.tasks[$tid].severity = $finalSev; Save-Status

    # change 4: approve on gate-pass when severity is advisory-only
    $advisoryApprove = ($g.pass -and "$($final.verdict)" -eq 'changes' -and $finalSev -in @('minor','nit'))
    if ($advisoryApprove) {
      foreach ($m in $mustfix) { $script:Prog.advisories += , ([ordered]@{ task = $tid; item = "$m" }) }
      Write-Log "  TASK $tid · advisory-only findings ($finalSev) — approved with notes" 'DarkYellow'
    }
    if (($g.pass -and $final.verdict -eq 'approve') -or $advisoryApprove) {
      "APPROVED at round $r$(if ($advisoryApprove) { ' (advisory notes recorded)' })" | Set-Content "$td\STATUS.txt"
      $script:Prog.tasks[$tid].state = 'approved'; $script:Prog.tasks[$tid].final = $(if ($advisoryApprove) { 'approve(advisory)' } else { 'approve' }); $script:Prog.doneTasks = [int]$script:Prog.doneTasks + 1
      Set-Phase $tid 'done' 1.0 $r
      Write-Log "  TASK $tid · APPROVED (round $r)  [$($script:Prog.percent)%]" 'Green'
      break
    }
    # not approved: classify blocker + escalate or fix
    $blk = "$mergedBlocker"
    if (-not $EscSev.ContainsKey($blk)) { $blk = if ("$($final.verdict)" -eq 'changes') { 'unresolved' } else { 'none' } }
    $esc = Resolve-Escalation $blk ($r + 1) ($r -eq $MaxFix)
    if ($esc.escalate) {
      "ESCALATED to $($esc.rank) [$blk] at round $r" | Set-Content "$td\STATUS.txt"
      $script:Prog.tasks[$tid].state = "escalated:$blk"; $script:Prog.tasks[$tid].final = "$($final.verdict)"; Save-Status
      Write-Escalation $tid $esc.rank $blk "$($final.notes)" $mustfix $r
      Write-Summary 'escalated'
      Write-Log "ESCALATE -> $($esc.rank) [$blk] at TASK $tid -- decision needed: $RunRoot\ESCALATION.md" 'Red'
      exit 3
    }
    if ($r -eq $MaxFix) {
      "NEEDS HUMAN (gate=$($g.pass), verdict=$($final.verdict), severity=$finalSev, blocker=$blk)" | Set-Content "$td\STATUS.txt"
      $script:Prog.tasks[$tid].state = 'unresolved'; $script:Prog.tasks[$tid].final = "$($final.verdict)"; Save-Status
      Add-Issue $tid 'unresolved' "not approved after $MaxFix fix-rounds (gate=$($g.pass), verdict=$($final.verdict), severity=$finalSev, blocker=$blk)" "$td\"
      $bug = Invoke-ReviewCli $Reviewer2 "Produce a short prioritized markdown bug list from these open items for TASK ${tid}: $($mustfix -join '; ')" $($Reviewer2.maxTurns)
      if ($bug.code -eq 0 -and "$($bug.text)".Trim()) { "$($bug.text)" | Set-Content "$RunRoot\BUGS.md" }
      Write-Summary 'failed'
      Write-Log "HALTED at TASK $tid - track: $RunRoot\RUN_SUMMARY.md" 'Red'
      exit 1
    }
    # 5) FIX
    Set-Phase $tid 'fix' 0.5 $r
    Write-Log "  TASK $tid · fix(round $r) -> $($mustfix.Count) item(s) [severity=$finalSev blocker=$blk]" 'Yellow'
    $fx = Invoke-Maker "TASK ${tid}: apply ONLY these must-fix items, change nothing else, and do not add other-task work. The orchestrator re-runs the gate. Do NOT run git. Must-fix: $($mustfix -join '; ')" "$td\fix_$r.md"
    if ($fx.code -ne 0)            { Fail-Run $tid "fixer($($Maker.cli))" "native exit $($fx.code)" "$td\fix_$r.md" }
    if (-not "$($fx.text)".Trim()) { Fail-Run $tid "fixer($($Maker.cli))" 'empty output' "$td\fix_$r.md" }
  }
}

# 6) BUG TRACKER (best-effort; failure here must not fake a run failure)
$reviews = (Get-ChildItem $RunRoot -Recurse -Filter 'review*_*.json' -ErrorAction SilentlyContinue | Get-Content -Raw) -join "`n"
if ($reviews) {
  $bug = Invoke-ReviewCli $Reviewer2 "From these per-task reviews, produce a prioritized open-bug / follow-up list as markdown, grouped by task with severity: $reviews" $($Reviewer2.maxTurns)
  if ($bug.code -eq 0 -and "$($bug.text)".Trim()) { "$($bug.text)" | Set-Content "$RunRoot\BUGS.md" }
  else { Add-Issue '-' 'bugtracker' 'bug-tracker seat failed (non-blocking)' "$RunRoot\" }
}
Write-Summary 'completed'
try { Write-Progress -Activity "Job $($cfg.name)" -Completed } catch {}
Write-Log "DONE · $($script:Prog.doneTasks)/$($script:Prog.total) approved · summary: $RunRoot\RUN_SUMMARY.md" 'Green'
exit 0
