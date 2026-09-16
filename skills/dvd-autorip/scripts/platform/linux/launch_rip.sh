#!/usr/bin/env bash
# Stage 4 -- launch a MakeMKV rip against one drive, detached, and return its PID.
#
# Mirrors scripts/platform/windows/launch_rip.ps1: same makemkvcon invocation, same
# staging-path-suffixed-with-drive-index collision fix (two drives can hold
# identically/generically-labeled discs, or a retried disc could collide with its own
# prior staging folder), same --process-list-path safe JSON merge convention -- the
# merge is a real read-modify-write round trip via python3, never hand-built JSON
# string concatenation (a volume label with a literal quote/apostrophe could break a
# hand-built version).
#
# Never launch more than one instance against the same drive index at once.
#
# Ported directly from the validated Windows logic -- NOT independently run against
# real hardware. Parallel multi-drive behavior should be identical (same makemkvcon
# binary) but has not actually been watched succeed here.
#
# Usage: launch_rip.sh --drive-index N --volume-label LABEL --staging-root PATH
#          [--min-length-seconds N] [--makemkv-path PATH] [--process-list-path PATH]
#          [--title-ids "0,1,2,3"]
# Output: JSON object to stdout: {"driveIndex":N,"processId":PID,"stagingPath":"...",
#          "logPath":"..."} (always printed, regardless of --process-list-path)
#
# --title-ids: rip only these specific titles instead of `all` -- use when a pre-rip
# scan confirmed a Play-All/concat title on this disc (see
# identification-technique.md), passing every real title id except the concat one.
# MakeMKV's CLI has no multi-title-select syntax (confirmed:
# https://forum.makemkv.com/forum/viewtopic.php?t=17435, a longstanding open feature
# request) -- only a single id or `all` per invocation. To stay at one background
# process/PID per drive (so monitor_rips.sh's model doesn't need to change), this
# generates a small runner script that loops through the given ids sequentially,
# calling makemkvcon once per title, and backgrounds *that* -- not N separate
# processes. Values are written into the runner via `printf '%q'` (shell-safe
# escaping), never raw string interpolation -- a path or label containing a quote
# could otherwise break a hand-built script the same way earlier hand-built-JSON bugs
# did in this pipeline's history (see gotchas.md).

set -euo pipefail

MIN_LENGTH_SECONDS=120
MAKEMKV_PATH="makemkvcon"
PROCESS_LIST_PATH=""
DRIVE_INDEX=""
VOLUME_LABEL=""
STAGING_ROOT=""
TITLE_IDS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --drive-index) DRIVE_INDEX="$2"; shift 2 ;;
        --volume-label) VOLUME_LABEL="$2"; shift 2 ;;
        --staging-root) STAGING_ROOT="$2"; shift 2 ;;
        --min-length-seconds) MIN_LENGTH_SECONDS="$2"; shift 2 ;;
        --makemkv-path) MAKEMKV_PATH="$2"; shift 2 ;;
        --process-list-path) PROCESS_LIST_PATH="$2"; shift 2 ;;
        --title-ids) TITLE_IDS="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$DRIVE_INDEX" || -z "$VOLUME_LABEL" || -z "$STAGING_ROOT" ]]; then
    echo "Usage: launch_rip.sh --drive-index N --volume-label LABEL --staging-root PATH [--min-length-seconds N] [--makemkv-path PATH] [--process-list-path PATH]" >&2
    exit 1
fi

if ! command -v "$MAKEMKV_PATH" >/dev/null 2>&1; then
    echo "Cannot find makemkvcon at '$MAKEMKV_PATH'. This is not a rip failure -- MakeMKV was never launched. Fix: pass the real path via --makemkv-path (check config.local.json's makemkv.path, or re-run check_dependencies.py to rediscover it)." >&2
    exit 1
fi

safe_label="$(printf '%s' "$VOLUME_LABEL" | tr -d '\\/:*?"<>|' | sed 's/^ *//;s/ *$//')"
[[ -z "$safe_label" ]] && safe_label="disc"

staging_path="${STAGING_ROOT%/}/${safe_label}__drive${DRIVE_INDEX}"
mkdir -p "$staging_path"

log_path="$staging_path/rip.log"
err_log_path="$staging_path/rip.err.log"

if [[ -n "$TITLE_IDS" ]]; then
    IFS=',' read -ra ids <<< "$TITLE_IDS"
    if [[ ${#ids[@]} -eq 0 ]]; then
        echo "--title-ids resolved to an empty list -- nothing to rip. Check the value passed ('$TITLE_IDS')." >&2
        exit 1
    fi

    runner_script="$staging_path/.rip_runner.sh"
    {
        printf '#!/usr/bin/env bash\n'
        printf 'MAKEMKV_PATH=%q\n' "$MAKEMKV_PATH"
        printf 'DRIVE_INDEX=%q\n' "$DRIVE_INDEX"
        printf 'DEST=%q\n' "$staging_path"
        printf 'MIN_LEN=%q\n' "$MIN_LENGTH_SECONDS"
        printf 'for id in'
        printf ' %q' "${ids[@]}"
        printf '; do\n'
        printf '  echo "=== Ripping title $id (disc:$DRIVE_INDEX) ==="\n'
        printf '  "$MAKEMKV_PATH" -r --decrypt --directio=true "--minlength=$MIN_LEN" mkv "disc:$DRIVE_INDEX" "$id" "$DEST"\n'
        printf 'done\n'
    } > "$runner_script"
    chmod +x "$runner_script"

    nohup bash "$runner_script" >"$log_path" 2>"$err_log_path" &
    proc_id=$!
    disown
else
    nohup "$MAKEMKV_PATH" -r --decrypt --directio=true "--minlength=$MIN_LENGTH_SECONDS" \
        mkv "disc:$DRIVE_INDEX" all "$staging_path" \
        >"$log_path" 2>"$err_log_path" &
    proc_id=$!
    disown
fi

python3 - "$DRIVE_INDEX" "$proc_id" "$staging_path" "$log_path" "$PROCESS_LIST_PATH" <<'PYEOF'
import json, os, sys

drive_index, proc_id, staging_path, log_path, process_list_path = sys.argv[1:6]
result = {
    "driveIndex": int(drive_index),
    "processId": int(proc_id),
    "stagingPath": staging_path,
    "logPath": log_path,
}

if process_list_path:
    existing = []
    if os.path.isfile(process_list_path):
        with open(process_list_path, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        # A single-element array and a bare object both need normalizing to a list --
        # mirrors launch_rip.ps1's @() normalization of ConvertFrom-Json's same quirk.
        existing = parsed if isinstance(parsed, list) else [parsed]
    existing.append(result)
    with open(process_list_path, "w", encoding="utf-8") as f:
        json.dump(existing, f)

print(json.dumps(result))
PYEOF
