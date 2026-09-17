# References

Deep-dive docs for `dvd-autorip`, kept out of `SKILL.md` itself so the operational
checklist there stays lean and only pulls these in when actually needed. Everything
here was written from real disc-ripping sessions, not designed on paper first.

- **[identification-technique.md](identification-technique.md)** — the core of the
  pipeline (Stage 7): the adaptive evidence-gathering procedure, the hard rule that a
  TV disc never spans seasons, the disc-level episode checklist, why a suspected
  duplicate encode is never auto-deleted, and how identification adapts when there's
  no media server at all (`media_server.type: "none"`).
- **[parallel-ripping.md](parallel-ripping.md)** — the multi-drive design: what's safe
  to run concurrently, the memory/collision considerations, and why eject + the
  completion signal are held until the whole batch is truly done.
- **[bonus-content.md](bonus-content.md)** — how deleted scenes, featurettes, and
  trailers get classified and handled per `bonus_content.handling`
  (`discard`/`keep`/`ask`), including the pre-rip movie-disc narrowing exception.
- **[config-schema.md](config-schema.md)** — every `config.local.json` field, what's
  required vs. optional, and what changes under `media_server.type: "none"`.
- **[dependency-install.md](dependency-install.md)** — Stage 1's install-offer flow
  for a missing tool: when to offer, when to hard-stop, when a decline gets
  remembered vs. asked again.
- **[linux-mac-adapter.md](linux-mac-adapter.md)** — what's implemented in
  `scripts/platform/linux/`, ported directly from the validated Windows logic but
  **not independently run against real hardware** — read this before relying on it.
- **[gotchas.md](gotchas.md)** — catalog of everything found the hard way across real
  discs and real runs, grouped by area. Start here if something's behaving
  unexpectedly; it's the project's incident log.
