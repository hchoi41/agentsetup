#Requires -Version 5.1
<#
.SYNOPSIS
Return a project/operation folder-name proposal as JSON. Never changes files.
.DESCRIPTION
ToOperation: YYYYMMDD_semantic_name -> semantic_name_YYYYMMDD_v0.01.
An explicit matching PreviousOperationFolderName preserves its version; add
IncrementVersion only for an authorized operation revision.
NextVersion increments canonical operation versions by one integer hundredth.
ToProject returns YYYYMMDD_semantic_name and the operation lineage to retain in
the recipe/RAM. A legacy date-first project name is returned unchanged.
The creation date always comes from the supplied name, never today's date.
#>
[CmdletBinding()]
param(
    [AllowEmptyString()][string]$Action,
    [AllowEmptyString()][string]$FolderName,
    [AllowEmptyString()][string]$PreviousOperationFolderName,
    [switch]$IncrementVersion
)
$ErrorActionPreference = 'Stop'

function Assert-LeafName([string]$Name) {
    if ([string]::IsNullOrEmpty($Name)) { throw 'A nonempty folder leaf name is required.' }
    if ($Name.Length -gt 255) { throw 'A Windows folder component cannot exceed 255 UTF-16 characters.' }
    if ($Name -ne $Name.Trim() -or $Name.StartsWith('.') -or $Name.EndsWith('.')) { throw 'Leading/trailing whitespace or periods are not allowed.' }
    if ($Name -match '[<>:"/\\|?*]' -or $Name -eq '.' -or $Name -eq '..') { throw 'Supply a leaf name, without paths, traversal, or unsafe Windows characters.' }
    foreach ($character in $Name.ToCharArray()) { if ([char]::IsControl($character)) { throw 'Control characters are not allowed.' } }
    # Windows reserves these basenames even when a file extension follows them.
    $baseName = (($Name -split '\.', 2)[0]).TrimEnd()
    if ($baseName -match '^(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$|CLOCK\$)$') { throw 'Reserved Windows device basenames are not allowed.' }
}

function Assert-CreationDate([string]$DateToken) {
    $parsed = [datetime]::MinValue
    if ($DateToken -cnotmatch '^[0-9]{8}$' -or -not [datetime]::TryParseExact($DateToken, 'yyyyMMdd', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsed)) { throw 'Creation date must be a valid calendar date in YYYYMMDD form.' }
}

function Assert-SemanticName([string]$Name) {
    Assert-LeafName $Name
    # Refuse an already operation-shaped suffix, including a malformed version.
    # Silently nesting it in a new operation would lose the intended lineage.
    if ($Name -cmatch '(?:^|_)[0-9]{8}_[vV][^_]*$') { throw 'Semantic name ends in an operation-shaped date/version suffix; supply the original project name and explicit previous operation lineage.' }
}

function Read-ProjectName([string]$Name) {
    Assert-LeafName $Name
    $match = [regex]::Match($Name, '^(?<date>[0-9]{8})_(?<semantic>.+)$')
    if (-not $match.Success) { throw 'Project names must be YYYYMMDD_semantic_name.' }
    $date = $match.Groups['date'].Value
    $semantic = $match.Groups['semantic'].Value
    Assert-CreationDate $date
    Assert-SemanticName $semantic
    return @{ creation_date = $date; semantic_name = $semantic }
}

function Read-OperationName([string]$Name) {
    Assert-LeafName $Name
    $match = [regex]::Match($Name, '^(?<semantic>.+)_(?<date>[0-9]{8})_v(?<major>0|[1-9][0-9]*)\.(?<minor>[0-9]{2})$')
    if (-not $match.Success) { throw 'Operation names must be semantic_name_YYYYMMDD_vMAJOR.MINOR, with a canonical major and exactly two minor digits.' }
    $date = $match.Groups['date'].Value
    $semantic = $match.Groups['semantic'].Value
    $major = $match.Groups['major'].Value
    $minor = $match.Groups['minor'].Value
    Assert-CreationDate $date
    Assert-SemanticName $semantic
    if ($major -ceq '0' -and $minor -ceq '00') { throw 'Operation versions start at v0.01; v0.00 is invalid.' }
    return @{ creation_date = $date; semantic_name = $semantic; version = ($major + '.' + $minor); major = $major; minor = $minor }
}

function Add-VersionHundredth($Operation) {
    # BigInteger prevents both floating-point rounding and fixed-width overflow.
    Add-Type -AssemblyName System.Numerics -ErrorAction Stop
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $hundredths = [Numerics.BigInteger]::Parse(($Operation.major + $Operation.minor), $culture) + [Numerics.BigInteger]::One
    $major = [Numerics.BigInteger]::Divide($hundredths, [Numerics.BigInteger]100)
    $minor = [Numerics.BigInteger]::Remainder($hundredths, [Numerics.BigInteger]100)
    return ($major.ToString($culture) + '.' + $minor.ToString('D2', $culture))
}

try {
    if ($Action -notin @('ToOperation', 'NextVersion', 'ToProject')) { throw 'Action must be ToOperation, NextVersion, or ToProject.' }
    if (-not $PSBoundParameters.ContainsKey('FolderName')) { throw 'FolderName is required.' }
    $hasPrevious = $PSBoundParameters.ContainsKey('PreviousOperationFolderName')
    if ($hasPrevious -and $Action -ne 'ToOperation') { throw 'PreviousOperationFolderName is allowed only with ToOperation.' }
    if ($hasPrevious -and [string]::IsNullOrEmpty($PreviousOperationFolderName)) { throw 'An explicitly supplied previous operation name cannot be empty.' }
    if ($PSBoundParameters.ContainsKey('IncrementVersion') -and ($Action -ne 'ToOperation' -or -not $hasPrevious)) { throw 'IncrementVersion requires ToOperation with an explicit previous operation name.' }

    $legacy = $false
    $version = $null
    $previous = $null
    $lineage = $null
    switch ($Action) {
        'ToOperation' {
            $Action = 'ToOperation'
            $parsed = Read-ProjectName $FolderName
            $version = '0.01'
            if ($hasPrevious) {
                $prior = Read-OperationName $PreviousOperationFolderName
                if (-not [string]::Equals($parsed.semantic_name, $prior.semantic_name, [StringComparison]::Ordinal) -or $parsed.creation_date -cne $prior.creation_date) { throw 'Previous operation must have the same exact semantic name and original creation date.' }
                $version = if ($IncrementVersion) { Add-VersionHundredth $prior } else { $prior.version }
                $previous = $PreviousOperationFolderName
                $lineage = @{ operation_folder_name = $previous; operation_version = $prior.version }
            }
            $target = $parsed.semantic_name + '_' + $parsed.creation_date + '_v' + $version
        }
        'NextVersion' {
            $Action = 'NextVersion'
            $parsed = Read-OperationName $FolderName
            $version = Add-VersionHundredth $parsed
            $previous = $FolderName
            $lineage = @{ operation_folder_name = $previous; operation_version = $parsed.version }
            $target = $parsed.semantic_name + '_' + $parsed.creation_date + '_v' + $version
        }
        'ToProject' {
            $Action = 'ToProject'
            # A date/version suffix identifies the operation grammar first.
            if ($FolderName -cmatch '_[0-9]{8}_[vV][^_]*$') {
                $parsed = Read-OperationName $FolderName
                $version = $parsed.version
                $previous = $FolderName
                $lineage = @{ operation_folder_name = $previous; operation_version = $version }
                $target = $parsed.creation_date + '_' + $parsed.semantic_name
            } else {
                $parsed = Read-ProjectName $FolderName
                $target = $FolderName
                $legacy = $true
            }
        }
    }
    Assert-LeafName $target
    [ordered]@{
        ok = $true
        action = $Action
        source = $FolderName
        target = $target
        semantic_name = $parsed.semantic_name
        creation_date = $parsed.creation_date
        version = $version
        previous_operation_folder = $previous
        lineage = $lineage
        legacy = $legacy
    } | ConvertTo-Json -Depth 5 -Compress
    exit 0
} catch {
    [ordered]@{
        ok = $false
        action = $Action
        source = $FolderName
        target = $null
        creation_date = $null
        version = $null
        previous_operation_folder = $PreviousOperationFolderName
        error = $_.Exception.Message
    } | ConvertTo-Json -Depth 5 -Compress
    exit 1
}
