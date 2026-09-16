# Parallel ripping (multiple drives)

Validated live across 12+ dual-drive runs. Written to scale to however many drives are
actually connected — nothing hardcodes a drive count, so adding drives is a matter of
buying them, not redesigning anything.

## What actually parallelizes

**Only rip (Stage 4) and eject (Stage 13) run concurrently across drives.**
Classification, identification, placement, and correction (Stages 6-9) stay exactly as
documented in SKILL.md — Claude-in-the-loop, one disc worked through at a time. Running
N rips concurrently just means N discs finish ripping around the same time and queue up
for that same sequential identification work; it doesn't remove the identification
bottleneck, which is the actual long-term throughput constraint, not the rip itself.

**Practical shape**: work through whichever disc's rip finishes first while other
drives are still ripping, rather than waiting for the whole batch to complete before
starting any identification — this overlaps the rip's dead time with identification
work instead of serializing the two.

## Setup, once per batch

1. **Suppress USB notifications** (`toggle_usb_notifications.ps1 -Disable`) — two
   drives spinning up ~20s apart reliably trips a Windows toast even on a
   properly-powered hub; this pipeline's own CPU-time-flat hang detection already
   covers the real failure mode, so the toast is understood noise, not signal.
2. **Discover drives fresh** (`drive_discovery.ps1`) — never assume a stable
   index-to-drive mapping between runs.
3. **Check MakeMKV's "Ask for single drive mode" setting** (Preferences → IO) is
   disabled, so two `makemkvcon` instances can access two different drives without one
   blocking or prompting the other. Confirm this once per machine/MakeMKV install, not
   necessarily every run.

## Launching

Reuse the detached-process pattern (`launch_rip.ps1`'s `Start-Process ... -PassThru`
on Windows; `launch_rip.sh`'s `nohup ... & disown` on Linux/Mac) — it already fully
separates the rip from the calling process, which is what makes parallel launches
safe. Still exactly one process/PID per drive even when a drive's Play-All/concat
title was detected pre-rip and `-TitleIds`/`--title-ids` is used instead of `all` —
see `identification-technique.md` — so nothing about the launch/monitor pattern below
changes per-drive just because one drive is ripping a specific title list instead of
everything. Launch one process per drive, back-to-back, **passing every drive in the batch
the same `-ProcessListPath`/`--process-list-path` (`<staging.path>\rip_processes.json`)**
— each call safely merges its own `{driveIndex, processId, stagingPath, logPath}` into
that shared file itself (a real JSON round-trip inside the script, not something you
assemble by hand from each call's stdout — PowerShell's own cmdlets on Windows, a
python3 round trip on Linux/Mac). Once every drive is launched, poll that whole file
together (`monitor_rips.ps1 -ProcessListJson <same path>` / `monitor_rips.sh
--process-list-json <same path>`) rather than waiting on one drive at a time. A drive
finishing (or failing) early doesn't block polling the others. Delete the process-list
file once monitoring finishes.

**Never launch more than one process against the same drive index at once** — this is
just as wrong in parallel as it would be single-threaded.

## Memory pressure — the real scaling risk

Treat "N drives" as "test 2, watch it, then decide whether to add more," not linear
scaling. This machine class runs close to its memory ceiling with even a **single**
rip in flight; two simultaneous processes compete for the same already-tight headroom,
plus whatever else is running (browser tabs, other sessions).

**Before the first parallel run**: check free system RAM the same way as any
single-rip run. **If a hang occurs on one of several concurrent rips**, check whether
the others are still healthy before assuming they're all affected — a hang on one
drive is not automatically evidence the whole batch is compromised. Diagnosis that's
worked in practice: compare the stuck process's CPU time against the others over a
2+ minute window — a genuinely hung process sits flat at ~0s CPU growth, while healthy
ones keep accumulating normally. `monitor_rips.ps1` implements exactly this check.

**Standing rule, from the user directly: if a parallel run ever causes one or more
processes to crash, revert to sequential (one `makemkvcon` process active at a time,
regardless of drive) for the rest of that run.** Don't try to diagnose your way back
into parallelism mid-batch — finish the batch safely, revisit parallelism next time.

## Staging path collisions

Two collision cases matter once more than one disc can be in flight at a time:
- **Generic/duplicate volume labels** (e.g. `DVD_VIDEO`), or two drives happening to
  hold the same disc — a real, repeated pattern, not hypothetical.
- **Retrying part of a batch after a partial failure** — a re-ripped disc could collide
  with its own prior staging folder from the same session.

Fix: suffix every staging path with the drive index at creation time, not just the
label — `<label>__drive<N>`. `launch_rip.ps1` does this automatically.

## Ordering — the completion sound and eject are the true last steps of the whole batch

Hold the per-drive eject calls (and the completion sound that precedes them, Stage
13) until the entire batch — every disc — is placed, verified against Jellyfin, has
had its staging working files cleaned up, and has had USB notifications re-enabled
(Stage 12). Ejecting early (right after rip, before identification/placement, or
before that disc's own cleanup/notification-restore has run) is technically harmless
to the data but wrong from the user's perspective: the completion sound and the
physical tray-eject are the real-world signal the user is watching/listening for to
know the run — including its own housekeeping — is genuinely finished and it's safe
to load the next discs. Sending that signal while cleanup or notification state is
still pending is a false "done." A real run got this backwards (drives were ejected
before staging was cleaned up and before notifications were turned back on) — the
fix is to keep notification-restore and cleanup strictly inside Stage 12, before
Stage 13 ever starts, not bundled alongside eject as if they were part of the same
final signal.

Apply eject verification (`eject.ps1`'s `Get-Volume` check) per drive, independently —
one drive's eject succeeding or failing has no bearing on another's.
