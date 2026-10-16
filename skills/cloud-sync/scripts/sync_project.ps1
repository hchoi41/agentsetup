#Requires -Version 7.0
<#
.SYNOPSIS
  cloud-sync engine: one-way publish of a project or operation from the local hub to its OneDrive counterpart.

.DESCRIPTION
  The folder decides what goes to the cloud. There are no job-type heuristics and no size rules.

    10.INPUT, 30.OUTPUT   copied in full: any size, any number of files, every version, empty folders too.
    20.PROCESS            never copied. Its metadata is written to PROCESS_MANIFEST.json, and its
                          RECIPE.md (how the work was done and why) is checked and copied.
    12.MEMORY_RAM         this project's RAM files are copied.

  Not copied from INPUT/OUTPUT, and always listed - never dropped silently:
    possible secrets          until a human clears the file (-Allow)
    names OneDrive refuses    .lock, anything containing _vti_, a leading or trailing space
    a newer copy in the cloud until a human says which one wins (-OverwriteNewer)
    lock, OS and .tmp files, .git internals   (not content)

  Blockers stop the run before anything is written: a link or junction inside INPUT/OUTPUT, a destination
  path over OneDrive's limit, a file over OneDrive's size limit, a folder in the way of a file,
  not enough free space on the destination drive.

  Nothing in the destination is ever deleted or renamed.

  Defaults to a DRY RUN: shows the plan and changes nothing.

  "OK" means: copied into the OneDrive folder on this computer and verified there. The OneDrive app uploads
  from that folder on its own schedule.

  Exit codes:  0 = OK
               2 = INCOMPLETE (ran, but something that belongs in the cloud is not there yet)
               1 = BLOCKED or FAILED

.EXAMPLE
  # Dry run (default): see what WOULD sync
  .\sync_project.ps1 -Project 20261010_coach_acme_onboarding -SrcRoot D:\hub -DstRoot "$env:OneDrive\workspace"

.EXAMPLE
  # Apply, clearing one file the secret scan flagged after a human looked at it
  .\sync_project.ps1 -Project 20261010_coach_acme_onboarding -SrcRoot D:\hub -DstRoot D:\OneDrive\ws -Allow '10.INPUT/notes/token_overview.md' -Apply

.EXAMPLE
  # No destination: only check the recipe and (with -Apply) write PROCESS_MANIFEST.json in the hub
  .\sync_project.ps1 -Project 20261010_coach_acme_onboarding -SrcRoot D:\hub -RecipeOnly
#>
[CmdletBinding(PositionalBinding = $false)]
param(
  [Parameter(Mandatory)] [string]$Project,   # folder name under 01.PROJECT or 10.OPERATION
  [string]$SrcRoot = $env:AGENT_HUB_ROOT,    # local hub root
  [string]$DstRoot,                          # OneDrive workspace root (not needed with -RecipeOnly)
  [string]$DstName,                          # folder name in the destination, when it differs from -Project
  [string]$Slug,                             # stable RAM prefix; defaults to semantic operation name or date-first project name
  [string[]]$Allow = @(),                    # paths (relative to the project folder) a human cleared after a secret match
  [string[]]$OverwriteNewer = @(),           # paths whose cloud copy is newer and may be overwritten by the hub's
  [switch]$Hash,                             # compare and verify by SHA-256 instead of size and time (reads every file)
  [int]$ManifestMaxFiles = 5000,             # cap on the per-file listing in PROCESS_MANIFEST.json (totals are always complete)
  [switch]$RecipeOnly,                       # no sync: report the recipe state; with -Apply also write the manifest in the hub
  [switch]$Apply                             # actually copy; omit for a dry run
)

$ErrorActionPreference = 'Stop'

# ---- limits (Microsoft Support: Restrictions and limitations in OneDrive and SharePoint) ----
$PathLimit      = 400      # whole path including the file name
$PathWarn       = 260      # classic Windows MAX_PATH; some apps cannot open longer paths
$FileLimitBytes = 250GB    # largest single file
$ManyFiles      = 50000    # OneDrive recommends no more than 300,000 synced items in total; warn on a large batch
$TmpSuffix      = '.cs.tmp' # files are copied under this name first; OneDrive does not upload .tmp files

$Roles        = @('10.INPUT', '30.OUTPUT')
$ControlFiles = @('RECIPE.md', 'PROCESS_MANIFEST.json', 'SYNC_REPORT.md', 'SYNC_ALLOW.json')

# ---- pattern tables (mirror references/exclude_and_secrets.md) ----
$SecretName = @('.env','*.env','.env.*','*.pem','*.key','*.pfx','*.p12','*.keystore','*.jks','id_rsa*','id_ed25519*','*.ppk',
  'known_hosts','credentials*','*.credentials','client_secret*','service-account*.json','*serviceaccount*.json','*secret*',
  '*token*','*apikey*','*api_key*','*password*','cookies.txt','*cookies*.json','*.session','auth.json','.netrc','_netrc','.Renviron')
$SecretDir  = @('.aws','.ssh','.gnupg')
# (two patterns are built by concatenation so that this file does not match a scanner that uses the same patterns)
$SecretRegex = @('AKIA[0-9A-Z]{16}','sk-[A-Za-z0-9]{20,}','sk-ant-','xox[baprs]-','ghp_[A-Za-z0-9]{20,}',('github' + '_pat_'),
  ('-----BEGIN [A-Z ]*PRIVATE ' + 'KEY-----'),'(?i)password\s*=','(?i)pwd\s*=','(?i)Server=.*;.*Password=')
$LockOrOs   = @('~$*','.~lock.*#','Thumbs.db','ehthumbs.db','desktop.ini','.DS_Store')
$RuntimeDir = @('node_modules','.venv','venv','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache',
  '.ipynb_checkpoints','.uv-cache','.cache','renv','ms-playwright','env','dist','build','.next','target','out',
  '.Rproj.user','packrat','.gradle')

# ---- helpers ----
function Join-Parts { param([string]$Base, [string[]]$Parts) $p = $Base; foreach ($x in $Parts) { $p = Join-Path $p $x }; return $p }
function To-Native  { param([string]$Rel) return ($Rel -replace '/', [IO.Path]::DirectorySeparatorChar) }
function Write-Utf8 { param([string]$Path, [string]$Text) [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false))) }
function Format-Bytes { param([double]$B)
  if ($B -ge 1GB) { return ('{0:N1} GB' -f ($B / 1GB)) }
  if ($B -ge 1MB) { return ('{0:N1} MB' -f ($B / 1MB)) }
  if ($B -ge 1KB) { return ('{0:N1} KB' -f ($B / 1KB)) }
  return ('{0:N0} B' -f $B)
}
function Get-Sum { param($Rows) $s = 0; foreach ($r in $Rows) { $s += [double]$r.Bytes }; return $s }
function Get-Sha { param([string]$Path) return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Stop-Blocked { param([string]$Msg) Write-Host "RESULT: BLOCKED - $Msg" -ForegroundColor Red; exit 1 }
function Test-FolderName { param([string]$Name) return ($Name -and $Name -notmatch '[\\/:*?"<>|]' -and $Name -ne '.' -and $Name -ne '..' -and $Name -eq $Name.Trim()) }
function Test-Layout { param([string]$Root) return (Test-Path -LiteralPath (Join-Path $Root '01.PROJECT') -PathType Container) }
function Get-OperationNameParts { param([string]$Name)
  # Recognize only the canonical suffix. Keep legacy names on their existing path.
  # Windows comparisons ignore case; no name or registry identity is rewritten.
  if (-not (Test-FolderName $Name) -or $Name.Length -gt 255 -or $Name.StartsWith('.') -or $Name.EndsWith('.') -or $Name -match '\p{Cc}') { return $null }
  $match = [regex]::Match($Name, '^(?<semantic>.+)_(?<date>[0-9]{8})_v(?<major>0|[1-9][0-9]*)\.(?<minor>[0-9]{2})$', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
  if (-not $match.Success) { return $null }
  $semantic = $match.Groups['semantic'].Value
  if ($semantic -ne $semantic.Trim() -or $semantic.StartsWith('.') -or $semantic.EndsWith('.') -or $semantic -match '(?:^|_)[0-9]{8}_[vV][^_]*$') { return $null }
  $baseName = ($semantic -split '\.', 2)[0].TrimEnd(' ')
  if ($baseName -match '^(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$|CLOCK\$)$') { return $null }
  $date = $match.Groups['date'].Value
  $parsedDate = [datetime]::MinValue
  if (-not [datetime]::TryParseExact($date, 'yyyyMMdd', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsedDate)) { return $null }
  $version = $match.Groups['major'].Value + '.' + $match.Groups['minor'].Value
  if ($version -ceq '0.00') { return $null }
  # Compare canonical version strings directly: even a large major cannot overflow.
  return @{ Semantic = $semantic; Date = $date; Version = $version }
}
function Split-PathList { param([string[]]$Items, [string]$Base)
  $out = New-Object System.Collections.Generic.List[string]
  foreach ($a in $Items) {
    if ([string]::IsNullOrWhiteSpace($a)) { continue }
    $parts = @($a)
    # "pwsh -File ... -Allow a,b" arrives as one string: split it unless it is itself an existing file
    if (-not (Test-Path -LiteralPath (Join-Path $Base (To-Native ($a -replace '\\', '/'))))) { $parts = $a -split ',' }
    foreach ($p in $parts) { $v = ($p -replace '\\', '/').Trim().TrimStart('/').ToLowerInvariant(); if ($v) { $out.Add($v) } }
  }
  return , $out
}

function Test-Link { param($Item)
  if (-not ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { return $false }
  if ($null -ne $Item.LinkTarget) { return $true }                         # symbolic link or junction
  return ($Item.LinkType -in @('SymbolicLink', 'Junction'))                # OneDrive placeholders are neither
}

# Inventory of a tree, metadata only. Links are reported, never followed.
function Get-Tree { param([string]$Base)
  $files = New-Object System.Collections.Generic.List[object]
  $dirs  = New-Object System.Collections.Generic.List[string]
  $links = New-Object System.Collections.Generic.List[string]
  $errs  = New-Object System.Collections.Generic.List[string]
  $stack = New-Object System.Collections.Generic.Stack[object]
  $stack.Push(@('', $Base))
  while ($stack.Count -gt 0) {
    $top = $stack.Pop(); $rel = $top[0]; $cur = $top[1]
    try { $entries = @([System.IO.DirectoryInfo]::new($cur).EnumerateFileSystemInfos()) }
    catch { $errs.Add("$(if ($rel) { $rel } else { '.' }): $($_.Exception.Message)"); continue }
    foreach ($e in $entries) {
      $erel = if ($rel) { "$rel/$($e.Name)" } else { $e.Name }
      if (Test-Link $e) { $links.Add($erel); continue }
      if ($e.Attributes -band [System.IO.FileAttributes]::Directory) { $dirs.Add($erel); $stack.Push(@($erel, $e.FullName)) }
      else { $files.Add([pscustomobject]@{ Rel = $erel; Name = $e.Name; Full = $e.FullName; Bytes = [int64]$e.Length; Mtime = $e.LastWriteTimeUtc; Ext = $e.Extension.ToLowerInvariant() }) }
    }
  }
  return [pscustomobject]@{ Files = @($files | Sort-Object Rel); Dirs = @($dirs | Sort-Object); Links = @($links | Sort-Object); Errors = $errs }
}

function Test-SecretContent { param([string]$Path)
  try { return [bool](Select-String -LiteralPath $Path -Pattern $SecretRegex -Quiet -ErrorAction SilentlyContinue) } catch { return $false }
}
function Test-Secret { param($File)
  foreach ($p in $SecretName) { if ($File.Name -like $p) { return "name matches $p" } }
  if ($File.Bytes -lt 1MB -and $File.Ext -match '^\.(env|txt|json|ya?ml|ini|cfg|conf|config|r|py|ps1|sh|md|csv|xml|toml|properties|js|ts)$') {
    if (Test-SecretContent $File.Full) { return 'content looks like a credential' }
  }
  return $null
}
function Test-RefusedName { param([string[]]$Segments)
  foreach ($s in $Segments) {
    if ($s -ieq '.lock') { return "OneDrive does not accept the name '.lock'" }
    if ($s -match '_vti_') { return "OneDrive does not accept '_vti_' in a name" }
    if ($s -ne $s.Trim()) { return 'OneDrive does not accept a name that starts or ends with a space' }
  }
  return $null
}
function Get-Lineage { param([string]$Name)   # groups versions of one artifact, to show where the iterations piled up
  $b = [IO.Path]::GetFileNameWithoutExtension($Name)
  $b = $b -replace '_v\d+(\.\d+)*', ''
  $b = $b -replace '(^|_)\d{8}(?=_|$)', ''
  $b = $b -replace '(?i)_(final|draft|ready|lockedin|submitted)', ''
  return ($b.Trim('_', '-', ' ').ToLower() + [IO.Path]::GetExtension($Name).ToLower())
}

# Decides what happens to one file that is allowed to go up: COPY, SAME or CONFLICT.
function Get-CopyAction { param($Row)
  if (Test-Path -LiteralPath $Row.Dst -PathType Container) { $Row.Action = 'BLOCK'; $Row.Reason = 'a folder with this name is in the way in the destination'; return }
  if (-not (Test-Path -LiteralPath $Row.Dst -PathType Leaf)) { $Row.Action = 'COPY'; $Row.Reason = 'new'; return }
  $d = Get-Item -LiteralPath $Row.Dst -Force
  $delta = ($d.LastWriteTimeUtc - $Row.Mtime).TotalSeconds
  if ($Hash) { $same = ($d.Length -eq $Row.Bytes) -and ((Get-Sha $Row.Src) -eq (Get-Sha $Row.Dst)) }
  else       { $same = ($d.Length -eq $Row.Bytes) -and ([math]::Abs($delta) -le 2) }
  if ($same) { $Row.Action = 'SAME'; return }
  if ($delta -gt 2 -and ($NewerSet -notcontains $Row.Rel.ToLowerInvariant())) {
    $Row.Action = 'CONFLICT'; $Row.Reason = "the cloud copy is newer ($($d.LastWriteTime.ToString('yyyy-MM-dd HH:mm')) vs $($Row.Mtime.ToLocalTime().ToString('yyyy-MM-dd HH:mm')))"; return
  }
  $Row.Action = 'COPY'; $Row.Reason = 'changed'
}

try {
  # ---- resolve source ----
  if (-not (Test-FolderName $Project)) { Stop-Blocked "-Project must be a folder name, not a path: '$Project'" }
  if ($DstName -and -not (Test-FolderName $DstName)) { Stop-Blocked "-DstName must be a folder name, not a path: '$DstName'" }
  if ([string]::IsNullOrWhiteSpace($SrcRoot)) { Stop-Blocked 'no source root: pass -SrcRoot or set AGENT_HUB_ROOT' }
  if (-not (Test-Path -LiteralPath $SrcRoot -PathType Container)) { Stop-Blocked "source root not found: $SrcRoot" }
  $SrcRoot = (Resolve-Path -LiteralPath $SrcRoot).ProviderPath.TrimEnd('\', '/')
  if (-not (Test-Layout $SrcRoot)) { Stop-Blocked "the source workspace has no 01.PROJECT folder: it is not on the project-first layout - $SrcRoot" }

  $kinds = @('01.PROJECT', '10.OPERATION' | Where-Object { Test-Path -LiteralPath (Join-Parts $SrcRoot @($_, $Project)) -PathType Container })
  if ($kinds.Count -eq 0) { Stop-Blocked "'$Project' not found under 01.PROJECT or 10.OPERATION in $SrcRoot" }
  if ($kinds.Count -gt 1) { Stop-Blocked "'$Project' exists under both 01.PROJECT and 10.OPERATION in ${SrcRoot}: it must be one or the other" }
  $Kind = $kinds[0]
  $KindWord = if ($Kind -eq '01.PROJECT') { 'project' } else { 'operation' }
  $SrcProj = Join-Parts $SrcRoot @($Kind, $Project)
  $operationName = if ($Kind -eq '10.OPERATION') { Get-OperationNameParts $Project } else { $null }
  if (-not $Slug) { $Slug = if ($operationName) { $operationName.Semantic } else { $Project -replace '^\d{8}_', '' } }
  $explicitDstName = -not [string]::IsNullOrEmpty($DstName)
  if (-not $DstName) { $DstName = $Project }

  # ---- resolve destination ----
  $DstProj = $null
  if (-not $RecipeOnly) {
    if ([string]::IsNullOrWhiteSpace($DstRoot)) { Stop-Blocked 'no destination: pass -DstRoot (or -RecipeOnly to check the recipe without syncing)' }
    if (-not (Test-Path -LiteralPath $DstRoot -PathType Container)) { Stop-Blocked "destination root not found: $DstRoot" }
    $DstRoot = (Resolve-Path -LiteralPath $DstRoot).ProviderPath.TrimEnd('\', '/')
    $a = $SrcRoot.ToLowerInvariant() + [IO.Path]::DirectorySeparatorChar; $b = $DstRoot.ToLowerInvariant() + [IO.Path]::DirectorySeparatorChar
    if ($a.StartsWith($b) -or $b.StartsWith($a)) { Stop-Blocked "source and destination are the same workspace, or one is inside the other: $SrcRoot / $DstRoot" }
    if (-not (Test-Layout $DstRoot)) { Stop-Blocked "the destination workspace has no 01.PROJECT folder: it is not on the project-first layout - $DstRoot" }
    $otherKind = if ($Kind -eq '01.PROJECT') { '10.OPERATION' } else { '01.PROJECT' }
    if (Test-Path -LiteralPath (Join-Parts $DstRoot @($otherKind, $DstName)) -PathType Container) {
      Stop-Blocked "'$DstName' is a $KindWord here but sits under $otherKind in the destination: reconcile that first"
    }
    $DstProj = Join-Parts $DstRoot @($Kind, $DstName)
    if ($operationName -and -not $explicitDstName -and -not (Test-Path -LiteralPath $DstProj)) {
      $dstOperations = Join-Path $DstRoot '10.OPERATION'
      if (Test-Path -LiteralPath $dstOperations -PathType Container) {
        $otherVersions = @(foreach ($sibling in (Get-ChildItem -LiteralPath $dstOperations -Directory -Force)) {
          $parts = Get-OperationNameParts $sibling.Name
          if ($parts -and [string]::Equals($parts.Semantic, $operationName.Semantic, [StringComparison]::OrdinalIgnoreCase) -and $parts.Date -ceq $operationName.Date -and $parts.Version -cne $operationName.Version) { $sibling.Name }
        })
        if ($otherVersions.Count) { Stop-Blocked "Operation lineage already exists under another destination version: $($otherVersions -join ', '). Review that counterpart and pass -DstName explicitly, or reconcile names first; no parallel operation was created." }
      }
    }
  }

  $AllowNow = Split-PathList $Allow $SrcProj
  $NewerSet = Split-PathList $OverwriteNewer $SrcProj
  $procDir  = Join-Path $SrcProj '20.PROCESS'
  $allowPath = Join-Path $procDir 'SYNC_ALLOW.json'
  $AllowSaved = @{}
  if (Test-Path -LiteralPath $allowPath -PathType Leaf) {
    try { foreach ($e in @((Get-Content -LiteralPath $allowPath -Raw | ConvertFrom-Json).cleared)) { if ($e.path) { $AllowSaved[([string]$e.path).ToLowerInvariant()] = $e } } }
    catch { Stop-Blocked "20.PROCESS/SYNC_ALLOW.json cannot be read: fix or remove it - $($_.Exception.Message)" }
  }
  $AllowUsed = New-Object System.Collections.Generic.List[object]     # cleared files copied in this run: {path, sha256, cleared}

  # Is this possible secret cleared? Returns $null when cleared, else the reason it is held.
  function Get-HeldReason { param([string]$Prel, [string]$Full, [string]$Why)
    $key = $Prel.ToLowerInvariant()
    if ($AllowNow -contains $key) { $AllowUsed.Add([pscustomobject]@{ path = $Prel; sha256 = (Get-Sha $Full); cleared = (Get-Date).ToString('yyyy-MM-dd') }); return $null }
    if ($AllowSaved.ContainsKey($key)) {
      $e = $AllowSaved[$key]
      if ((Get-Sha $Full) -eq ([string]$e.sha256).ToLowerInvariant()) { $AllowUsed.Add([pscustomobject]@{ path = $Prel; sha256 = [string]$e.sha256; cleared = [string]$e.cleared }); return $null }
      return "secret: $Why (cleared on $($e.cleared), but the file has changed since)"
    }
    return "secret: $Why"
  }

  $Plan     = New-Object System.Collections.Generic.List[object]
  $DirPlan  = New-Object System.Collections.Generic.List[object]
  $Notes    = New-Object System.Collections.Generic.List[string]
  $Warnings = New-Object System.Collections.Generic.List[string]
  $Blockers = New-Object System.Collections.Generic.List[string]

  # ---- 10.INPUT and 30.OUTPUT: everything ----
  foreach ($Role in $Roles) {
    $srcDir = Join-Path $SrcProj $Role
    if (-not (Test-Path -LiteralPath $srcDir -PathType Container)) { $Notes.Add("$Role does not exist in the source: nothing to copy from it"); continue }
    $tree = Get-Tree $srcDir
    foreach ($e in $tree.Errors) { $Blockers.Add("$Role/$e  [cannot be read]") }
    foreach ($l in $tree.Links)  { $Blockers.Add("$Role/$l  [a link or junction: OneDrive does not sync links - put the real files here, or remove the link]") }
    if ($DstProj) {
      $dstDir = Join-Path $DstProj $Role
      $DirPlan.Add([pscustomobject]@{ Rel = $Role; Dst = $dstDir })
      foreach ($d in $tree.Dirs) {
        if ((($d -split '/') -contains '.git') -or (Test-RefusedName ($d -split '/'))) { continue }
        $dd = Join-Path $dstDir (To-Native $d)
        if (Test-Path -LiteralPath $dd -PathType Leaf) { $Blockers.Add("$Role/$d  [a file with this name is in the way of the folder in the destination]") }
        else { $DirPlan.Add([pscustomobject]@{ Rel = "$Role/$d"; Dst = $dd }) }
      }
    }
    $runtimeSeen = @{}
    foreach ($f in $tree.Files) {
      $prel = "$Role/$($f.Rel)"
      $segs = $f.Rel -split '/'
      $dst  = if ($DstProj) { Join-Path (Join-Path $DstProj $Role) (To-Native $f.Rel) } else { '' }
      $row  = [pscustomobject]@{ Role = $Role; Rel = $prel; Src = $f.Full; Dst = $dst; Bytes = $f.Bytes; Mtime = $f.Mtime; Action = ''; Reason = '' }
      $isLock = $false
      foreach ($p in $LockOrOs) { if ($f.Name -like $p) { $isLock = $true; break } }
      if ($isLock) { $row.Action = 'RULE'; $row.Reason = 'lock/OS file' }
      elseif ($segs -contains '.git') { $row.Action = 'RULE'; $row.Reason = '.git internals' }
      elseif ($f.Ext -eq '.tmp') { $row.Action = 'RULE'; $row.Reason = 'temporary file (OneDrive does not upload .tmp)' }
      else {
        foreach ($s in $segs) { if ($RuntimeDir -contains $s) { $runtimeSeen[$s] = 1 + [int]$runtimeSeen[$s] } }
        $refused = Test-RefusedName $segs
        $sec = Test-Secret $f
        if (-not $sec) { foreach ($s in $segs) { if ($SecretDir -contains $s) { $sec = "inside a $s folder"; break } } }
        $heldWhy = if ($sec) { Get-HeldReason $prel $f.Full $sec } else { $null }
        if ($heldWhy) { $row.Action = 'HELD'; $row.Reason = $heldWhy }
        elseif ($refused) { $row.Action = 'NAME'; $row.Reason = $refused }
        elseif ($DstProj) {
          if (($dst.Length + $TmpSuffix.Length) -gt $PathLimit) { $row.Action = 'BLOCK'; $row.Reason = "destination path is $($dst.Length) characters (OneDrive limit $PathLimit)" }
          elseif ($f.Bytes -gt $FileLimitBytes) { $row.Action = 'BLOCK'; $row.Reason = "file is $(Format-Bytes $f.Bytes) (OneDrive limit $(Format-Bytes $FileLimitBytes))" }
          else { Get-CopyAction $row }
        }
      }
      $Plan.Add($row)
    }
    foreach ($k in $runtimeSeen.Keys) { $Warnings.Add("$Role contains $($runtimeSeen[$k]) files under a '$k' folder: runtime bulk belongs in 20.PROCESS. It is copied as it is unless you move it first.") }
  }

  # ---- RAM: this project's resume files (named <slug>_ram..., with or without a leading date) ----
  $RamPlan = New-Object System.Collections.Generic.List[object]
  $ramSrc = Join-Path $SrcRoot '12.MEMORY_RAM'
  if (Test-Path -LiteralPath $ramSrc -PathType Container) {
    $ramRe = '(?i)^(\d{8}_)?' + [regex]::Escape($Slug) + '_ram([_.]|$)'
    foreach ($f in (Get-ChildItem -LiteralPath $ramSrc -File)) {
      if ($f.Name -notmatch $ramRe) { continue }
      $prel = "12.MEMORY_RAM/$($f.Name)"
      $dst  = if ($DstProj) { Join-Path (Join-Path $DstRoot '12.MEMORY_RAM') $f.Name } else { '' }
      $row  = [pscustomobject]@{ Role = '12.MEMORY_RAM'; Rel = $prel; Src = $f.FullName; Dst = $dst; Bytes = [int64]$f.Length; Mtime = $f.LastWriteTimeUtc; Action = ''; Reason = '' }
      $heldWhy = if (Test-SecretContent $f.FullName) { Get-HeldReason $prel $f.FullName 'content looks like a credential' } else { $null }
      if ($heldWhy) { $row.Action = 'HELD'; $row.Reason = $heldWhy }
      elseif ($DstProj) { Get-CopyAction $row }
      $RamPlan.Add($row)
    }
  }
  foreach ($r in $RamPlan) { $Plan.Add($r) }

  # ---- 20.PROCESS: metadata only ----
  $procFiles = @(); $procLinks = @()
  if (Test-Path -LiteralPath $procDir -PathType Container) {
    $ptree = Get-Tree $procDir
    $procFiles = @($ptree.Files | Where-Object { $ControlFiles -notcontains $_.Rel })
    $procLinks = $ptree.Links
  } else { $Notes.Add('20.PROCESS does not exist in the source: no manifest is written') }
  $stray = @(Get-ChildItem -LiteralPath $SrcProj -Force | ForEach-Object { $_.Name } | Where-Object { $n = $_; ($n -notin @('10.INPUT', '20.PROCESS', '30.OUTPUT')) -and -not ($LockOrOs | Where-Object { $n -like $_ }) })
  if ($stray.Count) { $Warnings.Add("$($stray.Count) item(s) sit outside 10.INPUT / 20.PROCESS / 30.OUTPUT and are not synced: $(($stray | Select-Object -First 8) -join ', ')") }

  function New-Manifest {
    $isRuntime = { param($f) foreach ($s in ($f.Rel -split '/')) { if ($RuntimeDir -contains $s) { return $s } }; return $null }
    $work = New-Object System.Collections.Generic.List[object]; $rt = @{}
    foreach ($f in $procFiles) {
      $r = & $isRuntime $f
      if ($r) { if (-not $rt.ContainsKey($r)) { $rt[$r] = @{ files = 0; bytes = [int64]0 } }; $rt[$r].files++; $rt[$r].bytes += $f.Bytes } else { $work.Add($f) }
    }
    $byExt = @($work | Group-Object Ext | Sort-Object Count -Descending | ForEach-Object {
        [ordered]@{ ext = $(if ($_.Name) { $_.Name } else { '(none)' }); files = $_.Count; bytes = [int64](Get-Sum $_.Group) } })
    $byTop = @($work | Group-Object { $s = $_.Rel -split '/'; if ($s.Count -gt 1) { $s[0] + '/' } else { '(top level)' } } | Sort-Object Count -Descending | ForEach-Object {
        [ordered]@{ folder = $_.Name; files = $_.Count; bytes = [int64](Get-Sum $_.Group) } })
    $iter = @($work | Group-Object { Get-Lineage $_.Name } | Where-Object { $_.Count -ge 2 } | Sort-Object Count -Descending | Select-Object -First 25 | ForEach-Object {
        $t = @($_.Group | Sort-Object Mtime)
        [ordered]@{ artifact = $_.Name; versions = $_.Count; first = $t[0].Mtime.ToString('yyyy-MM-ddTHH:mm:ssZ'); last = $t[-1].Mtime.ToString('yyyy-MM-ddTHH:mm:ssZ') } })
    $sorted = @($work | Sort-Object Mtime)
    $listed = @($work | Sort-Object Rel | Select-Object -First $ManifestMaxFiles | ForEach-Object {
        [ordered]@{ path = $_.Rel; bytes = $_.Bytes; modified = $_.Mtime.ToString('yyyy-MM-ddTHH:mm:ssZ') } })
    return [ordered]@{
      schema          = 2
      about           = 'Inventory of 20.PROCESS on the machine that did the work. These files are NOT in the cloud; RECIPE.md says how the work was done and why.'
      project         = $Project
      kind            = $KindWord
      generated       = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
      device          = [System.Environment]::MachineName
      totals          = [ordered]@{ files = $procFiles.Count; bytes = [int64](Get-Sum $procFiles) }
      first_modified  = $(if ($sorted.Count) { $sorted[0].Mtime.ToString('yyyy-MM-ddTHH:mm:ssZ') } else { $null })
      last_modified   = $(if ($sorted.Count) { $sorted[-1].Mtime.ToString('yyyy-MM-ddTHH:mm:ssZ') } else { $null })
      runtime_folders = @($rt.Keys | Sort-Object | ForEach-Object { [ordered]@{ folder = $_; files = $rt[$_].files; bytes = $rt[$_].bytes } })
      by_extension    = $byExt
      by_top_folder   = $byTop
      iterations      = $iter
      files_listed    = $listed.Count
      files           = $listed
    }
  }

  # ---- recipe: exists, accounts for every output, checked against the current outputs ----
  $outRows = @($Plan | Where-Object { $_.Role -eq '30.OUTPUT' -and $_.Action -ne 'RULE' })
  $fpLines = [string[]]@($outRows | ForEach-Object { $_.Rel.Substring('30.OUTPUT/'.Length) + "`t" + $_.Bytes + "`t" + $_.Mtime.ToString('yyyyMMddHHmmss') })
  [Array]::Sort($fpLines, [System.StringComparer]::Ordinal)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $Fingerprint = (-join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($fpLines -join "`n"))) | ForEach-Object { $_.ToString('x2') })).Substring(0, 16)

  $recipePath  = Join-Path $procDir 'RECIPE.md'
  $RecipeState = 'missing'; $RecipeStatus = $null; $Uncovered = @(); $RecipeHeld = $false
  if (Test-Path -LiteralPath $recipePath -PathType Leaf) {
    $txt = [System.IO.File]::ReadAllText($recipePath)
    # the recipe is the one PROCESS file that goes up, so it gets the same credential scan as INPUT/OUTPUT
    if ((Test-SecretContent $recipePath) -and (Get-HeldReason '20.PROCESS/RECIPE.md' $recipePath 'content looks like a credential')) { $RecipeHeld = $true }
    $stamp = $null
    $m = [regex]::Match($txt, '(?im)^\s*[-*]?\s*\**outputs_fingerprint\**\s*:\s*\**\s*`?([0-9a-f]{16})\b')
    if ($m.Success) { $stamp = $m.Groups[1].Value.ToLower() }
    $ms = [regex]::Match($txt, '(?im)^\s*[-*]?\s*\**status\**\s*:\s*\**\s*`?(provisional|final)\b')
    if ($ms.Success) { $RecipeStatus = $ms.Groups[1].Value.ToLower() }
    $named = @{}
    foreach ($t in [regex]::Matches($txt, '`([^`\r\n]+)`')) {
      $v = ($t.Groups[1].Value.Trim() -replace '\\', '/').ToLowerInvariant()
      if ($v.StartsWith('30.output/') -and $v.Length -gt '30.output/'.Length) { $named[$v.Substring('30.output/'.Length)] = $true }
    }
    $Uncovered = @($outRows | Where-Object {
        $r = $_.Rel.Substring('30.OUTPUT/'.Length).ToLowerInvariant()
        if ($named.ContainsKey($r)) { return $false }
        $s = $r -split '/'
        for ($i = 1; $i -lt $s.Count; $i++) { if ($named.ContainsKey((($s[0..($i - 1)] -join '/') + '/'))) { return $false } }
        return $true
      } | ForEach-Object { $_.Rel })
    if ($Uncovered.Count -gt 0) { $RecipeState = 'incomplete' }
    elseif ($stamp -ne $Fingerprint) { $RecipeState = 'stale' }
    else { $RecipeState = 'verified' }
  }
  # What the recipe owes depends on what the folder is:
  #   a project whose recipe says final (or says nothing)  -> must be verified
  #   a project whose recipe says provisional              -> must exist; it is finished when the project closes
  #   an operation                                         -> must exist; its outputs change every cycle
  $recipeProblem = $null
  if ($RecipeHeld) { $recipeProblem = 'recipe held back: its text looks like it contains a credential' }
  elseif ($RecipeState -eq 'missing') { $recipeProblem = 'recipe missing' }
  elseif ($Kind -eq '01.PROJECT' -and $RecipeStatus -ne 'provisional' -and $RecipeState -ne 'verified') { $recipeProblem = "recipe $RecipeState" }
  $recipeLine = switch ($RecipeState) {
    'missing'    { 'missing - 20.PROCESS/RECIPE.md does not exist' }
    'incomplete' { "incomplete - $($Uncovered.Count) output file(s) are not accounted for" }
    'stale'      { 'stale - the outputs changed since the recipe was checked (or it was never stamped)' }
    'verified'   { 'verified' }
  }
  if (-not $recipeProblem -and $RecipeState -ne 'verified') {
    $recipeLine = if ($Kind -eq '10.OPERATION') { "present - operation: outputs change every cycle, so it is not held to a fingerprint ($RecipeState)" }
                  else { "provisional - to be checked against the outputs when the project closes ($RecipeState)" }
  }
  if ($RecipeHeld) { $recipeLine = "held back - its text looks like it contains a credential (remove it, or -Allow '20.PROCESS/RECIPE.md')" }
  if ($RecipeStatus) { $recipeLine += "  [status: $RecipeStatus]" }

  # ---- recipe only: no destination ----
  if ($RecipeOnly) {
    Write-Host ""
    Write-Host "=== recipe check: $Project  ($KindWord) ===" -ForegroundColor Cyan
    Write-Host ("  10.INPUT   {0,8:N0} files   30.OUTPUT {1,8:N0} files   20.PROCESS {2,8:N0} files ({3})" -f @($Plan | Where-Object Role -eq '10.INPUT').Count, $outRows.Count, $procFiles.Count, (Format-Bytes (Get-Sum $procFiles)))
    Write-Host "  outputs_fingerprint: $Fingerprint"
    foreach ($u in ($Uncovered | Select-Object -First 20)) { Write-Host "     ? $u" -ForegroundColor Yellow }
    if ($Uncovered.Count -gt 20) { Write-Host "     ? ... and $($Uncovered.Count - 20) more" -ForegroundColor Yellow }
    if ($Apply -and (Test-Path -LiteralPath $procDir -PathType Container)) {
      Write-Utf8 (Join-Path $procDir 'PROCESS_MANIFEST.json') (New-Manifest | ConvertTo-Json -Depth 6)
      Write-Host "  wrote 20.PROCESS/PROCESS_MANIFEST.json"
    }
    Write-Host "RECIPE: $recipeLine"
    if ($recipeProblem) { Write-Host "RESULT: INCOMPLETE - $recipeProblem" -ForegroundColor Yellow; exit 2 }
    Write-Host 'RESULT: OK' -ForegroundColor Green; exit 0
  }

  # ---- in the cloud only (kept, never deleted), leftovers, strays ----
  $srcSet = @{}; foreach ($r in $Plan) { $srcSet[$r.Rel.ToLowerInvariant()] = $true }
  $CloudOnly = New-Object System.Collections.Generic.List[string]
  $Leftovers = New-Object System.Collections.Generic.List[string]
  foreach ($Role in $Roles) {
    $dstDir = Join-Path $DstProj $Role
    if (Test-Path -LiteralPath $dstDir -PathType Container) {
      foreach ($f in (Get-Tree $dstDir).Files) {
        if ($f.Name.EndsWith($TmpSuffix)) { $Leftovers.Add($f.Full); continue }
        $prel = "$Role/$($f.Rel)"
        if (-not $srcSet.ContainsKey($prel.ToLowerInvariant())) { $CloudOnly.Add($prel) }
      }
    }
  }
  $dstProc = Join-Path $DstProj '20.PROCESS'
  if (Test-Path -LiteralPath $dstProc -PathType Container) {
    $extra = @((Get-Tree $dstProc).Files | Where-Object { $ControlFiles -notcontains $_.Rel } | ForEach-Object { $_.Rel })
    if ($extra.Count) { $Notes.Add("the destination's 20.PROCESS holds $($extra.Count) file(s) that this sync did not put there (kept): $(($extra | Select-Object -First 5) -join ', ')") }
  }
  if ($procLinks.Count) { $Notes.Add("20.PROCESS contains $($procLinks.Count) link(s); they are not followed and not counted") }

  # ---- blockers and warnings ----
  $toCopy    = @($Plan | Where-Object { $_.Action -eq 'COPY' })
  $held      = @($Plan | Where-Object { $_.Action -eq 'HELD' })
  $badName   = @($Plan | Where-Object { $_.Action -eq 'NAME' })
  $conflicts = @($Plan | Where-Object { $_.Action -eq 'CONFLICT' })
  $byRule    = @($Plan | Where-Object { $_.Action -eq 'RULE' })
  foreach ($b in @($Plan | Where-Object { $_.Action -eq 'BLOCK' })) { $Blockers.Add("$($b.Rel)  [$($b.Reason)]") }
  $needBytes = Get-Sum $toCopy
  $free = $null
  try { $free = (Get-Item -LiteralPath $DstRoot).PSDrive.Free } catch {}
  if ($null -ne $free -and $needBytes -gt $free) { $Blockers.Add("not enough free space on the destination drive: need $(Format-Bytes $needBytes), free $(Format-Bytes $free)") }
  $longPaths = @($Plan | Where-Object { $_.Action -in @('COPY', 'SAME') -and $_.Dst.Length -gt $PathWarn })
  if ($longPaths.Count) { $Warnings.Add("$($longPaths.Count) destination paths are longer than $PathWarn characters: they sync, but some Windows apps cannot open them") }
  if ($toCopy.Count -ge $ManyFiles) { $Warnings.Add("this sync adds $('{0:N0}' -f $toCopy.Count) files: OneDrive recommends no more than 300,000 synced items in total") }
  if ($Hash) { $Warnings.Add('-Hash reads every file on both sides: online-only files in the destination are downloaded to be compared') }

  # ---- the plan ----
  Write-Host ""
  Write-Host "=== cloud-sync plan: $Project  ($KindWord) ===" -ForegroundColor Cyan
  Write-Host "  from  $SrcProj"
  Write-Host "  to    $DstProj"
  Write-Host ""
  foreach ($Role in ($Roles + '12.MEMORY_RAM')) {
    $all = @($Plan | Where-Object { $_.Role -eq $Role })
    $c = @($all | Where-Object { $_.Action -eq 'COPY' }); $s = @($all | Where-Object { $_.Action -eq 'SAME' })
    $h = @($all | Where-Object { $_.Action -notin @('COPY', 'SAME') })
    Write-Host ("  {0,-13} {1,8:N0} files {2,10}  -> copy {3:N0} ({4}) | already in cloud {5:N0} | not copied {6:N0}" -f $Role, $all.Count, (Format-Bytes (Get-Sum $all)), $c.Count, (Format-Bytes (Get-Sum $c)), $s.Count, $h.Count)
  }
  Write-Host ("  {0,-13} {1,8:N0} files {2,10}  -> stays on this computer; manifest and recipe go up" -f '20.PROCESS', $procFiles.Count, (Format-Bytes (Get-Sum $procFiles)))

  if ($held.Count) {
    Write-Host "`n  HELD BACK as possible secrets - the cloud copy is not complete until a human settles each:" -ForegroundColor Red
    foreach ($r in $held) { Write-Host "     ! $($r.Rel)  [$($r.Reason)]" -ForegroundColor Red }
  }
  if ($badName.Count) {
    Write-Host "`n  NOT COPIED - OneDrive refuses the name; rename the file or folder in the hub:" -ForegroundColor Red
    foreach ($r in $badName) { Write-Host "     ! $($r.Rel)  [$($r.Reason)]" -ForegroundColor Red }
  }
  if ($conflicts.Count) {
    Write-Host "`n  NOT OVERWRITTEN - the cloud copy is newer than the hub's. Decide which wins (-OverwriteNewer <path> = the hub's):" -ForegroundColor Red
    foreach ($r in $conflicts) { Write-Host "     ! $($r.Rel)  [$($r.Reason)]" -ForegroundColor Red }
  }
  if ($AllowUsed.Count) {
    Write-Host "`n  Cleared by a human (copied):"
    foreach ($a in $AllowUsed) { Write-Host "     + $($a.path)  (cleared $($a.cleared))" }
  }
  if ($byRule.Count) {
    Write-Host "`n  Not copied, by rule (not content):"
    foreach ($g in ($byRule | Where-Object { $_.Reason -ne '.git internals' } | Group-Object Reason)) {
      foreach ($r in ($g.Group | Select-Object -First 10)) { Write-Host "     - $($r.Rel)  [$($g.Name)]" }
      if ($g.Count -gt 10) { Write-Host ("     - ... and {0:N0} more [{1}]" -f ($g.Count - 10), $g.Name) }
    }
    $git = @($byRule | Where-Object { $_.Reason -eq '.git internals' })
    foreach ($g in ($git | Group-Object { ($_.Rel -split '/\.git/')[0] + '/.git' })) { Write-Host ("     - {0:N0} files under {1}  [.git internals]" -f $g.Count, $g.Name) }
  }
  if ($Blockers.Count) {
    Write-Host "`n  BLOCKERS - fix these; nothing is skipped to get around them:" -ForegroundColor Red
    foreach ($b in $Blockers) { Write-Host "     x $b" -ForegroundColor Red }
  }
  foreach ($w in $Warnings) { Write-Host "`n  Warning: $w" -ForegroundColor Yellow }
  foreach ($n in $Notes) { Write-Host "`n  Note: $n" -ForegroundColor DarkGray }
  if ($CloudOnly.Count) {
    Write-Host ("`n  In the cloud only (kept, never deleted): {0:N0} files" -f $CloudOnly.Count)
    foreach ($c in ($CloudOnly | Select-Object -First 10)) { Write-Host "     . $c" }
    if ($CloudOnly.Count -gt 10) { Write-Host "     . ..." }
  }
  Write-Host "`n  outputs_fingerprint: $Fingerprint"
  foreach ($u in ($Uncovered | Select-Object -First 20)) { Write-Host "     ? not in the recipe: $u" -ForegroundColor Yellow }
  if ($Uncovered.Count -gt 20) { Write-Host "     ? ... and $($Uncovered.Count - 20) more" -ForegroundColor Yellow }
  if ($toCopy.Count -and -not $Apply) {
    Write-Host "`n  First files to copy:"
    foreach ($r in ($toCopy | Select-Object -First 15)) { Write-Host ("     {0}  ({1}, {2})" -f $r.Rel, (Format-Bytes $r.Bytes), $r.Reason) }
    if ($toCopy.Count -gt 15) { Write-Host ("     ... and {0:N0} more" -f ($toCopy.Count - 15)) }
  }

  if ($Blockers.Count) { Write-Host ""; Stop-Blocked "$($Blockers.Count) blocker(s); nothing was copied" }

  $fileProblems = @()
  if ($held.Count)      { $fileProblems += "$($held.Count) file(s) held back as possible secrets" }
  if ($badName.Count)   { $fileProblems += "$($badName.Count) file(s) with a name OneDrive refuses" }
  if ($conflicts.Count) { $fileProblems += "$($conflicts.Count) file(s) newer in the cloud, not overwritten" }
  $incomplete = @($fileProblems); if ($recipeProblem) { $incomplete += $recipeProblem }
  $filesLine = if ($fileProblems.Count) { "incomplete - $($fileProblems -join '; ')" } else { 'complete' }

  if (-not $Apply) {
    Write-Host "`n(dry run - nothing was changed. Re-run with -Apply to execute.)" -ForegroundColor Yellow
    Write-Host "FILES:  would be $filesLine"
    Write-Host "RECIPE: $recipeLine"
    if ($incomplete.Count) { Write-Host "RESULT: DRY RUN, would be INCOMPLETE - $($incomplete -join '; ')" -ForegroundColor Yellow; exit 2 }
    Write-Host 'RESULT: DRY RUN, clean' -ForegroundColor Green; exit 0
  }

  # ---- apply ----
  $failed = New-Object System.Collections.Generic.List[string]
  foreach ($l in $Leftovers) { try { [System.IO.File]::Delete($l) } catch {} }       # this script's own unfinished copies from an interrupted run
  foreach ($d in $DirPlan) { try { [void][System.IO.Directory]::CreateDirectory($d.Dst) } catch { $failed.Add("$($d.Rel)/  [cannot create the folder: $($_.Exception.Message)]") } }
  function Copy-One { param($Row)
    $tmp = $Row.Dst + $TmpSuffix
    try {
      [void][System.IO.Directory]::CreateDirectory((Split-Path -Parent $Row.Dst))
      [System.IO.File]::Copy($Row.Src, $tmp, $true)
      [System.IO.File]::SetLastWriteTimeUtc($tmp, $Row.Mtime)
      if (Test-Path -LiteralPath $Row.Dst -PathType Leaf) { [System.IO.File]::SetAttributes($Row.Dst, [System.IO.FileAttributes]::Normal) }   # read-only or hidden would refuse the swap
      [System.IO.File]::Move($tmp, $Row.Dst, $true)
    } catch {
      $failed.Add("$($Row.Rel)  [copy failed: $($_.Exception.Message)]")
      try { if (Test-Path -LiteralPath $tmp -PathType Leaf) { [System.IO.File]::Delete($tmp) } } catch {}
    }
  }
  foreach ($r in $toCopy) { Copy-One $r }

  $manifestJson = $null
  try {
    [void][System.IO.Directory]::CreateDirectory($dstProc)
    if (Test-Path -LiteralPath $procDir -PathType Container) {
      $manifestJson = (New-Manifest | ConvertTo-Json -Depth 6)
      Write-Utf8 (Join-Path $procDir 'PROCESS_MANIFEST.json') $manifestJson
      Write-Utf8 (Join-Path $dstProc 'PROCESS_MANIFEST.json') $manifestJson
      if ($AllowUsed.Count) {
        $merged = @{}; foreach ($k in $AllowSaved.Keys) { $merged[$k] = $AllowSaved[$k] }; foreach ($a in $AllowUsed) { $merged[$a.path.ToLowerInvariant()] = $a }
        $doc = [ordered]@{ about = 'Files a human cleared after the secret scan flagged them. A file counts as cleared only while its content still has this SHA-256. This file stays on this computer.'
                           cleared = @($merged.Keys | Sort-Object | ForEach-Object { [ordered]@{ path = [string]$merged[$_].path; sha256 = [string]$merged[$_].sha256; cleared = [string]$merged[$_].cleared } }) }
        Write-Utf8 $allowPath ($doc | ConvertTo-Json -Depth 4)
      }
    }
    if ((Test-Path -LiteralPath $recipePath -PathType Leaf) -and -not $RecipeHeld) { [System.IO.File]::Copy($recipePath, (Join-Path $dstProc 'RECIPE.md'), $true) }
  } catch { $failed.Add("20.PROCESS  [manifest or recipe could not be written: $($_.Exception.Message)]") }

  # ---- verify ----
  $synced = @($Plan | Where-Object { $_.Action -in @('COPY', 'SAME') })
  foreach ($r in $synced) {
    if (-not (Test-Path -LiteralPath $r.Dst -PathType Leaf)) { if (-not ($failed | Where-Object { $_.StartsWith($r.Rel + '  ') })) { $failed.Add("$($r.Rel)  [missing in destination]") }; continue }
    $d = Get-Item -LiteralPath $r.Dst -Force
    if ($d.Length -ne $r.Bytes) { $failed.Add("$($r.Rel)  [size differs: $($d.Length) vs $($r.Bytes)]"); continue }
    if ($Hash -and $r.Action -eq 'COPY' -and ((Get-Sha $r.Src) -ne (Get-Sha $r.Dst))) { $failed.Add("$($r.Rel)  [SHA-256 differs]") }
  }
  foreach ($d in $DirPlan) { if (-not (Test-Path -LiteralPath $d.Dst -PathType Container)) { $failed.Add("$($d.Rel)/  [folder missing in destination]") } }
  $failed = @($failed | Select-Object -Unique)
  $heldInCloud = @($held | Where-Object { $_.Dst -and (Test-Path -LiteralPath $_.Dst -PathType Leaf) } | ForEach-Object { $_.Rel })

  $result = if ($failed.Count) { 'FAILED' } elseif ($incomplete.Count) { 'INCOMPLETE' } else { 'OK' }

  # ---- sync report (written beside the recipe on both sides) ----
  $L = New-Object System.Collections.Generic.List[string]
  $L.Add("# Sync report - $Project"); $L.Add('')
  $L.Add("- result: $result$(if ($incomplete.Count) { ' (' + ($incomplete -join '; ') + ')' })")
  $L.Add("- files: $filesLine")
  $L.Add("- recipe: $recipeLine")
  $L.Add("- synced: $((Get-Date).ToString('yyyy-MM-dd HH:mm zzz')) from $([System.Environment]::MachineName)")
  $L.Add("- destination folder: ``$Kind/$DstName``$(if ($DstName -ne $Project) { " (in the hub: ``$Kind/$Project``)" })")
  $L.Add("- compared and verified by: $(if ($Hash) { 'SHA-256' } else { 'file size and time' })")
  $L.Add('')
  $L.Add('| Folder | In the cloud copy | Size | Copied this run | Not copied |')
  $L.Add('| --- | --- | --- | --- | --- |')
  foreach ($Role in ($Roles + '12.MEMORY_RAM')) {
    $a = @($synced | Where-Object { $_.Role -eq $Role }); $c = @($toCopy | Where-Object { $_.Role -eq $Role })
    $n = @($Plan | Where-Object { $_.Role -eq $Role -and $_.Action -notin @('COPY', 'SAME') })
    $L.Add(('| {0} | {1:N0} files | {2} | {3:N0} | {4:N0} |' -f $Role, $a.Count, (Format-Bytes (Get-Sum $a)), $c.Count, $n.Count))
  }
  $L.Add(('| 20.PROCESS | recipe and manifest only | {0} stays on {1} ({2:N0} files) | | |' -f (Format-Bytes (Get-Sum $procFiles)), [System.Environment]::MachineName, $procFiles.Count))
  $L.Add('')
  if ($held.Count) { $L.Add('## Held back as possible secrets (NOT in the cloud)'); $L.Add(''); foreach ($r in $held) { $L.Add("- ``$($r.Rel)`` - $($r.Reason)") }; $L.Add('') }
  if ($badName.Count) { $L.Add('## Not copied: OneDrive refuses the name (NOT in the cloud)'); $L.Add(''); foreach ($r in $badName) { $L.Add("- ``$($r.Rel)`` - $($r.Reason)") }; $L.Add('') }
  if ($conflicts.Count) { $L.Add('## Not overwritten: the cloud copy is newer'); $L.Add(''); foreach ($r in $conflicts) { $L.Add("- ``$($r.Rel)`` - $($r.Reason)") }; $L.Add('') }
  if ($AllowUsed.Count) { $L.Add('## Cleared by a human and copied'); $L.Add(''); foreach ($a in $AllowUsed) { $L.Add("- ``$($a.path)`` (cleared $($a.cleared))") }; $L.Add('') }
  if ($byRule.Count) {
    $L.Add('## Not copied, by rule (not content)'); $L.Add('')
    foreach ($g in ($byRule | Group-Object Reason)) { $L.Add(('- {0}: {1:N0} file(s)' -f $g.Name, $g.Count)) }
    foreach ($r in @($byRule | Where-Object { $_.Reason -ne '.git internals' } | Select-Object -First 50)) { $L.Add("  - ``$($r.Rel)``") }
    $L.Add('')
  }
  if ($heldInCloud.Count) { $L.Add('## Held back now, but present in the cloud from an earlier sync'); $L.Add(''); foreach ($h in $heldInCloud) { $L.Add("- ``$h``") }; $L.Add('') }
  if ($CloudOnly.Count) {
    $L.Add(('## In the cloud only (kept, never deleted): {0:N0} file(s)' -f $CloudOnly.Count)); $L.Add('')
    foreach ($c in ($CloudOnly | Select-Object -First 50)) { $L.Add("- ``$c``") }
    if ($CloudOnly.Count -gt 50) { $L.Add('- ...') }
    $L.Add('')
  }
  if ($failed.Count) { $L.Add('## Failures'); $L.Add(''); foreach ($b in $failed) { $L.Add("- $b") }; $L.Add('') }
  $reportText = ($L -join "`n") + "`n"
  try {
    Write-Utf8 (Join-Path $dstProc 'SYNC_REPORT.md') $reportText
    if (Test-Path -LiteralPath $procDir -PathType Container) { Write-Utf8 (Join-Path $procDir 'SYNC_REPORT.md') $reportText }
  } catch { Write-Host "  could not write SYNC_REPORT.md: $($_.Exception.Message)" -ForegroundColor Red }

  # ---- result ----
  Write-Host ("`nApplied -> {0}" -f $DstProj) -ForegroundColor Green
  Write-Host ("  copied {0:N0} files ({1}); verified {2:N0} files by {3}" -f ($toCopy.Count - @($failed | Where-Object { $_ -match 'copy failed' }).Count), (Format-Bytes (Get-Sum $toCopy)), $synced.Count, $(if ($Hash) { 'SHA-256' } else { 'size' }))
  foreach ($h in $heldInCloud) { Write-Host "  Note: $h is held back now but is in the cloud from an earlier sync (not deleted)" -ForegroundColor Yellow }
  Write-Host "FILES:  $(if ($failed.Count) { "failed - $($failed.Count) item(s) did not copy or verify" } else { $filesLine })"
  Write-Host "RECIPE: $recipeLine"
  if ($failed.Count) {
    foreach ($b in ($failed | Select-Object -First 30)) { Write-Host "     x $b" -ForegroundColor Red }
    Write-Host "RESULT: FAILED - $($failed.Count) item(s) did not copy or verify; run again after fixing the cause" -ForegroundColor Red; exit 1
  }
  if ($incomplete.Count) { Write-Host "RESULT: INCOMPLETE - $($incomplete -join '; ')" -ForegroundColor Yellow; exit 2 }
  Write-Host 'RESULT: OK' -ForegroundColor Green; exit 0
}
catch {
  Write-Host "RESULT: FAILED - unexpected error: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "  at: $($_.InvocationInfo.PositionMessage -replace '\r?\n', ' ')" -ForegroundColor DarkGray
  exit 1
}
