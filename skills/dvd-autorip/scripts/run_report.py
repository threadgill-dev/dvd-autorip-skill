#!/usr/bin/env python3
"""Accumulates a structured record of one batch's outcome -- per-disc placements,
bonus-content handling, pre-rip exclusions, needs-review resolutions, and any
hiccups/errors along the way -- then renders it into Stage 13's formalized closing
report.

A file-backed accumulator, not something Claude tracks in its own head, for the
same reason `rip_processes.json` and `run_timer.py`'s own file are: a real batch can
run for hours, span disc swaps, and survive one or more context compactions, and a
summary built only from what's still in context at Stage 13 would silently lose
whatever got compacted away. Every stage that has something worth recording writes
it here as it happens; Stage 13 just reads it back.

Usage:
    python run_report.py start --staging-path <staging.path>
        Creates <staging.path>/run_report.json, empty. Run once, at Stage 3,
        alongside run_timer.py start.

    python run_report.py add-disc --staging-path <staging.path> --data '<json>'
        Appends one disc's outcome. Run once per disc, at Stage 12, right after
        that disc's own verification finishes (same point Stage 12 already deletes
        that disc's staging working files). --data is a JSON object:
          {"volume_label": "ANGEL_S5D5", "drive_index": 1,
           "placed": [{"filename": "...", "type": "movie"|"episode",
                       "season": 5, "episode": 16}, ...],
           "bonus": [{"filename": "...", "handling": "kept"|"discarded"|"deferred",
                      "subtype": "featurette"}, ...],
           "excluded_pre_rip": [{"title_id": 3, "reason": "concat/Play-All"}, ...],
           "needs_review": [{"description": "...", "resolution": "resolved"|
                             "deferred"|"discarded"}, ...]}
        Every key is optional -- omit whatever doesn't apply to this disc rather
        than sending an empty list for it; render treats a missing key the same as
        an empty one.

    python run_report.py add-issue --staging-path <staging.path> --stage "Stage 1"
        --severity warning|error --message "..."
        Appends one hiccup/error, freeform but structured. Call this whenever
        something in the run didn't go the way it should have and is worth the
        user seeing in the closing report, even though the run continued past it
        -- a dependency install that failed, a Jellyfin auth fallback, a disc read
        needing salvage, an unexpected retry. This is a judgment call each time,
        same as everything else in Stages 6-11 -- there's no mechanized trigger for
        what counts as worth logging.

    python run_report.py render --staging-path <staging.path> [--elapsed "2h 14m 03s"]
        Reads the accumulated file, renders the plain-text closing report, deletes
        the file (a working artifact for one batch, same reasoning as
        rip_processes.json -- not meant to persist in staging between runs), and
        prints {"ok": true, "report": "<full text>"}. Run once, at Stage 13, after
        `run_timer.py elapsed` -- pass its "formatted" value through as --elapsed.
        Relay the "report" string to the user verbatim as the closing message.

Outputs JSON to stdout, `{"ok": true, ...}` on success or `{"ok": false, "error":
"..."}` on a missing/unreadable file (start/render) or invalid --data/--message
JSON (add-disc). Exit code 0 iff "ok" is true.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPORT_FILENAME = "run_report.json"


def _report_path(staging_path: str) -> str:
    return os.path.join(staging_path, REPORT_FILENAME)


def _load(staging_path: str) -> dict | None:
    try:
        with open(_report_path(staging_path), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _save(staging_path: str, data: dict) -> None:
    with open(_report_path(staging_path), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def cmd_start(staging_path: str) -> dict:
    os.makedirs(staging_path, exist_ok=True)
    _save(staging_path, {"discs": [], "issues": []})
    return {"ok": True}


def cmd_add_disc(staging_path: str, data_json: str) -> dict:
    try:
        disc = json.loads(data_json)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"--data is not valid JSON: {e}"}
    if not isinstance(disc, dict) or "volume_label" not in disc:
        return {"ok": False, "error": "--data must be a JSON object with at least a 'volume_label' field"}

    report = _load(staging_path)
    if report is None:
        return {"ok": False, "error": f"{_report_path(staging_path)} not found -- was `run_report.py start` run at Stage 3?"}

    report["discs"].append(disc)
    _save(staging_path, report)
    return {"ok": True}


def cmd_add_issue(staging_path: str, stage: str, severity: str, message: str) -> dict:
    report = _load(staging_path)
    if report is None:
        return {"ok": False, "error": f"{_report_path(staging_path)} not found -- was `run_report.py start` run at Stage 3?"}

    report["issues"].append({"stage": stage, "severity": severity, "message": message})
    _save(staging_path, report)
    return {"ok": True}


def _format_disc(disc: dict) -> str:
    label = disc.get("volume_label", "(unknown volume)")
    drive = disc.get("drive_index")
    header = f"--- {label}" + (f" (drive {drive})" if drive is not None else "") + " ---"
    lines = [header]

    placed = disc.get("placed") or []
    if placed:
        lines.append(f"Placed ({len(placed)}):")
        for item in placed:
            lines.append(f"  - {item.get('filename', '(unnamed)')}")
    else:
        lines.append("Placed: none")

    bonus = disc.get("bonus") or []
    if bonus:
        lines.append(f"Bonus content ({len(bonus)}):")
        for item in bonus:
            subtype = f" [{item['subtype']}]" if item.get("subtype") else ""
            lines.append(f"  - {item.get('filename', '(unnamed)')}{subtype} -- {item.get('handling', 'unknown')}")
    else:
        lines.append("Bonus content: none")

    excluded = disc.get("excluded_pre_rip") or []
    if excluded:
        lines.append(f"Excluded pre-rip ({len(excluded)}):")
        for item in excluded:
            lines.append(f"  - title {item.get('title_id', '?')}: {item.get('reason', 'unspecified')}")
    else:
        lines.append("Excluded pre-rip: none")

    needs_review = disc.get("needs_review") or []
    if needs_review:
        lines.append(f"Needs review ({len(needs_review)}):")
        for item in needs_review:
            lines.append(f"  - {item.get('description', '(no description)')} -- {item.get('resolution', 'unresolved')}")
    else:
        lines.append("Needs review: none")

    return "\n".join(lines)


def _render(report: dict, elapsed: str | None) -> str:
    lines = ["=== DVD Autorip -- Run Summary ==="]
    if elapsed:
        lines.append(f"Runtime: {elapsed}")
    discs = report.get("discs", [])
    lines.append(f"Discs processed: {len(discs)}")
    lines.append("")

    for disc in discs:
        lines.append(_format_disc(disc))
        lines.append("")

    issues = report.get("issues", [])
    if issues:
        lines.append(f"=== Issues ({len(issues)}) ===")
        for issue in issues:
            lines.append(f"[{issue.get('severity', 'warning')}] {issue.get('stage', '?')}: {issue.get('message', '')}")
    else:
        lines.append("No hiccups or errors this run.")

    return "\n".join(lines)


def cmd_render(staging_path: str, elapsed: str | None) -> dict:
    report = _load(staging_path)
    if report is None:
        return {"ok": False, "error": f"{_report_path(staging_path)} not found -- was `run_report.py start` run at Stage 3?"}

    text = _render(report, elapsed)
    try:
        os.remove(_report_path(staging_path))
    except OSError:
        pass  # not fatal -- the rendered report is already computed

    return {"ok": True, "report": text}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["start", "add-disc", "add-issue", "render"])
    ap.add_argument("--staging-path", required=True)
    ap.add_argument("--data", help="add-disc: JSON object describing one disc's outcome")
    ap.add_argument("--stage", help="add-issue: which stage the issue happened in, e.g. 'Stage 1'")
    ap.add_argument("--severity", choices=["warning", "error"], help="add-issue: severity")
    ap.add_argument("--message", help="add-issue: free-text description")
    ap.add_argument("--elapsed", help="render: run_timer.py's formatted elapsed-time string")
    args = ap.parse_args()

    if args.action == "start":
        result = cmd_start(args.staging_path)
    elif args.action == "add-disc":
        if not args.data:
            result = {"ok": False, "error": "add-disc requires --data"}
        else:
            result = cmd_add_disc(args.staging_path, args.data)
    elif args.action == "add-issue":
        if not (args.stage and args.severity and args.message):
            result = {"ok": False, "error": "add-issue requires --stage, --severity, and --message"}
        else:
            result = cmd_add_issue(args.staging_path, args.stage, args.severity, args.message)
    else:
        result = cmd_render(args.staging_path, args.elapsed)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
