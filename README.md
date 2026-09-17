# dvd-autorip

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-plugin-5A32FB)](https://code.claude.com/docs/en/plugins.md)

A Claude Code skill/plugin that rips a DVD via [MakeMKV](https://www.makemkv.com/),
**independently identifies its real content** (dialogue, subtitles, disc menus — never
just a fuzzy title/year guess), and places correctly-named files into your media
library. Works with [Jellyfin](https://jellyfin.org/) (writes corrected metadata
directly via its API), [Plex](https://www.plex.tv/), or no media server at all.

Built from ~200 real disc-ripping sessions' worth of accumulated technique — not a
theoretical design. Every gotcha in `skills/dvd-autorip/references/gotchas.md` was
found by actually hitting it on a real disc.

## Contents

- [Why this exists](#why-this-exists)
- [What this is (and isn't)](#what-this-is-and-isnt)
- [Recommended model](#recommended-model)
- [Status](#status)
- [Requirements](#requirements)
- [Installing](#installing)
- [Setup](#setup)
- [License](#license)

## Why this exists

Jellyfin's own fuzzy title/year search produces confidently wrong matches often enough
(misidentified movies, wrong TMDB collections pulled in by a same-titled unrelated
franchise) that it should never be the thing that decides what a ripped disc actually
is. A couple of real examples from this skill's own use:

- A season-3 disc's episodes were consistently identified one number off from what
  Jellyfin's own metadata provider expected, because that provider's data has
  "Episode22" at E22 instead of its usual chronological slot — invisible unless you
  check the actual episode content against what got assumed.
- "a well-known literary detective" alone has roughly ten separate TMDB entries for different
  actors/eras; picking the top search hit by title match alone reliably picks the
  wrong one.

This skill treats the disc's actual audio/video/subtitle content as the source of
truth, and only writes to a media server's metadata after that's independently
confirmed — never before.

**Jellyfin is optional** (`media_server.type: "none"` in config) — without it, the
skill still rips, identifies, and places correctly-named files into your library
folders, just without the Jellyfin-specific steps (auto-correct, refresh, post-write
verification). This is also the recommended mode for a **Plex** library: nothing here
talks to Plex's API directly (that would be unvalidated, unlike the Windows rip
pipeline — see `skills/dvd-autorip/references/gotchas.md`), but Plex's own scanner
picks up correctly-named, correctly-organized files on its own, and its built-in
matching is generally trusted more than Jellyfin's fuzzy matcher anyway — arguably
making Plex users a better fit for `"none"` mode than Jellyfin users are.

## What this is (and isn't)

**This is not a fire-and-forget script.** DVD authoring varies enough in practice
(bonus content, concatenated multi-episode titles, decoy metadata candidates, franchise
title collisions, physically damaged discs) that the identification stage is designed
to run with Claude actively reasoning through each disc — subtitle/dialogue matching,
web search, cross-referencing your existing library — not a fixed decision tree. Only
the rip and eject steps, which have needed zero judgment across hundreds of real discs,
are fully automated (and, with two or more optical drives, run in parallel).

## Recommended model

**Sonnet 5 or later, effort `high`.** Stage 7's identification is real judgment, not
a lookup — weighing conflicting evidence, catching a non-contiguous episode ordering,
knowing when a duplicate-looking title actually isn't — and this repo's own
development and hardening has all been done on Sonnet 5 at `effortLevel: high`
(this is a Claude Code setting: `/model` and `/effort`, or `modelSettings` in
`settings.json`). The fully-mechanized stages (rip, eject, dependency/config checks)
are plain scripts and don't need a strong model at all; it's specifically the
identification stage this recommendation is for. Not evaluated against a
lower-effort setting or a smaller model — this is what it's actually been built and
run on, not a guess at a minimum. The skill itself checks this live at the start of
every run (Stage 1 in `skills/dvd-autorip/SKILL.md`) and asks about upgrading if the
running session falls short — this section is just context for that, not the
mechanism itself.

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
nothing extra); and, unless you're running in `media_server.type: "none"` mode (see
[Setup](#setup) below), a running Jellyfin server.
[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) is optional — without
it, discs with only image-based subtitles fall back to frame-based visual
identification instead of OCR'd dialogue text.

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

## Setup

No config ships with real values — the skill asks you for what it needs the first
time you run it, and writes it to `~/.claude/dvd-autorip-skill/config.local.json` — a
fixed path outside this repo entirely, deliberately, so a plugin update never goes
stale against it or discards it (see `config-schema.md` for why). First question is
whether you have a Jellyfin server at all (`media_server.type`) — say "none" if you
don't, or if you're on Plex — then, only if you said Jellyfin: URL and API key.
Either way it also asks for your library folder paths, and what to do with
bonus/extra content like deleted scenes and featurettes (keep as Special Features,
discard, or ask each run). See `skills/dvd-autorip/references/config-schema.md` for
every field if you'd rather set it up by hand from `config/config.example.json`.

Once configured, just say something like "rip this DVD" or "run the autorip pipeline"
with a disc loaded — see `skills/dvd-autorip/SKILL.md` for the full stage-by-stage
pipeline, and `skills/dvd-autorip/references/` (start at its own
[README](skills/dvd-autorip/references/README.md)) for the identification technique,
parallel-ripping design, and gotcha catalog behind it.

## Known gaps and ideas

Bug reports and feature requests: [Issues](https://github.com/threadgill-dev/dvd-autorip-skill/issues);
general questions: [Discussions](https://github.com/threadgill-dev/dvd-autorip-skill/discussions).

## License

MIT — see `LICENSE`.
