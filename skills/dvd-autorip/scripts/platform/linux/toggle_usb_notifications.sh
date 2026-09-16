#!/usr/bin/env bash
# Stage 2 equivalent -- intentional no-op.
#
# scripts/platform/windows/toggle_usb_notifications.ps1 suppresses a Windows-specific
# shell toast ("USB Power Surge on Hub Port"). That's not a real hardware behavior, it's
# a Windows shell notification -- there's nothing equivalent to suppress on Linux/Mac.
# This script exists only so SKILL.md's Stage 2 call is valid on every platform without
# an OS-specific branch in the skill logic itself -- it always succeeds and does nothing.

set -euo pipefail

case "${1:-}" in
    --disable) echo "No-op on this OS -- Windows' USB-surge toast has no Linux/Mac equivalent (see references/linux-mac-adapter.md)." ;;
    --enable) echo "No-op on this OS -- nothing was suppressed to restore." ;;
    *) echo "Usage: toggle_usb_notifications.sh --disable|--enable" >&2; exit 1 ;;
esac
