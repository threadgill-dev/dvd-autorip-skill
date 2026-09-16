<#
.SYNOPSIS
    Poll one or more in-flight rip processes (from launch_rip.ps1), detect hangs, and
    report each one's real outcome once it exits.

.DESCRIPTION
    Two gotchas this script exists specifically to avoid, both found the hard way
    across real parallel-rip runs:

    1. Never name a PID-tracking loop variable `$pid` -- it's PowerShell's reserved,
       read-only current-session PID. A `foreach ($pid in ...)` loop throws
       VariableNotWritable on every iteration and silently does nothing. This script
       uses `$procId` throughout.

    2. `Out-File -Append` without `-Encoding` writes UTF-16LE, which a lot of external
       tooling (grep, some editors) can't read reliably. This script always writes its
       log with `-Encoding utf8`.

    Hang detection is CPU-time-flat polling, not a wall-clock timeout: a genuinely hung
    process sits at ~0s CPU growth over a multi-minute window, while a healthy one
    keeps accumulating CPU normally -- a cleaner signal than working-set memory size,
    which can look similar for a hung process and an early-stage healthy one.

    Don't trust a single "it printed a success-looking message" code as proof a rip
    succeeded -- some status codes appear from an earlier phase (e.g. the disc TOC
    scan) well before a real failure later in the same log. This script greps each
    finished process's log for the actual per-title save outcome codes instead.

.PARAMETER ProcessListJson
    Path to a JSON file containing an array of objects with at least
    { driveIndex, processId, logPath } -- the output shape of launch_rip.ps1, collected
    into one file/array by the caller for however many drives are running.

.PARAMETER PollIntervalSeconds
    How often to check each process. Default 60.

.PARAMETER HangThresholdMinutes
    If a still-running process's CPU time hasn't increased across this many minutes of
    polling, flag it as hung (but do not kill it -- surface it for a decision). Default 3.

.PARAMETER MaxTotalMinutes
    Hard ceiling on the whole polling loop, independent of hang detection above. Hang
    detection flags a suspicious process but keeps polling it forever -- there was no
    upper bound on the loop itself, so a process that never exits (hung or not) meant
    this script, and whatever was waiting on it, blocked indefinitely. A real caller
    waited 12+ hours on exactly this shape of thing (a different script, but the same
    unbounded-wait pattern) before anyone noticed. Once this many minutes have elapsed
    in total, stop polling and return "still_running_timeout" for whatever's still
    pending instead of continuing to wait -- doesn't kill anything, just stops blocking
    the caller so a decision can actually get made. Default 360 (6h) -- generous
    relative to this pipeline's normal ~40min run, but bounded rather than unbounded.

.PARAMETER LogPath
    Where to write the plain-text progress log (UTF-8). Defaults next to the process
    list file.

.OUTPUTS
    JSON array to stdout, one entry per process. Returned once ALL processes have
    exited, OR once MaxTotalMinutes has elapsed, whichever comes first:
    { driveIndex, processId, exitCode, elapsedMinutes, hungAtAnyPoint, outcome, detail }
    outcome is one of: "success", "failed", "unknown", "still_running_timeout".
#>
param(
    [Parameter(Mandatory)][string]$ProcessListJson,
    [int]$PollIntervalSeconds = 60,
    [int]$HangThresholdMinutes = 3,
    [int]$MaxTotalMinutes = 360,
    [string]$LogPath
)

$ErrorActionPreference = "Stop"

$entries = Get-Content -Raw -LiteralPath $ProcessListJson | ConvertFrom-Json
if (-not $LogPath) {
    $LogPath = Join-Path (Split-Path -Parent $ProcessListJson) "monitor.log"
}

function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Output $line
    $line | Out-File -Append -LiteralPath $LogPath -Encoding utf8
}

# Track per-process state: last-seen CPU time and how many consecutive polls it hasn't grown.
$state = @{}
foreach ($entry in $entries) {
    $state[$entry.processId] = @{
        lastCpu       = [timespan]::Zero
        flatPolls     = 0
        hungAtAnyPoint = $false
        startTime     = Get-Date
    }
}

$pending = [System.Collections.Generic.List[object]]::new()
$entries | ForEach-Object { $pending.Add($_) }

$results = @()
$hangPollThreshold = [math]::Ceiling(($HangThresholdMinutes * 60) / [math]::Max($PollIntervalSeconds, 1))
$scriptStart = Get-Date

Write-Log "Monitoring $($pending.Count) rip process(es): $(($pending | ForEach-Object { 'drive' + $_.driveIndex + '=PID' + $_.processId }) -join ', ')"

while ($pending.Count -gt 0) {
    if (((Get-Date) - $scriptStart).TotalMinutes -ge $MaxTotalMinutes) {
        Write-Log "WARNING: MaxTotalMinutes ($MaxTotalMinutes) reached with $($pending.Count) process(es) still running -- stopping polling without killing anything. Investigate manually; this is a real problem to look at, not something to keep waiting on."
        foreach ($entry in $pending) {
            $elapsedMin = [math]::Round(((Get-Date) - $state[$entry.processId].startTime).TotalMinutes, 1)
            $results += [pscustomobject]@{
                driveIndex      = $entry.driveIndex
                processId       = $entry.processId
                exitCode        = $null
                elapsedMinutes  = $elapsedMin
                hungAtAnyPoint  = $state[$entry.processId].hungAtAnyPoint
                outcome         = "still_running_timeout"
                detail          = "MaxTotalMinutes ($MaxTotalMinutes) reached -- process was not killed, still running (or genuinely hung) as of this report"
            }
        }
        $pending = [System.Collections.Generic.List[object]]::new()
        break
    }
    Start-Sleep -Seconds $PollIntervalSeconds
    $stillPending = [System.Collections.Generic.List[object]]::new()

    foreach ($entry in $pending) {
        $procId = $entry.processId
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue

        if (-not $proc) {
            # Process exited -- determine real outcome from its log, not just the fact it exited.
            $elapsedMin = [math]::Round(((Get-Date) - $state[$procId].startTime).TotalMinutes, 1)
            $logText = if (Test-Path $entry.logPath) { Get-Content -Raw -LiteralPath $entry.logPath } else { "" }

            # Count occurrences rather than checking only the first match -- a log can
            # now contain multiple titles' worth of completion/failure codes back to
            # back (rip_titles_sequential.ps1's -TitleIds mode calls makemkvcon once
            # per title into the same log), and a single -match only ever finds the
            # first occurrence regardless of what happened for later titles. Any
            # failure code anywhere in the combined log still wins over any success
            # code -- conservative on purpose: a partial failure should surface as
            # "failed", not get masked by an earlier or later title's success.
            $outcome = "unknown"
            $detail = "no recognizable MSG:500x completion code found in log"
            $failureCount = ([regex]::Matches($logText, 'MSG:5004,\d+,\d+,"0 titles saved')).Count `
                + ([regex]::Matches($logText, 'MSG:5003')).Count
            $successMatches = [regex]::Matches($logText, 'MSG:5036|MSG:5037')
            $copyCompleteMatches = [regex]::Matches($logText, 'Copy complete\.\s*(\d+) titles saved')

            if ($failureCount -gt 0) {
                $outcome = "failed"
                $detail = "$failureCount failure code occurrence(s) found (MSG:5003/5004) -- see log for which title(s)"
            } elseif ($successMatches.Count -gt 0 -or $copyCompleteMatches.Count -gt 0) {
                $outcome = "success"
                if ($copyCompleteMatches.Count -gt 0) {
                    $totalTitles = ($copyCompleteMatches | ForEach-Object { [int]$_.Groups[1].Value } | Measure-Object -Sum).Sum
                    $detail = "$totalTitles titles saved (across $($copyCompleteMatches.Count) completion message(s) in this log)"
                } else {
                    $detail = "$($successMatches.Count) copy-complete message(s) found"
                }
            }

            Write-Log "drive$($entry.driveIndex) (PID $procId) exited after ${elapsedMin}m -- outcome: $outcome ($detail)"
            $results += [pscustomobject]@{
                driveIndex      = $entry.driveIndex
                processId       = $procId
                exitCode        = $null
                elapsedMinutes  = $elapsedMin
                hungAtAnyPoint  = $state[$procId].hungAtAnyPoint
                outcome         = $outcome
                detail          = $detail
            }
            continue
        }

        $cpu = $proc.TotalProcessorTime
        if ($cpu -eq $state[$procId].lastCpu) {
            $state[$procId].flatPolls++
            if ($state[$procId].flatPolls -ge $hangPollThreshold -and -not $state[$procId].hungAtAnyPoint) {
                $state[$procId].hungAtAnyPoint = $true
                Write-Log "WARNING: drive$($entry.driveIndex) (PID $procId) has not accumulated CPU time in ~${HangThresholdMinutes}m -- possible hang. Check the other drive(s) independently before assuming they're also affected; kill only this PID if confirmed hung."
            }
        } else {
            $state[$procId].flatPolls = 0
        }
        $state[$procId].lastCpu = $cpu

        $stillPending.Add($entry)
    }

    $pending = $stillPending
}

if ($results | Where-Object { $_.outcome -eq "still_running_timeout" }) {
    Write-Log "Stopped polling early -- MaxTotalMinutes reached, not all processes exited."
} else {
    Write-Log "All processes exited."
}
if ($results.Count -eq 0) {
    "[]"
} elseif ($results.Count -eq 1) {
    "[" + ($results | ConvertTo-Json) + "]"
} else {
    $results | ConvertTo-Json
}
