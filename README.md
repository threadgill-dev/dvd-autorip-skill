# dvd-autorip

A Claude Code skill/plugin that rips a DVD via [MakeMKV](https://www.makemkv.com/),
independently identifies its real content, places the file(s) into a
[Jellyfin](https://jellyfin.org/) library, and corrects Jellyfin's metadata via direct
API writes — because Jellyfin's own fuzzy title/year search produces confidently wrong
matches often enough (misidentified movies, wrong TMDB collections pulled in by a
same-titled unrelated franchise) that it should never be the thing that decides.

Built from ~200 real disc-ripping sessions' worth of accumulated technique — not a
theoretical design. See `skills/dvd-autorip/references/` for the full identification
method, the parallel-ripping design, and a catalog of gotchas discovered the hard way.

## What this is (and isn't)

**This is not a fire-and-forget script.** DVD authoring varies enough in practice
(bonus content, concatenated multi-episode titles, decoy metadata candidates, franchise
title collisions, physically damaged discs) that the identification stage is designed
to run with Claude actively reasoning through each disc — subtitle/dialogue matching,
web search, cross-referencing your existing library — not a fixed decision tree. Only
the rip and eject steps, which have needed zero judgment across hundreds of real discs,
are fully automated (and, with two or more optical drives, run in parallel).

## Status

Windows-first, with a Linux/Mac adapter. The pipeline logic is plain Python; anything
OS-specific (drive discovery, ripping, eject, hang detection) lives behind a
`scripts/platform/` adapter boundary. The Windows adapter (`scripts/platform/windows/`)
is validated across 196+ real discs. The Linux/Mac adapter
(`scripts/platform/linux/`) is ported directly from that validated logic but has not
been independently run against real hardware — see
`skills/dvd-autorip/references/linux-mac-adapter.md` for exactly what's implemented and
what's weaker than the Windows equivalent.

## Requirements

Run `python skills/dvd-autorip/scripts/check_dependencies.py` to check your machine.
At minimum: Windows, Linux, or Mac; [MakeMKV](https://www.makemkv.com/) (the
`makemkvcon` CLI ships with it); [ffmpeg/ffprobe](https://ffmpeg.org/); PowerShell
(Windows) or bash 4+ (Linux/Mac); Python 3.8+; the `eject` CLI tool on Linux
specifically (Mac uses the always-present `drutil`/`diskutil` instead, Windows needs
nothing extra); and a running Jellyfin server.
[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) is optional — without
it, discs with only image-based subtitles fall back to frame-based visual
identification instead of OCR'd dialogue text.

## Setup

No config ships with real values — the skill asks you for what it needs (Jellyfin URL
and API key, your library folder paths, and what to do with bonus/extra content like
deleted scenes and featurettes — keep as Jellyfin Special Features, discard, or ask
each run) the first time you run it, and writes them to a gitignored
`config/config.local.json`. See
`skills/dvd-autorip/references/config-schema.md` for every field if you'd rather set it
up by hand from `config/config.example.json`.

## Installing

This repo is a Claude Code plugin (`.claude-plugin/plugin.json` at the root).

**Persistent install (recommended)** — available in every session on your machine
afterward, no flag needed each time:

```
claude plugin marketplace add threadgill-dev/dvd-autorip-skill
claude plugin install dvd-autorip@dvd-autorip-skill
```

Add `-s project` instead of the default user scope if you only want it available in
one project. If you cloned this repo locally instead of installing straight from
GitHub, point `marketplace add` at your local checkout path (`./path/to/repo`) instead
of `owner/repo` — a checkout on a network-mapped drive additionally needs declaring
under `extraKnownMarketplaces` in your user `settings.json` first (`claude plugin
marketplace add` reports the exact error and where to add it if this applies to you).

**One-off, session-only** — no install step, doesn't persist to future sessions:

```
claude --plugin-dir <path-to-this-repo>
```

See [Claude Code's plugin docs](https://code.claude.com/docs/en/plugins.md) for the
full plugin/marketplace reference.

**Launch in `dontAsk` permission mode, not `auto`** — `claude ... --permission-mode
dontAsk`. Auto mode silently drops this skill's `allowed-tools` rules and routes
every Bash/PowerShell call through a general-purpose classifier that doesn't
recognize DVD-ripping-specific commands, which can revert an entire session to full
manual prompting. See `skills/dvd-autorip/SKILL.md`'s "Launch this skill in
`dontAsk` permission mode" section for why.

## License

MIT — see `LICENSE`.
