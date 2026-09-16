<#
.SYNOPSIS
    Stage 13 -- eject a drive's tray, with verification (don't trust a silent
    "success" from the eject call itself).

.DESCRIPTION
    `InvokeVerb("Eject")` can report success without actually opening the tray -- seen
    once immediately following a staging-cleanup step, with the leading (unconfirmed)
    theory being a lingering file handle from the cleanup still holding the drive.
    Always verify via a follow-up Get-Volume check (empty label / Size 0 confirms the
    tray actually opened) rather than trusting the COM call alone. Retry once if the
    first attempt didn't visibly take.

    Only call this at the very end of a real pipeline run for a given disc -- never as
    an isolated test against a drive that might have a disc someone is waiting to have
    processed. This script does not know whether it's safe to eject; the caller (the
    skill's Stage 13 checklist) is responsible for only invoking it once identification
    and placement are fully done for that disc.

.PARAMETER DriveLetter
    e.g. "D:", "D", or "D:\" -- all normalized internally, so any of these work. (An
    earlier version required the colon literally and gave a raw, unhelpful
    "cannot call a method on a null-valued expression" error if you passed a bare
    letter -- normalizing here instead of just documenting the requirement avoids that
    whole class of mistake.)
#>
param(
    [Parameter(Mandatory)][string]$DriveLetter
)

$ErrorActionPreference = "Stop"

# Accept "D", "D:", or "D:\" and normalize to "D:" -- the one form ParseName() actually
# needs. Without this, a bare letter makes ParseName() silently return $null, and
# calling .InvokeVerb() on that null throws a confusing, unrelated-looking error.
$DriveLetter = $DriveLetter.Trim().TrimEnd('\')
if ($DriveLetter -notmatch ':$') { $DriveLetter = "${DriveLetter}:" }

function Test-DriveEjected([string]$letter) {
    $vol = Get-Volume -DriveLetter $letter.TrimEnd(':') -ErrorAction SilentlyContinue
    return (-not $vol) -or ([string]::IsNullOrWhiteSpace($vol.FileSystemLabel) -and $vol.Size -eq 0)
}

function Invoke-Eject([string]$letter) {
    $sh = New-Object -ComObject Shell.Application
    $driveItem = $sh.NameSpace(17).ParseName($letter)
    if (-not $driveItem) {
        Write-Error "Shell.Application could not find drive '$letter' in the Drives namespace -- does this drive actually exist on this machine?"
        exit 1
    }
    $driveItem.InvokeVerb("Eject")
}

Invoke-Eject $DriveLetter
Start-Sleep -Seconds 3

if (Test-DriveEjected $DriveLetter) {
    Write-Output "Ejected $DriveLetter (verified via Get-Volume)."
    exit 0
}

Write-Output "First eject attempt on $DriveLetter did not visibly take -- retrying once."
Invoke-Eject $DriveLetter
Start-Sleep -Seconds 3

if (Test-DriveEjected $DriveLetter) {
    Write-Output "Ejected $DriveLetter on retry (verified via Get-Volume)."
    exit 0
}

Write-Error "Could not verify $DriveLetter ejected after two attempts. Check the drive manually before telling the user it's safe to load the next disc."
exit 1
