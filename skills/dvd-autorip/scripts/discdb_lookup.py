#!/usr/bin/env python3
"""TheDiscDB pre-check: compute a mounted DVD's ContentHash and look it up against
TheDiscDb's public GraphQL API (https://thediscdb.com/graphql/, no auth needed for
reads) for an exact-disc match with a pre-mapped title-to-episode/movie layout.

**ContentHash algorithm** -- reverse-engineered directly from TheDiscDb's own
now-retired reference tool, ImportBuddy (MIT-licensed, github.com/TheDiscDb/data,
tag archive/importbuddy-final, TheDiscDb.Core/DiscHash/HashingExtensions.cs +
ImportBuddy/DiskContentHash.cs), not guessed or reverse-engineered from black-box
probing:
  1. List every file directly under `<VIDEO_TS path>` (top level only, no
     recursion, no extension filter -- .IFO/.VOB/.BUP, menu files included, not
     just title VOBs).
  2. Sort by filename, ascending.
  3. MD5 over each file's size as 8 raw little-endian bytes (a signed 64-bit
     integer), concatenated in that order, no separator.
  4. Uppercase hex, no dashes.
This is NOT derivable from MakeMKV's own robot-mode output -- it requires reading
the raw mounted disc filesystem directly (which is why this script takes a
VIDEO_TS path, not a MakeMKV disc index). It can run the moment a disc is
mounted, before any MakeMKV scan or rip.

**Query shape** -- filters the top-level `mediaItems.where` AND the nested
`releases(where:)`/`discs(where:)` connections with the same ContentHash
equality check, so the server returns only the matching release/disc directly
(confirmed live: ~27% smaller payload on a real 2-disc title vs. filtering only
the top level and re-filtering every disc client-side, and the saving scales
with box-set size). `disc.contentHash` (not the release-junction's own
`contentHash`) is the field TheDiscDB's own ImportBuddy writes to and the schema
exposes on both -- either currently works; this uses the junction's field since
it's one less nesting level.

**A hit here is strong evidence, not an auto-apply.** An exact ContentHash match
means this is a byte-for-byte identical pressing to one already catalogued --
about as strong as identification evidence gets -- but SKILL.md still requires a
cheap sanity cross-check (title count and duration pattern against MakeMKV's own
disc-info scan) before trusting the mapping, same discipline as
detect_exclusions.py's verdicts. This script never talks to MakeMKV or writes
anything -- it only reads the mounted filesystem and queries TheDiscDB.

Usage:
    python discdb_lookup.py --video-ts-path "D:\\VIDEO_TS"
    python discdb_lookup.py --video-ts-path /media/dvd/VIDEO_TS

Outputs JSON to stdout:
  {"ok": true, "content_hash": "...", "file_count": N, "matched": true,
   "media_item": {"title": "...", "type": "Movie"|"Series", "year": 2020,
                   "tmdb_id": "...", "imdb_id": "..."},
   "release_slug": "...", "disc_slug": "...",
   "titles": [{"index": 0, "type": "MainMovie"|"Episode"|"Extra"|"",
               "title": "...", "season": 1, "episode": 3, "episodes": [3],
               "duration_seconds": 1548, "size_bytes": 123456789}, ...]}
  {"ok": true, "content_hash": "...", "file_count": N, "matched": false}
  {"ok": false, "error_type": "filesystem"|"connection"|"http", "detail": "..."}
Exit code 0 iff "ok" is true (a well-formed "matched": false is still ok=true --
that's a legitimate "not in the catalog" result, not a failure).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import urllib.error
import urllib.request

DISCDB_GRAPHQL_URL = "https://thediscdb.com/graphql/"

# Filters both the outer mediaItems selection and the nested releases/discs
# connections with the same equality check, so the server returns only the
# matching release+disc directly -- see this file's docstring "Query shape".
HASH_LOOKUP_QUERY = """
query LookupByHash($hash: String!) {
  mediaItems(
    where: { releases: { some: { discs: { some: { contentHash: { eq: $hash } } } } } }
  ) {
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
          titles {
            index
            duration
            size
            item { title type season episode }
          }
        }
      }
    }
  }
}
"""


def compute_content_hash(video_ts_path: str) -> tuple[str, int]:
    """TheDiscDB's ContentHash for the disc mounted at `video_ts_path`.

    Raises FileNotFoundError/NotADirectoryError/PermissionError on a bad path --
    callers turn that into the {"ok": false, "error_type": "filesystem"} shape.
    """
    entries = [
        (name, os.path.getsize(os.path.join(video_ts_path, name)))
        for name in os.listdir(video_ts_path)
        if os.path.isfile(os.path.join(video_ts_path, name))
    ]
    entries.sort(key=lambda e: e[0])
    md5 = hashlib.md5()
    for _name, size in entries:
        md5.update(struct.pack("<q", size))
    return md5.hexdigest().upper(), len(entries)


def _parse_duration(duration_str: str) -> int:
    """TheDiscDB duration string like '1:13:14' -> seconds. '' -> 0."""
    if not duration_str:
        return 0
    parts = duration_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def _safe_int(value) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_episodes(value) -> list[int]:
    """Every episode number a TheDiscDB title claims, in order, '' -> [].

    TheDiscDB stores this as a string and a combined title carries the whole
    claim in it -- "17-18" for a two-segment block, "9-10" for a two-part
    episode, occasionally an out-of-order comma list. A bare int() raises on
    all of those (confirmed against a real third-party client's own source --
    see this file's module docstring), which silently drops TheDiscDB's answer
    for exactly the titles where guessing episode numbers is hardest.
    """
    if value is None or value == "":
        return []
    if isinstance(value, int):
        return [value]
    episodes: list[int] = []
    for part in str(value).replace("&", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            lo_i, hi_i = _safe_int(lo), _safe_int(hi)
            if lo_i is None or hi_i is None or hi_i < lo_i:
                continue
            episodes.extend(range(lo_i, hi_i + 1))
        else:
            one = _safe_int(part)
            if one is not None:
                episodes.append(one)
    seen: set[int] = set()
    return [e for e in episodes if not (e in seen or seen.add(e))]


def query_discdb(content_hash: str, timeout: int = 15) -> dict:
    body = json.dumps({"query": HASH_LOOKUP_QUERY, "variables": {"hash": content_hash}}).encode("utf-8")
    req = urllib.request.Request(
        DISCDB_GRAPHQL_URL, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "body": json.loads(resp.read().decode("utf-8"))}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error_type": "http", "detail": e.read().decode("utf-8", errors="replace")[:2000]}
    except urllib.error.URLError as e:
        return {"ok": False, "error_type": "connection", "detail": str(e.reason)}


def build_result(content_hash: str, file_count: int, graphql_body: dict) -> dict:
    nodes = (graphql_body.get("data") or {}).get("mediaItems", {}).get("nodes", [])
    if "errors" in graphql_body:
        return {
            "ok": False, "error_type": "http",
            "detail": f"GraphQL returned errors: {graphql_body['errors']}",
        }
    if not nodes:
        return {"ok": True, "content_hash": content_hash, "file_count": file_count, "matched": False}

    node = nodes[0]
    release = (node.get("releases") or [None])[0]
    disc = (release.get("discs") or [None])[0] if release else None
    if not disc:
        return {"ok": True, "content_hash": content_hash, "file_count": file_count, "matched": False}

    ext_ids = node.get("externalids") or {}
    titles = []
    for t in disc.get("titles", []):
        item = t.get("item") or {}
        episodes = _parse_episodes(item.get("episode"))
        titles.append({
            "index": t.get("index", 0),
            "type": item.get("type", ""),
            "title": item.get("title", ""),
            "season": _safe_int(item.get("season")),
            "episode": episodes[0] if episodes else None,
            "episodes": episodes,
            "duration_seconds": _parse_duration(t.get("duration", "")),
            "size_bytes": t.get("size", 0),
        })

    return {
        "ok": True,
        "content_hash": content_hash,
        "file_count": file_count,
        "matched": True,
        "media_item": {
            "title": node.get("title", ""),
            "type": node.get("type", ""),
            "year": node.get("year"),
            "tmdb_id": ext_ids.get("tmdb"),
            "imdb_id": ext_ids.get("imdb"),
        },
        "release_slug": release.get("slug") if release else None,
        "disc_slug": disc.get("slug"),
        "titles": titles,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video-ts-path", required=True, help=r"Path to the mounted disc's VIDEO_TS folder, e.g. D:\VIDEO_TS")
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()

    try:
        content_hash, file_count = compute_content_hash(args.video_ts_path)
    except OSError as e:
        print(json.dumps({"ok": False, "error_type": "filesystem", "detail": str(e)}))
        sys.exit(1)

    query_result = query_discdb(content_hash, args.timeout)
    if not query_result.get("ok"):
        print(json.dumps({"ok": False, "error_type": query_result["error_type"], "detail": query_result["detail"]}))
        sys.exit(1)

    result = build_result(content_hash, file_count, query_result["body"])
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
