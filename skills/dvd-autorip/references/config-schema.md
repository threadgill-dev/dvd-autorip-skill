# Config schema

`~/.claude/dvd-autorip-skill/config.local.json` holds every machine/account-specific
value this skill needs — **a fixed path outside this plugin's own directory tree
entirely**, not `config/config.local.json` under the plugin root. This is deliberate,
not incidental: installing or updating this plugin from a marketplace makes a fresh
filesystem copy of the plugin's whole directory into a new version-numbered cache
folder every time — confirmed empirically, including for a *local directory*
marketplace source, where it's easy to (wrongly) assume the installed copy just
references the live directory live. A file living inside that tree gets swept up in
every such copy: a live edit doesn't reach an already-running session until a
version-bump-plus-`update`-plus-restart cycle, and — more seriously — a config that
only exists because a *previous* version's install created it has no path to reach a
*new* version's freshly-copied folder at all, since the marketplace source (GitHub,
typically) never contains a real user's config in the first place. Keeping it
permanently outside the versioned plugin tree avoids both failure modes.

`config/config.local.json` at the plugin root is still recognized, but only as a
**one-time migration source** — if the new fixed path doesn't have a config yet and
the old location does, Stage 1 moves it rather than asking the user to redo setup.
Nothing writes to the old location anymore.

**`~` is safe to use literally** in every command this skill runs against the config
path — confirmed empirically to expand correctly both when Claude constructs a Bash
command and when it constructs a PowerShell command on Windows, including passed as
a plain argument to an external program (`python script.py ~/.claude/...`), not just
inside PowerShell-native cmdlets. No platform-specific handling needed here, unlike
some of this skill's other path-handling gotchas.

`config/config.example.json` (inside the repo, still at the old relative path — it's
just a template, never a real config) is the committed template (placeholders only).

Claude creates and edits the real config file directly during Stage 1 of a run (see
SKILL.md) — there is no setup wizard script that prompts interactively; the
conversation itself is the wizard. `scripts/setup/validate_config.py` only checks
that a given file (passed as a path argument) is present and well-formed — it never
prompts, never contains real values, and doesn't care which of the two locations it's
pointed at.

## Fields

### `media_server.type` (string, optional, default `"jellyfin"`)
One of `"jellyfin"` or `"none"`. Missing this field entirely is the same as
`"jellyfin"` — existing `config.local.json` files written before this field existed
keep working with no change. `"none"` is for ripping into correctly-named,
correctly-organized files with **no** media server at all — no reachability check, no
`Library/Refresh`, no metadata auto-correct, no post-write verification against a
server. This is also the right choice for a **Plex** library: nothing in this skill
talks to Plex's API specifically (untested, unlike the Windows rip pipeline — see
`gotchas.md`), but `"none"` mode's output is exactly what Plex's own scanner picks up
on its own, and Plex's built-in matching is generally trusted more than Jellyfin's
fuzzy matcher anyway (the reason this skill's independent-identification step exists
in the first place). See `identification-technique.md`'s "Identifying without a media
server" for how Stage 7 adapts, and `SKILL.md`'s Stage 9/12 for what gets skipped.

### `jellyfin.base_url` (string, required when `media_server.type` is `"jellyfin"`)
The Jellyfin server's URL as reachable from the machine running this skill, e.g.
`http://192.168.1.50:8096` or `http://localhost:8096`. No trailing slash. Not read at
all when `media_server.type` is `"none"`.

### `jellyfin.api_key` (string, required when `media_server.type` is `"jellyfin"`)
A Jellyfin API key with permission to create/update library items. Generate one via
Jellyfin's own web UI: **Dashboard → Advanced → API Keys → +** (any name works — Jellyfin
doesn't scope keys by permission, any valid key can read/write the library). Treat this
like a password: it is written only to `config.local.json`, never to anything committed,
never echoed back in full in conversation once set. Not read at all when
`media_server.type` is `"none"`.

### `library.movies_path` (string, required)
Absolute path to the movies library folder as seen by the machine running this skill
(e.g. `D:\Media\movies` or `/mnt/media/movies`). Flat, no subfolders assumed. Required
regardless of `media_server.type` — this is where files land either way; naming them
correctly and organizing them into the right folders is this field's job, with or
without Jellyfin/Plex/anything else pointed at it afterward.

### `library.shows_path` (string, required)
Absolute path to the shows library folder, expected to contain one subdirectory per
show, each with `Season {N}` subdirectories inside. Same as `movies_path` above:
required regardless of `media_server.type`.

### `library.naming.movie_template` / `library.naming.show_template` (string, optional)
Filename templates. Defaults match Jellyfin's own recommended naming
(`{title} ({year}).mkv` for movies, `{show} - S{season:02d}E{episode:02d} -
{episode_title}.mkv` for episodes). Override only if your library uses a different
convention — Stage 8 (place + scan) uses these verbatim.

### `staging.path` (string, optional)
Where in-progress rips land before identification/placement. Defaults to
`./_autorip_staging` relative to wherever this skill is invoked from. Use an absolute
path if you want staging to live somewhere specific regardless of working directory.

### `makemkv.path` (string or null, optional)
Absolute path to `makemkvcon`. Leave `null` **only** if MakeMKV is genuinely on the
system PATH (confirmed by `scripts/check_dependencies.py --json`'s `found_via_fallback`
field being `false`, not by eyeballing the `detail` path string). If MakeMKV was found
via a fallback location (`found_via_fallback: true`), Stage 1 sets this field for you
from the check's `resolved_path` — a `null` here in that case will make Stage 3/4 fail,
since the rip/discovery scripts' own process-launch mechanism (`Start-Process` on
Windows, `command -v` + direct exec on Linux/Mac) doesn't search the same fallback
locations the dependency checker does.

### `tesseract.path` (string or null, optional)
Absolute path to the `tesseract` CLI binary, same pattern and same reason as
`makemkv.path` above. Leave `null` only if `check_dependencies.py --json`'s tesseract
check reports `found_via_fallback: false`. **A real gap this field closes**: before
`check_tesseract()` had a fallback-location check at all, a genuinely-installed
Tesseract that simply wasn't on PATH (a real, confirmed case — installed at
`C:\Program Files\Tesseract-OCR\tesseract.exe` on Windows, not on PATH) was reported
as missing entirely, and Stage 1 offered to install something already present. If this
is `null` and Stage 7's OCR step is invoking a bare `tesseract` command, that only
works when tesseract is genuinely on PATH — pass this path explicitly when set, same
as `-MakeMkvPath` is always passed explicitly to the rip/discovery scripts.

### `setup.declined_optional_installs` (array of strings, optional, default `[]`)
List of `check_dependencies.py` `key` values (e.g. `"tesseract"`) the user has said no
to installing when Stage 1 offered. Stage 1 skips asking about any key already in this
list on future runs — it just notes the tool is unavailable and continues, same as
before this feature existed. Only ever populated by Stage 1 itself when the user
declines an **optional** tool's install offer; required-tool declines are never
remembered here (declining a required tool always hard-stops the run, every time,
since nothing after it can work without that tool anyway). Full mechanics:
`references/dependency-install.md`. Remove an entry (or clear the whole array) to make
Stage 1 ask about that tool again.

### `setup.model_effort_ack` (boolean, optional, default `false`)
Whether Stage 1's model/effort check has already been shown to the user once. `false`
(or missing) means Stage 1 step 1 checks the running session's model/effort against
the recommendation and asks about upgrading if it falls short, same as always. Once
that's happened — **regardless of whether the user upgraded or declined** — Stage 1
sets this to `true` and never runs that check again on any future run, so a user who
deliberately runs on a lower tier isn't asked about it every single session. Set it
back to `false` by hand (or delete the field) to be asked again — e.g. after
upgrading your plan/access and wanting the skill to confirm you're actually getting
the higher tier now.

### `bonus_content.handling` (string, optional, default `"ask"`)
One of `"discard"`, `"keep"`, or `"ask"` — what to do with deleted scenes,
featurettes, trailers, and other genuine bonus/extra content Stage 7 identifies on a
disc (distinct from the main movie/episode content, and distinct from Stage 11's
"needs review" bucket for genuinely ambiguous titles). `"keep"` files it into
Jellyfin's real Special Features folders; `"discard"` deletes it from staging
permanently; `"ask"` prompts once per run at Stage 10, **before** the completion
signal/eject (eject must always be the true last action — see `bonus-content.md`'s
"ask flow" for why). Missing this field entirely is the same as `"ask"` — the safest
default. Full mechanics, including why the Play-All/concat title is unconditionally
skipped regardless of this setting, and how movie-with-extras folders get created:
`references/bonus-content.md`.

### `bonus_content.remember_choice` (boolean, optional, default `false`)
Only meaningful when `handling` is `"ask"`. If `true`, the user's answer at Stage 10
gets written back into this file (converting `handling` to whatever they chose),
so future runs stop asking. If `false` (default), `handling` stays `"ask"` and every
future run prompts again.

### `parallel.max_drives` (integer or null, optional)
Caps how many optical drives are ripped from simultaneously. `null` means "use however
many drives Stage 3's discovery scan finds." See `references/parallel-ripping.md` for
why more drives isn't always better on memory-constrained machines.

## Validating

`scripts/setup/validate_config.py <path-to-config.local.json>` checks the file exists,
parses as JSON, and has every required field non-empty — `jellyfin.base_url`/
`jellyfin.api_key` are only required when `media_server.type` is `"jellyfin"` (or the
field is missing, which defaults to `"jellyfin"`); under `"none"` those two fields
aren't checked at all, even if present. It does not check that the Jellyfin URL/key
actually work — that's a live reachability check (`GET /System/Info`) Claude performs
directly as part of Stage 1, since it needs a real network call, not just schema
validation, and it's skipped entirely under `"none"`.
