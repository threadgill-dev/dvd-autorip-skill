<#
.SYNOPSIS
    Stage 4 -- launch a MakeMKV rip against one drive, detached, and return its PID.

.DESCRIPTION
    Launches `makemkvcon ... mkv disc:N all <staging>` via Start-Process with
    -RedirectStandardOutput/-RedirectStandardError/-NoNewWindow/-PassThru. This fully
    detaches the rip from the calling process, which is what makes it safe to launch
    several of these in parallel (one per drive) without them blocking each other or
    the caller.

    Never launch more than one instance against the same drive index at once -- one
    process per drive is the whole point; contention within a single drive is exactly
    as wrong single-threaded as it is in parallel.

    Staging path is suffixed with the drive index (`<label>__drive<N>`), not just the
    volume label -- two drives can hold identically- or generically-labeled discs
    (e.g. "DVD_VIDEO"), and a retried disc within the same session could otherwise
    collide with its own prior staging folder.

.PARAMETER DriveIndex
    The MakeMKV disc index (from drive_discovery.ps1's "index" field), NOT a Windows
    drive letter.

.PARAMETER VolumeLabel
    The disc's volume label (from drive_discovery.ps1), used only to name the staging
    folder. Sanitized for filesystem-illegal characters.

.PARAMETER StagingRoot
    Root staging directory (from config.local.json's staging.path).

.PARAMETER MinLengthSeconds
    Titles shorter than this are skipped by MakeMKV itself -- filters out menu loops
    and short bonus fragments. Default 120s, matching this pipeline's validated
    bonus-content threshold.

.PARAMETER TitleIds
    Optional. Comma-separated specific title ids to rip (e.g. "0,1,2,3") instead of
    `all`. Use this when a pre-rip scan has confirmed a Play-All/concat title on this
    disc (see identification-technique.md) -- pass every real title id except the
    concat one, rather than ripping `all` and discarding the concat afterward (wastes
    real rip time on a title that's pure duplicate content). MakeMKV's CLI has no
    multi-title-select syntax of its own, so when this is given, the actual ripping is
    delegated to rip_titles_sequential.ps1 (launched as one detached process, same as
    the `all` case) which calls makemkvcon once per title id, sequentially, within
    that single process -- monitor_rips.ps1 still only tracks one PID per drive
    either way. Omit entirely to rip `all` (unchanged default behavior).

.PARAMETER MakeMkvPath
    Full path to makemkvcon(64).exe. If omitted, defaults to "makemkvcon" and relies on
    it being resolvable on PATH -- but `Start-Process -FilePath` does NOT search the
    same locations `check_dependencies.py`/`Get-Command` do (e.g. a Program Files
    install dir that isn't actually on the PATH env var). If MakeMKV isn't confirmed
    literally on PATH, always pass the real path explicitly -- ideally the same one
    `check_dependencies.py` already discovered and Stage 1 persisted into
    config.local.json's `makemkv.path`, rather than leaving this at the bare default.

.PARAMETER ProcessListPath
    Optional. If given, this script's result is safely merged into a shared JSON array
    file at this path (creating it if it doesn't exist yet, appending if it does) using
    PowerShell's own JSON round-trip -- not string concatenation -- so launching
    several drives in a batch and building up the list `monitor_rips.ps1` expects never
    requires hand-constructing JSON yourself. Use the same path for every drive in one
    batch, then pass it straight to `monitor_rips.ps1 -ProcessListJson`. Delete this
    file once monitoring finishes -- it's a working artifact for one batch, not meant
    to persist between runs.

.OUTPUTS
    JSON object to stdout: { "driveIndex", "processId", "stagingPath", "logPath" }
    (this is always printed, regardless of whether -ProcessListPath was also given)
#>
param(
    [Parameter(Mandatory)][int]$DriveIndex,
    [Parameter(Mandatory)][string]$VolumeLabel,
    [Parameter(Mandatory)][string]$StagingRoot,
    [int]$MinLengthSeconds = 120,
    [string]$MakeMkvPath = "makemkvcon",
    [string]$ProcessListPath,
    [string]$TitleIds
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command $MakeMkvPath -ErrorAction SilentlyContinue)) {
    Write-Error (
        "Cannot find makemkvcon at '$MakeMkvPath'. This is not a rip failure -- MakeMKV " +
        "was never launched. Fix: pass the real path via -MakeMkvPath (check " +
        "config.local.json's makemkv.path, or re-run check_dependencies.py to " +
        "rediscover it -- PATH lookup alone often misses a Program Files install)."
    )
    exit 1
}

$safeLabel = ($VolumeLabel -replace '[\\/:*?"<>|]', '_').Trim()
if ([string]::IsNullOrWhiteSpace($safeLabel)) { $safeLabel = "disc" }

$stagingPath = Join-Path $StagingRoot "${safeLabel}__drive${DriveIndex}"
New-Item -ItemType Directory -Force -Path $stagingPath | Out-Null

$logPath = Join-Path $stagingPath "rip.log"
$errLogPath = Join-Path $stagingPath "rip.err.log"

if ($TitleIds) {
    # No native multi-title-select in MakeMKV's CLI -- delegate to the sequential
    # helper (still one detached process/PID, see its own docstring) rather than
    # ripping `all` and discarding the concat title afterward.
    $helperScript = Join-Path $PSScriptRoot "rip_titles_sequential.ps1"
    $currentHostExe = (Get-Process -Id $PID).Path
    $processArgs = @(
        "-NoProfile"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        "`"$helperScript`""
        "-MakeMkvPath"
        "`"$MakeMkvPath`""
        "-DriveIndex"
        "$DriveIndex"
        "-TitleIds"
        "`"$TitleIds`""
        "-Dest"
        "`"$stagingPath`""
        "-MinLengthSeconds"
        "$MinLengthSeconds"
    )
    $proc = Start-Process -FilePath $currentHostExe -ArgumentList $processArgs `
        -RedirectStandardOutput $logPath -RedirectStandardError $errLogPath `
        -NoNewWindow -PassThru
} else {
    $processArgs = @(
        "-r"
        "--decrypt"
        "--directio=true"
        "--minlength=$MinLengthSeconds"
        "mkv"
        "disc:$DriveIndex"
        "all"
        "`"$stagingPath`""
    )
    $proc = Start-Process -FilePath $MakeMkvPath -ArgumentList $processArgs `
        -RedirectStandardOutput $logPath -RedirectStandardError $errLogPath `
        -NoNewWindow -PassThru
}

$result = [pscustomobject]@{
    driveIndex  = $DriveIndex
    processId   = $proc.Id
    stagingPath = $stagingPath
    logPath     = $logPath
}

if ($ProcessListPath) {
    $existing = @()
    if (Test-Path -LiteralPath $ProcessListPath) {
        $parsed = Get-Content -Raw -LiteralPath $ProcessListPath | ConvertFrom-Json
        # ConvertFrom-Json on a single-element JSON array yields a bare object, not a
        # 1-element array -- @() around it normalizes both cases to a real array.
        $existing = @($parsed)
    }
    $merged = $existing + $result
    if ($merged.Count -eq 1) {
        "[" + ($merged | ConvertTo-Json) + "]" | Set-Content -LiteralPath $ProcessListPath -Encoding utf8
    } else {
        $merged | ConvertTo-Json | Set-Content -LiteralPath $ProcessListPath -Encoding utf8
    }
}

$result | ConvertTo-Json
