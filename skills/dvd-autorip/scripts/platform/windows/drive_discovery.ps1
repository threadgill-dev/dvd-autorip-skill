<#
.SYNOPSIS
    Stage 3 -- enumerate optical drives via MakeMKV's own robot-mode disc scan.

.DESCRIPTION
    Never hardcode disc:0/disc:1 -- MakeMKV's index assignment isn't guaranteed stable
    across drive add/remove or app restarts. This re-derives the drive -> index mapping
    fresh every run via `makemkvcon -r info disc:9999`, which enumerates every drive
    regardless of whether media is loaded.

    DRV: line format (confirmed against MakeMKV's own robot-mode output and community
    parsers, e.g. https://pkg.go.dev/github.com/curt-hash/mkvbot/pkg/makemkv):
        DRV:index,visible,enabled,flags,"drive name","disc volume label","device/drive letter"
    Example: DRV:0,2,999,1,"DVD-ROM TEAC DVD-ROM DV28SV R.0C","WRATH_OF_THE_TITANS","D:"

    The "visible"/"enabled"/"flags" numeric fields are not a reliable presence signal in
    practice (real captures show inconsistent values across MakeMKV versions) -- what
    this pipeline has actually validated across 196+ real discs is simpler: a real drive
    always has a non-empty drive-name field, and a disc is loaded iff the volume-label
    field is non-empty. Use that, not the numeric flags.

.PARAMETER MakeMkvPath
    Full path to makemkvcon(64).exe. If omitted, relies on PATH.

.OUTPUTS
    JSON array to stdout: [{ "index": 0, "driveName": "...", "driveLetter": "D:",
    "volumeLabel": "...", "mediaLoaded": true }, ...]
#>
param(
    [string]$MakeMkvPath = "makemkvcon"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command $MakeMkvPath -ErrorAction SilentlyContinue)) {
    Write-Error (
        "Cannot find makemkvcon at '$MakeMkvPath'. Note that being 'found' by " +
        "check_dependencies.py does not guarantee this -- that check also looks in a " +
        "few common install locations beyond PATH. Fix: pass the real path via " +
        "-MakeMkvPath, ideally the one already persisted in config.local.json's " +
        "makemkv.path from Stage 1."
    )
    exit 1
}

$raw = & $MakeMkvPath -r info disc:9999 2>&1
if ($LASTEXITCODE -ne 0 -and -not ($raw -match "DRV:")) {
    Write-Error "makemkvcon did not return drive info (exit $LASTEXITCODE). Is MakeMKV installed and on PATH, or is config.local.json's makemkv.path set correctly?"
    exit 1
}

# Quoted-string-aware: fields 5-7 are quoted and may contain commas, so split on the
# regex below rather than a naive -split ','.
$pattern = '^DRV:(\d+),(-?\d+),(-?\d+),(-?\d+),"([^"]*)","([^"]*)","([^"]*)"'

$drives = @()
foreach ($line in $raw) {
    $m = [regex]::Match($line, $pattern)
    if (-not $m.Success) { continue }

    $driveName   = $m.Groups[5].Value
    $volumeLabel = $m.Groups[6].Value
    $driveLetter = $m.Groups[7].Value

    if ([string]::IsNullOrWhiteSpace($driveName)) { continue }  # not a real drive entry

    $drives += [pscustomobject]@{
        index        = [int]$m.Groups[1].Value
        driveName    = $driveName
        driveLetter  = $driveLetter
        volumeLabel  = $volumeLabel
        mediaLoaded  = -not [string]::IsNullOrWhiteSpace($volumeLabel)
    }
}

if ($drives.Count -eq 0) {
    "[]"
} elseif ($drives.Count -eq 1) {
    "[" + ($drives | ConvertTo-Json) + "]"
} else {
    $drives | ConvertTo-Json
}
