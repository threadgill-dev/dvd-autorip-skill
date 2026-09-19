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
- **`check_library_duplicate.py`** — checks whether a confirmed movie (TMDB id) is
  already in the Jellyfin library, by fetching the movie list and matching
  client-side (the obvious server-side filter, `AnyProviderIdEquals`, doesn't work
  on this project's real server — confirmed live). Imports `jellyfin_api.py`
  directly rather than reimplementing its HTTP logic. Two call sites: Stage 4 check
  5 (before ripping, a discdb-confirmed movie) and Stage 8 (after ripping, a Stage
  7-identified movie) — movies only at either.
- **`detect_exclusions.py`** — pre-rip exclusion detection from MakeMKV's own disc
  info scan: concat/Play-All titles and duplicate-source titles, before wasting time
  ripping either. Stage 4.
- **`discdb_lookup.py`** — computes a mounted disc's TheDiscDB ContentHash directly
  from its `VIDEO_TS` folder and queries TheDiscDB's public GraphQL API for an
  exact-disc match with a pre-mapped title layout. Read-only, no MakeMKV involved.
  Stage 3.5.
- **`discdb_crosscheck.py`** — binds a `discdb_lookup.py` hit's title mapping to
  `detect_exclusions.py`'s real MakeMKV title list by nearest-duration match (not by
  assuming the same index — confirmed live that same-index binding fails badly when
  MakeMKV's title count doesn't match TheDiscDB's). Stage 4 check 4.
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
