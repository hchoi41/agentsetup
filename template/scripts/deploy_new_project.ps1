<#
.SYNOPSIS
    Scaffolds a new Claude Cowork project workspace using the v5 folder layout.

.DESCRIPTION
    Creates the full v5 folder hierarchy (including WIKI and HDD ingest
    subfolders), copies governance documents from the clean template, and
    initialises empty control files (PROJECT_INDEX.md, PROJECT_REGISTRY.json,
    ingest_manifest.jsonl, log.md).

    Run once per new project.

.PARAMETER TargetPath
    The root directory of the new project workspace. Created if it does not exist.

.PARAMETER TemplatePath
    Path to the clean install_v2 template folder. Defaults to the sibling
    install_v2_clean folder relative to this script's location.

.PARAMETER ProjectName
    Optional display name for the project. If provided, a starter entry is added
    to PROJECT_REGISTRY.json and PROJECT_INDEX.md.

.PARAMETER ProjectSlug
    Optional canonical slug for the project. Derived from ProjectName if omitted.

.EXAMPLE
    .\deploy_new_project.ps1 -TargetPath (Join-Path $env:OneDrive "000_thinkfirst\20260401_new_project")
    .\deploy_new_project.ps1 -TargetPath "D:\Projects\tax_analysis" -ProjectName "Tax Analysis Q2"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,

    [string]$TemplatePath,

    [string]$ProjectName,

    [string]$ProjectSlug
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Get-Slug {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    $slug = $Text.ToLowerInvariant()
    $slug = $slug -replace '[^a-z0-9]+', '_'
    $slug = $slug -replace '^_+|_+$', ''
    if ([string]::IsNullOrWhiteSpace($slug)) { return $null }
    return $slug
}

function Normalize-ProjectSlug {
    param([string]$Text)
    $slug = Get-Slug $Text
    if (-not $slug) { return $null }
    $normalized = $slug -replace '^\d{6,8}_?', ''
    if ([string]::IsNullOrWhiteSpace($normalized)) { return $slug }
    return $normalized
}

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        $null = New-Item -ItemType Directory -Path $Path -Force
    }
}

function Write-Utf8File {
    param([string]$Path, [string]$Content)
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

# ---------------------------------------------------------------------------
# Resolve template path
# ---------------------------------------------------------------------------

if (-not $TemplatePath) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $TemplatePath = Join-Path (Split-Path -Parent $scriptDir) ""
    # Default: assume script lives inside install_v2_clean/scripts/
    # so template root is one level up
    $TemplatePath = Split-Path -Parent $scriptDir
}

if (-not (Test-Path -LiteralPath $TemplatePath)) {
    throw "Template path not found: $TemplatePath"
}

$templateAbout = Join-Path $TemplatePath "00.ABOUT"
if (-not (Test-Path -LiteralPath $templateAbout)) {
    throw "Template 00.ABOUT folder not found at: $templateAbout"
}

# ---------------------------------------------------------------------------
# Create folder hierarchy
# ---------------------------------------------------------------------------

Write-Output "Creating workspace at: $TargetPath"

$folders = @(
    "00.ABOUT",
    "01.INPUT",
    "02.INPUT_TEMPLATES",
    "03.OUTPUT_FINAL",
    "11.OUTPUT_ROUGH",
    "12.MEMORY_RAM",
    "13.MEMORY_HDD\inbox",
    "13.MEMORY_HDD\processing",
    "13.MEMORY_HDD\raw_transcripts",
    "13.MEMORY_HDD\capsules",
    "13.MEMORY_HDD\index",
    "13.MEMORY_HDD\failed",
    "14.LESSONS_LEARNED",
    "15.WIKI\processed\MOC",
    "scripts"
)

foreach ($folder in $folders) {
    Ensure-Directory (Join-Path $TargetPath $folder)
}

# ---------------------------------------------------------------------------
# Copy governance documents
# ---------------------------------------------------------------------------

$governanceDocs = @(
    "orchestration_protocol.md",
    "claude_cowork_hdd_ingestion_protocol.md"
)

foreach ($doc in $governanceDocs) {
    $source = Join-Path $templateAbout $doc
    $dest = Join-Path $TargetPath "00.ABOUT\$doc"
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $dest -Force
        Write-Output "  Copied: 00.ABOUT\$doc"
    }
    else {
        Write-Warning "Template governance doc not found: $doc"
    }
}

# ---------------------------------------------------------------------------
# Copy ingest script
# ---------------------------------------------------------------------------

$templateScript = Join-Path $TemplatePath "scripts\ingest_claude_cowork_transcript.ps1"
$destScript = Join-Path $TargetPath "scripts\ingest_claude_cowork_transcript.ps1"
if (Test-Path -LiteralPath $templateScript) {
    Copy-Item -LiteralPath $templateScript -Destination $destScript -Force
    Write-Output "  Copied: scripts\ingest_claude_cowork_transcript.ps1"
}

# ---------------------------------------------------------------------------
# Initialise control files
# ---------------------------------------------------------------------------

$aboutDir = Join-Path $TargetPath "00.ABOUT"
$projectIndexPath = Join-Path $aboutDir "PROJECT_INDEX.md"
$projectRegistryPath = Join-Path $aboutDir "PROJECT_REGISTRY.json"
$manifestPath = Join-Path $TargetPath "13.MEMORY_HDD\index\ingest_manifest.jsonl"

# PROJECT_INDEX.md
if (-not (Test-Path -LiteralPath $projectIndexPath)) {
    $indexHeader = @(
        "| project_name | status | latest_ram_path | current_state_summary |",
        "| --- | --- | --- | --- |"
    ) -join [Environment]::NewLine

    if ($ProjectName -or $ProjectSlug) {
        $slug = if ($ProjectSlug) { $ProjectSlug } else { Normalize-ProjectSlug $ProjectName }
        $indexHeader += [Environment]::NewLine + "| $slug | active | | New project workspace created. |"
    }

    Write-Utf8File -Path $projectIndexPath -Content ($indexHeader + [Environment]::NewLine)
    Write-Output "  Created: 00.ABOUT\PROJECT_INDEX.md"
}

# PROJECT_REGISTRY.json
if (-not (Test-Path -LiteralPath $projectRegistryPath)) {
    if ($ProjectName -or $ProjectSlug) {
        $slug = if ($ProjectSlug) { $ProjectSlug } else { Normalize-ProjectSlug $ProjectName }
        $displayName = if ($ProjectName) { $ProjectName } else { $slug }
        $normalizedTarget = $TargetPath -replace '/', '\'
        $normalizedTarget = $normalizedTarget -replace '\\+$', ''

        $registry = @{
            version = 1
            projects = @(
                @{
                    canonical_slug = $slug
                    display_name = $displayName
                    root_paths = @($normalizedTarget)
                    aliases = @($slug)
                    title_keywords = @()
                }
            )
        }

        $json = $registry | ConvertTo-Json -Depth 4
        Write-Utf8File -Path $projectRegistryPath -Content ($json + [Environment]::NewLine)
    }
    else {
        $emptyRegistry = @'
{
  "version": 1,
  "projects": []
}
'@
        Write-Utf8File -Path $projectRegistryPath -Content ($emptyRegistry + [Environment]::NewLine)
    }
    Write-Output "  Created: 00.ABOUT\PROJECT_REGISTRY.json"
}

# ingest_manifest.jsonl
if (-not (Test-Path -LiteralPath $manifestPath)) {
    Write-Utf8File -Path $manifestPath -Content ""
    Write-Output "  Created: 13.MEMORY_HDD\index\ingest_manifest.jsonl"
}

# WIKI log.md
$wikiLogPath = Join-Path $TargetPath "15.WIKI\log.md"
if (-not (Test-Path -LiteralPath $wikiLogPath)) {
    Write-Utf8File -Path $wikiLogPath -Content ""
    Write-Output "  Created: 15.WIKI\log.md"
}

# ---------------------------------------------------------------------------
# Create OUTPUT conjugate pair (if project specified)
# ---------------------------------------------------------------------------

if ($ProjectName -or $ProjectSlug) {
    $slug = if ($ProjectSlug) { $ProjectSlug } else { Normalize-ProjectSlug $ProjectName }
    $datePrefix = (Get-Date -Format "yyyyMMdd")
    $subfolderName = "${datePrefix}_${slug}"

    $roughSubfolder = Join-Path $TargetPath "11.OUTPUT_ROUGH\$subfolderName"
    $finalSubfolder = Join-Path $TargetPath "03.OUTPUT_FINAL\$subfolderName"

    Ensure-Directory $roughSubfolder
    Ensure-Directory $finalSubfolder

    Write-Output "  Created conjugate pair:"
    Write-Output "    11.OUTPUT_ROUGH\$subfolderName"
    Write-Output "    03.OUTPUT_FINAL\$subfolderName"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

Write-Output ""
Write-Output "Deployment complete."
Write-Output "  Workspace: $TargetPath"
Write-Output "  Layout: v5 (CLAUDE.md v5.2)"
if ($ProjectName -or $ProjectSlug) {
    $slug = if ($ProjectSlug) { $ProjectSlug } else { Normalize-ProjectSlug $ProjectName }
    Write-Output "  Project: $slug"
}
Write-Output ""
Write-Output "Next steps:"
Write-Output "  1. Add title_keywords to PROJECT_REGISTRY.json for transcript auto-mapping."
Write-Output "  2. Ensure your global CLAUDE.md is updated to v5.2."
Write-Output "  3. Start a Cowork session pointing to this folder."
