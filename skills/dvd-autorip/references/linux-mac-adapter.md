# Linux/Mac adapter

`scripts/platform/linux/*.sh` implements the same five jobs as
`scripts/platform/windows/*.ps1` — drive discovery, launch, monitor, eject, and the
USB-notification toggle (a no-op here, see below) — for Linux and Mac. The directory is
still named `linux/` for historical reasons (it started as a stub for Linux only); the
scripts inside are the validated adapter for `Darwin` (Mac) too, and
`check_dependencies.py` treats both OSes identically.

**This adapter is ported directly from the Windows logic, not independently run
against real hardware.** Every one of this pipeline's 196+ real-disc tests ran on
Windows. The method — the `makemkvcon -r info disc:9999` command, the `DRV:` line
parsing, the CPU-time-flat hang detection, the MSG:500x-family log outcome parsing, the
process-list JSON merge convention — is identical to the Windows scripts and should
port cleanly, since it's the same `makemkvcon` binary doing the actual work either way.
But "should port cleanly" isn't "watched succeeding on real hardware" — treat a
Linux/Mac run with the same "verify before trusting" posture this pipeline's whole
design philosophy applies to any new code path.

## Requirements

- **bash 4+.** The scripts use associative arrays (`declare -A`) in `monitor_rips.sh`.
  macOS ships bash 3.2 by default (Apple stopped updating it for licensing reasons) —
  install a current one with `brew install bash` and make sure it's what actually runs
  when the skill invokes `bash scripts/platform/linux/*.sh`.
  `check_dependencies.py`'s `bash` check flags an old version.
- **`eject`** (Linux only — the `util-linux` package's CLI tool). Mac doesn't need it;
  `eject.sh` uses the always-present `drutil`/`diskutil` there instead.
  `check_dependencies.py` checks for this only on Linux.
- **python3** — already a hard requirement of this whole skill. The bash scripts
  deliberately delegate all JSON encoding/decoding and process-table reading to small
  python3 invocations rather than hand-rolling that logic in bash — the same "never
  hand-build JSON" discipline the Windows scripts follow with PowerShell's own
  `ConvertTo-Json`/`ConvertFrom-Json`, and for the same reason: a volume label
  containing a literal quote or apostrophe has broken hand-built JSON in this
  pipeline's history before (see `gotchas.md`).

## What's weaker here than the Windows adapter

- **Eject verification.** Windows' `eject.ps1` confirms a real tray-open via
  `Get-Volume` (empty label / zero size). There's no single cross-distro API
  equivalent. `eject.sh` falls back to checking that `blkid <device>` no longer finds
  media as a proxy — reasonable, but it can't distinguish "tray genuinely open" from
  "tray closed but empty" the way `Get-Volume` can, and `blkid` itself may not be
  installed on every distro (in which case the script just trusts `eject`'s own exit
  code). Treat a "verified" eject result here with less confidence than the Windows
  equivalent, especially on an unfamiliar distro.
- **USB-notification suppression is a genuine no-op.**
  `toggle_usb_notifications.ps1` suppresses a Windows-specific shell toast — not a real
  hardware behavior. There's nothing to suppress on Linux/Mac, so
  `toggle_usb_notifications.sh` just prints a message and exits 0. This isn't a gap to
  fix later, it's the correct behavior for this OS.
- **Memory-pressure behavior** documented in `gotchas.md` was characterized on the
  specific Windows machine these 196+ tests ran on. A Linux/Mac box's real headroom and
  failure signature under 2+ parallel `makemkvcon` processes is unknown — don't assume
  the same thresholds apply.

## Testing note (Git Bash on Windows)

If you're exercising these scripts on Windows via Git Bash rather than on real
Linux/Mac hardware (e.g. to validate script logic before a real Linux/Mac run), two
Windows/MSYS-specific quirks can surface that are **not** real issues on genuine
Linux/Mac and don't need fixing there:

- **PATH entries must be POSIX-style** (`/c/some/dir`, not `C:/some/dir` or
  `M:/some/dir`) for bash's `command -v`/PATH lookup to find an executable — but a
  *native* Windows `python3.exe` (including the Microsoft Store alias) can't open a
  POSIX-style file path at all. If you're constructing test fixtures under Git Bash,
  keep the PATH directory POSIX-style and pass data paths (staging root, process-list
  path, etc.) in Windows form (`M:/...` or `C:/...`) — real Linux/Mac has no such split
  since there's only one path convention.
- **A native Windows python3 writing to stdout in text mode emits CRLF**, which can
  leave a trailing `\r` on the last field of a line read via bash's `read`. The scripts
  here already guard against this defensively (see `monitor_rips.sh`'s field-parsing
  loop) since it's a cheap, always-safe strip — but it's a Windows-Python artifact, not
  something a real Linux/Mac python3 (which writes plain `\n`) would ever produce.

## If you find a real issue running this on actual Linux/Mac hardware

Open a PR (or tell the maintainer) rather than silently patching around it — this
pipeline's whole design philosophy is "don't trust a code path you haven't actually
watched succeed on real hardware," and a Linux/Mac disc-ripping session is exactly the
kind of real-world validation this adapter still needs.
