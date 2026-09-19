# Library duplicate check (movies only)

Two different points in this pipeline can establish a movie's real-world identity
(a TMDB id) — Stage 3.5's TheDiscDB hash confirmation, *before* anything gets
ripped, and Stage 7's live identification, only possible *after* the disc's been
ripped. Both are a chance to ask "do we already have this" — the consequences are
just different, since only the first one can still avoid wasting the rip itself.

**The obvious Jellyfin query doesn't work — confirmed live, not assumed.**
`GET /Items?AnyProviderIdEquals=tmdb.{id}` is what a standard Jellyfin API search
would reach for, but tested directly against this project's real server (Jellyfin
12.1.0): the parameter is silently ignored, and the call returns the **entire
unfiltered movie library** instead of erroring or filtering — confirmed by
comparing the "filtered" result's item count and order against a genuinely
unfiltered baseline query (both returned the same 357 movies in the same order). A
naive "any results = duplicate" check built on this would have reported every
single movie in the library as a duplicate, always. Confirmed via the server's own
live OpenAPI spec (`GET /api-docs/openapi.json`) that no id-equals filter or
provider-id lookup endpoint exists anywhere in the real API surface for this
version — `/Items`'s real parameters include `hasTmdbId` (existence only, not a
specific value) and nothing else provider-id-shaped, and no path anywhere in the
spec is a dedicated provider-id lookup either.

`scripts/check_library_duplicate.py` does the only approach that actually works:
fetch the movie list with `Fields=ProviderIds,Path` and match the target TMDB id
client-side. It imports `jellyfin_api.py`'s own `load_config`/`do_request`
directly rather than reimplementing the auth-header-fallback logic — same
mechanism as every other Jellyfin call in this skill, just a different query
shape than Stage 12's existing check (which looks up one already-known item by its
own internal Jellyfin id to verify a write, not the whole library by an external
id).

```
python check_library_duplicate.py <config path> --tmdb-id 12345
```
Returns `{"ok": true, "found": false}` or `{"ok": true, "found": true, "item":
{"id": ..., "name": ..., "path": ..., "is_salvaged": ...}}`. `is_salvaged` is
`true` iff the existing item's `Path` contains the `[salvaged-incomplete]`
filename tag — the same marker `identification-technique.md`'s damaged-disc
salvage procedure already uses for a lossy, incomplete recovery rip. Verified
live against three real cases in the library this was built against: a normal
owned movie (`found: true`, `is_salvaged: false`), a movie not owned at all
(`found: false`), and the one real salvaged title in that library (`found: true`,
`is_salvaged: true`).

`config.local.json`'s `library_duplicates.handling` (`references/config-schema.md`
has the full field) decides what happens on a `found: true` result, at either call
site — `"skip"`, `"skip_unless_damaged"` (treat as not-a-real-duplicate when
`is_salvaged` is `true`), `"replace"`, or `"ask"`. The *action* each of those takes
differs by call site, since one runs before the rip and the other after:

## Call site 1: Stage 3.5/4 check 5 (before ripping)

Only runs for a title Stage 3.5/4 check 4 confirmed as `"MainMovie"` — the disc
hasn't been ripped yet, so there's nothing to place or discard, only a rip to
avoid or not:
- `"skip"`, or `"skip_unless_damaged"` with `is_salvaged: false` — **exclude this
  title from the rip**, same exclusion-list mechanism as Stage 4's concat/
  duplicate/discdb-extra checks (1/2/4).
- `"skip_unless_damaged"` with `is_salvaged: true`, or `"replace"` — no exclusion;
  rip normally.
- `"ask"` — tell the user what was found and let them choose skip-or-proceed for
  this title, once, before the rip launches.

## Call site 2: Stage 8 (after ripping, before placement)

Only runs for a movie Stage 7 identified live — a title Stage 3.5/4 check 5
already resolved never reaches Stage 7 at all (Stage 6/7's skip-for-confirmed-
titles rule), so the two call sites never both fire for the same title. The file
already exists in staging at this point, so "not worth keeping" means discarding
the fresh rip, not avoiding it, and "proceed" can mean displacing what's already
there:
- `"skip"`, or `"skip_unless_damaged"` with `is_salvaged: false` — **discard the
  freshly-ripped file from staging** instead of placing it; do not move it into
  `{library.movies_path}`.
- `"skip_unless_damaged"` with `is_salvaged: true`, or `"replace"` — **place the
  new file and delete the existing library file it's replacing first** (its path
  came back in the duplicate-check result) — the config choice itself is the
  standing authorization for this, same precedent as `bonus_content.handling:
  "discard"` already deleting permanently without re-confirming each time.
  Deleting the old file before placing the new one avoids leaving two library
  entries for the same movie for Stage 8's `Library/Refresh` to find.
- `"ask"` — tell the user what was found and let them choose keep-existing
  (discard the new rip) or replace (delete the old file, place the new one),
  once, before this title's placement.

## Scope

Movies only, both call sites — TV episodes aren't checked at either point.
Matching an existing episode needs season/episode numbering against an
already-owned series, not a single TMDB id equality check, and that's a
genuinely different (harder) problem left for later.
