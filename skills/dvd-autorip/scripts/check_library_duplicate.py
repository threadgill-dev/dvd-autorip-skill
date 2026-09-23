#!/usr/bin/env python3
"""Checks whether a movie already exists in the Jellyfin library -- the pre-rip
duplicate check for a Stage 3.5/6-confirmed movie title (`library_duplicates.handling`
in config).

**Why this can't just reuse a server-side filter.** The obvious approach --
`GET /Items?AnyProviderIdEquals=tmdb.{id}` -- does not work on this project's real
server (Jellyfin 12.1.0, confirmed live): the parameter is silently ignored and the
call returns the *entire unfiltered library* instead of erroring or filtering,
which would make a naive "any results = duplicate" check report every single movie
as a duplicate. Confirmed via the server's own live OpenAPI spec
(`GET /api-docs/openapi.json`) that no id-equals filter or provider-id lookup
endpoint exists anywhere in the real API surface for this version -- `/Items`'s
real parameters include `hasTmdbId` (existence only, not a specific value) and
nothing else provider-id-shaped. The only working approach is what this script
does: fetch the movie list with `Fields=ProviderIds,Path` and match client-side.

**A second, more serious Jellyfin quirk, confirmed live -- `IncludeItemTypes=Movie`
silently excludes any movie that belongs to a Jellyfin BoxSet/collection.** A real
run confirmed a disc's identity via TheDiscDB (a real TMDB id, for a well-known
animated franchise movie) and still got `"found": false` against a library that, it
turned out, already had that exact movie. Root-caused by A/B testing the live query
directly, not guessed: with `collapseBoxSetItems` left at its default, `GET
/Items?IncludeItemTypes=Movie&Recursive=true` returned 358 items and the franchise's
own Collection BoxSet itself, but **not one of its three member movies** --
`TotalRecordCount` confirmed the response wasn't just truncated, those items were
genuinely absent from the result set. Adding `&collapseBoxSetItems=false` to the
exact same query returned 509 items, all three franchise movies included. This
isn't a rare edge case: any franchise film a user has grouped into a collection
(this server alone had 151 additional movies hidden this way) was systematically
invisible to the duplicate check, entirely independent of whether its TMDB id was
correct. Fixed by always passing `collapseBoxSetItems=false` on the one list-fetch
this script does.

**A second, independent safety net: title+year fallback.** Even with the boxset
fix, an exact TMDB-id match still assumes the existing library entry's own
provider id is correct -- and this whole project's premise is that an
unverified, pre-existing entry (Jellyfin's own fuzzy auto-match, not run through
this pipeline) can't be trusted to have one. When the exact-id pass finds nothing
and a `--title`/`--year` were given, this script falls back to a case-insensitive
title + exact year match over the same already-fetched list -- no extra API call.
Both match types are treated as equally trustworthy duplicates (the caller doesn't
need to special-case which one fired) -- the config's `library_duplicates.handling`
decides what to do with either the same way. `"match_type"` in the result is still
included, informationally, for a closing report or an `"ask"` prompt to be
specific about which kind of match it was.

Imports `jellyfin_api.py`'s own `load_config`/`do_request` directly rather than
reimplementing the auth-header-fallback HTTP logic -- same mechanism as every other
Jellyfin call in this skill, just a different query shape.

Usage:
    python check_library_duplicate.py <config path> --tmdb-id 12345
    python check_library_duplicate.py <config path> --tmdb-id 12345 --title "Movie Title" --year 2001

`--title`/`--year` are optional -- omit either to skip the fallback pass entirely
(exact-id-only, the original behavior). Both call sites in SKILL.md always have a
title and year on hand (from `discdb_lookup.py`'s `media_item`, or Stage 7's own
confirmed identification) so should always pass them.

Outputs JSON to stdout:
  not found:  {"ok": true, "found": false}
  found:      {"ok": true, "found": true, "match_type": "tmdb_id"|"title_year",
               "item": {"id": "...", "name": "...", "path": "...",
                        "is_salvaged": false}}
  failure:    {"ok": false, "error_type": "...", "detail": "..."} (same shape
              `jellyfin_api.py` itself uses -- propagated from the same GET call)
`"is_salvaged"` is true iff the existing item's `Path` contains the
`[salvaged-incomplete]` filename tag `identification-technique.md`'s damaged-disc
salvage procedure already uses to mark a lossy, incomplete recovery rip -- the
signal `library_duplicates.handling: "skip_unless_damaged"` keys off of.
Exit code 0 iff "ok" is true (a well-formed "found": false is still ok=true -- a
normal, common, non-error result, not a failure).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jellyfin_api import do_request, load_config  # noqa: E402

SALVAGE_TAG = "[salvaged-incomplete]"


def _found(item: dict, match_type: str) -> dict:
    path = item.get("Path") or ""
    return {
        "ok": True,
        "found": True,
        "match_type": match_type,
        "item": {
            "id": item.get("Id"),
            "name": item.get("Name"),
            "path": path,
            "is_salvaged": SALVAGE_TAG in path,
        },
    }


def find_duplicate(
    base_url: str, api_key: str, tmdb_id: str,
    title: str | None = None, year: int | None = None, timeout: int = 30,
) -> dict:
    result = do_request(
        base_url, api_key, "GET",
        "/Items?IncludeItemTypes=Movie&Recursive=true&Fields=ProviderIds,Path&collapseBoxSetItems=false",
        None, timeout,
    )
    if not result.get("ok"):
        return result

    items = (result.get("body") or {}).get("Items", [])

    for item in items:
        provider_ids = item.get("ProviderIds") or {}
        if str(provider_ids.get("Tmdb", "")) == str(tmdb_id):
            return _found(item, "tmdb_id")

    if title and year is not None:
        normalized_title = title.strip().casefold()
        for item in items:
            item_title = (item.get("Name") or "").strip().casefold()
            if item_title == normalized_title and item.get("ProductionYear") == year:
                return _found(item, "title_year")

    return {"ok": True, "found": False}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config_path")
    ap.add_argument("--tmdb-id", required=True)
    ap.add_argument("--title", help="Fallback match when no exact TMDB-id match is found -- requires --year too.")
    ap.add_argument("--year", type=int, help="Fallback match when no exact TMDB-id match is found -- requires --title too.")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    cfg = load_config(args.config_path)
    result = find_duplicate(cfg["base_url"], cfg["api_key"], args.tmdb_id, args.title, args.year, args.timeout)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
