#!/usr/bin/env python3
r"""Moves a ripped file from staging into the library, refusing to silently
overwrite an existing file at the destination unless explicitly told to.

**Why this exists.** Stage 8 (place + scan) previously had no dedicated script --
just prose telling Claude to "move into {library.movies_path}...", so every actual
move was a hand-constructed `Move-Item`/`mv` command, reconstructed fresh each
placement. A real run overwrote an already-owned movie this way: the destination
filename collided with an existing library file, and whatever move command got
built that time clobbered it silently -- with no independent check protecting the
destination regardless of whether the upstream duplicate-check logic
(`check_library_duplicate.py` / `library_duplicates.handling`) had run, found
something, or decided correctly. This script is the fix: a destination collision
is a hard stop by default, and replacing an existing file requires the caller to
pass `--replace` explicitly -- there is no code path in this script that deletes
or overwrites anything without that flag. This makes the safety property
independent of whatever reasoning happened upstream; the two layers (the
duplicate-check policy deciding *whether* to replace, this script enforcing *that
only an explicit decision* can) have to independently agree before anything at the
destination is touched.

Usage:
    python place_file.py <source path> <destination path>
    python place_file.py <source path> <destination path> --replace

Creates the destination's parent directory if it doesn't exist yet (needed for a
new show's `{shows_path}\{Show}\Season N\` the first time an episode lands there).
Uses a cross-filesystem-safe move (works across drives, not just `os.rename`,
which fails moving across a Windows drive letter boundary the way staging ->
library commonly does).

Outputs JSON to stdout:
  moved (no collision):     {"ok": true, "action": "moved"}
  replaced (--replace used,
    destination existed):   {"ok": true, "action": "replaced"}
  collision, no --replace:  {"ok": false, "error_type": "collision",
                              "detail": "..."}
  source missing / other
    filesystem error:       {"ok": false, "error_type": "filesystem",
                              "detail": "..."}
Exit code 0 iff "ok" is true.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys


def place(source: str, destination: str, replace: bool) -> dict:
    if not os.path.isfile(source):
        return {"ok": False, "error_type": "filesystem", "detail": f"source file not found: {source}"}

    destination_exists = os.path.exists(destination)
    if destination_exists and not replace:
        return {
            "ok": False,
            "error_type": "collision",
            "detail": (
                f"destination already exists: {destination} -- pass --replace to delete it and "
                "place the new file, only once that's a deliberate decision (e.g. "
                "library_duplicates.handling resolved to replace this specific title)"
            ),
        }

    try:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if destination_exists:
            os.remove(destination)
        shutil.move(source, destination)
    except OSError as e:
        return {"ok": False, "error_type": "filesystem", "detail": str(e)}

    return {"ok": True, "action": "replaced" if destination_exists else "moved"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("destination")
    ap.add_argument("--replace", action="store_true", help="Delete an existing file at destination first, if present.")
    args = ap.parse_args()

    result = place(args.source, args.destination, args.replace)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
