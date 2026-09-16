#!/usr/bin/env python3
"""Stage 4 step 1 -- pre-rip exclusion detection: concat/Play-All titles and
duplicate-source titles, from MakeMKV's own robot-mode per-disc info scan.

This mechanizes the two checks documented in identification-technique.md
("Detecting a full-season 'concat' title before ripping it") and gotchas.md
("Multi-angle and duplicate-title discs") -- the step that's been skipped past
twice in real runs despite three separate written reminders. Running this
script is now the mandatory step 1 itself, not a judgment call to remember.

- Concat/Play-All detection: TINFO field 25 (SegmentsCount) as a fast
  first-pass filter, confirmed by checking whether a candidate title's
  duration equals the summed duration of a cluster of similarly-sized
  "episode-shaped" titles on the same disc -- operationalizes the doc's
  "duration divides evenly across the disc's real episode count" test
  without needing an externally-supplied episode count.
- Duplicate-source detection: titles sharing MakeMKV's VTS/title-set grouping
  tag (TINFO field 49 -- the same "A5"/"D2"/"E1"-style tag gotchas.md already
  documents as a free signal, visible as the prefix of field 27's output
  filename too, e.g. "A5_t00.mkv") and near-identical duration are flagged as
  candidates, cross-checked against each candidate's own video stream aspect
  ratio/resolution (SINFO fields 20/19) to rule out a genuine flipper
  (double-sided) disc or dual widescreen/fullscreen authoring -- gotchas.md
  is explicit that duration alone isn't enough to call this.

CONFIDENCE NOTE: MakeMKV does not publicly document its robot-mode
attribute-id table (the AP_ItemAttributeId enum lives in its SDK's apdefs.h,
which isn't shipped alongside makemkvcon itself). Field 25 = SegmentsCount is
validated against this skill's own 196+-disc test history (see
identification-technique.md). Fields 9 (Duration), 19 (VideoSize), 20
(VideoAspectRatio), and 49 (the VTS/title-set grouping tag) were CONFIRMED
2026-09-08 against a real MakeMKV v1.18.4 disc scan on the machine this
pipeline actually runs on (real values observed: duration "0:50:03"-style
H:MM:SS with a non-zero-padded hour, "16:9"/"720x480", and "A5"/"A8"/"D2"/
"D5"/"E1" grouping tags matching gotchas.md's documented convention exactly).
**Field 16, this script's first guess for a "source file name" signal, does
NOT appear anywhere in real MakeMKV v1.18.4 output at all** -- there is no
such attribute on that version; field 49's grouping tag is the real,
confirmed signal and is what this script now uses. What remains UNCONFIRMED
is the duplicate-detection *algorithm* end-to-end -- the disc used for this
confirmation had no actual duplicate-source or concat titles, so the field
numbers are now trusted but a real "exclude-duplicate"/"exclude-concat"
verdict has not yet been produced against a genuine positive case. Because of
that, this script still never treats a single field alone as sufficient to
auto-exclude a duplicate -- see the verdict semantics below.

Usage:
    python detect_exclusions.py --disc-index 0 [--makemkv-path ...]
    python detect_exclusions.py --input-file scan.txt      # parse a saved scan
    cat scan.txt | python detect_exclusions.py --input-file -

Outputs JSON to stdout:
{
  "titles": [ {"id": 0, "duration_s": 1548.0, "segments_count": 1,
               "title_group_tag": "A5", "output_file_name": "A5_t00.mkv",
               "video_aspect_ratio": "16:9", "video_size": "720x480",
               "verdict": "keep", "reason": null}, ... ],
  "feature_length_title_ids": [...],   # duration >= 50min, informational only
                                        # -- for the movie-disc dominant-title
                                        # narrowing in SKILL.md Stage 4 check 3,
                                        # which still needs bonus_content.handling
                                        # and the "ambiguous -> rip all" judgment
                                        # call this script does not make
  "confirmed_exclude_ids": [...],      # verdict startswith "exclude-"
  "ambiguous_ids": [...],              # verdict startswith "ambiguous-" --
                                        # check the evidence yourself, don't
                                        # exclude on this alone
  "field_confidence_note": "..."
}

Verdicts:
  "keep"                -- no exclusion signal found.
  "exclude-concat"       -- both the segments_count and duration-sum signals
                            agree; safe to exclude per the doc's own stated
                            confirmation bar.
  "ambiguous-concat"     -- only one of the two signals matched; confirm by
                            hand before excluding.
  "exclude-duplicate"    -- near-identical duration to another title, the
                            title_group_tag matches, AND aspect ratio/
                            resolution match (rules out a flipper/WS-FS disc).
  "ambiguous-duplicate"  -- near-identical duration to another title but the
                            group-tag or format corroboration didn't line up
                            (or wasn't populated) -- check the evidence
                            yourself before excluding; see the confidence
                            note above re: the algorithm not yet having been
                            exercised against a genuine positive case.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass

TINFO_RE = re.compile(r'^TINFO:(\d+),(\d+),(-?\d+),"(.*)"$')
SINFO_RE = re.compile(r'^SINFO:(\d+),(\d+),(\d+),(-?\d+),"(.*)"$')

FIELD_CHAPTER_COUNT = 8         # community-standard convention, unconfirmed
FIELD_DURATION = 9              # CONFIRMED 2026-09-08 against real MakeMKV v1.18.4
                                 # output -- format is "H:MM:SS" with a non-zero-
                                 # padded hour (e.g. "0:50:03"), parse_duration()
                                 # below handles that fine via a plain split(":")
FIELD_SEGMENTS_COUNT = 25       # validated -- see identification-technique.md
FIELD_OUTPUT_FILE_NAME = 27     # CONFIRMED 2026-09-08 -- e.g. "A5_t00.mkv"; not
                                 # used directly (field 49 gives the same grouping
                                 # tag without string-parsing), kept as a parsed
                                 # field for corroboration/debugging only
FIELD_TITLE_GROUP_TAG = 49      # CONFIRMED 2026-09-08 -- MakeMKV's VTS/title-set
                                 # grouping tag (e.g. "A5", "D2", "E1"), matching
                                 # gotchas.md's already-documented "C1/D1/E1-style
                                 # output prefixes" signal exactly. This REPLACES
                                 # an earlier, wrong guess (field 16, hypothesized
                                 # as "SourceFileName") that does not appear at all
                                 # in real MakeMKV v1.18.4 output -- see this file's
                                 # module docstring for the correction.
FIELD_VIDEO_SIZE = 19           # SINFO, CONFIRMED 2026-09-08 (e.g. "720x480")
FIELD_VIDEO_ASPECT_RATIO = 20   # SINFO, CONFIRMED 2026-09-08 (e.g. "16:9")

TV_EPISODE_MIN_S = 15 * 60
TV_EPISODE_MAX_S = 50 * 60
FEATURE_LENGTH_MIN_S = 50 * 60


def parse_duration(value: str) -> float | None:
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = (float(p) for p in parts)
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


@dataclass
class Title:
    id: int
    duration_s: float | None = None
    segments_count: int | None = None
    chapter_count: int | None = None
    output_file_name: str | None = None
    title_group_tag: str | None = None
    video_size: str | None = None
    video_aspect_ratio: str | None = None


def parse_scan(raw_lines: list[str]) -> dict[int, Title]:
    titles: dict[int, Title] = {}
    for line in raw_lines:
        line = line.strip()
        m = TINFO_RE.match(line)
        if m:
            tid, attr, value = int(m.group(1)), int(m.group(2)), m.group(4)
            t = titles.setdefault(tid, Title(id=tid))
            if attr == FIELD_DURATION:
                t.duration_s = parse_duration(value)
            elif attr == FIELD_SEGMENTS_COUNT:
                try:
                    t.segments_count = int(value)
                except ValueError:
                    pass
            elif attr == FIELD_CHAPTER_COUNT:
                try:
                    t.chapter_count = int(value)
                except ValueError:
                    pass
            elif attr == FIELD_OUTPUT_FILE_NAME:
                t.output_file_name = value or None
            elif attr == FIELD_TITLE_GROUP_TAG:
                t.title_group_tag = value or None
            continue
        m = SINFO_RE.match(line)
        if m:
            tid, attr, value = int(m.group(1)), int(m.group(3)), m.group(5)
            t = titles.setdefault(tid, Title(id=tid))
            if attr == FIELD_VIDEO_SIZE and t.video_size is None:
                t.video_size = value or None
            elif attr == FIELD_VIDEO_ASPECT_RATIO and t.video_aspect_ratio is None:
                t.video_aspect_ratio = value or None
    return titles


def cluster_by_duration(titles: list[Title], tolerance: float) -> list[list[Title]]:
    """Group titles with near-identical duration (within `tolerance` fraction
    of the larger duration in each pairwise comparison)."""
    clusters: list[list[Title]] = []
    used: set[int] = set()
    sorted_titles = sorted((t for t in titles if t.duration_s), key=lambda t: t.duration_s)
    for t in sorted_titles:
        if t.id in used:
            continue
        group = [t]
        used.add(t.id)
        for other in sorted_titles:
            if other.id in used:
                continue
            if abs(other.duration_s - t.duration_s) <= tolerance * max(t.duration_s, other.duration_s):
                group.append(other)
                used.add(other.id)
        clusters.append(group)
    return clusters


def detect_concat_candidates(titles: list[Title]) -> dict[int, tuple[str, str]]:
    results: dict[int, tuple[str, str]] = {}
    clusters = cluster_by_duration(titles, tolerance=0.05)
    episode_clusters = [
        c for c in clusters
        if len(c) >= 2 and all(TV_EPISODE_MIN_S <= x.duration_s <= TV_EPISODE_MAX_S for x in c)
    ]
    for t in titles:
        if not t.duration_s:
            continue
        for cluster in episode_clusters:
            cluster_ids = {c.id for c in cluster}
            if t.id in cluster_ids:
                continue
            cluster_sum = sum(c.duration_s for c in cluster)
            if cluster_sum == 0:
                continue
            duration_match = abs(t.duration_s - cluster_sum) <= 0.03 * cluster_sum
            segments_match = t.segments_count is not None and t.segments_count == len(cluster)
            if duration_match and segments_match:
                results[t.id] = (
                    "exclude-concat",
                    f"duration ({t.duration_s:.0f}s) matches the summed duration of a "
                    f"{len(cluster)}-title episode-shaped cluster ({cluster_sum:.0f}s), and "
                    f"segments_count={t.segments_count} matches that episode count",
                )
            elif duration_match or segments_match:
                results.setdefault(t.id, (
                    "ambiguous-concat",
                    f"only one of the two concat signals matched against a {len(cluster)}-title "
                    f"episode cluster (duration_match={duration_match}, segments_match={segments_match}) "
                    "-- confirm manually before excluding",
                ))
    return results


def detect_duplicate_candidates(titles: list[Title]) -> dict[int, tuple[str, str]]:
    results: dict[int, tuple[str, str]] = {}
    clusters = cluster_by_duration(titles, tolerance=0.02)
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        representative = min(cluster, key=lambda t: t.id)
        tags = {t.title_group_tag for t in cluster if t.title_group_tag}
        source_match = len(tags) == 1 and all(t.title_group_tag for t in cluster)
        aspects = {t.video_aspect_ratio for t in cluster if t.video_aspect_ratio}
        sizes = {t.video_size for t in cluster if t.video_size}
        format_known = bool(aspects) or bool(sizes)
        same_format = len(aspects) <= 1 and len(sizes) <= 1

        # A same-duration cluster of 3+ titles all sitting in the normal TV-episode
        # range is the EXPECTED shape of a real season disc (most episodes on a
        # season disc run about the same length) -- not evidence of duplication.
        # Without this guard, every normal multi-episode disc would flag every
        # episode as an "ambiguous-duplicate" of every other same-length episode,
        # which is exactly the kind of alarm-fatigue false positive that would
        # train Claude to stop trusting this signal. Only act on a cluster this
        # size if source_file_name evidence actually contradicts "these are
        # distinct episodes" -- duration proximity alone isn't suspicious here the
        # way it is for a movie disc (which should only ever have one feature-length
        # title) or a small 2-title cluster.
        is_plausible_episode_set = (
            len(cluster) >= 3
            and all(TV_EPISODE_MIN_S <= t.duration_s <= TV_EPISODE_MAX_S for t in cluster)
        )
        if is_plausible_episode_set and not source_match:
            continue

        for t in cluster:
            if t.id == representative.id:
                continue
            if source_match and format_known and same_format:
                results[t.id] = (
                    "exclude-duplicate",
                    f"shares title_group_tag ({t.title_group_tag!r}) and near-identical "
                    f"duration/aspect-ratio/resolution with title {representative.id} -- "
                    f"keeping {representative.id} as the representative",
                )
            elif format_known and not same_format:
                results[t.id] = (
                    "ambiguous-duplicate",
                    f"near-identical duration to title {representative.id} but aspect ratio/"
                    "resolution differ -- likely a flipper disc or dual widescreen/fullscreen "
                    "authoring, NOT a duplicate; do not exclude without checking manually",
                )
            else:
                results[t.id] = (
                    "ambiguous-duplicate",
                    f"near-identical duration to title {representative.id} but title_group_tag "
                    "and/or format fields didn't corroborate (or weren't populated) -- confirm "
                    "manually before excluding",
                )
    return results


def run_scan(makemkv_path: str, disc_index: int) -> list[str]:
    try:
        proc = subprocess.run(
            [makemkv_path, "-r", "info", f"disc:{disc_index}"],
            capture_output=True, text=True, timeout=300,
        )
    except FileNotFoundError:
        print(json.dumps({"error": f"could not run makemkvcon at '{makemkv_path}'"}))
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(json.dumps({"error": "makemkvcon info scan timed out after 300s"}))
        sys.exit(1)
    if "TINFO:" not in proc.stdout:
        print(json.dumps({
            "error": f"makemkvcon exited {proc.returncode} with no TINFO output -- is a disc "
                     f"actually loaded in drive {disc_index}?",
            "stderr": proc.stderr[-2000:],
        }))
        sys.exit(1)
    return proc.stdout.splitlines()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disc-index", type=int, help="drive index to scan (`-r info disc:N`)")
    ap.add_argument("--makemkv-path", default="makemkvcon")
    ap.add_argument("--input-file", help="parse a previously-saved scan instead of invoking makemkvcon ('-' for stdin)")
    args = ap.parse_args()

    if args.input_file:
        if args.input_file == "-":
            raw = sys.stdin.read().splitlines()
        else:
            with open(args.input_file, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read().splitlines()
    elif args.disc_index is not None:
        raw = run_scan(args.makemkv_path, args.disc_index)
    else:
        print(json.dumps({"error": "must pass --disc-index or --input-file"}))
        sys.exit(2)

    titles_by_id = parse_scan(raw)
    titles = list(titles_by_id.values())
    if not titles:
        print(json.dumps({
            "error": "no TINFO lines parsed -- is this really a per-disc info scan "
                     "(`-r info disc:N`), not the drive-enumeration one (`disc:9999`)?"
        }))
        sys.exit(1)

    verdicts: dict[int, tuple[str, str]] = {}
    verdicts.update(detect_concat_candidates(titles))
    for tid, v in detect_duplicate_candidates(titles).items():
        verdicts.setdefault(tid, v)

    feature_length_ids = [t.id for t in titles if t.duration_s and t.duration_s >= FEATURE_LENGTH_MIN_S]

    out_titles = []
    for t in sorted(titles, key=lambda t: t.id):
        verdict, reason = verdicts.get(t.id, ("keep", None))
        out_titles.append({
            "id": t.id,
            "duration_s": t.duration_s,
            "segments_count": t.segments_count,
            "title_group_tag": t.title_group_tag,
            "output_file_name": t.output_file_name,
            "video_aspect_ratio": t.video_aspect_ratio,
            "video_size": t.video_size,
            "verdict": verdict,
            "reason": reason,
        })

    print(json.dumps({
        "titles": out_titles,
        "feature_length_title_ids": feature_length_ids,
        "confirmed_exclude_ids": [t["id"] for t in out_titles if t["verdict"].startswith("exclude-")],
        "ambiguous_ids": [t["id"] for t in out_titles if t["verdict"].startswith("ambiguous-")],
        "field_confidence_note": (
            "All field numbers this script relies on (duration, segments_count, the "
            "title_group_tag, video_size, video_aspect_ratio) are confirmed against real "
            "MakeMKV v1.18.4 output as of 2026-09-08 -- see this script's module docstring. "
            "What is NOT yet validated is the duplicate-detection algorithm's end-to-end "
            "behavior against a genuine duplicate-source or concat disc (the confirming run "
            "had neither). Treat 'ambiguous-*' as 'check the evidence yourself', and treat a "
            "first real 'exclude-duplicate'/'exclude-concat' verdict as worth double-checking "
            "by hand before trusting it unattended on future runs."
        ),
    }, indent=2))


if __name__ == "__main__":
    main()
