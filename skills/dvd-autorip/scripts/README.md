# Scripts

Every mechanized piece of the pipeline — deliberately just the parts that have needed
zero judgment across hundreds of real discs (rip, eject, dependency/config checks).
Identification itself (Stage 7) is **not** here — it's live Claude reasoning, driven
by `../references/identification-technique.md`, not a script. Usage for every script
below is documented at the point `../SKILL.md` calls it; this file is just an index
for browsing the repo.

## Cross-platform (this directory)

- **`check_dependencies.py`** — scans the machine for MakeMKV, ffmpeg/ffprobe,
  PowerShell/bash, Python, and (optional) Tesseract. PATH-first, with fallback
  install-location checks; never a single hardcoded path. Stage 1.
- **`detect_exclusions.py`** — pre-rip exclusion detection from MakeMKV's own disc
  info scan: concat/Play-All titles and duplicate-source titles, before wasting time
  ripping either. Stage 4.
- **`file_bonus_content.py`** — files "keep" bonus/extra content into the standard
  Special Features folder convention. Filesystem-only; not Jellyfin-specific. Stages
  8/10.
- **`jellyfin_api.py`** — thin Jellyfin API wrapper. Bakes in the auth-header
  fallback (`Authorization: MediaBrowser Token=...` → `X-Emby-Token`) and hard-stops
  on `Items/RemoteSearch/Episode`, which doesn't exist. Used for every Jellyfin call
  in the skill; not invoked at all under `media_server.type: "none"`.
- **`run_timer.py`** — tracks total wall-clock run time across a batch for Stage 13's
  closing message.

## `setup/`

- **`check_directory_scoping.py`** — offers and enables
  `permissions.blockReadsOutsideWorkingDirectories`, scoped to this plugin's own
  directory tree. Stage 1.
- **`validate_config.py`** — checks `config.local.json` exists, parses, and has every
  field required for the configured `media_server.type`. Never prompts, never
  contains real values.

## `platform/`

OS-specific adapters behind one boundary — drive discovery, rip launch/monitoring,
eject, USB-notification toggling. `platform/windows/*.ps1` is validated across 196+
real discs; `platform/linux/*.sh` is ported directly from that logic but **not
independently run against real hardware** — see `../references/linux-mac-adapter.md`
before relying on it.
