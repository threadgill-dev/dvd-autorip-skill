<#
.SYNOPSIS
    Internal helper for launch_rip.ps1 -- rips a specific list of titles sequentially,
    one makemkvcon invocation per title, within a single process/PID. Not meant to be
    invoked directly from SKILL.md.

.DESCRIPTION
    MakeMKV's CLI has no syntax for selecting multiple specific titles in one `mkv`
    call -- confirmed via https://forum.makemkv.com/forum/viewtopic.php?t=17435 (a
    longstanding, still-open feature request), the title argument is either a single
    id or the literal `all`. There is no `0,1,2` comma-list form. launch_rip.ps1 calls
    this helper (itself launched as ONE detached Start-Process, exactly like the
    direct-makemkvcon "all" case) when the caller wants to rip a specific subset of
    titles instead of everything -- e.g. every real episode except a detected
    Play-All/concat title (see identification-technique.md's concat-detection
    section). Looping here, inside one process, keeps monitor_rips.ps1's
    one-PID-per-drive model completely unchanged; the alternative (one OS process per
    title) would need N separate process-list entries per drive instead of one.

    Each title's makemkvcon invocation is synchronous (the `&` call operator waits for
    it to exit before the loop continues) -- titles rip one at a time here, not in
    parallel with each other. A single title failing doesn't stop the rest: `&`
    against a native executable doesn't throw a terminating PowerShell error on a
    non-zero exit code, so the loop naturally continues to the next title regardless.
    monitor_rips.ps1's log classification already treats any MSG:5003/5004 anywhere in
    the combined log as an overall failure, so a partial failure here is still
    surfaced accurately, not silently lost in the "successful" titles' output.

.PARAMETER MakeMkvPath
    Full path (or bare name, if genuinely on PATH) to makemkvcon(64).exe.

.PARAMETER DriveIndex
    The MakeMKV disc index.

.PARAMETER TitleIds
    Comma-separated title ids to rip, e.g. "0,1,2,3". Order is preserved (rips in the
    order given, not necessarily numeric order, though callers should normally pass
    them in numeric order for a readable log).

.PARAMETER Dest
    Destination directory -- same for every title. MakeMKV names each output file from
    its own title/track info, so multiple titles ripped into the same staging folder
    don't collide with each other.

.PARAMETER MinLengthSeconds
    Same meaning as launch_rip.ps1's own parameter, passed through unchanged to every
    title's invocation.
#>
param(
    [Parameter(Mandatory)][string]$MakeMkvPath,
    [Parameter(Mandatory)][int]$DriveIndex,
    [Parameter(Mandatory)][string]$TitleIds,
    [Parameter(Mandatory)][string]$Dest,
    [int]$MinLengthSeconds = 120
)

$ids = $TitleIds -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }

if ($ids.Count -eq 0) {
    Write-Error "TitleIds resolved to an empty list -- nothing to rip. Check the -TitleIds value passed in ('$TitleIds')."
    exit 1
}

foreach ($id in $ids) {
    Write-Output "=== Ripping title $id (disc:$DriveIndex) ==="
    & $MakeMkvPath -r --decrypt --directio=true "--minlength=$MinLengthSeconds" mkv "disc:$DriveIndex" $id "$Dest"
}
