# Identification technique (Stage 7)

This is the core of the pipeline and the reason it isn't a fire-and-forget script:
Jellyfin's own fuzzy title/year search produces confidently wrong matches often enough
that this pipeline never lets it be the thing that decides. Every real title gets
independently identified from disc evidence *before* Jellyfin's scanner ever runs, and
Jellyfin's own guess is unconditionally overwritten afterward (see SKILL.md's Stage 9)
regardless of whether it happened to be right.

## Evidence-gathering: adaptive and incremental, not exhaustive

**This is live work you do directly with `ffmpeg`/`ffprobe`/Tesseract tool calls —
there is no dedicated evidence-gathering script.** (There was, briefly — built and
abandoned 2026-08-30 for reasons in `gotchas.md`'s "Mechanized evidence-gathering,
tried and abandoned.") Across 196+ real discs, the pattern that actually worked was
**gather the cheapest evidence first, stop the moment you're reasonably confident,
escalate only if you're not** — many real titles never needed OCR or frame
extraction at all because the disc's own label, duration, or embedded metadata was
already unambiguous; many that did need OCR were confirmed by a single strong hit,
not by exhaustively sampling the whole title. Don't build a fixed "always gather
everything up front" habit — it's slower than necessary in the common case and isn't
how this was actually validated.

**Always run `ffmpeg` with `-nostdin`** for every command in this section. Without it,
`ffmpeg` can block waiting to read an interactive keypress from stdin when run
non-interactively (e.g. from a tool call) — the symptom is a plain stream-copy that
should finish in seconds instead hanging until the calling tool's own timeout kills it,
which looks like a stuck disc read but is really just `ffmpeg` waiting on input that
will never come.

**Always write every output file in this section — extracted subtitle streams,
cropped/burned-in OCR frames, corroborating frame grabs — under a dedicated
subdirectory of `staging.path`, e.g. `<staging.path>\<drive's staging folder>\_evidence\`,
never a system temp directory (`/tmp`, `$env:TEMP`, etc.) or an unspecified relative
path.** A real run defaulted to `/tmp/ocr0/...` for its first few frame extractions —
outside this pipeline's staging tree and outside the sandbox's approved working
directories — and only discovered the problem when a later *read* of those same files
was refused. Writing under `staging.path` from the first command keeps every
evidence file inside the same tree Claude already has full read/write access to for
this run, with no separate case to remember.

### Step 0 — check what's already unambiguous, before touching subtitles or frames at all

**Disc's own embedded title metadata.** Check `CINFO`/`TINFO` title fields (distinct
from the raw volume label) — some discs report the real title directly (e.g.
`CINFO:2 = "<Movie Title>"`). Not universal (many report a generic
`"DVD disc"` or nothing), but free when present, and real tests repeatedly skipped
every other evidence-gathering step once the disc's own label plus runtime was
already unambiguous (a distinctive volume label, a title that's clearly not shared
with anything else, a runtime that only matches one real candidate). **If you're
already confident from this alone, don't manufacture more evidence for its own
sake** — "needs review" exists for genuine ambiguity, not as a reason to over-verify
an already-clear case.

### Step 1 — probe the subtitle situation once, then branch

One `ffprobe` call per title tells you which of three situations you're in — decide
the rest of the approach from that, don't guess:

**1a. Real text subtitle track (`subrip`/CC608), if present.** Extract the whole
thing in one call (`ffmpeg -nostdin -map 0:s:N -c:s copy`) and read it — cheap
regardless of episode length, no reason to sample partially. Search for distinctive
lines, cross-reference against web-searched episode/movie candidates. Fastest, most
reliable path when available; CC608 (a text track MakeMKV sometimes synthesizes even
without a `subrip`/VOBSUB-derived track) carries the same value once you know to
check for it.

**1b. Image-based subtitle (`dvd_subtitle`/VOBSUB) — OCR, starting small.** Real
dialogue text is worth extracting even from image-only subtitles rather than
skipping straight to frames:
- Extract the subtitle-only stream: `ffmpeg -nostdin -map 0:s:0 -c:s copy`
- Get exact per-cue timestamps: `ffprobe -show_packets` on that extraction — packet
  `pts_time` values are enough even without a proper `.idx`/`.sub` mux.
- **Start with a small, evenly-spread sample — a handful of cues, not all of
  them.** At each sampled cue timestamp, burn the subtitle onto the source video and
  crop to the subtitle region, then grab one frame (OUTPUT-side seeking — `-ss`
  placed *after* `-i`, never before; input-side seeking here silently desyncs the
  burned-in overlay against the source video, a real bug found and fixed live):
  `ffmpeg -nostdin -i <file> -filter_complex "[0:v][0:s:0]overlay,crop=<bottom third>" -ss <t> -frames:v 1 <out.png>`
- OCR each cropped frame with **Tesseract** (genuinely headless — no GUI, doesn't
  hang an unattended session the way some GUI-based OCR tools do). **Use
  `config.local.json`'s `tesseract.path` if it's set** rather than assuming a bare
  `tesseract` command is on PATH — Stage 1 sets this field when
  `check_dependencies.py` found it only via a fallback location, and a real machine
  has hit exactly this.
- **Stop the moment one cue gives you something distinctive enough to confirm
  identity** — across real discs, a single strong hit (a verbatim-matching line, a
  named character, an unmistakable plot detail) has repeatedly done the whole job on
  its own; sampling the rest of the title after that adds time without adding
  confidence. If the first small sample is inconclusive, sample a few more
  (different points in the title, not the same region again) before assuming you
  need to escalate further.
- **If OCR keeps coming back garbled rather than just inconclusive, escalate
  technique, not just cue count** — a real disc's low-resolution source produced OCR
  text too noisy to act on until the cropped frames were upscaled before running
  Tesseract on them; re-sampling more cues at the same quality wouldn't have helped,
  the crop itself needed higher effective resolution first.
- Do this cue-by-cue in small batches (or one at a time), not as one long loop of
  many `ffmpeg`/Tesseract calls inside a single tool invocation — a big batch can
  run long enough to hit the calling tool's own execution timeout partway through,
  which looks like a hang but is really just too much work crammed into one call.
  Re-running only the cut-off items individually is the fix if this happens, not a
  sign anything is actually broken. **This applies just as much to plain subtitle
  extraction (step 1a) as it does to the OCR loop** — a single `ffmpeg` stream copy
  is normally fast, but looping many of them (e.g. one per episode on a
  multi-episode disc) in one tool call can still add up past the timeout even though
  no individual command is slow on its own.

**1c. No subtitle track of any kind.** Frame extraction becomes your primary
evidence, not a last resort to dread — grab 1-2 sample frames at distinctive
timestamps and visually confirm against known cast/setting/era details. A couple of
well-chosen frames can be an unambiguous positive on their own (distinct actor
faces, period-specific settings) without needing any dialogue at all. Frame
extraction is also worth using **alongside** subtitle-based evidence (not only when
subtitles are absent) as corroboration when the subtitle evidence alone feels thin.

### Step 2 — duration matching / official disc-breakdown research

Cross-check candidate runtime against the real film/episode's known runtime,
especially to disambiguate between similarly-titled candidates (e.g. two
different-year films sharing a title). Cheap, and often decisive on its own once you
have a short list of real candidates from steps 0-1.

Not every disc has subtitles, and not every disc lacks them — build for both, and
expect the frame-extraction fallback to actually get used, not just exist on paper.

### Step 2b — for TV discs, build a disc-level episode checklist before identifying individual titles

Before working through titles one at a time, web search for the season's official
disc breakdown (e.g. `"<series>" season N DVD disc breakdown`, or a fan wiki/release
page listing which episodes shipped on which disc) and note the expected episode
range for *this specific disc* — e.g. "Disc 2 = S04E05-E08." This is a checklist to
compare against, not a replacement for per-title identification: it doesn't tell you
which title is which episode, only how many distinct episodes this disc should
produce and which numbers they should be.

Use it as a standing cross-check while working through Steps 0-2 per title:
- If the episode numbers you land on don't match the checklist (wrong count, a gap,
  numbers outside the expected range), treat that as a real signal to re-examine your
  evidence before finalizing anything — not something to explain away.
- **Especially apply this before concluding two titles are duplicate encodes of the
  same episode** — see "Never permanently delete a suspected duplicate encode without
  explicit confirmation" below. If the checklist says this disc should produce N
  distinct episodes and treating two titles as duplicates would leave only N-1,
  that mismatch blocks the duplicate conclusion until resolved — it does not get
  absorbed into "I guess this disc only has N-1 episodes."
- The checklist is corroborating evidence, not proof by itself — official
  disc-breakdown listings aren't always available or accurate, and finding one
  doesn't replace confirming each title's actual identity via Steps 0-2.

## Identifying without a media server

`media_server.type: "none"` (see `config-schema.md`) means there's no Jellyfin
instance to query — used for a plain correctly-named-files setup, or for a Plex
library, since nothing here talks to Plex's API. This changes exactly one thing in
this stage and nothing else: **the `Items/RemoteSearch/Movie`/`Series` call
(Step 2's "official disc-breakdown research" and the id-resolution step in SKILL.md's
Stage 7) is skipped.** That call serves two purposes normally — a second independent
source corroborating the evidence-based identification, and producing a real TMDB id
for Stage 9's Jellyfin write. Neither purpose applies without a server: there's no
write to produce an id for, and a plain web search (already used throughout Steps
0-2, and explicitly in Step 2b) fills the corroboration role just as well — it's the
same kind of independent, external cross-check, just not routed through Jellyfin's
own TMDB proxy.

**Everything else about this stage is identical.** The evidence-gathering discipline
(cheapest-first, stop once confident, frame extraction when subtitles are absent),
the TV-discs-never-span-seasons hard rule, the duplicate-encode caution below, the
disc-level episode checklist — none of it is Jellyfin-specific, and none of it gets
relaxed just because there's no server to write to afterward. If anything, get this
right the first time matters *more* in `"none"` mode: there's no Stage 12
verification-against-a-server to catch a wrong filename later (see `SKILL.md`'s
Stage 12), so a mistake here is more likely to go unnoticed than in the `jellyfin`
path.

## Never permanently delete a suspected duplicate encode without explicit confirmation

Two titles independently matching the same search result is weaker evidence of
duplication than it looks — generic dialogue can plausibly match more than one real
episode, especially when both titles were searched independently rather than diffed
directly against each other. A real run on a a TV series season disc
concluded two titles were duplicate encodes of the same episode (matching segment
counts, near-identical duration, and two independent web searches both landing on
that same episode) and permanently deleted one. The deleted title was actually a
distinct episode — the disc's episode checklist (Step 2b), only checked afterward,
showed the disc should have produced 4 distinct episodes, not 3. The file was gone
for good: a `staging.path` on a network share has no Recycle Bin, and a permanent
delete doesn't route through one even on a local disk.

Before treating two ripped titles as duplicate encodes and discarding one:
1. Check the Step 2b disc-level checklist (if one was built) for a count/number
   mismatch — treat any mismatch as a hard block on the duplicate conclusion until
   it's resolved.
2. Diff the two titles' actual dialogue directly against each other, not just each
   one independently against a web search — matching web-search attribution alone is
   exactly the evidence that produced the real deletion above.
3. **Classify it as needs-review, not a confirmed duplicate, and let SKILL.md's
   Stage 11 resolve it with the user before anything is deleted.** This is a hard
   rule, not a judgment call left to confidence level, precisely because the action
   is irreversible and has already destroyed real content once. No config setting
   (including `bonus_content.handling`) authorizes skipping this — that setting
   governs bonus/extra content, not suspected duplicate main-content encodes, and
   nothing should ever bypass this confirmation.

### A note on parallelism here

Bundling several genuinely independent tool calls together in one response (e.g.
extracting different titles' subtitle streams at the same time) still works and is
fine when it's actually useful — the harness runs them concurrently. Two patterns
remain wrong regardless:
- ❌ **A shell loop pretending to be parallel** — each command inside still waits for
  the previous one to finish; zero wall-clock overlap, no speedup, only fewer
  round-trips:
  ```bash
  for f in C1_t00 C2_t01 D1_t02 D2_t04; do
    ffmpeg -nostdin -v error -i "$f.mkv" -map 0:s:0 -c:s copy -f matroska "${f}_subs.mks" -y
  done
  ```
- ❌ **Delegating any of this to a subagent** — prohibited outright, see SKILL.md's
  intro section. A real, properly-guardrailed subagent-per-title design remains a
  plausible *future* direction (see PLAN.md's `Potential_Enhancements`) — this whole
  adaptive, per-title procedure is deliberately written clearly enough to double as
  the basis for what that design would eventually need to do independently per
  agent — but nothing here authorizes building or using it now.

Work through each title's actual identification decision as soon as its evidence is
in hand, one title at a time, in order — this naturally preserves the "verify every
title independently, don't extrapolate a whole disc's identity from one confirmed
anchor" discipline from the gotchas below.

This doesn't touch Stage 8-11 (place, scan, correct, verify) — those stay per-title and
sequential regardless, since concurrent Jellyfin API writes against the same show risk
racing each other.

## Hard rule: TV show discs never span seasons

**A TV show disc's content belongs entirely to the one season that disc is authored
for — full stop, no exceptions.** If content on a TV disc doesn't match that season's
known episode list, the correct conclusions, in order, are:
1. It's a duplicate of an episode from the *same* season already identified elsewhere
   on the disc.
2. It's bonus/menu content (deleted scenes, a blooper reel, a short, a
   cross-promotional episode of a *different show* — a real, documented DVD-authoring
   practice).
3. The identification itself is wrong and needs to be redone.

**"This disc must span into the next season" is never the right conclusion.** This
rule exists because getting it wrong once produced a real misidentification that
cascaded into every subsequent placement on that disc being wrong too.

## Other validated patterns

- **A Jellyfin episode showing the "right" title after a library refresh is not proof
  the underlying video content is correct.** For TV episodes, that title comes from the
  metadata provider's lookup against the *filename's* season/episode number, not
  anything checked against the actual video — a file placed under the wrong episode
  number displays that wrong number's real title and passes every refresh check
  indefinitely. When a *new* disc's content directly contradicts an already-placed
  episode's assumed identity, treat that as real evidence to re-investigate the
  existing placement, not just the new one.
- **A double-length disc title is not automatically a multi-episode-combined case.**
  Duration alone doesn't distinguish "two numbered episodes combined into one disc
  title" from "one episode's extended/deleted-scenes DVD cut." Verify by checking
  whether distinct fragments sampled from within the long title resolve (via a real web
  search) to the *same* real episode or *different* episodes before deciding how many
  episode-number slots it occupies. Getting this wrong cascades into every subsequent
  position on the same disc being off by one.
- **Zero-result and empty-candidate searches both need a second pass, for different
  reasons:**
  - Zero results: retry with a shorter/simpler substring before concluding the item is
    unmatched — stylized punctuation (ampersands, superscripts, unusual characters) in
    a real title is common enough to break an exact-feeling search term.
  - Zero *populated* candidates (a title uses stylized characters a text search can't
    find at all): look the id up externally, then pass it directly via
    `SearchInfo.ProviderIds` rather than `Name` — an id-based lookup works where a text
    search doesn't.
  - "Decoy" candidates (empty-metadata entries ranked among real ones by
    `RemoteSearch`) show up regularly, scaling with how common/reused a title string
    is. Always prefer the populated/corroborated candidate over position or year
    match alone — and check the full overview text, not just title/year: a title
    sharing words with a long-running unrelated franchise (the "many actor-specific
    versions of a classic character" problem — a well-known literary detective is the canonical
    example, with roughly ten separate TMDB entries for different actors/eras) can bury
    the real match in noise that only the overview text reliably filters.
- **Cross-reference new evidence against what's already in the library.** Beyond
  narrowing down identity, this has surfaced real data-integrity problems in
  already-placed files that nothing else was looking for. Worth a deliberate check, not
  just an incidental one.
- **A disc having more episode-shaped titles than expected doesn't mean more
  episodes.** Always check whether "new" content actually duplicates something already
  identified (via dialogue, not just duration/chapter-count) before trusting
  sequential-numbering assumptions.
- **Disc title order is normally sequential episode order — a real, useful
  consistency check, not a hard rule.** A real run identified a 4-title disc's titles
  (in on-disc order) as S03E05, E06, **E13**, E08 — non-monotonic, jumping forward 7
  episodes then backward 5. That should have been treated as a strong signal to
  double-check the outlier before finalizing, especially since its evidence chain was
  weaker (a plot-plausibility inference) than its neighbors' (distinctive quotes
  directly tied to a specific scene). It turned out to actually be **E07**, restoring
  a clean contiguous E05-E06-E07-E08 run, and a second, different line of dialogue
  extracted from that same title had already pointed toward E07 before being set
  aside in favor of the E13 guess. **This is a consideration, not the TV-discs
  hard rule above** — legitimate non-contiguous discs exist (custom compilations,
  reordered content), so don't force a sequential fit the evidence doesn't actually
  support. But when an otherwise-solid, mostly-sequential set of identifications
  produces one wildly out-of-order title — particularly one resting on weaker
  evidence than its neighbors — that combination is a real reason to re-examine that
  specific title before treating the set as finished, not something to wave off as
  "the disc must have skipped around."

## Detecting a full-season "concat" title before ripping it

Some TV discs author one title that's every episode on the disc concatenated together,
alongside the normal per-episode titles — ripping it wastes real time/space if you
don't need it.

**Run `scripts/detect_exclusions.py --disc-index N` during the initial info scan,
before ripping anything** — it implements the check below itself so this isn't a
math procedure to apply by hand:

Check `TINFO` field 25 (source/segment count):
- `1` = a genuine single-episode title.
- Equal to the disc's real episode count = the concat title.

**Caveat:** field 25 = 1 is not universal — some discs author each real episode with
2+ cell blocks (main content + a small trailing tag chapter), so field 25 can read >1
on genuine episodes too. The more reliable underlying test: does a candidate concat
title's duration divide evenly across the disc's real episode count, landing in the
individual titles' own duration range? The script requires *both* signals to agree
(field 25 equals the size of a same-duration episode cluster it finds on the disc,
and the candidate's own duration matches that cluster's summed duration) before
returning `"exclude-concat"` — a `"1"` on either signal alone with the other still
supportive comes back as `"ambiguous-concat"` instead, which is your cue to also
check whether the candidate's chapter grouping repeats the individual titles'
per-episode chapter shape N times before excluding it.

**This check happens before calling `launch_rip` at all — it's Stage 4's mandatory
step 1, not an optional/background note.** A real run skipped it and paid for it in
wasted rip time on the oversized concat title (caught and discarded afterward at
classification, but the time was already spent). Once the script confirms
`"exclude-concat"`, pass every real per-episode title id to
`launch_rip.ps1`/`launch_rip.sh` via `-TitleIds`/`--title-ids` (comma-separated, e.g.
`"0,1,2,3"`) instead of ripping `all` — MakeMKV's own CLI has no multi-title-select
syntax (confirmed via
[the MakeMKV forum](https://forum.makemkv.com/forum/viewtopic.php?t=17435), a
longstanding open feature request, not a flag this pipeline missed), so the scripts
handle the "one id at a time" requirement internally: `-TitleIds` still launches one
detached process/PID per drive, same as `all` does, it just loops through the given
titles sequentially inside that one process rather than needing N separate ones.
Nothing about the process-list/monitoring flow in SKILL.md's Stage 4 changes because
of this.

## Damaged-disc salvage (when MakeMKV fails a title completely and reproducibly)

Signature: MakeMKV logs `MSG:4004 "corrupt or invalid at offset..."` tied to a specific
`VTS_NN_*.VOB`, and either works around it silently (verify the output via `ffprobe` as
always) or fails outright with `MSG:5003`/`MSG:5004`. Reproducing the identical failure
twice is enough evidence to stop retrying and surface it to the user rather than
looping indefinitely.

A real salvage path exists before writing the disc off:
1. Copy the title's `VTS_NN_*.VOB` chunks individually to local disk — a plain file
   copy often succeeds even when MakeMKV can't process the data, which distinguishes a
   byte-unreadable sector from a byte-readable-but-corrupted payload.
   **Check this step's actual result before assuming the rest of this procedure
   applies.** If every chunk copies cleanly, the damage is corrupted-but-present
   payload data (bad checksums/coefficients MakeMKV treats as fatal but a decoder
   doesn't have to) — proceed with steps 2-5, this is the validated case. **If the
   plain copy itself fails with I/O errors on part of the file, that's a different
   and more severe damage class — genuinely unreadable sectors, not just corrupted
   payload** — steps 2-5 below won't help (there's nothing for `ffmpeg` to
   read past), and a real run's attempt to improvise sector-level recovery for this
   case cost it over 3 days for one file with no clean recovery to show for it. See
   `gotchas.md`'s "A failed raw VOB copy is a different, more severe damage class
   than MakeMKV-fails-but-copy-succeeds" before spending any real time on this case —
   treat it as a cost/benefit question for the user, not an automatic next step.
2. Rebuild a proper local `VIDEO_TS/` folder (the VOBs plus their matching
   `.IFO`/`.BUP` files) and read it with ffmpeg's `-f dvdvideo` demuxer — not raw
   `concat:` on the VOBs directly, which fails to even identify the codec; the DVD
   navigation structure is required.
3. Transcode (not stream-copy) with
   `-err_detect ignore_err -fflags +genpts+igndts+discardcorrupt` so the decoder
   conceals corrupt macroblocks frame-by-frame instead of aborting.
4. If a genuine NAV-packet-level failure still halts it, note the timestamp reached and
   re-run with `-ss <past that point>` to resume, repeating until reaching the real end.
5. Concatenate the clean segments (`ffmpeg -f concat`) and spot-check each seam by
   frame-extracting immediately before/after.

Mark any placed result clearly (e.g. a `[salvaged-incomplete]` filename tag) — it's a
lossy re-encode with real content gaps, not this pipeline's normal pristine
stream-copy.

⚠️ The raw `dvdvideo` demuxer only exposes the disc's native image-based VOBSUB
subtitle tracks, not a CC608-derived text track MakeMKV would normally synthesize — so
the subtitle-extraction identification method may not be available on a salvaged title;
fall back to frame-based identification for these specifically.
