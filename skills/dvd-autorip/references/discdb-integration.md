# TheDiscDB pre-check (Stage 3.5)

[TheDiscDB](https://thediscdb.com) is a public, crowd-contributed catalog of disc
layouts — for a specific physical pressing, it knows exactly how many titles are on
the disc and what each one actually is (a specific episode, the main feature, a named
extra). Looking a disc up by an exact cryptographic hash of its own on-disc file
layout, when it hits, is about as strong as identification evidence gets — stronger
than anything Stage 7's own live evidence-gathering can establish on its own, because
it means this is a byte-for-byte identical pressing to one a person has already
physically confirmed. That is also exactly why a hit is still not an unconditional
auto-apply — see "The cross-check discipline" below.

`scripts/discdb_lookup.py` does the whole read side in one call: computes the disc's
`ContentHash` from the raw mounted filesystem and queries TheDiscDB's public GraphQL
API (`https://thediscdb.com/graphql/`, no auth needed for reads). It never talks to
MakeMKV and never writes anything — read-only, best-effort, safe to call on every
disc.

## Where this runs

**Stage 3.5, after drive discovery and before the rip** — this only needs the disc
mounted (the `VIDEO_TS` folder readable), not a MakeMKV scan, so it can run before
Stage 4's `detect_exclusions.py` and inform it. Gate the whole stage on
`config.local.json`'s `discdb.enabled` (default `true`) — checked once, before the
first call, same spirit as `media_server.type: "none"` skipping Jellyfin-specific
work. A user who doesn't want this skill making any third-party network call at all,
even a read-only one, sets this to `false` and every disc falls through to the
pre-this-feature Stage 4→6→7 flow unchanged.

For each drive with `mediaLoaded: true`:
```
python discdb_lookup.py --video-ts-path "<driveLetter>\VIDEO_TS"
```
(Linux/Mac: the mount point's `VIDEO_TS` subfolder — same field, OS-appropriate
value, same convention `drive_discovery`'s own `driveLetter` field already uses.)

**A miss (`"matched": false`) or a transient failure (`"ok": false` — network down,
timeout, disc unreadable) is a normal, silent, common outcome, not something to
report to the user or slow down for.** Proceed straight to Stage 4 exactly as this
pipeline already worked before this feature existed. TheDiscDB's coverage skews
Blu-ray-heavy and is nowhere near exhaustive for DVDs specifically — confirmed
empirically: a real disc (Sample Show Season 5) produced a clean, correctly-computed hash
with no match, and a follow-up query confirmed the show isn't in the catalog at all.
Don't treat a miss as evidence of anything wrong with the disc, the hash computation,
or the query.

## The ContentHash algorithm

Reverse-engineered directly from TheDiscDB's own now-retired reference tool,
ImportBuddy (MIT-licensed, `github.com/TheDiscDb/data`, tag
`archive/importbuddy-final`, `TheDiscDb.Core/DiscHash/HashingExtensions.cs` +
`ImportBuddy/DiskContentHash.cs`) — read from the actual source, not guessed or
inferred from black-box probing:

1. List every file directly under `VIDEO_TS\` (top level only, no recursion, **no
   extension filter** — every `.IFO`/`.VOB`/`.BUP`, menu files included, not just
   title VOBs).
2. Sort by filename, ascending.
3. MD5 over each file's size as 8 raw little-endian bytes (a signed 64-bit integer),
   concatenated in that order, no separator between files.
4. Uppercase hex, no dashes.

**This cannot be derived from MakeMKV's own robot-mode output** — MakeMKV reports
per-title playback-stream sizes reconstructed from cell/PGC info, not the raw on-disc
`IFO`/`VOB` file sizes this hash actually uses. `discdb_lookup.py` reads the mounted
filesystem directly instead, which is also *why* it can run before any MakeMKV scan.
Verified live and deterministic against real discs currently in this machine's
drives (same disc, same hash, run twice; different disc swapped in, different hash).

The Blu-ray equivalent (not used by this skill, DVD-only pipeline) hashes only
`BDMV\STREAM\*.m2ts`, same MD5-of-sizes method.

## The query

```graphql
query LookupByHash($hash: String!) {
  mediaItems(where: { releases: { some: { discs: { some: { contentHash: { eq: $hash } } } } } }) {
    nodes {
      title
      type
      year
      externalids { tmdb imdb }
      releases(where: { discs: { some: { contentHash: { eq: $hash } } } }) {
        slug
        discs(where: { contentHash: { eq: $hash } }) {
          contentHash
          slug
          titles { index duration size item { title type season episode } }
        }
      }
    }
  }
}
```

`MediaItem.releases` and `Release.discs` both accept their own `where` argument
(confirmed via live schema introspection), so applying the *same* equality filter at
every nesting level — not just the outer `mediaItems.where` — makes the server
return only the matching release and disc directly. A third-party tool built on this
same API (`mkv-episode-matcher`/Engram, confirmed by reading its actual
`discdb_classifier.py` source, not just its own comments) filters only at the outer
level and re-filters every disc client-side to find the one that matched — correct,
but it fetches every release/disc on the title first. Verified live on a real
2-disc release: nested filtering is ~27% smaller (11,130 vs. 15,290 bytes); the gap
widens with box-set size, since the naive form's payload grows with every disc on
the title regardless of which one actually matched.

`disc.contentHash` (the canonical `Disc` entity, shared across every release that
includes an identical physical disc) and the release-junction's own `contentHash`
(mirrored onto `ReleaseDisc`) both work today and return identical values — confirmed
live against a real disc appearing in two different box-set releases. This script
filters on the junction's field since it's one less nesting level; an older
third-party client's comment claims the junction field used to be unfilterable
("Unexpected Execution Error" server-side) — not reproducible against the schema as
it stands now, so likely fixed since, but noted here in case it regresses.

## The cross-check discipline

**A ContentHash hit confirms the whole disc; it does not by itself confirm that
MakeMKV's title numbering matches TheDiscDB's `title.index` 1:1 for every title on
it.** MakeMKV version drift can shuffle title indices, and TheDiscDB's own index
was recorded against whatever MakeMKV version originally scanned that disc for the
catalog — the same reasoning a real third-party client applies to its own
crowd-sourced disc-mapping network (confirmed by reading its source: "MakeMKV version
drift can shuffle title indices, so we never trust the network's title_index blindly
— we re-bind by physical signature").

So: run Stage 4's `detect_exclusions.py` (its own MakeMKV `-r info disc:N` scan) as
normal, then cross-check each `discdb_lookup.py` title against the MakeMKV title at
the *same index* — **duration within ±3 seconds** (matching the tolerance a real
third-party client applies for its own physical-signature re-bind). Only a title
that passes this cross-check is "confirmed" for the shortcuts below. A title whose
discdb-mapped type looks like an episode but fails the duration cross-check gets
**no shortcut at all** — it falls through to normal Stage 6/7 treatment (and Stage 11
if still ambiguous after that), never auto-excluded or auto-placed on the strength of
a mismatched cross-check.

**The "TV disc never spans seasons" hard rule (SKILL.md Stage 7) still applies
unconditionally.** A confirmed hit shouldn't ever disagree with it — a hash-confirmed
disc is definitionally one specific real-world release — but a confirmed mapping is
never itself the authority for overriding that rule if something looks inconsistent;
treat a disagreement as a sign the cross-check should be revisited, not as license to
span seasons.

## What a confirmed title unlocks

For each title where the cross-check above passes, keyed by the disc-info scan's
`item.type`:

- **`"MainMovie"` or `"Episode"`** — confirmed main content. Skip Stage 6
  (classification) and Stage 7 (identification) for this title entirely: its
  season/episode/title and TMDB id are already known. Stage 8 places it directly
  using this data (same naming templates, same folder conventions as any other
  Stage 8 placement); Stage 9's Jellyfin `RemoteSearch/Apply` uses the confirmed TMDB
  id, same as if Stage 7 had found it live. Everything else about Stage 8/9 — respecting
  `media_server.type`, the naming templates, the unconditional-apply behavior —
  is unchanged; only the live evidence-gathering step is skipped.
- **`"Extra"`** — confirmed bonus content. Skip Stage 7 for this title; apply
  `bonus_content.handling` exactly as Stage 8 already does for anything Stage 7
  itself classifies as bonus content (`references/bonus-content.md`) — `"discard"`,
  `"keep"`, or `"ask"`, unchanged mechanics.
- **A combined title's episode range** (TheDiscDB stores this as a string like
  `"17-18"` or `"9-10"` — a two-segment cartoon block, a two-part episode) —
  `discdb_lookup.py` already expands this into the full `episodes` list; treat every
  episode in that list as covered by this one title, same as this pipeline already
  handles a combined title identified live in Stage 7.
- **Empty/unrecognized `item.type` (no mapping for that title at all)** — not
  confirmed, no shortcut. TheDiscDB's own catalog leaves plenty of titles unmapped
  (menu loops, studio bumpers, trailers with no assigned type) — this is expected,
  not a sign the disc's hash match is wrong. Falls through to normal Stage 6/7.

## Pre-rip exclusion (feeds Stage 4 step 1)

A confirmed `"Extra"` title under `bonus_content.handling: "discard"` is excluded
from the rip itself, not just from placement afterward — add its title id to the
same exclusion list Stage 4 step 1 already builds from `detect_exclusions.py`'s
concat/duplicate verdicts, before calling `launch_rip`. This is a new *source* for
that existing exclusion mechanism, not a new mechanism — Stage 4's `-TitleIds`
flag and its "leave this out entirely when nothing to exclude" rule are unchanged.

Under `"keep"` or `"ask"`, don't apply this narrowing — same reasoning Stage 4
already documents for bonus content generally: it can only become a real Jellyfin
Special Feature (`"keep"`) or get a chance at manual review (`"ask"`) if it was
actually ripped.

## Coverage and scope notes

- Read-only, best-effort. No contribution/reciprocity side implemented here —
  TheDiscDB's own contribution flow (`https://thediscdb.com/contribute`) requires a
  Chrome/Edge browser session; ImportBuddy, the CLI tool that used to support a
  scriptable contribution path, was retired (see
  `github.com/TheDiscDb/data`'s README). A separate, third-party API path
  (`/api/engram/disc`) exists specifically for one other tool's own crowd-sourced
  network and was not evaluated here — using it from an unrelated tool is untested
  and not assumed to be welcome without asking first.
- DVD entries do exist in the catalog with populated ContentHash values (confirmed
  live: sampled discs tagged `format: "DVD"` had hashes populated in the same
  proportion as Blu-ray/UHD entries) — this isn't a Blu-ray-only mechanism, just a
  Blu-ray-skewed catalog.
