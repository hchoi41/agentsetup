#Requires -Version 7.0
<#
.SYNOPSIS
Enforce this hub's explicit CLI selection without invoking tools or changing files.
.DESCRIPTION
Checks the engine installation and resolved job hub, including their ancestor hubs.
Missing state preserves legacy behavior. Existing state must be valid; pending and
none block previews, preflight and real runs. Selection never proves authentication.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$InstallationRoot,
  [Parameter(Mandatory)][string]$HubRoot,
  [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$RequiredClis
)
$ErrorActionPreference='Stop'
$knownAgents=@('codex','claude','copilot','gemini','opencode')
$seenRoots=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($start in @($InstallationRoot,$HubRoot)) {
  if ([string]::IsNullOrWhiteSpace($start)) { throw 'Agent CLI selection check requires explicit installation and hub roots.' }
  $current=[IO.Path]::GetFullPath($start)
  while ($current) {
    if ($seenRoots.Add($current.TrimEnd('\','/'))) {
      $statePath=Join-Path $current '00.ABOUT/AGENT_CLI_SETUP.json'
      $item=$null
      try { $item=Get-Item -LiteralPath $statePath -Force -ErrorAction Stop }
      catch [System.Management.Automation.ItemNotFoundException] { }
      if ($item) {
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
          throw "Invalid agent CLI selection file: $statePath. Use a regular JSON file."
        }
        try {
          $raw=Get-Content -LiteralPath $statePath -Raw -ErrorAction Stop
          $document=[System.Text.Json.JsonDocument]::Parse($raw)
          try {
            if ($document.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { throw 'State must be a JSON object.' }
            $keys=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            foreach ($property in $document.RootElement.EnumerateObject()) {
              if (-not $keys.Add($property.Name)) { throw 'Duplicate state property.' }
            }
          } finally { $document.Dispose() }
          $state=ConvertFrom-Json -InputObject $raw -AsHashtable -ErrorAction Stop
          foreach ($key in @('schema_version','selection_state','selected_agents')) {
            if (-not ($state.Keys -ccontains $key)) { throw "Missing state property: $key" }
          }
          if (($state.schema_version -isnot [int] -and $state.schema_version -isnot [long]) -or $state.schema_version -ne 1) { throw 'Unsupported schema_version.' }
          if ($state.selection_state -isnot [string] -or $state.selection_state -cnotin @('pending','none','selected')) { throw 'Invalid selection_state.' }
          if ($state.selected_agents -isnot [array]) { throw 'selected_agents must be a JSON array.' }
          $selected=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
          foreach ($agent in $state.selected_agents) {
            if ($agent -isnot [string] -or $agent -cnotin $knownAgents -or -not $selected.Add($agent)) { throw 'selected_agents contains an unknown or duplicate provider.' }
          }
          if ($state.selection_state -eq 'selected' -and $selected.Count -eq 0) { throw 'Selected state requires at least one provider.' }
          if ($state.selection_state -ne 'selected' -and $selected.Count -ne 0) { throw 'Pending/none state must have an empty selected_agents array.' }
        } catch {
          throw "Invalid agent CLI selection at $statePath. Run Setup-AgentCLIs.ps1 to inspect or correct the selection. $($_.Exception.Message)"
        }
        if ($state.selection_state -eq 'pending') {
          throw "Agent CLI selection is pending at $statePath. Complete Setup-AgentCLIs.ps1 before orchestration, including preflight or preview."
        }
        if ($state.selection_state -eq 'none') {
          throw "Agent CLI selection is none at $statePath. Multi-agent orchestration is disabled; choose supported agents with Setup-AgentCLIs.ps1 to enable it."
        }
        foreach ($cli in $RequiredClis) {
          if ([string]::IsNullOrWhiteSpace($cli) -or -not $selected.Contains($cli)) {
            throw "Job requires CLI '$cli', which is not selected at $statePath. Select it explicitly or configure compatible roles; providers are never remapped automatically."
          }
        }
      }
    }
    $parent=[IO.Directory]::GetParent($current)
    if ($null -eq $parent) { break }
    $current=$parent.FullName
  }
}
