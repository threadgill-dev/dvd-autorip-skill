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

**A second Jellyfin quirk, more serious than the first — `IncludeItemTypes=Movie`
silently excludes any movie belonging to a collection/BoxSet.** Confirmed live, and
confirmed to be the actual cause of a real miss: a disc's identity was confirmed
via TheDiscDB (TMDB id 12345, Example Movie) and the check still returned `"found": false`
against a library that already had it. Root-caused by A/B testing the query
directly: `GET /Items?IncludeItemTypes=Movie&Recursive=true` returned the "Example Movie
Collection" BoxSet itself but none of its three member movies —
`TotalRecordCount` confirmed the response genuinely didn't include them, not just
a truncated page. Adding `&collapseBoxSetItems=false` to the same query fixed it
completely (358 → 509 items, all three Example Movie movies present). Any user with
franchise films grouped into collections — not a rare setup — would have had every
one of those movies invisible to this check. `check_library_duplicate.py` now
always passes `collapseBoxSetItems=false` on its one list-fetch.

```
python check_library_duplicate.py <config path> --tmdb-id 12345 --title Example Movie --year 2001
```
Returns `{"ok": true, "found": false}` or `{"ok": true, "found": true,
"match_type": "tmdb_id"|"title_year", "item": {"id": ..., "name": ..., "path":
..., "is_salvaged": ...}}`. `is_salvaged` is `true` iff the existing item's `Path`
contains the `[salvaged-incomplete]` filename tag — the same marker
`identification-technique.md`'s damaged-disc salvage procedure already uses for a
lossy, incomplete recovery rip.

`--title`/`--year` (optional, but both call sites always have them on hand) add a
second, independent safety net: **even with the boxset fix, an exact TMDB-id match
still assumes the existing library entry's own provider id is correct** — and this
whole project's premise is that an unverified, pre-existing entry (Jellyfin's own
fuzzy auto-match, not run through this pipeline) can't be trusted to have one. When
the exact-id pass finds nothing, this falls back to a case-insensitive title +
exact year match over the same already-fetched list — no extra API call. **Both
match types are treated as equally trustworthy** — `library_duplicates.handling`
doesn't distinguish between them; `"match_type"` is included in the result only for
an `"ask"` prompt or closing report to be specific about which kind of match it
was.

Verified live against five real cases: an owned movie by exact id (`match_type:
"tmdb_id"`), the same movie found only by title+year (a wrong id substituted, to
confirm the fallback independently), a movie not owned at all (`found: false`), the
one real salvaged title in the library (`is_salvaged: true`), and the no-title/year
backward-compatible exact-id-only path.

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
- `"skip_unless_damaged"` with `is_salvaged: true`, or `"replace"` — **place with
  `place_file.py`'s `--replace` flag** (destination: `item.path` from the
  duplicate-check result) — the config choice itself is the standing authorization
  for the deletion that flag performs, same precedent as `bonus_content.handling:
  "discard"` already deleting permanently without re-confirming each time. Without
  `--replace`, `place_file.py` would refuse the collision outright (see
  `../references/gotchas.md`'s "A hand-built file move silently overwrote an
  already-owned movie" — this is exactly the case that incident fixed).
- `"ask"` — tell the user what was found and let them choose keep-existing
  (discard the new rip) or replace (delete the old file, place the new one),
  once, before this title's placement.

## Scope

Movies only, both call sites — TV episodes aren't checked at either point.
Matching an existing episode needs season/episode numbering against an
already-owned series, not a single TMDB id equality check, and that's a
genuinely different (harder) problem left for later.
