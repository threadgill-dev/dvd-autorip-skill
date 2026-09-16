#!/usr/bin/env bash
# Stage 3 -- enumerate optical drives via MakeMKV's own robot-mode disc scan.
#
# Mirrors scripts/platform/windows/drive_discovery.ps1 exactly in method and output
# shape -- same `makemkvcon -r info disc:9999` command, same DRV: line parsing, same
# "non-empty drive name = real drive, non-empty volume label = disc loaded" heuristic
# validated across 196+ real discs (Windows). Only the last DRV: field's meaning
# differs: a Unix device path (e.g. /dev/sr0) instead of a Windows drive letter --
# everything else is identical, since MakeMKV's robot-mode output format doesn't change
# per OS. The output field is still called "driveLetter" (not renamed) so downstream
# logic that reads this JSON doesn't need an OS-specific field name.
#
# JSON encoding is delegated to python3 (already a hard requirement of this skill)
# rather than hand-built in bash -- see gotchas.md for why hand-built JSON has bitten
# this pipeline before (an unescaped apostrophe in a volume label).
#
# Ported directly from the validated Windows logic -- NOT independently run against
# real hardware. This pipeline's 196+ real-disc tests all ran on Windows; treat this
# script with a "verify before trusting" posture, same as any new code path.
#
# Usage: drive_discovery.sh [--makemkv-path PATH]
# Output: JSON array to stdout: [{"index":0,"driveName":"...","driveLetter":"/dev/sr0",
#          "volumeLabel":"...","mediaLoaded":true}, ...]

set -euo pipefail

MAKEMKV_PATH="makemkvcon"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --makemkv-path) MAKEMKV_PATH="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if ! command -v "$MAKEMKV_PATH" >/dev/null 2>&1; then
    echo "Cannot find makemkvcon at '$MAKEMKV_PATH'. Note that being 'found' by check_dependencies.py does not guarantee this -- that check also looks in a few common install locations beyond PATH. Fix: pass the real path via --makemkv-path, ideally the one already persisted in config.local.json's makemkv.path from Stage 1." >&2
    exit 1
fi

raw="$("$MAKEMKV_PATH" -r info disc:9999 2>&1 || true)"
if ! grep -q "DRV:" <<<"$raw"; then
    echo "makemkvcon did not return drive info. Is MakeMKV installed and on PATH, or is config.local.json's makemkv.path set correctly?" >&2
    exit 1
fi

python3 - "$raw" <<'PYEOF'
import json, re, sys

raw = sys.argv[1]
pattern = re.compile(r'^DRV:(\d+),(-?\d+),(-?\d+),(-?\d+),"([^"]*)","([^"]*)","([^"]*)"')

drives = []
for line in raw.splitlines():
    m = pattern.match(line)
    if not m:
        continue
    drive_name = m.group(5)
    volume_label = m.group(6)
    drive_letter = m.group(7)
    if not drive_name.strip():
        continue  # not a real drive entry
    drives.append({
        "index": int(m.group(1)),
        "driveName": drive_name,
        "driveLetter": drive_letter,
        "volumeLabel": volume_label,
        "mediaLoaded": bool(volume_label.strip()),
    })

print(json.dumps(drives))
PYEOF
