# Bonus/extra content handling

DVDs routinely carry more than the movie or episodes themselves: deleted scenes,
behind-the-scenes featurettes, trailers, interviews, alternate cuts. Left unhandled,
these get ripped alongside the real content (MakeMKV's `all` + `--minlength` only
filters out very short menu-loop fragments, not genuine multi-minute extras) and then
just sit in staging indefinitely once Stage 7 determines they aren't the movie/episode
— nothing in the pipeline used to resolve them one way or the other. `config.local.json`'s
`bonus_content.handling` setting controls what happens to them instead.

## Config

```json
"bonus_content": {
  "handling": "ask",       // "discard" | "keep" | "ask"
  "remember_choice": false
}
```

Both fields are optional — a missing `bonus_content` block behaves as
`{"handling": "ask", "remember_choice": false}`, the safest default (never silently
deletes real content, never silently restructures the library). Ask the user for this
preference during Stage 1 setup on a new machine, same conversational way as every
other config field.

- **`"discard"`** — bonus/extra content gets deleted from staging once Stage 7
  classifies it as bonus content (not placed anywhere). Permanent — the only way to
  get it back is re-ripping the disc. Tell the user this plainly when they're choosing
  this setting during Stage 1, and again in the Stage 10 prompt if `handling` is
  `"ask"` and they choose discard for a given run.
- **`"keep"`** — bonus/extra content gets filed into Jellyfin's real "Special
  Features" convention (see below) so it's actually organized and watchable, not left
  in staging forever.
- **`"ask"`** — resolved once per run, at Stage 10 — **before** the completion
  signal/eject (Stages 11-12), not after: eject is the real physical "you're done,
  safe to reload" signal, and it must never fire while a real question is still
  outstanding. If `remember_choice` is `true`, whatever the user answers gets
  written back into `config.local.json`'s `bonus_content.handling` (converting it to
  `"keep"` or `"discard"`) so future runs stop asking; if `false` (the default), the
  setting stays `"ask"` and every future run prompts again.

## What counts as "bonus content" — and what doesn't

**The Play-All/concat title on a TV disc is not bonus content and this setting doesn't
apply to it.** It's a pure duplicate of the individual episode titles already being
ripped separately — see `identification-technique.md`'s "Detecting a full-season
'concat' title before ripping it." Skipping it is unconditional good practice
regardless of `bonus_content.handling`, including under `"keep"` — keeping a redundant
concatenation of content already ripped individually serves no purpose.

**Genuine bonus/extra content** (deleted scenes, featurettes, behind-the-scenes,
trailers, interviews, alternate cuts) is a title Stage 7 has confidently determined is
*not* the movie/episode itself, but *does* have recognizable evidence of what it
actually is (on-screen "Deleted Scene" text, interview-style framing, a making-of
structure, etc.) — a real, positive identification, just not of the main feature.

**This is a different bucket from Stage 11's "needs review."** A title Stage 7 can't
confidently place in *either* category — not confirmed as the main content, but also
not confidently bonus content — stays exactly as it always has: untouched in staging,
presented to the user at Stage 11 with its evidence bundle. `bonus_content.handling`
never applies to a genuinely ambiguous item; only to one Claude can actually name as
bonus content. When in doubt, prefer Stage 11 (defer to a human) over guessing it's
bonus content and discarding/misfiling it.

## Pre-rip skipping — honest limits, and the one real exception

The user-facing framing of `"discard"` is "don't rip this at all if possible, delete
it afterward if not" — but **on a TV disc, pre-rip skipping is only reliably
possible for the Play-All/concat case above**, which already happens
unconditionally. Distinguishing a genuine bonus feature from a real (if short)
episode *before* ripping and identifying it is the same problem Stage 7 exists to
solve — there's no cheap, reliable pre-rip signal for "this title is a deleted scene,
not episode 4" the way there is for "this title is the concat of every episode."

`gotchas.md` documents one imperfect heuristic for the TV case: MakeMKV's own
VTS/title-set grouping (the `C1`/`D1`/`E1`-style output prefixes) plus a title being a
duration outlier from the disc's main-content cluster. Under `"discard"`, this can be
used as a best-effort pre-rip filter for *only the most obvious* TV cases — but when
it's not a strong, unambiguous signal, **rip it anyway** and let Stage 7's real
identification do the actual classification. Under-ripping risks silently losing a
real episode, which is a much worse failure than ripping a few extra minutes of
something that gets deleted shortly after.

**Movie discs are the deliberate exception** (SKILL.md's Stage 4, step 3) — the
duration-outlier signal is much more trustworthy there specifically because a movie
disc only ever has *one* file surviving to placement, so "is this title dramatically
shorter than the disc's one dominant title" doesn't have the TV case's ambiguity
between "short bonus content" and "short-but-real episode." Under `"discard"`, an
unambiguous movie-shaped disc (one clearly dominant feature-length title, the rest
clearly shorter) skips ripping the non-feature titles entirely rather than
ripping-then-discarding them — a real, confirmed waste this pipeline paid for once
already (a full duplicate feature-length rip on a multi-angle disc, kept in until
Stage 7 confirmed it was redundant). If two or more titles are independently
feature-length (a dual-cut release, a double-feature disc), that's genuinely
ambiguous — rip all the candidates and let Stage 7 resolve it, same discipline as the
TV case. This exception only applies under `"discard"`; `"keep"`/`"ask"` need the
titles actually ripped to do anything useful with them.

In practice, `"discard"`'s real mechanism is still the post-identification cleanup
below for TV discs and for any ambiguous movie disc — the pre-rip filter only
confidently fires for the one narrow, unambiguous movie-disc case above.

## Filing kept content: Jellyfin's real extras convention

Verified against Jellyfin's own docs (not guessed) — [Movies](https://jellyfin.org/docs/general/server/media/movies/),
[TV Shows](https://jellyfin.org/docs/general/server/media/shows/).

**`scripts/file_bonus_content.py` executes the mechanics below** — the folder
creation, file moves, and flat/foldered movie handling are deterministic once
Stage 7's subtype classification is decided, so the script owns the filesystem side
of this; you still decide *what* is bonus content and *what subtype* it is.

**Recognized subfolder names** (use these exact names, spaces not underscores —
`behind_the_scenes` is not recognized, `behind the scenes` is):
`behind the scenes`, `deleted scenes`, `interviews`, `scenes`, `samples`, `shorts`,
`featurettes`, `clips`, `other`, `extras`, `trailers`, `theme-music`, `backdrops`.
The script maps whatever subtype string you pass to the closest matching folder
name, falling back to the generic `extras` folder when it isn't an exact match to
one of these — don't force a specific-but-uncertain guess just to avoid using the
generic bucket.

### Movies — requires its own folder (a real structural difference from today)

**This library's movies are flat today** (`{movies_path}\{Title} ({Year}).mkv`, no
per-movie folder — see `config-schema.md`), and Jellyfin's extras convention requires
the movie to live in its own folder. Resolving this conflict, per an explicit user
decision (2026-08-29): **only movies that actually have bonus content to keep** get
moved into a per-movie folder; a movie with no bonus content (the common case) stays
exactly as flat as it always has. The library ends up with a mix of flat and foldered
movies — that's intentional, not a bug to "fix" later.

When a movie has bonus content to keep:
1. `python scripts/file_bonus_content.py movie --movies-path <library.movies_path>
   --title "<Title>" --year <Year> --movie-file <staging path, only if not already
   placed> --items '[{"path": "...", "subtype": "..."}, ...]'`. It creates
   `{movies_path}\{Title} ({Year})\`, moves the movie file in under the same
   filename, creates each subtype subfolder, moves each bonus item in, and never
   overwrites an existing destination — it errors instead, listing whatever *did*
   complete first so nothing is left ambiguous. Omit `--movie-file` when the movie is
   already placed flat and this run is only adding newly-found extras to it (the
   script finds and moves the existing flat file itself).
2. `POST /Library/Refresh` via `jellyfin_api.py` (full scan — moving the movie file
   is exactly the kind of rename/relocation a per-item `Refresh` doesn't detect, same
   gotcha as always).
3. Verify the movie still resolves to the same TMDB match it had before the move
   (Stage 9's auto-correct logic applies here too if anything drifted) — a full-path
   change is a real opportunity for Jellyfin's own fuzzy matching to kick in and get it
   wrong again if the explicit `RemoteSearch/Apply` isn't re-confirmed.

**Don't use the filename-suffix shortcut method** (`Movie (Year)-trailer.mp4` sitting
flat next to the movie) even though Jellyfin supports it — it was considered and
rejected: it only reliably supports one canonical extra per type, and a disc can
easily have multiple deleted scenes or multiple featurettes. The per-movie-folder
method above has no such limit and is what's actually built.

### TV shows — no structural change needed

The existing `{shows_path}\{Show}\Season N\` structure already gives extras a home:
`python scripts/file_bonus_content.py tv --shows-path <library.shows_path> --show
"<Show>" --season <N> --level season|series --items '[{"path": "...", "subtype":
"..."}, ...]'`. Use `--level season` (files under
`{shows_path}\{Show}\Season N\<subtype>\`) when the bonus content is specific to that
season/disc, or `--level series` (`{shows_path}\{Show}\<subtype>\`, alongside the
`Season N` folders, not inside one) for content that isn't season-specific (a general
making-of, a full-series trailer). Use judgment from the evidence — if unsure,
`season` is the safer default since almost everything on a season-disc set is
season-specific.

## The `"ask"` flow (Stage 10)

Only runs when `bonus_content.handling` is `"ask"`, and only if at least one item was
classified as bonus content this run. **Happens before the completion signal/eject
(Stages 11-12), not after** — a real, previously-hit bug was ejecting the discs before
this prompt ran, which sends the user the "you're done, safe to reload" signal while a
real question is still outstanding. Eject only after this stage (if it ran) is fully
resolved.

1. List what's sitting in staging classified as bonus content, grouped by its parent
   movie/show, with each item's guessed subtype and a one-line reason (the evidence
   that identified it as bonus content, not the main feature).
2. Ask the user: keep (file as Special Features, per above) or discard (delete
   permanently) — a single run-wide choice is fine unless the user wants to decide
   per-item.
3. Execute their choice — a "keep" answer uses the same `file_bonus_content.py`
   invocation described above, per parent movie/show.
4. **If `remember_choice` is `true`**, update `config.local.json`'s
   `bonus_content.handling` to whatever they just chose (`"keep"` or `"discard"`) —
   tell the user plainly that this means future runs won't ask again, and how to
   change it back (edit the config field, or ask Claude to change it).

## Edge case: bonus content whose parent movie/show is itself still unresolved

If the disc's main content landed in Stage 11 (needs-review, not yet placed), there's
no real movie/show folder to file that disc's bonus content under yet. Leave it in
staging until the parent is resolved — don't create a folder for a not-yet-confirmed
identity. Once Stage 11 resolves the parent (placed for real), the bonus content can
be filed on a later run using the now-known target folder.
