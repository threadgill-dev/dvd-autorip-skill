#!/usr/bin/env python3
"""Stage 8/10's "keep" bonus-content filing -- the deterministic filesystem
half of bonus-content.md's "Filing kept content: Jellyfin's real extras
convention". Judgment (is this genuinely bonus content, what subtype, keep
vs. discard) stays with Claude; this only executes the folder/move mechanics
once that's decided, since those mechanics have real, already-worked-out edge
cases (the flat-vs-foldered movie split, exact recognized subfolder names)
that are easy to get subtly wrong hand-typing Move-Item/mkdir sequences per
disc.

Usage:
    # Movie -- fresh placement (movie file still sitting in staging)
    python file_bonus_content.py movie --movies-path "D:\\Media\\movies" \\
        --title "Movie Title" --year 2016 --movie-file "<staging>\\movie.mkv" \\
        --items '[{"path": "<staging>\\deleted1.mkv", "subtype": "deleted scenes"}]'

    # Movie -- already placed flat, retrofitting a folder for newly-found extras
    python file_bonus_content.py movie --movies-path "D:\\Media\\movies" \\
        --title "Movie Title" --year 2016 \\
        --items '[{"path": "<staging>\\trailer.mkv", "subtype": "trailers"}]'

    # TV -- season-level (the safer default; use --level series only for
    # content that genuinely isn't season-specific)
    python file_bonus_content.py tv --shows-path "D:\\Media\\shows" --show "Sample Show" \\
        --season 3 --level season \\
        --items '[{"path": "<staging>\\deleted1.mkv", "subtype": "deleted scenes"}]'

Recognized subfolder names come from Jellyfin's own docs (verified, not
guessed -- see bonus-content.md): behind the scenes, deleted scenes,
interviews, scenes, samples, shorts, featurettes, clips, other, extras,
trailers, theme-music, backdrops. An unrecognized subtype falls back to
"extras" per bonus-content.md's own stated default, rather than forcing a
specific-but-uncertain guess.

Never partially moves and leaves it ambiguous which items succeeded: on any
failure, "moved" in the JSON output lists whatever DID complete before the
failure (for cleanup/retry), and the script stops rather than continuing past
a bad item. Never overwrites an existing destination file.

Outputs JSON: {"ok": true, "movie_folder"/"target_dir": "...", "moved": [...]}
or {"ok": false, "error": "...", "moved": [...]}. Exit code 0 iff "ok" is true.
This does not call Jellyfin's API -- run a full POST /Library/Refresh (via
jellyfin_api.py) after this, same as any other file placement/move, since a
per-item Refresh does not detect a renamed/relocated file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

RECOGNIZED_SUBFOLDERS = {
    "behind the scenes", "deleted scenes", "interviews", "scenes", "samples",
    "shorts", "featurettes", "clips", "other", "extras", "trailers",
    "theme-music", "backdrops",
}


def normalize_subtype(subtype: str) -> str:
    s = subtype.strip().lower()
    return s if s in RECOGNIZED_SUBFOLDERS else "extras"


def move_items(base_dir: str, items: list[dict], moved: list[dict]) -> dict | None:
    """Moves each item into base_dir/<subtype>/. Returns an error dict (with
    `moved` already populated with whatever succeeded) on the first failure,
    or None on full success."""
    for item in items:
        subtype = normalize_subtype(item["subtype"])
        sub_dir = os.path.join(base_dir, subtype)
        os.makedirs(sub_dir, exist_ok=True)
        dest = os.path.join(sub_dir, os.path.basename(item["path"]))
        if os.path.exists(dest):
            return {"ok": False, "error": f"destination already exists: {dest}", "moved": moved}
        if not os.path.exists(item["path"]):
            return {"ok": False, "error": f"source item does not exist: {item['path']}", "moved": moved}
        shutil.move(item["path"], dest)
        moved.append({"from": item["path"], "to": dest})
    return None


def file_movie(args) -> dict:
    moved: list[dict] = []
    movie_dir_name = f"{args.title} ({args.year})"
    movie_folder = os.path.join(args.movies_path, movie_dir_name)
    flat_path = os.path.join(args.movies_path, f"{movie_dir_name}.mkv")
    foldered_path = os.path.join(movie_folder, f"{movie_dir_name}.mkv")

    os.makedirs(movie_folder, exist_ok=True)

    if args.movie_file:
        if os.path.exists(foldered_path):
            return {"ok": False, "error": f"destination already exists: {foldered_path}", "moved": moved}
        if not os.path.exists(args.movie_file):
            return {"ok": False, "error": f"--movie-file does not exist: {args.movie_file}", "moved": moved}
        shutil.move(args.movie_file, foldered_path)
        moved.append({"from": args.movie_file, "to": foldered_path})
    elif os.path.exists(flat_path):
        if os.path.exists(foldered_path):
            return {
                "ok": False,
                "error": f"both a flat file ({flat_path}) and a foldered file ({foldered_path}) "
                         "exist for this title -- resolve manually before filing extras",
                "moved": moved,
            }
        shutil.move(flat_path, foldered_path)
        moved.append({"from": flat_path, "to": foldered_path})
    elif not os.path.exists(foldered_path):
        return {
            "ok": False,
            "error": f"no movie file found -- expected either --movie-file, an existing flat "
                     f"file at {flat_path}, or an already-foldered file at {foldered_path}",
            "moved": moved,
        }
    # else: foldered_path already exists (a second run adding more extras later) -- nothing
    # to move for the movie file itself, just proceed to file the new items.

    err = move_items(movie_folder, args.items, moved)
    if err:
        return err
    return {"ok": True, "movie_folder": movie_folder, "moved": moved}


def file_tv(args) -> dict:
    moved: list[dict] = []
    if args.level == "season":
        if args.season is None:
            return {"ok": False, "error": "--season is required with --level season", "moved": moved}
        base_dir = os.path.join(args.shows_path, args.show, f"Season {args.season}")
    else:
        base_dir = os.path.join(args.shows_path, args.show)

    if not os.path.isdir(base_dir):
        return {"ok": False, "error": f"expected directory does not exist: {base_dir}", "moved": moved}

    err = move_items(base_dir, args.items, moved)
    if err:
        return err
    return {"ok": True, "target_dir": base_dir, "moved": moved}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="kind", required=True)

    movie = sub.add_parser("movie")
    movie.add_argument("--movies-path", required=True)
    movie.add_argument("--title", required=True)
    movie.add_argument("--year", required=True)
    movie.add_argument("--movie-file", default=None, help="omit if the movie is already placed flat and just needs extras added")
    movie.add_argument("--items", required=True, help='JSON list: [{"path": "...", "subtype": "..."}]')

    tv = sub.add_parser("tv")
    tv.add_argument("--shows-path", required=True)
    tv.add_argument("--show", required=True)
    tv.add_argument("--season", type=int, default=None)
    tv.add_argument("--level", choices=["season", "series"], default="season")
    tv.add_argument("--items", required=True, help='JSON list: [{"path": "...", "subtype": "..."}]')

    args = ap.parse_args()
    try:
        args.items = json.loads(args.items)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"--items is not valid JSON: {e}", "moved": []}))
        sys.exit(1)

    result = file_movie(args) if args.kind == "movie" else file_tv(args)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
