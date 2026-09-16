#!/usr/bin/env python3
"""Tracks total wall-clock run time across a whole batch, for Stage 13's closing
message. A tiny dedicated script rather than an inline `python -c` one-liner in
SKILL.md prose -- a real one-liner attempt for this (an f-string with nested
braces/`%`/`//`) broke under PowerShell's quoting rules during testing, which is
exactly the class of cross-shell fragility this pipeline's scripts exist to avoid
putting into prose Claude has to reconstruct correctly by hand every run.

Usage:
    python run_timer.py start --staging-path <staging.path>
        Writes the current time into <staging.path>/run_start.txt. Run once, at
        Stage 3 (drive discovery), before any rip/identification work begins.

    python run_timer.py elapsed --staging-path <staging.path>
        Reads that file, computes elapsed time, deletes the file (a working
        artifact for one batch, same reasoning as rip_processes.json -- not
        meant to persist in staging between runs), and prints the result. Run
        once, at Stage 13, after eject and the USB-notification restore.

Outputs JSON to stdout:
  start:   {"ok": true, "started_at": <epoch float>}
  elapsed: {"ok": true, "elapsed_seconds": <float>, "formatted": "2h 14m 03s"}
  either:  {"ok": false, "error": "..."} on a missing/unreadable file
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

RUN_START_FILENAME = "run_start.txt"


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def cmd_start(staging_path: str) -> dict:
    os.makedirs(staging_path, exist_ok=True)
    path = os.path.join(staging_path, RUN_START_FILENAME)
    now = time.time()
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(now))
    return {"ok": True, "started_at": now}


def cmd_elapsed(staging_path: str) -> dict:
    path = os.path.join(staging_path, RUN_START_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            started_at = float(f.read().strip())
    except FileNotFoundError:
        return {"ok": False, "error": f"{path} not found -- was `run_timer.py start` run at Stage 3?"}
    except ValueError as e:
        return {"ok": False, "error": f"{path} did not contain a valid timestamp: {e}"}

    elapsed = time.time() - started_at
    try:
        os.remove(path)
    except OSError:
        pass  # not fatal -- the timing result itself is already computed

    return {"ok": True, "elapsed_seconds": elapsed, "formatted": format_duration(elapsed)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["start", "elapsed"])
    ap.add_argument("--staging-path", required=True)
    args = ap.parse_args()

    result = cmd_start(args.staging_path) if args.action == "start" else cmd_elapsed(args.staging_path)
    print(json.dumps(result))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
