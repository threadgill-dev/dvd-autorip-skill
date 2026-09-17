#!/usr/bin/env bash
# Poll one or more in-flight rip processes (from launch_rip.sh), detect hangs, and
# report each one's real outcome once it exits.
#
# Mirrors scripts/platform/windows/monitor_rips.ps1: CPU-time-flat polling for hang
# detection (not a wall-clock timeout -- a genuinely hung process sits at ~0s CPU growth
# over a multi-minute window, while a healthy one keeps accumulating normally), and the
# same MSG:500x-family log parsing for the real per-title outcome rather than trusting
# exit code or an early-phase status code alone (some codes appear from the disc TOC
# scan well before a real failure later in the log).
#
# Requires bash 4+ (associative arrays) -- macOS ships bash 3.2 by default;
# check_dependencies.py flags this if the bash on PATH is too old. See
# references/linux-mac-adapter.md.
#
# Every intermediate exchange with python3 here passes data via argv, never by
# interpolating values into a python source string -- a detail message or volume label
# containing a quote/apostrophe would otherwise break exactly the way earlier
# hand-built-JSON bugs did in this pipeline's history (see gotchas.md).
#
# Ported directly from the validated Windows logic -- NOT independently run against
# real hardware.
#
# Usage: monitor_rips.sh --process-list-json PATH [--poll-interval-seconds N]
#          [--hang-threshold-minutes N] [--max-total-minutes N] [--log-path PATH]
# Output: JSON array to stdout once ALL processes have exited, OR once
# --max-total-minutes has elapsed, whichever comes first (outcome
# "still_running_timeout" for whatever's still pending in that case).
#
# --max-total-minutes (default 360 / 6h) is a hard ceiling on the whole polling loop,
# independent of hang detection above. Hang detection flags a suspicious process but
# keeps polling it forever -- there was no upper bound on the loop itself, so a
# process that never exits (hung or not) meant this script, and whatever was waiting
# on it, blocked indefinitely. A real caller waited 12+ hours on exactly this shape of
# thing (a different script, but the same unbounded-wait pattern) before anyone
# noticed. Reaching the ceiling doesn't kill anything, it just stops blocking the
# caller so a decision can actually get made.

set -euo pipefail

POLL_INTERVAL_SECONDS=60
HANG_THRESHOLD_MINUTES=3
MAX_TOTAL_MINUTES=360
LOG_PATH=""
PROCESS_LIST_JSON=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --process-list-json) PROCESS_LIST_JSON="$2"; shift 2 ;;
        --poll-interval-seconds) POLL_INTERVAL_SECONDS="$2"; shift 2 ;;
        --hang-threshold-minutes) HANG_THRESHOLD_MINUTES="$2"; shift 2 ;;
        --max-total-minutes) MAX_TOTAL_MINUTES="$2"; shift 2 ;;
        --log-path) LOG_PATH="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$PROCESS_LIST_JSON" ]]; then
    echo "Usage: monitor_rips.sh --process-list-json PATH [--poll-interval-seconds N] [--hang-threshold-minutes N] [--log-path PATH]" >&2
    exit 1
fi
[[ -z "$LOG_PATH" ]] && LOG_PATH="$(dirname "$PROCESS_LIST_JSON")/monitor.log"

results_file="${PROCESS_LIST_JSON}.results"
rm -f "$results_file"

log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S')  $1"
    echo "$line"
    echo "$line" >>"$LOG_PATH"
}

# Cumulative CPU seconds for $1, portable across Linux (/proc) and macOS (ps fallback).
cpu_seconds() {
    python3 - "$1" <<'PYEOF'
import os, subprocess, sys

pid = sys.argv[1]
stat_path = f"/proc/{pid}/stat"
if os.path.exists(stat_path):
    try:
        with open(stat_path) as f:
            fields = f.read().split()
        clk = os.sysconf("SC_CLK_TCK")
        print((int(fields[13]) + int(fields[14])) / clk)
    except (OSError, IndexError, ValueError):
        print(0)
else:
    try:
        out = subprocess.run(
            ["ps", "-p", pid, "-o", "time="], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        out = ""
    if not out:
        print(0)
    else:
        try:
            secs = 0.0
            for part in out.replace("-", ":").split(":"):
                secs = secs * 60 + float(part)
            print(int(secs))
        except (ValueError, IndexError):
            # macOS's ps -o time= prints fractional seconds (e.g. "0:04.11"),
            # unlike Linux's whole-second "01:02:03" -- confirmed via a real
            # crash report (int() rejecting "04.11"). float() handles that; this
            # guard is the same safety net the /proc branch above already has,
            # now applied here too so an unexpected future ps format can't take
            # the whole monitor down.
            print(0)
PYEOF
}

# Reads the exited process's log, classifies the real outcome, appends a record to
# $results_file (safe read-modify-write, same convention as launch_rip.sh's merge), and
# prints "outcome\tdetail" to stdout for the caller's log line.
record_exit() {
    python3 - "$1" "$2" "$3" "$4" "$5" "$6" <<'PYEOF'
import json, os, re, sys

results_file, drive_index, proc_id, elapsed_min, log_path, hung_flag = sys.argv[1:7]

try:
    with open(log_path, "r", errors="replace") as f:
        text = f.read()
except OSError:
    text = ""

# Count occurrences rather than checking only the first match -- a log can now
# contain multiple titles' worth of completion/failure codes back to back
# (launch_rip.sh's --title-ids mode calls makemkvcon once per title into the same
# log), and a single re.search only ever finds the first occurrence regardless of
# what happened for later titles. Any failure code anywhere in the combined log still
# wins over any success code -- conservative on purpose: a partial failure should
# surface as "failed", not get masked by an earlier or later title's success.
failure_count = (
    len(re.findall(r'MSG:5004,\d+,\d+,"0 titles saved', text))
    + len(re.findall(r'MSG:5003', text))
)
success_matches = re.findall(r'MSG:5036|MSG:5037', text)
copy_complete_matches = re.findall(r'Copy complete\.\s*(\d+) titles saved', text)

outcome, detail = "unknown", "no recognizable MSG:500x completion code found in log"
if failure_count > 0:
    outcome = "failed"
    detail = f"{failure_count} failure code occurrence(s) found (MSG:5003/5004) -- see log for which title(s)"
elif success_matches or copy_complete_matches:
    outcome = "success"
    if copy_complete_matches:
        total = sum(int(n) for n in copy_complete_matches)
        detail = f"{total} titles saved (across {len(copy_complete_matches)} completion message(s) in this log)"
    else:
        detail = f"{len(success_matches)} copy-complete message(s) found"

record = {
    "driveIndex": int(drive_index),
    "processId": int(proc_id),
    "exitCode": None,
    "elapsedMinutes": float(elapsed_min),
    "hungAtAnyPoint": hung_flag == "1",
    "outcome": outcome,
    "detail": detail,
}

existing = []
if os.path.isfile(results_file):
    with open(results_file) as f:
        existing = json.load(f)
existing.append(record)
with open(results_file, "w") as f:
    json.dump(existing, f)

print(f"{outcome}\t{detail}")
PYEOF
}

# Appends a "still_running_timeout" record directly (no log parsing -- the process is
# still running, so there's no outcome to read from its log yet).
record_timeout() {
    python3 - "$1" "$2" "$3" "$4" "$5" <<'PYEOF'
import json, os, sys

results_file, drive_index, proc_id, elapsed_min, hung_flag = sys.argv[1:6]

record = {
    "driveIndex": int(drive_index),
    "processId": int(proc_id),
    "exitCode": None,
    "elapsedMinutes": float(elapsed_min),
    "hungAtAnyPoint": hung_flag == "1",
    "outcome": "still_running_timeout",
    "detail": "max-total-minutes reached -- process was not killed, still running (or genuinely hung) as of this report",
}

existing = []
if os.path.isfile(results_file):
    with open(results_file) as f:
        existing = json.load(f)
existing.append(record)
with open(results_file, "w") as f:
    json.dump(existing, f)
PYEOF
}

declare -A last_cpu flat_polls hung start_epoch drive_of log_of
pending_pids=()
script_start_epoch=$(date +%s)

while IFS=$'\t' read -r drive_index proc_id staging_path log_path; do
    # Strip a stray trailing \r defensively -- a python3 build that writes stdout in
    # text mode (seen under native Windows Python during dev-testing of this script via
    # Git Bash) emits CRLF, and `read` only splits on IFS, not \r, so the last field on
    # each line would otherwise silently carry a trailing \r into a path/lookup key.
    log_path="${log_path%$'\r'}"
    staging_path="${staging_path%$'\r'}"
    last_cpu[$proc_id]=0
    flat_polls[$proc_id]=0
    hung[$proc_id]=0
    start_epoch[$proc_id]=$(date +%s)
    drive_of[$proc_id]=$drive_index
    log_of[$proc_id]=$log_path
    pending_pids+=("$proc_id")
done < <(python3 -c "
import json
with open('$PROCESS_LIST_JSON') as f:
    data = json.load(f)
entries = data if isinstance(data, list) else [data]
for e in entries:
    print(f\"{e['driveIndex']}\t{e['processId']}\t{e['stagingPath']}\t{e['logPath']}\")
")

hang_poll_threshold=$(( (HANG_THRESHOLD_MINUTES * 60 + POLL_INTERVAL_SECONDS - 1) / POLL_INTERVAL_SECONDS ))
[[ $hang_poll_threshold -lt 1 ]] && hang_poll_threshold=1

summary=""
for p in "${pending_pids[@]}"; do
    summary+="drive${drive_of[$p]}=PID${p} "
done
log "Monitoring ${#pending_pids[@]} rip process(es): ${summary}"

while [[ ${#pending_pids[@]} -gt 0 ]]; do
    elapsed_total_min=$(( ($(date +%s) - script_start_epoch) / 60 ))
    if [[ $elapsed_total_min -ge $MAX_TOTAL_MINUTES ]]; then
        log "WARNING: max-total-minutes ($MAX_TOTAL_MINUTES) reached with ${#pending_pids[@]} process(es) still running -- stopping polling without killing anything. Investigate manually; this is a real problem to look at, not something to keep waiting on."
        for proc_id in "${pending_pids[@]}"; do
            elapsed_min="$(python3 -c "print(round(($(date +%s) - ${start_epoch[$proc_id]}) / 60, 1))")"
            record_timeout "$results_file" "${drive_of[$proc_id]}" "$proc_id" "$elapsed_min" "${hung[$proc_id]}"
            log "drive${drive_of[$proc_id]} (PID $proc_id) still running after ${elapsed_min}m -- outcome: still_running_timeout"
        done
        pending_pids=()
        break
    fi
    sleep "$POLL_INTERVAL_SECONDS"
    still_pending=()

    for proc_id in "${pending_pids[@]}"; do
        if ! kill -0 "$proc_id" 2>/dev/null; then
            elapsed_min="$(python3 -c "print(round(($(date +%s) - ${start_epoch[$proc_id]}) / 60, 1))")"
            IFS=$'\t' read -r outcome detail <<<"$(record_exit "$results_file" "${drive_of[$proc_id]}" "$proc_id" "$elapsed_min" "${log_of[$proc_id]}" "${hung[$proc_id]}")"
            log "drive${drive_of[$proc_id]} (PID $proc_id) exited after ${elapsed_min}m -- outcome: $outcome ($detail)"
            continue
        fi

        cpu="$(cpu_seconds "$proc_id")"
        if [[ "$cpu" == "${last_cpu[$proc_id]}" ]]; then
            flat_polls[$proc_id]=$(( flat_polls[$proc_id] + 1 ))
            if [[ ${flat_polls[$proc_id]} -ge $hang_poll_threshold && ${hung[$proc_id]} -eq 0 ]]; then
                hung[$proc_id]=1
                log "WARNING: drive${drive_of[$proc_id]} (PID $proc_id) has not accumulated CPU time in ~${HANG_THRESHOLD_MINUTES}m -- possible hang. Check the other drive(s) independently before assuming they're also affected; kill only this PID if confirmed hung."
            fi
        else
            flat_polls[$proc_id]=0
        fi
        last_cpu[$proc_id]=$cpu

        still_pending+=("$proc_id")
    done

    pending_pids=("${still_pending[@]}")
done

if grep -q '"still_running_timeout"' "$results_file" 2>/dev/null; then
    log "Stopped polling early -- max-total-minutes reached, not all processes exited."
else
    log "All processes exited."
fi
if [[ -f "$results_file" ]]; then
    cat "$results_file"
    rm -f "$results_file"
else
    echo "[]"
fi
