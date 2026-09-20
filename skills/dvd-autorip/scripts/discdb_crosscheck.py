#!/usr/bin/env python3
"""Binds `discdb_lookup.py`'s title mapping to `detect_exclusions.py`'s real
MakeMKV title list by nearest-duration match, not by assuming the two share the
same title index/id.

**Why not same-index.** The original design cross-checked "does the MakeMKV title
at index N have the same duration as TheDiscDB's title N" -- confirmed live against
a real disc (Example Movie, 2003 Fullscreen DVD) that this is wrong: MakeMKV found only 18
real titles on that disc, while TheDiscDB's mapping had 33 entries (short bonus
clips TheDiscDB catalogued that this machine's MakeMKV scan didn't even list --
see `references/discdb-integration.md` for why that gap exists). Once the two
title counts diverge, same-index comparison is nonsense past the point of
divergence: TheDiscDB's index 3 was this disc's real MakeMKV title 2, index 7 was
MakeMKV title 4, and so on. Checking same-index-only confirmed just 3 of 33 titles
on that real disc; nearest-duration binding (what this script does) confirmed 18 of
18 -- every MakeMKV title the disc actually has, correctly and uniquely, with zero
collisions. This is the same "re-bind by physical signature" approach a real
third-party crowd-sourced disc-mapping network applies to its own title-index
drift problem (see `references/discdb-integration.md`'s cross-check discipline
section) -- this script is that same idea, just for this skill's own two data
sources instead of a network hit.

**Still never a blind trust.** A MakeMKV title with no discdb title within
tolerance, or vice versa, is left unbound -- it falls through to normal Stage 6/7
identification, exactly as if Stage 3.5 had missed the disc entirely. Binding two
titles requires their durations to actually be close (default +/-3 seconds,
matching the tolerance a real third-party client uses for the same kind of
physical-signature rebind); this script never guesses past that tolerance, and each
side is used at most once (no MakeMKV title claimed by two discdb entries, or vice
versa).

Usage:
    python discdb_crosscheck.py --discdb-json '<discdb_lookup.py stdout>'
        --makemkv-json '<detect_exclusions.py stdout>' [--tolerance-seconds 3]

Both JSON blobs are the *whole* stdout object each script already prints -- this
reads `"titles"` out of each itself, no need to extract that yourself. Pass
`--discdb-json` for a disc that missed (`"matched": false`) and every MakeMKV title
comes back unbound -- a normal, expected result, not an error.

Outputs JSON to stdout:
  {"ok": true,
   "bound": [{"makemkv_title_id": 0, "discdb_index": 0, "type": "MainMovie",
              "title": "Example Movie", "season": null, "episode": null, "episodes": [],
              "duration_diff_seconds": 0.0}, ...],
   "unbound_makemkv_ids": [...],    # no confirmed discdb mapping -- normal Stage 6/7
   "unbound_discdb_indices": [...]} # discdb entries with no matching real title on
                                     # this scan (commonly short bonus clips this
                                     # machine's MakeMKV didn't enumerate at all)
  {"ok": false, "error": "..."} on malformed/missing input.
Exit code 0 iff "ok" is true.
"""
from __future__ import annotations

import argparse
import json
import sys

DEFAULT_TOLERANCE_SECONDS = 3.0


def cross_check(discdb_titles: list[dict], makemkv_titles: list[dict], tolerance: float) -> dict:
    # (abs_diff, discdb_index, makemkv_id) candidate pairs within tolerance, best first.
    candidates: list[tuple[float, int, int]] = []
    for d in discdb_titles:
        d_idx = d.get("index")
        d_dur = d.get("duration_seconds")
        if d_idx is None or d_dur is None:
            continue
        for m in makemkv_titles:
            m_id = m.get("id")
            m_dur = m.get("duration_s")
            if m_id is None or m_dur is None:
                continue
            diff = abs(float(m_dur) - float(d_dur))
            if diff <= tolerance:
                candidates.append((diff, d_idx, m_id))

    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    used_discdb: set[int] = set()
    used_makemkv: set[int] = set()
    bound = []
    for diff, d_idx, m_id in candidates:
        if d_idx in used_discdb or m_id in used_makemkv:
            continue
        used_discdb.add(d_idx)
        used_makemkv.add(m_id)
        d = next(t for t in discdb_titles if t.get("index") == d_idx)
        bound.append({
            "makemkv_title_id": m_id,
            "discdb_index": d_idx,
            "type": d.get("type", ""),
            "title": d.get("title", ""),
            "season": d.get("season"),
            "episode": d.get("episode"),
            "episodes": d.get("episodes", []),
            "duration_diff_seconds": diff,
        })

    bound.sort(key=lambda b: b["makemkv_title_id"])
    unbound_makemkv_ids = sorted(m["id"] for m in makemkv_titles if m.get("id") not in used_makemkv)
    unbound_discdb_indices = sorted(d["index"] for d in discdb_titles if d.get("index") not in used_discdb)

    return {
        "ok": True,
        "bound": bound,
        "unbound_makemkv_ids": unbound_makemkv_ids,
        "unbound_discdb_indices": unbound_discdb_indices,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--discdb-json", required=True, help="discdb_lookup.py's whole stdout JSON")
    ap.add_argument("--makemkv-json", required=True, help="detect_exclusions.py's whole stdout JSON")
    ap.add_argument("--tolerance-seconds", type=float, default=DEFAULT_TOLERANCE_SECONDS)
    args = ap.parse_args()

    try:
        discdb_result = json.loads(args.discdb_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"--discdb-json is not valid JSON: {e}"}))
        sys.exit(1)
    try:
        makemkv_result = json.loads(args.makemkv_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"--makemkv-json is not valid JSON: {e}"}))
        sys.exit(1)

    if not discdb_result.get("matched"):
        # Not an error -- a disc that missed (or a failed lookup already turned into
        # matched:false upstream) just has nothing to bind. Every MakeMKV title is
        # unbound, same as if Stage 3.5 had never run.
        makemkv_titles = makemkv_result.get("titles", [])
        result = {
            "ok": True,
            "bound": [],
            "unbound_makemkv_ids": sorted(m["id"] for m in makemkv_titles if "id" in m),
            "unbound_discdb_indices": [],
        }
    else:
        result = cross_check(discdb_result.get("titles", []), makemkv_result.get("titles", []), args.tolerance_seconds)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
