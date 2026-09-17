# Gotchas catalog

Everything here was found the hard way across real discs. Grouped by area; see
`identification-technique.md` and `parallel-ripping.md` for the gotchas specific to
those topics (kept there rather than duplicated here).

## A live edit to this repo silently didn't reach a running session — twice, for two different reasons

A real session ran a `media_server.type: "none"` batch and reported success, but a
separate long-running/resumed session's Stage 1 had been marked "done" for the
whole session back before that feature existed, and independently, the
*persistently-installed marketplace plugin* was running on an 11:39am cache
snapshot with zero mentions of `media_server` at all — confirmed by diffing the
cache copy against the live repo file directly. Both were real, but neither
explained the specific run in question; jumping from "the cache is stale" to "that's
why this run behaved oddly" without checking which one actually launched it was a
real mistake, corrected only because the run's own closing message was checked
against the theory rather than the other way around.

Root cause, once actually traced: **`claude plugin install`/`update` makes a real,
version-numbered filesystem copy of the plugin directory — even for a `directory`-
type marketplace source pointing at this exact live repo.** Confirmed by diffing a
cached `SKILL.md` against the live one and finding genuine content differences, and
independently confirmed via `claude plugin update` reporting "already at latest"
against a stale cache purely because `plugin.json`'s version string hadn't changed —
content differences alone don't trigger a refresh. **A running session only loads
plugin content once, at process start — not on `/clear`, not on each skill
invocation.** So a repo edit needs three things before it reaches a session using
the *installed* plugin: a version bump in `plugin.json`, `claude plugin update`, and
a full process restart (not `/clear`). None of the three alone is sufficient, and
skipping any one silently leaves the old content in place with no error.

**A second, more serious consequence of the same raw-copy behavior**: the copy
included `config.local.json` (containing a real API key), `staging/monitor.log`,
and `.gitignore` — everything in the directory, gitignored or not, `.git/` itself
being the sole exclusion. Since `config.local.json` lived inside the plugin's own
tree at the time, it inherited the exact same staleness problem as code, plus a
worse one: a marketplace source that isn't a local directory (GitHub, the normal
case for anyone who isn't developing this repo) never contains a real user's
config in the first place, so there'd be no way for a version update to carry an
existing user's config forward into the new version's freshly-copied folder at
all — a plugin update could plausibly wipe a user's Jellyfin URL/key/library paths
with no warning. **Fixed by moving `config.local.json` to a fixed path outside the
plugin tree entirely** (`~/.claude/dvd-autorip-skill/config.local.json`, see
`config-schema.md`), with a one-time migration from the old location so existing
users don't lose their setup, and by teaching `check_directory_scoping.py` to add
that fixed path to `additionalDirectories` automatically, since it's now
unconditionally outside whatever directory `blockReadsOutsideWorkingDirectories`
would otherwise scope reads to.

**General lesson**: for a local-directory-sourced plugin, "I edited the source" and
"the running session sees the edit" are two separate facts, and confirming one
never proves the other — verify which artifact (cache vs. live source, this
process's loaded content vs. current disk content) a specific observed behavior
actually came from before building an explanation on top of it.

## A dependency check that should finish in under a second waited 12 hours instead

Stage 1 backgrounded `check_dependencies.py` and then waited on it indefinitely — a
real run sat there for 12+ hours before anyone noticed, on a script whose own worst
case (every external tool check timing out) is a few tens of seconds.

The root cause was a real Windows `subprocess` gap, not anything wrong with the
script's logic: `_run()` called `subprocess.run(..., timeout=10)` for each external
tool it shells out to (`makemkvcon`, `powershell`, `tesseract`), which looks safe —
but on Windows, a launched process (e.g. `powershell.exe`) can spawn a grandchild
that inherits the stdout/stderr pipe handles. `timeout` kills the immediate PID, not
the tree, so if a grandchild is still holding those handles open, the pipe read
blocks past the timeout waiting for an EOF that never comes. The 10-second budget
never actually protected anything in that case.

Two independent fixes, so a similar gap somewhere else can't reproduce the same
12-hour wait: (1) `_run()` now kills the whole process tree (`taskkill /F /T`) on a
timeout rather than just the immediate PID, which is the actual fix; (2) the whole
scan additionally runs on a daemon thread with a hard 90-second wall-clock ceiling,
so even an unforeseen future hang can't block a caller indefinitely — it reports
`timed_out: true` and stops rather than hanging forever. Also fixed the process-level
symptom directly: `SKILL.md`'s Stage 1 now says to run this script in the
foreground, not backgrounded — it's fast enough by design that there's never a real
reason to background it and wait.

`monitor_rips.ps1`/`.sh` had the same class of gap for a different reason: hang
detection (CPU-time-flat polling) flags a suspicious rip process but was designed to
keep polling it forever regardless — there was no upper bound on the polling loop
itself. Both now take a `-MaxTotalMinutes`/`--max-total-minutes` ceiling (default
360) and return `"still_running_timeout"` for whatever's still pending once it's
reached, instead of polling indefinitely. Nothing gets killed either way — the point
is only that the wait itself can't become unbounded.

## A confident "duplicate encode" conclusion during identification permanently deleted a real, distinct episode

Two titles on a a TV series season disc had matching segment counts,
near-identical duration, and — independently — both had their dialogue attributed to
the same episode by web search. That combination read as a duplicate encode of the
same episode, so one title was discarded as redundant. It wasn't redundant: the
disc's real episode range (checked only afterward) showed the disc should have
produced 4 distinct episodes, not 3, and the deleted title was the genuine missing
one. The file is gone for good — `staging.path` on a network share has no Recycle
Bin, and this pipeline's own delete doesn't route through one even on a local disk.

The actual weakness: two independent web searches landing on the same episode isn't
strong evidence the *content* is the same — generic dialogue can plausibly match
more than one real episode, and nothing here diffed the two titles against each
other directly. This also was not, and never has been, gated by any
`config.local.json` setting — `bonus_content.handling` governs bonus/extra content
only, and there is no config path that authorizes deleting a suspected duplicate
main-content encode.

Fixed by adding two things to `identification-technique.md`: a pre-identification
web-searched disc→episode-count checklist for TV discs (catches a count/number
mismatch before finalizing anything), and a hard rule that a suspected duplicate
encode is always classified as needs-review — never auto-discarded — so `SKILL.md`
Stage 11 puts the decision in front of the user before anything irreversible
happens. See `identification-technique.md`'s "Never permanently delete a suspected
duplicate encode without explicit confirmation" for the full procedure.

## A rip that takes hours instead of minutes may have nothing to do with this pipeline — measure `staging.path`'s real throughput before assuming disc/CPU/memory

If `staging.path` lives on a network-mapped drive (common when the library/NAS setup
this skill targets keeps staging on the same volume as the media library), this
pipeline's actual rip speed is only as fast as that network path allows — MakeMKV's
own rip is normally a plain stream-copy, limited by disc read speed, so a rip taking
many times longer than usual is a strong signal to check the network path *first*,
not disc damage, CPU, or memory (all of which have their own, separate entries in
this file already).

A real investigation measured a rip's own per-title throughput independently against
a raw file-write test to the same staging path and got consistent, converging
numbers both times (~1-2 MB/s where several-times-that should be trivial) — a clean
confirmation that the bottleneck was the network path, not the pipeline. **What
wasn't clean was diagnosing the actual root cause underneath that**, and it's worth
recording the mistake, not just the eventual fix:

- A network-adapter check showed a badly degraded link at the time (a Wi-Fi adapter
  negotiating far below its normal capability) — a plausible, measurable culprit,
  fixed by switching to a wired connection.
- **The fix didn't actually fix it.** Real throughput after switching to a good
  wired link was unchanged — still slow, with the same class of network error on
  both reads and writes. This should have been the signal to keep digging rather
  than have already declared the link speed the root cause.
- Disconnecting and reconnecting the network mount (not just the physical link)
  produced a dramatic improvement — tens of times faster, both directions.
- **But this "fix" wasn't independently verified either**: the underlying wireless
  link's own negotiated rate had *also* changed substantially between the slow
  measurement and the fast one, for reasons unrelated to the remount, and this only
  came to light because it was mentioned directly — not because the test design
  caught it. Two variables moved between the "before" and "after" measurement, so
  which one (or whether both) actually mattered is genuinely unresolved.

**General lesson**: when a fix appears to work, confirm that it's the *only* thing
that changed before crediting it — a live network link's quality can fluctuate on
its own, independent of anything you just did, and a before/after comparison with
more than one variable in flight isn't evidence for either variable specifically.
For this class of problem specifically: check real read/write throughput to
`staging.path` directly (a plain timed file write is enough) rather than inferring
it from a link-speed reading alone, and re-measure after *each* change individually
rather than after several changes at once.

## Staging was never actually being cleaned up — two weeks of accumulated debris, discovered only by asking

No stage anywhere in this pipeline ever deleted a disc's staging working files once
that disc was fully placed and verified. A real installation ran from 2026-08-28 to
2026-09-11 (~40 discs across several TV seasons and movies) without a single cleanup
happening — confirmed by directly inspecting the staging directory: roughly 40
disc-named folders contained nothing but `rip.log`/`rip.err.log` (zero video files
remaining, meaning every one of them had already been successfully identified and
placed), a 155MB `evidence/` directory of OCR cue screenshots and anchor frames from
one specific day (2026-08-29/30) sat there completely unused since, and several
working files (`evidence_processes.json`, `monitor_evidence.log`) were leftovers from
the mechanized-evidence-gathering system that was itself abandoned and deleted from
the codebase on 2026-08-30 — meaning some of this debris outlived the very
subsystem that produced it by two weeks.

**Also found in the same pass: two genuinely different situations that looked similar
from a directory listing alone and needed separate handling, not a blanket
"clean everything" pass.**
- One disc (`SAMPLEDISC_1__drive0/B1_t00.mkv`, 7.3GB) turned out to be a real,
  successfully-ripped movie that had simply never been run through identification and
  placement — confirmed not loaded into Jellyfin (checked both `GET
  Items?SearchTerm=...` and the raw `movies_path` folder directly) before concluding
  anything about it. This one needed *action*, not deletion.
- Another (`OCEANS_THIRTEEN_VTS01_COPY/`, 3.7GB of raw `VIDEO_TS/` VOB fragments) was
  leftover debris from the exact disc-damage salvage attempt already documented
  elsewhere in this file, in "A failed raw VOB copy is a different, more severe
  damage class than MakeMKV-fails-but-copy-succeeds" — already
  concluded not economically salvageable, so this was safe to delete, but *only*
  because that conclusion was already on record; a directory of raw VOB files sitting
  in staging isn't inherently safe to assume dead without checking what it actually
  is first.

**Fix**: Stage 13 (Eject) now includes an explicit, mandatory cleanup step, per disc,
right before that disc is ejected — deleting its `rip.log`-only working folder and
any Stage 7 evidence subfolders for titles that came from it, since nothing has any
further use once that disc's own Stage 9-12/Stage 10-11 resolution has actually
finished. **General lesson**: "delete X once it's no longer needed" needs an actual
trigger point written into the pipeline somewhere — knowing a category of file has no
reuse value (already correctly documented for Stage 7 evidence specifically, in
"Between batches" and elsewhere) is not the same as anything ever actually deleting
it. Two weeks of silent accumulation is what "correct in theory, never wired to an
action" looks like in practice.

## Verify a resource claim before stating it as a root cause

**Never tell the user "this is slow because of X resource constraint" without an
actual verified number backing it — this is easily checkable, so check it, don't
infer it from process-alive/progress observations alone.** A real run diagnosed a
severe slowdown as "CPU contention from 8 concurrent processes" and stated it as a
confirmed finding — but the diagnostic command that would have actually measured CPU
load (`Get-CimInstance Win32_Processor | Select-Object LoadPercentage`) returned no
data at all, and the conclusion was stated anyway without noticing the check had
silently failed. The user's own directly-observed system stats afterward (~30% CPU,
~50% RAM) directly contradicted the claim — nowhere near contended.

**`Win32_Processor.LoadPercentage` is not a reliable way to check CPU load** — it can
return empty/null depending on the system, and even when populated returns one value
*per logical processor*, not a single aggregate figure, so `Select-Object` against it
can silently show nothing useful. Use one of these instead, and confirm you actually
got a number before drawing any conclusion from it:
- Windows: `Get-Counter '\Processor(_Total)\% Processor Time'` — a single reliable
  aggregate percentage. `(Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples.CookedValue`
  gives just the number.
- Linux/Mac: `top -bn1 | grep "Cpu(s)"` or read `/proc/loadavg` (Linux) for a
  quick load-average proxy; there's no single universal one-liner across distros the
  way there is on Windows, so pick whichever is actually available and confirm it
  returned real output before trusting it.

If you can't get a reliable number for the resource you suspect, say so plainly
("progress is slow, cause unconfirmed — could not get a reliable CPU/memory/network
reading") rather than asserting an unverified cause with false confidence. An
unconfirmed "it's probably fine, just contention" is exactly the kind of claim that
led directly to a 6.5+ hour run nobody caught in time — see "Mechanized
evidence-gathering, tried and abandoned" below for the fuller incident and what
changed as a result.

## Disc menu content is a real, underused identification signal

DVD menus routinely render show name / season number / episode titles as literal
on-screen text in the background loop — real authored metadata, not inferred from
episode content, and faster/more reliable to read directly than identifying a season
from episode content alone.

- **IFO files contain no usable text** (confirmed via raw byte extraction — pure binary
  structure, a fixed format signature, nothing more). The menu *video* is the only
  place this metadata lives.
- Grab a couple of frames directly from the menu video with ffmpeg, reading straight
  off the mounted drive letter (no rip needed): try `VIDEO_TS\VTS_01_0.VOB` first, then
  `VIDEO_TS\VIDEO_TS.VOB` (the root/VMG-domain menu) if the first is empty/tiny —
  whichever has real size (generally tens of MB) is where the actual menu content lives.
- **A DVD menu VOB is several independently-encoded MPEG segments concatenated
  back-to-back** (root menu, episode index, bonus, setup, ...), each restarting its own
  sequence header and PTS clock near zero. A naive linear ffmpeg decode across one of
  these seams produces decode corruption and never reaches the later segments. Working
  method: dump per-packet PTS values for the video stream
  (`ffprobe -select_streams v:0 -show_entries packet=pts_time,flags -of csv=p=0 <vob>`),
  find a PTS discontinuity at a keyframe, carve everything from that byte offset to EOF
  into its own file (a plain binary slice), then decode *that* in isolation — it has a
  clean sequence header at offset 0.

## CSS encryption is disc-dependent, not universal

Some discs allow raw unencrypted reads of a `.VOB` straight off the mounted drive
letter; others throw a Windows-level "Copy Protection Error - The read failed because
the sector is encrypted." When this happens, `makemkvcon backup disc:N <dest>`
(full-disc decrypt) gives clean access.

⚠️ **Do not pre-create `<dest>` as a folder first** — MakeMKV has a real bug where a
pre-existing destination folder (even empty, even a brand-new never-used name) makes it
fail instantly with `"Folder ... already contains a backup, please choose another
folder"`. Let `backup` create the destination itself.

When the destination doesn't pre-exist, `backup` produces a single monolithic file (not
a `VIDEO_TS` tree) — mount it via `Copy-Item ... .iso` + `Mount-DiskImage` and read from
the resulting drive letter. Real cost: roughly half an hour per single-layer DVD — not
worth it as a routine per-disc step, but reasonable as a one-time fallback specifically
to unblock menu-mining or a CSS-blocked read (the rip was going to happen anyway).

## Empty tray vs. a genuinely bad disc read differently

An empty tray reports `DRV:0,0,...` with an empty volume-label field and
`MSG:5010 "Failed to open disc"` — distinct from a disc present but damaged/unreadable.
Check the drive's actual media-loaded state at the OS level
(`drive_discovery.ps1`'s `mediaLoaded` field) to confirm which situation you're
actually in before assuming a disc read failure means physical damage.

## Genuine physical disc damage is its own failure category

Distinct from a network-write stall or memory pressure (see below): MakeMKV logs
explicit `MSG:4004 "corrupt or invalid at offset..."` tied to a specific
`VTS_NN_*.VOB`, and either works around it silently (verify via `ffprobe` as always) or
fails outright with `MSG:5003`/`MSG:5004` — an unambiguous failure, not a misleadingly
optimistic "successfully completed" message. Reproducing the identical failure twice is
enough evidence to stop retrying and surface it rather than looping indefinitely
trying to diagnose an ambiguous cause. See `identification-technique.md` for the
salvage procedure if this happens.

**Don't grep only for a generic "success" code to confirm a rip succeeded** — some
status codes can appear from an earlier phase (e.g. the disc TOC scan) well before a
real failure later in the same log. The actual per-title save result lives in the
`MSG:500x`/`MSG:5037`-family lines that follow the "Saving N titles..." line.
`monitor_rips.ps1` implements this check rather than trusting exit code alone.

## A failed raw VOB copy is a different, more severe damage class than MakeMKV-fails-but-copy-succeeds

`identification-technique.md`'s damaged-disc salvage procedure is built on a real,
validated case where MakeMKV failed but a plain file copy of the VOB chunks
succeeded cleanly — that distinguishes corrupted-but-present payload data (something
a decoder can conceal/interpolate around) from a byte-unreadable sector. **A real
run hit the other case**: MakeMKV failed on `a movie`'s disc, and then the
plain `robocopy` step *also* failed partway through — `VTS_01_0-4` (~3.8GB) copied
clean, but `VTS_01_5-8` (~3.3GB, the movie's back half) hit reproducible CRC errors
on the copy itself. This means the documented salvage technique (rebuild
`VIDEO_TS/`, error-tolerant `ffmpeg -f dvdvideo` transcode, `-ss` skip/resume,
concat) **doesn't apply** — there's no readable payload underneath for `ffmpeg` to
decode past. Check which case you're actually in before proceeding past step 1 of
that procedure.

**What happened next is the actual cautionary part.** Rather than surfacing this as
a likely-unrecoverable disc at that point, the run improvised a custom sector-level
reader on the spot (no validated precedent for this case existed) and paid for it
twice over:
- **First attempt wrote its scratch output to `M:\` (the network-mapped staging
  path)** and hit a network write failure mid-scan. Its own error handling
  conflated that write-side failure with a read-side one, so it reported nearly
  every sector in the file as a physically bad sector — a false signal, not real
  damage. This ran for **~9h46m** before anyone noticed the "damage" was actually a
  network hiccup. This is the same class of problem already covered by "Verify a
  resource claim before stating it as a root cause" and the network-mapped-staging
  caution in "Mechanized evidence-gathering, tried and abandoned" above — it just
  wasn't front-of-mind while improvising a brand-new script live, which is exactly
  the situation this catalog exists to prevent recurring in.
- **The corrected version (writing locally instead) took ~64 hours** to sector-scan
  a single ~1GB file, because it retried every failing sector to exhaustion before
  marking it bad rather than skipping ahead and revisiting later (the strategy real
  recovery tools like `ddrescue` use specifically because naive per-sector retry is
  catastrophically slow against a heavily-damaged region). Confirmed genuinely
  severe once it finished: 524,253 of 524,287 sectors (99.99%) unreadable.

**Takeaway**: if a raw VOB copy itself fails with I/O errors (not just MakeMKV),
treat that as a strong signal the disc may not be economically salvageable at all,
and say so to the user early rather than defaulting to "try harder" — a
multi-day, ad hoc sector-recovery effort with no validated technique behind it is a
cost/benefit call for the user to make, not something to grind through
autonomously. If sector-level recovery is ever genuinely worth attempting, write
scratch/output to local disk, never the network-mapped staging path, and prefer a
skip-and-revisit scan order over naive sequential per-sector retry.

## Memory pressure — a distinct failure signature from physical damage

If rips that were previously succeeding start failing — especially larger/longer ones
specifically, while shorter ones keep working — check system memory first
(`Get-CimInstance Win32_OperatingSystem` for free/total,
`Get-Process | Sort-Object WorkingSet64 -Descending` for the top consumers) before
chasing disc-specific or destination-specific theories. This machine class has been
seen running close to its memory ceiling with even a single rip in flight, and stale
processes accumulating across a long session (e.g. orphaned host processes from earlier
work) are a real, recurring contributor — not always the rip itself.

Don't conflate this with physical damage just because both can produce a failed rip —
distinguish by symptom: memory pressure tends to fail larger/longer titles specifically
while shorter ones keep working, and clears once memory is freed / stale processes are
killed; physical damage reproduces identically regardless of what else is running.

## Multi-angle and duplicate-title discs

`mkv disc:N all` isn't safe to assume blindly — multi-angle discs can produce multiple
titles pointing at the same underlying source (same VTS/title-set grouping tag,
near-identical size/duration) that would otherwise all get ripped as if they were
distinct content. Check title info for a shared grouping tag before ripping and rip
only one representative title per source.

**This is a mandatory pre-rip step (SKILL.md's Stage 4, step 1) run via
`scripts/detect_exclusions.py`, not background context to weigh after the fact.** A
real movie disc's pre-rip title scan already showed the exact duplicate pattern — 2
source titles × 2 angles each, all sharing the identical duration — and that was
correctly *noticed*, but the run ripped every title anyway on the reasoning that
Stage 7 identification would sort out which copies were redundant. It did sort it
out correctly, but only after a full duplicate feature-length title (~1h44m) had
already been ripped and then discarded — wasted rip time, disk I/O, and
evidence-gathering work for a title that was never going to survive placement.
**MakeMKV's own dedup logic (`MSG:3027`) only catches *angle*-level duplication
within one title entry** (it correctly collapsed the 4 angle-variant titles down to
2 in this exact case) **— it does not catch two separate *source* titles that both
happen to be full duplicate encodes of the same content**, which is exactly the case
this pre-rip check needs to catch instead. Don't rely on the dedup logic to cover
this class of duplication; the script's `"exclude-duplicate"` verdict is what drives
`-TitleIds` before ripping, same mechanism as the concat-title exclusion.

**Flipper (double-sided) discs** and **dual widescreen/fullscreen authoring** are both
real, encountered patterns — `detect_exclusions.py` checks each candidate title's own
aspect-ratio/format field (`SINFO`) before ever returning `"exclude-duplicate"`, and
downgrades to `"ambiguous-duplicate"` (never auto-excludes) if the format differs,
rather than assuming duplicate-looking titles are redundant from duration alone.
**The grouping-tag field (TINFO field 49, e.g. `"A5"`/`"D2"`/`"E1"`) and the `SINFO`
size/aspect fields were confirmed 2026-09-08 against a real MakeMKV v1.18.4 scan on
this pipeline's own machine** — an earlier version of the script guessed field 16
("source file name") instead, which turned out not to exist at all in real MakeMKV
output; field 49 is the real signal, matching this section's own long-documented
"C1/D1/E1-style output prefix" convention exactly. **What's still unconfirmed is the
algorithm's end-to-end behavior on a genuine duplicate/concat disc** (the confirming
run had neither) — treat a first real `"exclude-duplicate"`/`"exclude-concat"`
verdict as worth double-checking by hand before trusting it unattended, and always
check the raw evidence fields yourself before excluding anything the script marked
`"ambiguous-*"` instead of excluding it outright.

MakeMKV's output filename prefixes (`C1`/`D1`/`E1`, reflecting VTS/title-set grouping)
are a free signal for distinguishing real content blocks from bonus material on a
fragmented disc.

## Mechanized evidence-gathering, tried and abandoned (2026-08-30)

Stage 7's evidence-gathering (subtitle extraction, OCR, frame grabs) was, for a
period, mechanized via `gather_evidence`/`monitor_evidence` scripts mirroring
`launch_rip`/`monitor_rips`'s detached-process pattern — launch one title's
extraction as a quick, non-blocking background process, poll for completion, read a
`manifest.json`. **It was fully removed 2026-08-30** (scripts deleted from
`scripts/platform/{windows,linux}/`) in favor of Claude doing the extraction live
with direct `ffmpeg`/`ffprobe`/Tesseract tool calls, per `identification-technique.md`.
Two real, separate failures led here, worth understanding in case anything like this
gets proposed again:

1. **Concurrency across titles made things dramatically worse, not better.** A real
   run launched all 22 titles' evidence-gathering concurrently on a disc where most
   needed the slow image-OCR fallback, with staging on a network-mapped drive. It ran
   6.5+ hours and still hadn't finished when killed — far worse than the ~2.5-3 hours
   a naive sequential estimate would have predicted from that same run's own early
   per-title timings (~18-20 min each, before concurrency kicked in). The run also
   mid-flight *claimed* to have "confirmed CPU contention" as the cause — never
   actually backed by a real measurement (see "Verify a resource claim before stating
   it as a root cause" above) and independently contradicted by directly-observed
   system stats afterward (~30% CPU, ~50% RAM — nowhere near contended). The exact
   mechanism was never fully pinned down (no NAS-side visibility was available to
   check further), but the practical lesson held regardless: concurrent local
   extraction work against a network-mapped staging path can be dramatically *worse*
   than sequential, not just "no faster." This was rolled back to a sequential
   launch-wait-repeat pattern first, before the scripts themselves were dropped.
2. **Even sequential, the mechanized pipeline was slower than live ad hoc work for
   the common case.** The scripted worker always ran its full extraction pass up to a
   fixed cue cap (`MaxOcrCues`, default 40) regardless of how quickly a confident
   answer emerged — no early-stop. Across the real 196+-disc test history, most
   titles were identified from unambiguous disc metadata/duration alone (no OCR
   needed) or from a single strong OCR hit within the first few cues — exhaustively
   processing up to 40 cues added real time for zero benefit in exactly the cases
   that were already cheap and fast. A live, Claude-driven approach can make that
   stop-early call in real time; a fixed script can't without becoming a second,
   more complex piece of logic to get right — not worth it once concurrency (the
   original reason to mechanize this at all) had already been rolled back.

Bundled tool calls (several `Bash`/`WebFetch` invocations issued together in one
response) genuinely do run concurrently when it's useful — confirmed directly from a
real transcript (multiple `WebFetch`/`ffmpeg` calls sharing one underlying model
turn, dispatched together, not sequential despite each being logged as its own
line) — but relying on that as the *only* mechanism for real concurrency means it
only happens when a given turn actually bundles calls; a real run's concat-detection
scan (Stage 4) issued two drives' info scans as fully sequential single calls despite
its own narration saying "in parallel," and nothing caught that until asked directly.
Worth remembering as a general caution, not a reason to reach for scripted
concurrency again for Stage 7 specifically — see point 1 above for why that made
things worse here, not better.

## The Play-All/concat pre-rip check is easy to skip past — it's a mandatory step, not background context

A real run went straight from drive discovery to `launch_rip ... all` without doing
the per-disc concat-detection scan first (see `identification-technique.md`'s
"Detecting a full-season 'concat' title before ripping it") — recovered correctly at
classification (duration math confirmed the oversized title was the concat, discarded
without placing it), but the rip time already spent on it was real, wasted overhead.
SKILL.md's Stage 4 now states this as an explicit numbered step 1, before any
`launch_rip` call, specifically because it's too easy to read the old prose-only
version as rationale/background rather than an action to actually perform every run.
**This is now `scripts/detect_exclusions.py`, run unconditionally as part of step 1
rather than a math procedure to remember and apply by hand** — the goal is making
the step structurally hard to skip past a second time, not just re-stating it more
emphatically in prose (which is what this catalog entry already tried once, in the
paragraph above, before that turned out not to be enough on its own).

**Separately: MakeMKV's CLI genuinely has no way to select multiple specific titles
in one `mkv` call** — confirmed via
[a MakeMKV forum thread](https://forum.makemkv.com/forum/viewtopic.php?t=17435), a
longstanding open feature request, not something this pipeline missed or a flag that
exists but wasn't discovered. The title argument is a single id or the literal `all`,
nothing else — so "rip everything except the concat title" can't be done as one
call excluding one id. `launch_rip.ps1`/`launch_rip.sh`'s `-TitleIds`/`--title-ids`
parameter works around this by ripping the given titles sequentially, one makemkvcon
invocation per title, but still inside a single detached process/PID — so
`monitor_rips`' one-PID-per-drive model didn't need to change. A side effect worth
knowing: a rip log can now contain several titles' worth of completion/failure codes
back to back, so both monitor scripts' outcome classification was changed from
"check only the first matching code" to "count every occurrence, and any failure
anywhere in the log wins over any success" — a partial failure among several
sequentially-ripped titles now correctly surfaces as `"failed"` instead of getting
masked by an earlier or later title's success message.

## A shell loop over titles is not the same thing as parallel tool calls

A real run's Stage 7 evidence-gathering for a 4-title TV disc used one `Bash` tool
call containing a shell `for` loop over all 4 titles' `ffmpeg` extractions, instead of
4 separate tool calls issued together. Confirmed directly from the session transcript
(every one of that run's 268 assistant turns issued at most one tool call — genuine
parallel tool calls never happened anywhere in it): the loop *looks* like batching and
does reduce round-trips, but each `ffmpeg` inside it still waits for the previous one
to finish — zero wall-clock overlap, no actual speedup over doing titles one at a
time. See `identification-technique.md`'s "A note on parallelism" for the concrete
right-vs-wrong command example this produced. Sequential
execution is a legitimate, permitted choice when the machine's own resources
genuinely can't support running several `ffmpeg`/Tesseract processes concurrently
(same consideration as parallel ripping's memory-pressure caution) — but reaching for
a shell loop as a *substitute* for real parallel tool calls, without an actual
resource reason, defeats the whole point and won't be caught by anything short of
someone actually checking the transcript, since the log output looks identical either
way.

## Don't trust a metadata provider's episode order without checking which provider is actually configured

A `ProviderIds` field being populated (e.g. a TVDB id) does not mean that's the real
fetch source for episode ordering — it can be a secondary cross-reference id embedded
in a *different* provider's data. Before trusting any TV episode order, confirm which
provider the target library is actually configured to use
(`GET /Library/VirtualFolders` → `LibraryOptions.TypeOptions` → the relevant
`MetadataFetchers` list). Checking an internally-consistent but wrong provider's order
can still produce a confidently-wrong placement.

Also: don't extrapolate a whole disc's episode identity from a single confirmed anchor
point, even after several prior discs in a row where simple sequential
disc-order-equals-episode-order held — that pattern can and does break on the next new
show. Verify every individual title's content.

## A dependency check needs the same fallback-location lookup as the tool that actually uses it

`check_makemkv()` has always checked common install-location fallbacks (e.g. Program
Files) beyond a plain PATH lookup, since MakeMKV rarely ends up on PATH from its own
installer. `check_tesseract()` didn't get the same treatment until this was actually
hit on a real machine: **Tesseract was genuinely installed
(`C:\Program Files\Tesseract-OCR\tesseract.exe`, confirmed working), just not on
PATH** — `shutil.which("tesseract")` alone reported it as missing, so Stage 1 offered
to install something that was already there. Fixed by giving `check_tesseract()` the
same fallback-directory check `check_makemkv()` already had (per-OS common install
locations), and by adding explicit `found_via_fallback`/`resolved_path` fields to
`CheckResult` so Stage 1 (and `tesseract.path`/`makemkv.path` persistence) doesn't
have to infer "was this a fallback hit?" by eyeballing the `detail` string, which is
exactly the kind of implicit judgment call that's easy to skip past.

**General lesson for adding a new dependency check to `check_dependencies.py`**: if
the tool being checked is one real installers commonly leave off PATH (anything with
a Windows installer that doesn't offer an "add to PATH" checkbox, basically), a plain
`shutil.which()` isn't enough — it needs the same fallback-location list the thing
that actually *uses* the tool would need to find it too, or the check can report a
false "missing" for a tool that's genuinely fine.

## Needs-review items were surfaced too late — after eject, not before (two fixes, same incident)

A real run finished a two-disc batch (5 episodes placed and verified, bonus content
discarded, both drives ejected) with 3 titles correctly held back for human review —
technically correct, nothing placed wrong, nothing lost, exactly what "needs review"
is supposed to look like. But the closing report mentioned this as a brief clause
inside an otherwise routine completion summary, easy to read past, delivered *after*
eject had already fired — the user only learned 3 real episodes were still
unaccounted-for in Jellyfin by asking directly afterward, and initially read the run
as having silently mis-skipped them rather than correctly deferred them.

**First fix attempt (superseded, described here for the record): make the closing
message lead with it.** SKILL.md originally described the needs-review stage
("stays in staging, presented to the user") without ever wiring that presentation
into the actual end-of-run closing message — a real completion summary could satisfy
the letter of "presented to the user" with a passing mention while the practical
effect (the user not registering that work remains) went unaddressed. Requiring the
closing message to lead with an unresolved-item count fixed the *visibility* problem
but not the underlying one: it still let eject — the real-world "you're done, safe to
reload" signal — fire while genuinely unresolved content sat unaddressed, exactly the
same ordering mistake `bonus-content.md`'s `"ask"` flow was already explicit about
avoiding for bonus content specifically.

**Actual fix: what's now Stage 11 moved to run *before* completion signal/eject
(Stages 12-13), not after, mirroring Stage 10's bonus-content-ask ordering exactly.**
It's no longer a passive "stays in staging" fact reported at the end — it's an
active step that presents each held title's evidence, asks the user what to do
(resolve it now with a hint, explicitly defer it, or discard it), and executes that
choice, all before eject is allowed to run. The closing message still gets a brief
recap if anything was explicitly deferred, but only as a confirmation for the
record — the user already heard about it live, before the "done" signal fired, not
after. **General lesson**: for a stage whose outcome is "a human needs to make a
call," fixing the wording of a report after the fact is not the same fix as moving
the human-input point to before the action that signals completion — the first
attempt here genuinely helped, but it took a second pass (and the user directly
pointing out the ordering problem) to catch that it hadn't gone far enough.

## `allowed-tools` only ever covered the `Bash` tool — `PowerShell` is a completely separate tool namespace

**This is the actual, complete explanation for why every other permission fix in this
file never resolved the live symptom, discovered only after all of them had already
shipped.** Claude Code has two distinct tools for running shell commands — `Bash` and
`PowerShell` — and `allowed-tools` rules are namespaced by tool name: a
`Bash(*ffmpeg*)` rule matches only calls made through the `Bash` tool, and does
**nothing** for the identical command run through the `PowerShell` tool, or vice
versa. This skill's `allowed-tools` frontmatter, through every fix documented in this
file so far (the `powershell.exe`→`powershell` correction, the ffmpeg/ffprobe/
tesseract substring additions, all of it) used **only** `Bash(...)` rules. This skill
genuinely uses both tools in practice — confirmed directly by the user, and visible in
a real permission prompt labeled plainly "**PowerShell** requests permission" for a
`python jellyfin_api.py ...` call, the exact kind of call `Bash(python *)` was already
supposed to cover. It never did, for any `PowerShell`-tool call, all day.

**Confirmed empirically, cleanly, in three steps** (`claude -p --allowedTools '<rule>'
--permission-prompts none 'Run the <X> tool with command: ffmpeg -version'`, checking
`permission_denials`):
1. `Bash(*ffmpeg*)` against a **Bash**-tool call → allowed (this is what every test
   earlier in this file actually checked, every single time — always phrased "run the
   Bash tool," never "run the PowerShell tool").
2. The identical `Bash(*ffmpeg*)` rule against the identical command via the
   **PowerShell** tool → denied.
3. `PowerShell(*ffmpeg*)` against that same PowerShell-tool call → allowed.

**Why this stayed hidden through several rounds of fixes and testing**: every
empirical test run earlier the same day explicitly specified "the Bash tool" in its
prompt, since that's what a bare `claude -p '...run this command...'` naturally
defaults to describing. The real, live session wasn't making that choice — it (or the
`shell: powershell` frontmatter setting steering it) was reaching for the dedicated
`PowerShell` tool for at least some calls, and testing methodology never happened to
cross that line. **General lesson, sharper than the one already in this file about
testing under the right permission mode**: verifying a fix requires exercising the
same *tool*, not just the same command text and the same permission mode — two tools
that both execute "a command line" are not interchangeable for permission-matching
purposes just because they look interchangeable from the outside.

**Fix**: mirrored every existing `Bash(...)` rule with an equivalent `PowerShell(...)`
rule in `allowed-tools`, rather than picking one tool to standardize on — this skill
needs both, confirmed directly, not a design choice worth trying to collapse into one.

## `MSYS_NO_PATHCONV=1` collides with directory-scoping protection — use the PowerShell tool instead, not the env-var workaround

Immediately after the `Bash`/`PowerShell` tool-namespace fix above, a real run's next
`jellyfin_api.py` call still got blocked — a genuinely different cause this time, not
a leftover of the same bug. Git Bash's MSYS layer mangles a leading-`/`-style argument
(`/System/Info`, the REST path `jellyfin_api.py` takes) into a Windows path before
Python ever sees it; the standard, correct fix is prefixing the command with
`MSYS_NO_PATHCONV=1`. That prefix, though, defeats directory-scoping protection
(`permissions.blockReadsOutsideWorkingDirectories`, see the "Directory-scoping" entry
above) in a way nothing can route around: confirmed directly (denial text: *"couldn't
be verified against the configured read-path restrictions (the env-var prefix isn't
on the safe list)"*), and per Claude Code's own documentation this exact category —
a command the sandbox can't statically verify — is one of the handful of checks no
permission mode or `allowed-tools` rule ever bypasses, `dontAsk` included.

**Fix: use the PowerShell tool for `jellyfin_api.py` (and anything else with a
leading-`/`-style argument) instead of reaching for the env-var workaround at all.**
PowerShell has no MSYS path-mangling behavior in the first place, so the argument
passes through literally — the collision is avoided by not needing the workaround,
not by trying to make the workaround compatible with the protection. **General
lesson**: fixing two separate problems independently (tool-namespace coverage, then
directory-scoping) doesn't guarantee they compose cleanly — a fix for one gotcha
(the MSYS mangling workaround) can walk straight into a completely different,
already-fixed protection, and the resolution is often "avoid needing the first
workaround at all" rather than trying to carve out an exception in the second.

## `allowed-tools: Bash(powershell.exe *)` never matched anything — the actual invocation is `powershell`, no `.exe`

**This affected every mechanized PowerShell script call in the whole pipeline —
Stages 2, 3, 4, and 12 — not just Stage 7.** SKILL.md's own documented invocation
convention (and every real run's actual practice) is `powershell -NoProfile
-ExecutionPolicy Bypass -File "..."`, with no `.exe` suffix — but the frontmatter's
`allowed-tools` said `Bash(powershell.exe *)`. Confirmed empirically (not guessed)
via `claude -p --allowedTools 'Bash(powershell.exe *)' --permission-prompts none
'... powershell -NoProfile -Command "1+1"'`: denied, every time, with no `cd`
chaining or anything else involved — a plain, exact-string prefix mismatch. Since
prefix-style `allowed-tools` rules require the command to literally *start with* the
given prefix, `powershell.exe` and `powershell` are different strings as far as the
matcher is concerned, full stop.

**Practical impact was severe precisely because it looked like it was working.**
Every affected command still completed successfully — it just required a human to
click approve on literally every single PowerShell invocation for the life of the
session (drive discovery, every rip launch, every monitor poll, eject, USB-toggle),
which is indistinguishable from "working fine" to anything checking exit codes or
tool results, including to the agent whose commands were being approved — it has no
visibility into whether a call was silently pre-approved or approved-after-a-prompt.
This is why the underlying `dvd-autorip` skill's whole "Stage 2/3/4/12 are
mechanized, meant to run without babysitting" premise was silently defeated for the
entire life of a real multi-hour session before anyone noticed — the symptom
("nothing is auto-running, everything asks permission") only became visible when a
human was actually watching the session and noticed how much manual clicking was
actually happening, not from any error or log.

Fixed to `Bash(powershell *)` plus `Bash(pwsh *)` (SKILL.md documents `pwsh` as a
valid alternative invocation when PowerShell 7+ is installed — same class of gap
would have applied to it too). **General lesson**: when adding a Bash/PowerShell
`allowed-tools` prefix rule, verify it against the *exact literal command string*
this skill's own docs instruct running — not the executable's formal/full name — and
where there's any doubt, test empirically (`claude -p --allowedTools '<rule>'
--permission-prompts none '<command>'`, check `permission_denials` in the JSON
result) rather than trust that a plausible-looking pattern actually matches.

**Follow-up correction (2026-09-10): the above fix is real, but it is not sufficient
in "auto" permission mode specifically, for a completely different reason.** The
`powershell.exe`/ffmpeg/tesseract fixes above were all verified under permission
mode `"default"` — every empirical test that day used `claude -p` without
`--permission-mode auto`, which is a materially different code path. When the user
reported the fix hadn't actually resolved anything in their real session (still
prompting for every Bash/PowerShell call after a full restart), re-investigating
under real auto mode surfaced the actual mechanism, straight from Claude Code's own
permission-modes documentation:

> On entering auto mode, broad allow rules that grant arbitrary code execution are
> dropped: Blanket `Bash(*)` or `PowerShell(*)`; wildcarded interpreters like
> `Bash(python*)`; ... Narrow rules like `Bash(npm test)` stay in effect.

**Every rule in this skill's `allowed-tools` — before today's fixes and after — is
wildcarded**: `Bash(python *)`, `Bash(powershell *)`, `Bash(*ffmpeg*)`,
`Bash(*tesseract*)`, etc. All of them are silently dropped the instant a session
enters auto mode, regardless of how correctly they're written. This is why file
edits (Edit/Write) never prompted — those are auto-approved by an entirely separate
rule ("read-only actions and file edits... are auto-approved") that has nothing to
do with `allowed-tools` — while every Bash/PowerShell call fell through to auto
mode's classifier instead, which has no default-allow rule recognizing DVD-ripping-
specific commands (complex `ffmpeg` filter graphs, PowerShell script invocations
with real staging paths) as safe.

**Compounding effect, confirmed independently by a live session**: per the same
documentation, "if the classifier blocks an action 3 times in a row or 20 times
total, auto mode pauses and Claude Code resumes prompting" — for the rest of that
session. A separate live session (not the one first reporting the bug) independently
observed a system-reminder announcing "Exited Auto Mode" mid-conversation, unprompted
by the user, after several Bash calls — direct, real-world confirmation of this exact
fallback firing. Once tripped, a session stays in full manual-prompting mode
indefinitely; this is why the symptom looked like "everything, permanently" rather
than "occasional prompts."

**Fix: run this skill under `--permission-mode dontAsk` instead of auto mode.**
`dontAsk` mode's own documentation is explicit that it does not drop allow rules —
it runs only actions matching `permissions.allow`, the built-in read-only command
set, and `PreToolUse`-hook-approved calls, with no classifier involved at all. That
is a better structural fit for this skill regardless of the bug above: the pipeline
already has its own explicit human-checkpoint design (Stage 10, Stage 11) rather
than relying on a general-purpose classifier to recognize an unfamiliar workload's
commands as safe turn by turn.

**General lesson**: a permission-related fix verified under one permission mode is
not verified for a different mode — `default`, `acceptEdits`, `auto`, and `dontAsk`
are genuinely different code paths with different rules about what `allowed-tools`
even means, not just different strictness levels of the same mechanism.

## Directory-scoping: a real safety feature, a global-vs-project-scope trap, and the staging-path gotcha

Real incident, 2026-09-09/10, in three parts:

**Part 1 — the original trigger.** A live run defaulted Stage 7 evidence output to a
system temp directory outside its staging tree (see the entry above this one) — the
run caught its own mistake when a later *read* of those files was refused, and
offered to sandbox itself against a recurrence. The user said yes.

**Part 2 — the wrong scope.** That offer wrote
`permissions.blockReadsOutsideWorkingDirectories: true` into the user's **global**
`~/.claude/settings.json` — Claude Code's own documented behavior for that prompt's
"Block from now on" answer. A global setting applies to *every* Claude Code session
on the machine, for every project, in every permission mode (this specific setting
is one of the handful nothing auto-approves, not even `auto` or `bypassPermissions`)
— not scoped to the one skill that prompted for it. This is what actually caused
"prompting for all Bash/PowerShell actions," including in a completely unrelated
session with no `dvd-autorip` plugin loaded at all: a `cd`-into-a-computed-path
command can't be statically verified as staying inside the working directory the
way a direct Read/Edit tool call can, so nearly every command in this pipeline (and
plenty outside it) tripped the rule. The user's actual intent — confine *this
skill* to its own directory tree — was reasonable; the mechanism they were offered
delivered something much broader, silently, with no way to tell from inside a
session that this was the cause rather than a classifier or `allowed-tools` problem
(both of which were real, separate bugs also found and fixed this same day — see
the entries above).

**Part 3 — the actual fix, now built into Stage 1.** The setting is legitimate and
worth keeping — just scoped to a **project-local** `.claude/settings.local.json`
inside the plugin root instead of the global user settings file. Verified
empirically: from a session whose working directory is the plugin root, a read
outside it is denied with the setting explicitly named as the reason; from an
unrelated directory, the same read is denied only by the ordinary "no one available
to answer a first-time out-of-scope-read prompt" path, never mentioning the setting
— confirming the scoping actually holds. `scripts/setup/check_directory_scoping.py`
and Stage 1 step 4 now offer this to every user of the published skill, not just
the one who hit the original incident — but **only after checking whether
`staging.path` is actually nested inside the plugin root first**. `staging.path` is
commonly configured outside the plugin root (a sibling directory, a different
drive — the exact shape a real run's `config.local.json` had it in), and enabling
this protection without accounting for that recreates the identical failure: every
staging read/write starts failing the same unconditional, every-mode check that
started this whole incident. The check-before-offer ordering in Stage 1 exists
specifically to not repeat Part 2's mistake in miniature, once per new installer of
this plugin, forever.

**Part 4 — Stage 1's own check had the same "already enabled, skip the real check"
gap it was built to prevent, and a real restart caught it.** The first version of
Stage 1 step 4 read `"if protection_enabled is already true, nothing to do"` —
which never re-checks `staging_path_in_scope` once the setting is already on. A
fresh session on the same machine (protection already enabled from the original
incident, `staging.path` still genuinely outside the plugin root) hit exactly this:
it improvised past the incomplete instruction on its own judgment and blocked
before ripping anything rather than following "nothing to do" literally — a good
outcome, but not one the instructions actually produced. Fixed by checking both
fields independently, every time, regardless of `protection_enabled`'s value (see
SKILL.md's Stage 1 step 4 for the corrected logic).

**Part 5 — a real "where should this scope to" disagreement, resolved by direct
evidence over assumption.** The user's original intent was scoping to their whole
working drive (`M:\`), not just the plugin's own subfolder, since `staging.path`
and the plugin sit side by side under a common parent in their actual layout. This
led to genuine back-and-forth about *where* Claude Code's project-settings lookup
actually keys off — the shell's literal launch directory, or the `--plugin-dir`
path — including one real misstep: a test showed a settings file placed in an
*ancestor* directory doesn't get inherited by a session running in a descendant
(confirmed), which was then over-generalized into deleting the plugin-root
settings file entirely on the theory that only the shell's launch directory
mattered. **That was wrong, and a live session's own screenshot proved it** — it
had already found `protection_enabled: true` by reading the plugin-root file
before it was deleted, which the ancestor-inheritance test never actually
addressed (a different question: whether `--plugin-dir`'s own path is *separately*
checked, not whether an ancestor's settings leak downward). The file was restored
once the contradiction was pointed out directly, rather than trusting the more
recent test over the more direct evidence.

**Settled design, per explicit user decision**: the settings file stays at the
plugin root, permanently, on the principle that every skill dependency belongs
inside the skill's own folder tree, not scattered across the machine depending on
how a given user happens to launch things — `config.local.json` was the other
example of this principle at the time this was written, but it's since moved to a
fixed path *outside* the plugin tree for an unrelated reason (see config-schema.md
— marketplace installs/updates copy the plugin root fresh each time, which
`config.local.json` needed to survive and this settings file doesn't, since Claude
Code itself, not this skill, manages what happens to it on update). The directory-
scoping check script accounts for that directly: `additionalDirectories` in this
same settings file gets the fixed config path added automatically, rather than
requiring the manual `--add-dir` fallback below. **Widening scope to cover a
`staging.path` outside the plugin root is `--add-dir <staging.path>` at launch,
not relocating this settings file** — `--add-dir` is Claude Code's own general
mechanism for extending a session's working-directory set, and using it sidesteps
ever needing to know the exact mechanics of where Claude Code's own project-settings
lookup keys off in a
given launch pattern. **General lesson**: when a single test result contradicts a
broader claim, check whether the test actually addressed that claim before acting
on it — "ancestor settings don't get inherited by a descendant" and "`--plugin-dir`
isn't separately recognized as its own working directory" are two different
claims, and only the first one was ever actually tested.

## Stage 7 evidence output defaulting outside the sandbox, and ffmpeg/tesseract permission prompts on every call

Two related real problems, both from the same run, both fixed 2026-09-09:

**Frame/crop output defaulted to a system temp directory instead of staging.** With
no explicit output-path guidance in `identification-technique.md`, a real run wrote
its first few OCR frame extractions to `/tmp/ocr0/...` (`$env:TEMP\ocr0\...` on
Windows) — outside this pipeline's staging tree and outside the sandbox's approved
working directories. The problem wasn't caught at write time; it surfaced later when
the run tried to *read* those same files back and got refused
(`permissions.blockReadsOutsideWorkingDirectories`). The run worked around it by
copying the two files it needed into the staging dir under different names, rather
than fixing where new extractions were written — later calls in the same run
self-corrected to target staging directly, but nothing forced that. Fixed by adding
an explicit rule to `identification-technique.md`: every evidence output file goes
under a `staging.path` subdirectory, always, from the first command.

**`ffmpeg`/`ffprobe`/`tesseract` had no `allowed-tools` coverage at all**, so every
single invocation prompted for permission regardless of the session's permission
mode — silently defeating the "Stage 4/12 are mechanized, Stage 7 is live tool calls
but still meant to run without babysitting every command" design. Root cause: the
frontmatter's `allowed-tools` list covered the bundled scripts and bare
`powershell`/`python`/`bash` prefixes, but never covered a bare `ffmpeg`/`ffprobe`/
`tesseract` invocation — because Stage 7 is deliberately *not* wrapped in a script
(see "Mechanized evidence-gathering, tried and abandoned" above), there was no script
path for those commands to inherit coverage from. **Confirmed empirically** (not
guessed) that a plain prefix pattern isn't enough either: `tesseract.path` gets
invoked as a full quoted path when Tesseract isn't on PATH (e.g.
`"C:\Program Files\Tesseract-OCR\tesseract.exe" ...`, per Stage 1's own
`found_via_fallback` handling), and a `Bash(tesseract *)`-style prefix rule does
**not** match that — tested directly via `claude -p --allowedTools 'Bash(tesseract
*)' --permission-prompts none` against that exact command, which denied it. The
substring form `Bash(*tesseract*)`, tested the same way, allowed it. Fixed by adding
`Bash(*ffmpeg*)`, `Bash(*ffprobe*)`, `Bash(*tesseract*)` to `allowed-tools` in
SKILL.md's frontmatter — substring, not prefix, specifically so both the bare-command
and full-quoted-path forms are covered without needing two separate rules.

## PowerShell-specific script gotchas

- **Never name a PID-tracking loop variable `$pid`** — it's PowerShell's reserved,
  read-only current-session PID; a loop using it throws `VariableNotWritable` silently
  every iteration and does nothing useful. Use `$procId` or similar.
- **`Out-File -Append` without `-Encoding` writes UTF-16LE**, which breaks plain
  `grep`/`tail`-style waits on the log from another tool. Always pass `-Encoding utf8`.
- **`InvokeVerb("Eject")` can report success without actually opening the tray.**
  Always verify with a follow-up `Get-Volume` check rather than trusting the call
  silently; retry once before treating it as a real failure.
- **`ConvertTo-Json -AsArray` doesn't exist on Windows PowerShell 5.1** — it was added
  in PowerShell 6.2. On 5.1 (the default on most Windows machines, distinct from
  PowerShell 7+/`pwsh`, which may not be installed at all), passing it is a hard error.
  Without it, `ConvertTo-Json` collapses a 0- or 1-element array into `""` or a bare
  object instead of a JSON array, which silently breaks any consumer expecting an
  array back. Fix used throughout this skill's scripts: branch explicitly on
  `.Count` (0 → `"[]"`, 1 → wrap the single serialized object in `[...]` by hand, 2+ →
  `ConvertTo-Json` alone already produces a real array). Don't reintroduce `-AsArray`
  even on a machine that does have PowerShell 7+ installed — these scripts are meant to
  run unmodified on stock Windows PowerShell 5.1 too, and the manual-branch form is
  correct output on both.
- **Windows' default execution policy blocks every local `.ps1` script outright**
  (`running scripts is disabled on this system`) — not a bug in any of these scripts,
  it's Windows' own out-of-the-box posture on most machines. Always invoke with
  `-ExecutionPolicy Bypass` (see SKILL.md's "Running the bundled PowerShell scripts"
  section) rather than calling a `.ps1` path directly — this affects only the one
  invocation, not the machine's persistent policy.
- **`Start-Process -FilePath "makemkvcon"` does not search the same locations
  `check_dependencies.py`/`Get-Command` do.** A machine can have MakeMKV correctly
  installed and even show up as found by the dependency check (via its Program-Files
  fallback lookup), while `Start-Process` still fails outright with "the system cannot
  find the file specified" — because that fallback path was never on the actual PATH
  environment variable Start-Process's own file resolution uses. `drive_discovery.ps1`
  and `launch_rip.ps1` both now validate `-MakeMkvPath` with `Get-Command` up front and
  fail with a clear message instead of a raw Start-Process error, but the real fix is
  upstream of that: Stage 1 should persist the resolved path into `config.local.json`'s
  `makemkv.path` whenever check_dependencies.py needed its fallback to find it, so
  `-MakeMkvPath` is always passed explicitly rather than left at the scripts' bare
  `"makemkvcon"` default.
- **A bare drive letter (`D`) instead of `D:` breaks `Shell.Application`'s
  `ParseName()` silently** — it returns `$null` rather than throwing, so the failure
  actually surfaces one line later as an unrelated-looking
  `You cannot call a method on a null-valued expression` when `.InvokeVerb()` is called
  on that `$null`. `eject.ps1` now normalizes `D`/`D:`/`D:\` to `D:` internally, so this
  shouldn't recur, but if you see that exact error from a different script, a
  missing/malformed drive-letter argument is the first thing to check.

## A script's parameter name doesn't always match the config field it comes from

`launch_rip.ps1`/`launch_rip.sh`'s staging-root parameter is
`-StagingRoot`/`--staging-root` — but the config field it's populated from is
`staging.path` (see `config-schema.md`). **This mismatch caused a real, repeated
error** (`-StagingPath` guessed from the config field name, not the script's actual
parameter name) across more than one live run before SKILL.md's Stage 4 was made
fully explicit about it. If a script invocation fails with an unrecognized-parameter
error, check the script's own `param()` block (or its bash `case` arg-parsing) for the
real name rather than assuming it mirrors whatever config field the value came from —
this pipeline doesn't guarantee flag names match config field names anywhere else
either, this is just the one place it's actually bitten a real run.

## Jellyfin's real "extras" folder convention (verified via Jellyfin's own docs, 2026-08-29)

Relevant to bonus/extra content handling — see `bonus-content.md` for the full design,
this is just the verified facts:
- Recognized subfolder names, spaces not underscores (`behind_the_scenes` is **not**
  recognized, `behind the scenes` is): `behind the scenes`, `deleted scenes`,
  `interviews`, `scenes`, `samples`, `shorts`, `featurettes`, `clips`, `other`,
  `extras`, `trailers`, `theme-music`, `backdrops`.
- **A movie needs its own folder** for this to work at all (`Movie (Year)/Movie
  (Year).mkv` + `Movie (Year)/deleted scenes/...`) — doesn't work against a flat
  single-file movie library the way this pipeline's movies library is set up by
  default. A filename-suffix shortcut exists (`Movie (Year)-trailer.mp4`, no
  subfolder) but only reliably supports one canonical extra per type — not usable for
  multiple deleted scenes/featurettes from the same disc.
- TV shows don't have this problem — extras attach at the already-existing Season or
  Series folder level, no restructuring needed.

## Jellyfin API gotchas

- **`POST /Items/RemoteSearch/Episode` does not exist** — confirmed via a live 404,
  not just missing documentation. Movies, Series, and BoxSets all have a working
  `RemoteSearch/<Type>` + `RemoteSearch/Apply/{id}` pair; episodes don't. See SKILL.md
  Stage 9 for the actual episode-correction path (full-library refresh relying on
  correct season/episode numbering, not a forced id apply).
- **A per-item `POST /Items/{id}/Refresh` does not detect a file that was renamed or
  newly moved into place** — only a full `POST /Library/Refresh` scan picks up a path
  change. This matters directly for Stage 8: the file placement *is* effectively a
  move/rename from Jellyfin's perspective, so the full-library refresh there isn't
  optional convenience, it's required for the item to be discovered at its new path
  at all.
- **Jellyfin API auth header can change across server versions — don't assume a 401
  mid-run means the key itself went bad.** A real run had every Jellyfin call
  succeed for hours using `X-Emby-Token: {api_key}`, then every call — including a
  bare `/System/Info` with the same key — started returning 401 with no config
  change on this pipeline's side. Confirmed the server itself was healthy and
  reachable the whole time via an unauthenticated check before suspecting the key.
  Regenerating the key **did not fix it either** — the new key still 401'd against
  `X-Emby-Token`. Switching the same new key to
  `Authorization: MediaBrowser Token={api_key}` worked immediately. The server
  reported `Version: 12.0.0` — this looks like a real server-side change in which
  auth header Jellyfin accepts, not anything wrong with the key or this pipeline's
  config. **Caveat — not fully isolated**: the *original* key was never retried
  against the new header form (only the regenerated key was tested against both),
  so it's confirmed the new key needs the new header, but not confirmed whether the
  header switch alone (no regeneration) would have saved the original key too. If
  this happens again: before regenerating a key in Jellyfin's dashboard, first
  retry the existing key with `Authorization: MediaBrowser Token=...` instead of
  `X-Emby-Token` — it may turn out nothing was actually wrong with the key at all.
  **This is why `scripts/jellyfin_api.py` exists and every Jellyfin call in this
  skill routes through it** — it tries the new header first and falls back to the
  old one on every single call, not just at Stage 1 setup, so this fallback is
  already in effect if the same thing happens again mid-session.
