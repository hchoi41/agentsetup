param(
    [Parameter(Mandatory = $true)]
    [string]$TranscriptZip,

    [string]$WorkspaceRoot = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

function Get-Slug {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }

    $slug = $Text.ToLowerInvariant()
    $slug = $slug -replace '[^a-z0-9]+', '_'
    $slug = $slug -replace '^_+|_+$', ''

    if ([string]::IsNullOrWhiteSpace($slug)) {
        return $null
    }

    return $slug
}

function Normalize-ProjectSlug {
    param([string]$Text)

    $slug = Get-Slug $Text
    if (-not $slug) {
        return $null
    }

    $normalized = $slug -replace '^\d{6,8}_?', ''
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $slug
    }

    return $normalized
}

function Normalize-ComparablePath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }

    $normalized = $Path.Trim()
    $normalized = $normalized -replace '/', '\'
    $normalized = $normalized -replace '\\+$', ''

    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $null
    }

    return $normalized.ToLowerInvariant()
}

function Is-IgnoredProjectCandidate {
    param([string]$Candidate)

    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        return $true
    }

    return @(
        "uploads",
        "upload",
        "downloads",
        "download",
        "temp",
        "tmp"
    ) -contains $Candidate
}

function Get-MeaningfulTokens {
    param([string]$Text)

    $stopwords = @(
        "a", "an", "and", "analysis", "chat", "conversation", "for", "from",
        "message", "new", "of", "project", "session", "the", "to", "with", "your"
    )

    $slug = Get-Slug $Text
    if (-not $slug) {
        return @()
    }

    return @(
        $slug -split '_' |
            Where-Object {
                $_ -and
                $_.Length -ge 3 -and
                $_ -notmatch '^\d+$' -and
                $stopwords -notcontains $_
            }
    )
}

function Get-FileHashSha256 {
    param([string]$Path)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $hashBytes = $sha256.ComputeHash($stream)
        }
        finally {
            $stream.Dispose()
        }
    }
    finally {
        $sha256.Dispose()
    }

    return ([BitConverter]::ToString($hashBytes)).Replace("-", "").ToLowerInvariant()
}

function Ensure-Directory {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        $null = New-Item -ItemType Directory -Path $Path -Force
    }
}

function Ensure-File {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        $null = New-Item -ItemType File -Path $Path -Force
    }
}

function Get-ZipEntryText {
    param(
        [System.IO.Compression.ZipArchive]$Zip,
        [string]$EntryName
    )

    $entry = $Zip.GetEntry($EntryName)
    if (-not $entry) {
        throw "Missing zip entry: $EntryName"
    }

    $reader = [System.IO.StreamReader]::new($entry.Open())
    try {
        return $reader.ReadToEnd()
    }
    finally {
        $reader.Dispose()
    }
}

function Get-JsonLinesObjects {
    param([string]$JsonlText)

    $records = New-Object System.Collections.Generic.List[object]
    foreach ($line in $JsonlText -split "`r?`n") {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        try {
            $records.Add(($line | ConvertFrom-Json))
        }
        catch {
        }
    }

    return $records
}

function Get-ProjectRootNameFromPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }

    if ($Path -match '/mnt/([^/\\]+)(?:/|\\|$)') {
        return $Matches[1]
    }

    if ($Path -match '\\([^\\]+)\\(?:00\.ABOUT|00\.DATA|01\.DATA|00\.PROJECTS|02\.PROJECTS|03\.TEMPLATES|01\.INPUT|02\.INPUT_TEMPLATES|03\.OUTPUT_FINAL|11\.DE DELIVERABLES|11\.OUTPUT_ROUGH|12\.DE RAM|12\.MEMORY_RAM|13\.DE LESSONS LEARNED|13\.MEMORY_HDD|14\.LESSONS_LEARNED|88\.DE HDD)(?:\\|$)') {
        return $Matches[1]
    }

    if (Test-Path -LiteralPath $Path -PathType Container) {
        return Split-Path -Path $Path -Leaf
    }

    if (Test-Path -LiteralPath $Path) {
        $parent = Split-Path -Path $Path -Parent
        if ($parent) {
            return Split-Path -Path $parent -Leaf
        }
    }

    return $null
}

function Get-ProjectRootPathFromPath {
    param([string]$Path)

    $normalizedPath = Normalize-ComparablePath $Path
    if (-not $normalizedPath) {
        return $null
    }

    if ($normalizedPath -match '^(?<root>[a-z]:\\.*?)(?:\\(?:00\.about|00\.data|01\.data|00\.projects|02\.projects|03\.templates|01\.input|02\.input_templates|03\.output_final|11\.de deliverables|11\.output_rough|12\.de ram|12\.memory_ram|13\.de lessons learned|13\.memory_hdd|14\.lessons_learned|88\.de hdd)(?:\\|$))') {
        return $Matches["root"]
    }

    return $null
}

function Get-TranscriptIdentifier {
    param(
        $Metadata,
        [System.Collections.Generic.List[object]]$Records,
        [string]$FallbackName
    )

    foreach ($value in @($Metadata.cliSessionId, $Metadata.sessionId, $Metadata.processName)) {
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value
        }
    }

    foreach ($record in $Records) {
        foreach ($value in @($record.sessionId, $record.promptId, $record.uuid)) {
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value
            }
        }
    }

    return $FallbackName
}

function Get-FirstUserPrompt {
    param([System.Collections.Generic.List[object]]$Records)

    foreach ($record in $Records) {
        if ($record.type -ne "user" -or -not $record.message) {
            continue
        }

        $content = $record.message.content
        if ($content -is [string] -and -not [string]::IsNullOrWhiteSpace($content)) {
            return $content.Trim()
        }

        if ($content -is [System.Array]) {
            foreach ($item in $content) {
                if ($item.type -eq "text" -and -not [string]::IsNullOrWhiteSpace($item.text)) {
                    return $item.text.Trim()
                }
            }
        }
    }

    return $null
}

function Get-ReferencedPaths {
    param(
        $Metadata,
        [System.Collections.Generic.List[object]]$Records
    )

    $paths = New-Object System.Collections.Generic.List[string]

    foreach ($folder in @($Metadata.userSelectedFolders)) {
        if (-not [string]::IsNullOrWhiteSpace($folder)) {
            $paths.Add([string]$folder)
        }
    }

    foreach ($approved in @($Metadata.userApprovedFileAccessPaths)) {
        if (-not [string]::IsNullOrWhiteSpace($approved)) {
            $paths.Add([string]$approved)
        }
    }

    foreach ($record in $Records) {
        if ($record.toolUseResult -and $record.toolUseResult.filenames) {
            foreach ($fileName in @($record.toolUseResult.filenames)) {
                if (-not [string]::IsNullOrWhiteSpace($fileName)) {
                    $paths.Add([string]$fileName)
                }
            }
        }

        if ($record.message -and $record.message.content -is [System.Array]) {
            foreach ($item in $record.message.content) {
                if ($item.type -eq "tool_use" -and $item.input) {
                    foreach ($candidate in @($item.input.file_path, $item.input.path)) {
                        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
                            $paths.Add([string]$candidate)
                        }
                    }
                }
            }
        }
    }

    return @($paths | Where-Object { $_ } | Select-Object -Unique)
}

function Read-ProjectRegistry {
    param([string]$RegistryPath)

    if (-not (Test-Path -LiteralPath $RegistryPath)) {
        return [pscustomobject]@{
            IsActive = $false
            Projects = @()
            Path = $RegistryPath
        }
    }

    $json = Get-Content -Raw -LiteralPath $RegistryPath
    if ([string]::IsNullOrWhiteSpace($json)) {
        return [pscustomobject]@{
            IsActive = $false
            Projects = @()
            Path = $RegistryPath
        }
    }

    $registry = $json | ConvertFrom-Json
    if (-not ($registry.PSObject.Properties.Name -contains "projects")) {
        throw "PROJECT_REGISTRY.json must contain a top-level `projects` array."
    }

    $projects = New-Object System.Collections.Generic.List[object]
    foreach ($project in @($registry.projects)) {
        $canonicalSlug = Normalize-ProjectSlug ([string]$project.canonical_slug)
        if (-not $canonicalSlug) {
            throw "Each project registry entry must define a non-empty `canonical_slug`."
        }

        $displayName = if (-not [string]::IsNullOrWhiteSpace([string]$project.display_name)) {
            [string]$project.display_name
        }
        else {
            $canonicalSlug
        }

        $normalizedRootPaths = New-Object System.Collections.Generic.List[string]
        foreach ($rootPath in @($project.root_paths)) {
            $normalizedRootPath = Normalize-ComparablePath ([string]$rootPath)
            if ($normalizedRootPath) {
                $normalizedRootPaths.Add($normalizedRootPath)
            }
        }

        $normalizedAliases = New-Object System.Collections.Generic.List[string]
        $normalizedAliases.Add($canonicalSlug)
        foreach ($alias in @($project.aliases)) {
            $normalizedAlias = Normalize-ProjectSlug ([string]$alias)
            if ($normalizedAlias -and -not (Is-IgnoredProjectCandidate $normalizedAlias)) {
                $normalizedAliases.Add($normalizedAlias)
            }
        }

        $normalizedTitleKeywords = New-Object System.Collections.Generic.List[string]
        foreach ($keyword in @($project.title_keywords)) {
            if (-not [string]::IsNullOrWhiteSpace([string]$keyword)) {
                $normalizedTitleKeywords.Add(([string]$keyword).Trim().ToLowerInvariant())
            }
        }

        $projects.Add([pscustomobject]@{
            canonical_slug = $canonicalSlug
            display_name = $displayName
            root_paths = @($project.root_paths)
            aliases = @($project.aliases)
            title_keywords = @($project.title_keywords)
            normalized_root_paths = @($normalizedRootPaths | Select-Object -Unique)
            normalized_aliases = @($normalizedAliases | Select-Object -Unique)
            normalized_title_keywords = @($normalizedTitleKeywords | Select-Object -Unique)
        })
    }

    $projectArray = @($projects.ToArray())
    return [pscustomobject]@{
        IsActive = ($projectArray.Count -gt 0)
        Projects = $projectArray
        Path = $RegistryPath
    }
}

function Resolve-LegacyProjectMapping {
    param(
        $Metadata,
        [string[]]$ReferencedPaths
    )

    $pathCandidates = New-Object System.Collections.Generic.List[string]
    foreach ($folder in @($Metadata.userSelectedFolders)) {
        if (-not [string]::IsNullOrWhiteSpace($folder)) {
            $folderPath = ([string]$folder) -replace '[\\/]+$',''
            $folderLeaf = Split-Path -Path $folderPath -Leaf
            $normalizedFolder = Normalize-ProjectSlug $folderLeaf
            if ($normalizedFolder -and -not (Is-IgnoredProjectCandidate $normalizedFolder)) {
                $pathCandidates.Add($normalizedFolder)
            }
        }
    }

    foreach ($path in $ReferencedPaths) {
        $rootName = Get-ProjectRootNameFromPath $path
        $normalized = Normalize-ProjectSlug $rootName
        if ($normalized -and -not (Is-IgnoredProjectCandidate $normalized)) {
            $pathCandidates.Add($normalized)
        }
    }

    $pathCandidates = @($pathCandidates | Select-Object -Unique)
    $title = [string]$Metadata.title
    $titleTokens = Get-MeaningfulTokens $title

    if ($pathCandidates.Count -gt 1) {
        return @{
            ProjectSlug = "_unassigned"
            ShouldUpdateRam = $false
            Reason = "legacy heuristic fallback: conflicting path signals"
            PathCandidates = $pathCandidates
        }
    }

    if ($pathCandidates.Count -eq 1) {
        $candidate = $pathCandidates[0]
        $candidateTokens = Get-MeaningfulTokens $candidate
        $sharedTokens = @($candidateTokens | Where-Object { $titleTokens -contains $_ })
        $reason = if ($sharedTokens.Count -gt 0) { "legacy heuristic fallback: path signals with title confirmation" } else { "legacy heuristic fallback: path signals" }

        return @{
            ProjectSlug = $candidate
            ShouldUpdateRam = $true
            Reason = $reason
            PathCandidates = $pathCandidates
        }
    }

    $titleSlug = Normalize-ProjectSlug $title
    if ($titleSlug -and -not (Is-IgnoredProjectCandidate $titleSlug)) {
        return @{
            ProjectSlug = $titleSlug
            ShouldUpdateRam = $true
            Reason = "legacy heuristic fallback: title inference"
            PathCandidates = @()
        }
    }

    return @{
        ProjectSlug = "_unassigned"
        ShouldUpdateRam = $false
        Reason = "legacy heuristic fallback: no single project could be resolved"
        PathCandidates = @()
    }
}

function Resolve-RegistryProjectMapping {
    param(
        $Metadata,
        [string[]]$ReferencedPaths,
        $ProjectRegistry
    )

    $rootPathCandidates = New-Object System.Collections.Generic.List[string]
    $aliasCandidates = New-Object System.Collections.Generic.List[string]

    foreach ($folder in @($Metadata.userSelectedFolders)) {
        if (-not [string]::IsNullOrWhiteSpace($folder)) {
            $normalizedFolderPath = Normalize-ComparablePath ([string]$folder)
            if ($normalizedFolderPath) {
                $rootPathCandidates.Add($normalizedFolderPath)
            }

            $folderPath = ([string]$folder) -replace '[\\/]+$', ''
            $folderLeaf = Split-Path -Path $folderPath -Leaf
            $normalizedFolderAlias = Normalize-ProjectSlug $folderLeaf
            if ($normalizedFolderAlias -and -not (Is-IgnoredProjectCandidate $normalizedFolderAlias)) {
                $aliasCandidates.Add($normalizedFolderAlias)
            }
        }
    }

    foreach ($path in $ReferencedPaths) {
        $rootPath = Get-ProjectRootPathFromPath $path
        if ($rootPath) {
            $rootPathCandidates.Add($rootPath)
        }

        $rootName = Get-ProjectRootNameFromPath $path
        $normalizedAlias = Normalize-ProjectSlug $rootName
        if ($normalizedAlias -and -not (Is-IgnoredProjectCandidate $normalizedAlias)) {
            $aliasCandidates.Add($normalizedAlias)
        }
    }

    $rootPathCandidates = @($rootPathCandidates | Select-Object -Unique)
    $aliasCandidates = @($aliasCandidates | Select-Object -Unique)

    $rootPathMatches = New-Object System.Collections.Generic.List[object]
    foreach ($project in $ProjectRegistry.Projects) {
        $hasRootPathMatch = @($project.normalized_root_paths | Where-Object { $rootPathCandidates -contains $_ }).Count -gt 0
        if ($hasRootPathMatch) {
            $rootPathMatches.Add($project)
        }
    }

    $rootPathMatches = @($rootPathMatches | Sort-Object canonical_slug -Unique)
    if ($rootPathMatches.Count -gt 1) {
        return @{
            ProjectSlug = "_unassigned"
            ShouldUpdateRam = $false
            Reason = "conflicting registry root_paths"
            PathCandidates = $rootPathCandidates
        }
    }

    if ($rootPathMatches.Count -eq 1) {
        return @{
            ProjectSlug = $rootPathMatches[0].canonical_slug
            ShouldUpdateRam = $true
            Reason = "registry root_paths"
            PathCandidates = $rootPathCandidates
        }
    }

    $aliasMatches = New-Object System.Collections.Generic.List[object]
    foreach ($project in $ProjectRegistry.Projects) {
        $hasAliasMatch = @($project.normalized_aliases | Where-Object { $aliasCandidates -contains $_ }).Count -gt 0
        if ($hasAliasMatch) {
            $aliasMatches.Add($project)
        }
    }

    $aliasMatches = @($aliasMatches | Sort-Object canonical_slug -Unique)
    if ($aliasMatches.Count -gt 1) {
        return @{
            ProjectSlug = "_unassigned"
            ShouldUpdateRam = $false
            Reason = "conflicting registry aliases"
            PathCandidates = $aliasCandidates
        }
    }

    if ($aliasMatches.Count -eq 1) {
        return @{
            ProjectSlug = $aliasMatches[0].canonical_slug
            ShouldUpdateRam = $true
            Reason = "registry aliases"
            PathCandidates = $aliasCandidates
        }
    }

    $titleText = ([string]$Metadata.title).ToLowerInvariant()
    $titleKeywordMatches = New-Object System.Collections.Generic.List[object]
    foreach ($project in $ProjectRegistry.Projects) {
        $matchedKeywords = @(
            $project.normalized_title_keywords |
                Where-Object { $_ -and $titleText.Contains($_) } |
                Select-Object -Unique
        )

        if ($matchedKeywords.Count -ge 2) {
            $titleKeywordMatches.Add([pscustomobject]@{
                Project = $project
                MatchedKeywords = $matchedKeywords
            })
        }
    }

    if ($titleKeywordMatches.Count -eq 1) {
        return @{
            ProjectSlug = $titleKeywordMatches[0].Project.canonical_slug
            ShouldUpdateRam = $true
            Reason = "registry title keywords"
            PathCandidates = @()
        }
    }

    if ($titleKeywordMatches.Count -gt 1) {
        return @{
            ProjectSlug = "_unassigned"
            ShouldUpdateRam = $false
            Reason = "conflicting registry title keywords"
            PathCandidates = @()
        }
    }

    return @{
        ProjectSlug = "_unassigned"
        ShouldUpdateRam = $false
        Reason = "no registry match"
        PathCandidates = @()
    }
}

function Resolve-ProjectMapping {
    param(
        $Metadata,
        [string[]]$ReferencedPaths,
        $ProjectRegistry
    )

    if ($ProjectRegistry -and $ProjectRegistry.IsActive) {
        return Resolve-RegistryProjectMapping -Metadata $Metadata -ReferencedPaths $ReferencedPaths -ProjectRegistry $ProjectRegistry
    }

    return Resolve-LegacyProjectMapping -Metadata $Metadata -ReferencedPaths $ReferencedPaths
}

function Get-NextVersionedPath {
    param(
        [string]$Directory,
        [string]$ProjectSlug,
        [string]$ContentType,
        [datetime]$Date,
        [string]$Extension = "md"
    )

    Ensure-Directory $Directory

    $dateStamp = $Date.ToString("yyyyMMdd")
    $version = 1

    while ($true) {
        $fileName = "{0}_{1}_{2}_v{3}.{4}" -f $ProjectSlug, $ContentType, $dateStamp, $version, $Extension
        $candidate = Join-Path $Directory $fileName
        if (-not (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
        $version += 1
    }
}

function Get-RelativePath {
    param(
        [string]$Root,
        [string]$Target
    )

    $rootUri = [System.Uri]((Resolve-Path -LiteralPath $Root).Path.TrimEnd('\') + '\')
    $targetUri = [System.Uri]((Resolve-Path -LiteralPath $Target).Path)
    $relativeUri = $rootUri.MakeRelativeUri($targetUri).ToString()
    return [System.Uri]::UnescapeDataString($relativeUri).Replace('/', '\')
}

function Read-ManifestEntries {
    param([string]$ManifestPath)

    $entries = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        return $entries
    }

    foreach ($line in Get-Content -LiteralPath $ManifestPath) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        try {
            $entries.Add(($line | ConvertFrom-Json))
        }
        catch {
        }
    }

    return $entries
}

function Append-ManifestEntry {
    param(
        [string]$ManifestPath,
        [hashtable]$Entry
    )

    $json = $Entry | ConvertTo-Json -Compress
    [System.IO.File]::AppendAllText(
        $ManifestPath,
        $json + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Update-ProjectIndex {
    param(
        [string]$ProjectIndexPath,
        [string]$ProjectSlug,
        [string]$Status,
        [string]$LatestRamPath,
        [string]$Summary
    )

    $header = @(
        "| project_name | status | latest_ram_path | current_state_summary |",
        "| --- | --- | --- | --- |"
    )

    if (-not (Test-Path -LiteralPath $ProjectIndexPath)) {
        Write-Utf8File -Path $ProjectIndexPath -Content (($header -join [Environment]::NewLine) + [Environment]::NewLine)
    }

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($line in Get-Content -LiteralPath $ProjectIndexPath) {
        $lines.Add($line)
    }

    if ($lines.Count -lt 2) {
        $lines.Clear()
        foreach ($line in $header) {
            $lines.Add($line)
        }
    }

    $row = "| {0} | {1} | {2} | {3} |" -f $ProjectSlug, $Status, $LatestRamPath, ($Summary -replace '\|', '/')
    $rowUpdated = $false

    for ($i = 2; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\|\s*$ProjectSlug\s*\|") {
            $lines[$i] = $row
            $rowUpdated = $true
            break
        }
    }

    if (-not $rowUpdated) {
        $lines.Add($row)
    }

    Write-Utf8File -Path $ProjectIndexPath -Content (($lines -join [Environment]::NewLine) + [Environment]::NewLine)
}

function Get-ArtifactList {
    param(
        $Metadata,
        [string[]]$ReferencedPaths
    )

    $artifacts = New-Object System.Collections.Generic.List[string]

    foreach ($path in @($Metadata.userApprovedFileAccessPaths)) {
        if (-not [string]::IsNullOrWhiteSpace($path)) {
            $artifacts.Add([string]$path)
        }
    }

    foreach ($path in $ReferencedPaths) {
        if ($path -match '11\.DE DELIVERABLES|11\.OUTPUT_ROUGH|12\.DE RAM|12\.MEMORY_RAM') {
            $artifacts.Add($path)
        }
    }

    return @($artifacts | Select-Object -Unique | Select-Object -First 10)
}

function Get-DurableDecisions {
    param(
        [string]$ProjectSlug,
        [string[]]$Artifacts,
        [bool]$ShouldUpdateRam
    )

    $decisions = New-Object System.Collections.Generic.List[string]
    $decisions.Add("Transcript was mapped to `"$ProjectSlug`" for historical storage.")

    if ($ShouldUpdateRam) {
        $decisions.Add("Project RAM should be refreshed from this capsule because the project mapping was unambiguous.")
    }
    else {
        $decisions.Add("No project RAM update was allowed because the export could not be mapped to a single project.")
    }

    if ($Artifacts.Count -gt 0) {
        $decisions.Add("User-visible artifacts were referenced in the session and should remain discoverable through RAM pointers instead of raw transcript scans.")
    }
    else {
        $decisions.Add("No canonical artifact was confirmed automatically from the export metadata alone.")
    }

    return $decisions
}

function Format-Bullets {
    param([string[]]$Items)

    if (-not $Items -or $Items.Count -eq 0) {
        return "- none"
    }

    return ($Items | ForEach-Object { "- $_" }) -join [Environment]::NewLine
}

function Write-Utf8File {
    param(
        [string]$Path,
        [string]$Content
    )

    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

Ensure-Directory $WorkspaceRoot

$aboutRoot = Join-Path $WorkspaceRoot "00.ABOUT"
$inputRoot = Join-Path $WorkspaceRoot "01.INPUT"
$inputTemplatesRoot = Join-Path $WorkspaceRoot "02.INPUT_TEMPLATES"
$outputFinalRoot = Join-Path $WorkspaceRoot "03.OUTPUT_FINAL"
$outputRoughRoot = Join-Path $WorkspaceRoot "11.OUTPUT_ROUGH"
$ramRoot = Join-Path $WorkspaceRoot "12.MEMORY_RAM"
$hddRoot = Join-Path $WorkspaceRoot "13.MEMORY_HDD"
$lessonsLearnedRoot = Join-Path $WorkspaceRoot "14.LESSONS_LEARNED"
$projectIndexPath = Join-Path $aboutRoot "PROJECT_INDEX.md"
$projectRegistryPath = Join-Path $aboutRoot "PROJECT_REGISTRY.json"
$manifestPath = Join-Path $hddRoot "index\ingest_manifest.jsonl"

foreach ($path in @(
    $aboutRoot,
    $inputRoot,
    $inputTemplatesRoot,
    $outputFinalRoot,
    $outputRoughRoot,
    $ramRoot,
    $lessonsLearnedRoot,
    (Join-Path $hddRoot "inbox"),
    (Join-Path $hddRoot "processing"),
    (Join-Path $hddRoot "raw_transcripts"),
    (Join-Path $hddRoot "capsules"),
    (Join-Path $hddRoot "index"),
    (Join-Path $hddRoot "failed")
)) {
    Ensure-Directory $path
}

Ensure-File $manifestPath

$sourceZipPath = (Resolve-Path -LiteralPath $TranscriptZip).Path
$sourceZipName = Split-Path -Path $sourceZipPath -Leaf
$inboxPath = Join-Path $hddRoot "inbox\$sourceZipName"
$inboxDir = Join-Path $hddRoot "inbox"

if ((Split-Path -Path $sourceZipPath -Parent) -ne $inboxDir) {
    Copy-Item -LiteralPath $sourceZipPath -Destination $inboxPath -Force
}
else {
    $inboxPath = $sourceZipPath
}

$processingPath = Join-Path $hddRoot "processing\$sourceZipName"
if (Test-Path -LiteralPath $processingPath) {
    Remove-Item -LiteralPath $processingPath -Force
}
Move-Item -LiteralPath $inboxPath -Destination $processingPath

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($processingPath)
    try {
        $metadataText = Get-ZipEntryText -Zip $zip -EntryName "metadata.json"
        $metadata = $metadataText | ConvertFrom-Json

        $jsonlEntry = $zip.Entries | Where-Object { $_.FullName -like "*.jsonl" } | Select-Object -First 1
        if (-not $jsonlEntry) {
            throw "The transcript export does not contain a JSONL conversation file."
        }

        $jsonlText = Get-ZipEntryText -Zip $zip -EntryName $jsonlEntry.FullName
        $records = Get-JsonLinesObjects $jsonlText
    }
    finally {
        $zip.Dispose()
    }

    $transcriptId = Get-TranscriptIdentifier -Metadata $metadata -Records $records -FallbackName ([System.IO.Path]::GetFileNameWithoutExtension($sourceZipName))
    $contentHash = Get-FileHashSha256 $processingPath

    $manifestEntries = Read-ManifestEntries $manifestPath
    $existingEntry = $manifestEntries | Where-Object {
        $_.transcript_id -eq $transcriptId -and $_.content_hash -eq $contentHash
    } | Select-Object -First 1

    if ($existingEntry) {
        Remove-Item -LiteralPath $processingPath -Force
        Write-Output "Skipped duplicate transcript: $transcriptId"
        Write-Output "Existing raw path: $($existingEntry.raw_path)"
        exit 0
    }

    $referencedPaths = Get-ReferencedPaths -Metadata $metadata -Records $records
    $projectRegistry = Read-ProjectRegistry $projectRegistryPath
    $mapping = Resolve-ProjectMapping -Metadata $metadata -ReferencedPaths $referencedPaths -ProjectRegistry $projectRegistry
    $projectSlug = $mapping.ProjectSlug
    $sessionDate = if ($metadata.createdAt) {
        [DateTimeOffset]::FromUnixTimeMilliseconds([int64]$metadata.createdAt).DateTime
    }
    else {
        (Get-Item -LiteralPath $processingPath).LastWriteTime
    }

    $rawProjectDir = Join-Path $hddRoot "raw_transcripts\$projectSlug"
    $capsuleProjectDir = Join-Path $hddRoot "capsules\$projectSlug"
    Ensure-Directory $rawProjectDir
    Ensure-Directory $capsuleProjectDir

    $rawPath = Join-Path $rawProjectDir $sourceZipName
    if (Test-Path -LiteralPath $rawPath) {
        Remove-Item -LiteralPath $rawPath -Force
    }
    Move-Item -LiteralPath $processingPath -Destination $rawPath

    $objective = if (-not [string]::IsNullOrWhiteSpace($metadata.title)) {
        [string]$metadata.title
    }
    else {
        Get-FirstUserPrompt $records
    }

    if (-not $objective) {
        $objective = "Transcript export imported for historical context setup."
    }

    $artifacts = Get-ArtifactList -Metadata $metadata -ReferencedPaths $referencedPaths
    $durableDecisions = Get-DurableDecisions -ProjectSlug $projectSlug -Artifacts $artifacts -ShouldUpdateRam $mapping.ShouldUpdateRam
    $recommendedRamImpact = if ($mapping.ShouldUpdateRam) {
        "Create or refresh RAM for `"$projectSlug`" and use this capsule only as a historical pointer."
    }
    else {
        "Keep this export in HDD only. Do not update project RAM until a single project is confirmed."
    }

    $capsulePath = Get-NextVersionedPath -Directory $capsuleProjectDir -ProjectSlug $projectSlug -ContentType "capsule" -Date $sessionDate
    $relativeRawPath = Get-RelativePath -Root $WorkspaceRoot -Target $rawPath

    $capsuleContent = @"
# ${projectSlug} Capsule

- project: $projectSlug
- session_date: $($sessionDate.ToString("yyyy-MM-dd"))
- transcript_id: $transcriptId
- source: $relativeRawPath
- mapping_reason: $($mapping.Reason)

## objective
$objective

## durable_decisions
$(Format-Bullets $durableDecisions)

## artifacts_referenced_or_produced
$(Format-Bullets $artifacts)

## recommended_ram_impact
$recommendedRamImpact
"@
    Write-Utf8File -Path $capsulePath -Content ($capsuleContent.Trim() + [Environment]::NewLine)

    $ramPath = $null
    if ($mapping.ShouldUpdateRam) {
        $ramPath = Get-NextVersionedPath -Directory $ramRoot -ProjectSlug $projectSlug -ContentType "ram" -Date $sessionDate
        $relativeCapsulePath = Get-RelativePath -Root $WorkspaceRoot -Target $capsulePath
        $relativeRawPath = Get-RelativePath -Root $WorkspaceRoot -Target $rawPath
        $ramSummary = "Transcript history ingested into HDD and summarized for startup."
        $canonicalArtifacts = if ($artifacts.Count -gt 0) { $artifacts } else { @("No canonical artifact confirmed automatically.") }
        $topReferencedPaths = @()
        if ($referencedPaths.Count -gt 0) {
            $take = [Math]::Min($referencedPaths.Count, 5)
            $topReferencedPaths = $referencedPaths[0..($take - 1)]
        }
        else {
            $topReferencedPaths = @("No referenced file paths were captured automatically.")
        }

        $ramContent = @"
# ${projectSlug} RAM

## Current Objective
$objective

## Current Status
- Historical transcript context was ingested on $($sessionDate.ToString("yyyy-MM-dd")).
- Use this RAM as the default startup checkpoint for the project.

## Decisions Already Made
$(Format-Bullets $durableDecisions)

## Unresolved Questions or Blockers
- Review the linked capsule or raw transcript only if this RAM is insufficient for the next task.

## Key Inputs or Referenced Files
- Transcript title: $objective
$(Format-Bullets $topReferencedPaths)

## Output Locations
- Capsule: $relativeCapsulePath
- Raw transcript: $relativeRawPath

## Canonical Artifacts
$(Format-Bullets $canonicalArtifacts)

## HDD Pointers
- Capsule: $relativeCapsulePath
- Raw transcript: $relativeRawPath

## Recommended Next Action
- Start future Claude Cowork sessions from this RAM and follow the HDD pointers only when deeper history is required.
"@
        Write-Utf8File -Path $ramPath -Content ($ramContent.Trim() + [Environment]::NewLine)

        $relativeRamPath = Get-RelativePath -Root $WorkspaceRoot -Target $ramPath
        Update-ProjectIndex -ProjectIndexPath $projectIndexPath -ProjectSlug $projectSlug -Status "active" -LatestRamPath $relativeRamPath -Summary $ramSummary
    }

    $manifestEntry = @{
        transcript_id = $transcriptId
        content_hash = $contentHash
        project_slug = $projectSlug
        raw_path = Get-RelativePath -Root $WorkspaceRoot -Target $rawPath
        capsule_path = Get-RelativePath -Root $WorkspaceRoot -Target $capsulePath
        ram_path = if ($ramPath) { Get-RelativePath -Root $WorkspaceRoot -Target $ramPath } else { $null }
        ingested_at = (Get-Date).ToString("s")
    }
    Append-ManifestEntry -ManifestPath $manifestPath -Entry $manifestEntry

    Write-Output "Ingest complete."
    Write-Output "Transcript ID: $transcriptId"
    Write-Output "Project slug: $projectSlug"
    Write-Output "Mapping reason: $($mapping.Reason)"
    Write-Output "Raw transcript: $($manifestEntry.raw_path)"
    Write-Output "Capsule: $($manifestEntry.capsule_path)"
    if ($ramPath) {
        Write-Output "RAM: $($manifestEntry.ram_path)"
    }
    else {
        Write-Output "RAM: not updated"
    }
}
catch {
    $failedPath = Join-Path $hddRoot ("failed\" + (Split-Path -Path $processingPath -Leaf))
    if (Test-Path -LiteralPath $processingPath) {
        if (Test-Path -LiteralPath $failedPath) {
            Remove-Item -LiteralPath $failedPath -Force
        }
        Move-Item -LiteralPath $processingPath -Destination $failedPath
    }
    throw
}
