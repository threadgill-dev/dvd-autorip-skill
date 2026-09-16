#!/usr/bin/env bash
# Stage 13 -- eject a drive's tray, with best-effort verification.
#
# Mirrors scripts/platform/windows/eject.ps1's intent (don't trust a silent "success"
# from the eject call alone) but verification is genuinely weaker here -- there's no
# single cross-distro/cross-OS API equivalent to Windows' Get-Volume for confirming a
# tray physically opened. See verify_ejected() below and
# references/linux-mac-adapter.md for exactly what is and isn't confirmed.
#
# Only call this at the very end of a real pipeline run for a given disc -- this script
# does not know whether it's safe to eject; the caller (the skill's Stage 13 checklist)
# is responsible for only invoking it once identification and placement are fully done.
#
# Ported directly from the validated Windows logic -- NOT independently run against
# real hardware.
#
# Usage: eject.sh --device PATH   (e.g. --device /dev/sr0)

set -euo pipefail

DEVICE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --device) DEVICE="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$DEVICE" ]]; then
    echo "Usage: eject.sh --device PATH (e.g. /dev/sr0)" >&2
    exit 1
fi

os_name="$(uname -s)"

do_eject() {
    if [[ "$os_name" == "Darwin" ]]; then
        if command -v drutil >/dev/null 2>&1; then
            drutil eject "$DEVICE" >/dev/null 2>&1 || drutil eject >/dev/null 2>&1 || true
        elif command -v diskutil >/dev/null 2>&1; then
            diskutil eject "$DEVICE" >/dev/null 2>&1 || true
        else
            echo "Neither drutil nor diskutil found -- can't eject on this Mac." >&2
            exit 1
        fi
    else
        if ! command -v eject >/dev/null 2>&1; then
            echo "The 'eject' command isn't installed (Linux). Install it via your package manager (e.g. apt install eject, dnf install util-linux) -- check_dependencies.py flags this too." >&2
            exit 1
        fi
        eject "$DEVICE"
    fi
}

# Best-effort only: absence of media (blkid finds nothing) is a reasonable proxy for
# "the tray opened or the disc unmounted," but unlike Windows' Get-Volume this can't
# reliably distinguish "tray open" from "tray closed but empty" on every distro/tool
# combination, and blkid itself may not be present. Treat a "verified" result here with
# less confidence than the Windows script's equivalent -- see
# references/linux-mac-adapter.md.
verify_ejected() {
    if command -v blkid >/dev/null 2>&1; then
        ! blkid "$DEVICE" >/dev/null 2>&1
    else
        return 0  # no way to check -- trust the eject command's own exit code
    fi
}

do_eject
sleep 3

if verify_ejected; then
    echo "Ejected $DEVICE (best-effort verified -- see references/linux-mac-adapter.md)."
    exit 0
fi

echo "First eject attempt on $DEVICE did not visibly take -- retrying once."
do_eject
sleep 3

if verify_ejected; then
    echo "Ejected $DEVICE on retry (best-effort verified)."
    exit 0
fi

echo "Could not verify $DEVICE ejected after two attempts. Check the drive manually before telling the user it's safe to load the next disc." >&2
exit 1
