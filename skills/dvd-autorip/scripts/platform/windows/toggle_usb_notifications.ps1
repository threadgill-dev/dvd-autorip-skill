<#
.SYNOPSIS
    Stage 2 -- suppress (or restore) Windows' "USB Power Surge on Hub Port" toast for
    the duration of a parallel rip batch.

.DESCRIPTION
    Two optical drives spinning up ~20s apart reliably trips this notification, even on
    a hub with its own power supply -- confirmed benign in practice (drives stayed
    healthy, rips completed uninterrupted). This pipeline's own hang detection
    (monitor_rips.ps1) already covers the real failure mode, so the toast is just noise
    once that's understood -- but it should still be restored at the end of every run so
    it's never accidentally left off between sessions.

    This only suppresses the popup -- it does not touch Windows' actual USB port-power
    enforcement, so a genuinely disconnected/power-cut drive is still independently
    detectable via Get-Volume / CDROM device status.

.PARAMETER Disable
    Suppress the notification (call at batch start).

.PARAMETER Enable
    Restore the notification (call at batch end -- pair with the final eject, not a
    separate pass, so it's never skipped on an early exit).
#>
param(
    [switch]$Disable,
    [switch]$Enable
)

if ($Disable -eq $Enable) {
    Write-Error "Specify exactly one of -Disable or -Enable."
    exit 1
}

$regPath = "HKCU:\Software\Microsoft\Shell\USB"
$value = if ($Disable) { 0 } else { 1 }

New-ItemProperty -Path $regPath -Name "NotifyOnUsbErrors" -Value $value -PropertyType DWord -Force | Out-Null

if ($Disable) {
    Write-Output "USB power-surge notifications suppressed for this session."
} else {
    Write-Output "USB power-surge notifications restored."
}
