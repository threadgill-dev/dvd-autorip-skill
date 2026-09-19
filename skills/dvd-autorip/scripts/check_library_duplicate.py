#!/usr/bin/env python3
"""Checks whether a movie with a given TMDB id already exists in the Jellyfin
library -- the pre-rip duplicate check for a Stage 3.5/6-confirmed movie title
(`library_duplicates.handling` in config).

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

Imports `jellyfin_api.py`'s own `load_config`/`do_request` directly rather than
reimplementing the auth-header-fallback HTTP logic -- same mechanism as every other
Jellyfin call in this skill, just a different query shape.

Usage:
    python check_library_duplicate.py <config path> --tmdb-id 12345

Outputs JSON to stdout:
  not found:  {"ok": true, "found": false}
  found:      {"ok": true, "found": true,
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


def find_duplicate(base_url: str, api_key: str, tmdb_id: str, timeout: int = 30) -> dict:
    result = do_request(
        base_url, api_key, "GET",
        "/Items?IncludeItemTypes=Movie&Recursive=true&Fields=ProviderIds,Path",
        None, timeout,
    )
    if not result.get("ok"):
        return result

    items = (result.get("body") or {}).get("Items", [])
    for item in items:
        provider_ids = item.get("ProviderIds") or {}
        if str(provider_ids.get("Tmdb", "")) == str(tmdb_id):
            path = item.get("Path") or ""
            return {
                "ok": True,
                "found": True,
                "item": {
                    "id": item.get("Id"),
                    "name": item.get("Name"),
                    "path": path,
                    "is_salvaged": SALVAGE_TAG in path,
                },
            }

    return {"ok": True, "found": False}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config_path")
    ap.add_argument("--tmdb-id", required=True)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    cfg = load_config(args.config_path)
    result = find_duplicate(cfg["base_url"], cfg["api_key"], args.tmdb_id, args.timeout)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
