---
name: dvd-autorip
description: Rip a DVD via MakeMKV, independently identify its real content (never relying on Jellyfin's own fuzzy title/year matcher, which produces confidently wrong matches often enough to be untrustworthy alone), place the file(s) correctly named into a media library, and — when a Jellyfin server is configured — correct Jellyfin's metadata via direct API writes. Jellyfin is optional (media_server.type: "none" in config); works equally well feeding a Plex library or a plain folder structure with no media server at all. Use when the user says things like "rip this DVD", "add this disc to Jellyfin", "run the autorip pipeline", or loads a disc and asks what to do with it. Windows-first; see references/config-schema.md and scripts/check_dependencies.py before the first run on a new machine.
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/**), Bash(powershell *), Bash(pwsh *), Bash(python *), Bash(bash *), Bash(*ffmpeg*), Bash(*ffprobe*), Bash(*tesseract*), PowerShell(${CLAUDE_SKILL_DIR}/scripts/**), PowerShell(powershell *), PowerShell(pwsh *), PowerShell(python *), PowerShell(bash *), PowerShell(*ffmpeg*), PowerShell(*ffprobe*), PowerShell(*tesseract*)
shell: powershell
---

# DVD auto-rip pipeline

**Recommended model: Sonnet 5 or later, effort `high`** (`/model`, `/effort`, or
`modelSettings` in `settings.json`) — this is what this skill has actually been built
and hardened on, not a guessed minimum. Matters specifically for Stage 7 below, which
is real judgment, not a lookup; the mechanized stages (rip, eject, dependency/config
checks) don't need it.

**This is deliberately not a hands-off script for Stages 6-9.** The disc-to-disc
variance in real DVD authoring (bonus content, concatenated episodes, decoy metadata
candidates, franchise title collisions, physically damaged discs) keeps producing
genuinely new judgment calls — a fixed decision tree would either miss cases or
silently guess wrong. Stage 4 (rip) and Stage 13 (eject) are the parts that have
proven to need zero judgment across hundreds of real discs, and those are fully
mechanized. **Stage 7 (identify) is deliberately NOT mechanized, not even the
evidence-gathering** — a scripted `gather_evidence`/`monitor_evidence` pipeline was
built and then fully abandoned 2026-08-30 (see `gotchas.md`'s "Mechanized
evidence-gathering, tried and abandoned") after real runs showed it made the common
case (a few cheap lookups, stop once confident) slower than just doing the extraction
live, and one run's attempt at concurrency across titles made things dramatically
worse. Stage 7 is live `ffmpeg`/`ffprobe`/Tesseract tool calls, driven adaptively by
your own judgment about how much evidence is actually needed — see
`identification-technique.md`. Everything else in Stages 6-9 is a checklist for
**you** (Claude) to work through per disc, using your own reasoning, web search, and
the Jellyfin API — not a spec for code to replace that.

**Never spawn a subagent (fork/Task/Agent, any of them) anywhere in this skill's
execution — for evidence-gathering, identification, placement, verification, or
anything else.** This was explicitly considered as a design option early on (see
PLAN.md's `Potential_Enhancements`) and rejected in favor of direct parallel tool
calls (see Stage 7 and `identification-technique.md`). A real run did it anyway,
unprompted, and it produced exactly the failure mode that rejection anticipated: the
delegated work quietly exceeded its actual assignment (did placement, Jellyfin
writes, permanent bonus-content deletion, and ejected drives without checking back),
reported evidence with more confidence than it had actually established, and missed
a cross-title consistency check (disc titles landing in a non-sequential episode
order) that a single continuous reasoning stream — the whole premise of "not a
hands-off script" above — would have been positioned to catch. The entire point of
Claude-in-the-loop for Stages 6-9 is that judgment calls happen in one continuous,
accountable reasoning stream with real checkpoints (Stage 10's bonus-content ask,
Stage 11's human checkpoint) — delegating that reasoning to a subagent that reports
back once at the end defeats the actual design, even when the delegation itself
seems like a reasonable reading of "parallelize this."

Full technique detail lives in `references/` and is intentionally not repeated here in
full — read the relevant reference file when you reach that stage, don't try to hold
all of it in context up front:
- `references/identification-technique.md` — Stage 7 in full, including the hard rule
  about TV discs and the concat/salvage procedures
- `references/parallel-ripping.md` — running more than one drive at once
- `references/gotchas.md` — disc-menu mining, CSS/backup, damage-vs-memory diagnosis,
  multi-angle discs, PowerShell-specific gotchas
- `references/config-schema.md` — every config field, config validation
- `references/linux-mac-adapter.md` — what's implemented for Linux/Mac, what's weaker
  than the Windows adapter (mainly eject verification), bash 4+ requirement
- `references/bonus-content.md` — deleted scenes/featurettes/trailers: what counts as
  bonus content vs. Stage 11's "needs review", the `bonus_content.handling` config
  setting, and how kept content gets filed into Jellyfin's real Special Features
  folders
- `references/dependency-install.md` — Stage 1's missing-tool flow: when to offer
  installing something, when to hard stop, when to remember a decline so future runs
  stop asking

**Stage numbering note**: stages here are numbered sequentially (1-13). If you're
cross-referencing against `M:\.claude\projects\dvd-autorip-pipeline\PLAN.md` (the
private working document this skill was extracted from, which uses an older `0`/`0.4`/
`1.5`/`5.5`-style decimal numbering left over from its own edit history), see that
doc's "Stage numbering: PLAN.md vs. the published skill" section for the mapping
between the two.

## Running the bundled scripts

`check_dependencies.py`'s report (Stage 1) tells you which OS this machine is —
`"Windows"`, `"Linux"`, or `"Darwin"` — and that decides which adapter every scripted
stage below (2, 3, 4, 12) uses. Don't try the PowerShell scripts on Linux/Mac or the
bash scripts on Windows; each OS has exactly one adapter, and it's the one
`check_dependencies.py` already validated the machine against. Flag names differ
slightly between the two adapters (PowerShell uses `-PascalCase`, bash uses
`--kebab-case`) — each stage below gives both forms.

**Windows** — `scripts/platform/windows/*.ps1`. Most Windows machines default to an
execution policy that refuses to run any local `.ps1` file at all (`running scripts is
disabled on this system`) — this has nothing to do with the scripts themselves, it's
Windows' own default posture, and it will bite on a fresh machine before Stage 1 even
finishes. Always invoke with `-ExecutionPolicy Bypass`:

```
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/scripts/platform/windows/drive_discovery.ps1" -MakeMkvPath "..."
```

(`pwsh` works identically if PowerShell 7+ is installed. `-ExecutionPolicy Bypass` only
affects that one process invocation, not the machine's actual policy — nothing
persistent is being changed.)

**Linux/Mac** — `scripts/platform/linux/*.sh` (the directory is named `linux/` from
when it was a stub; the scripts inside are validated as the adapter for `Darwin` too).
Requires bash 4+ (macOS ships bash 3.2 by default — `check_dependencies.py`'s `bash`
check flags this if it's what's actually on PATH):

```
bash "${CLAUDE_SKILL_DIR}/scripts/platform/linux/drive_discovery.sh" --makemkv-path "..."
```

**Ported directly from the validated Windows logic — not independently run against
real hardware.** This pipeline's 196+ real-disc tests all ran on Windows; treat a
Linux/Mac run with a "watch it, don't assume it just works" posture, same as any new
code path. See `references/linux-mac-adapter.md` for what's specifically weaker than
the Windows equivalent (mainly: eject verification has no real cross-distro
equivalent to Windows' `Get-Volume`).

Every script reference below assumes one of these two invocation patterns even where
it isn't spelled out again.

**Cross-platform Python** — `scripts/*.py` directly under `scripts/` (not under
`platform/windows` or `platform/linux`): `check_dependencies.py`,
`setup/validate_config.py`, `setup/check_directory_scoping.py`, `detect_exclusions.py`,
`jellyfin_api.py`, `file_bonus_content.py`, and `run_timer.py`. These have no OS-specific behavior (MakeMKV robot-mode
parsing, HTTP calls, and filesystem moves work identically everywhere Python 3.8+
runs) so there's exactly one version, invoked the same way on every OS:

```
python "${CLAUDE_SKILL_DIR}/scripts/jellyfin_api.py" <config path> GET System/Info
```

**Never write the REST path with a leading `/`** (`System/Info`, not `/System/Info`,
`Library/Refresh` not `/Library/Refresh`, and so on for every `jellyfin_api.py` call
in this skill) — the script accepts either form and normalizes internally, but a
leading `/` is exactly the pattern Git Bash's MSYS layer auto-converts into a Windows
path before Python ever sees it, which is what makes the `MSYS_NO_PATHCONV=1`
workaround seem necessary in the first place. **Don't reach for that workaround if
the mangling happens anyway** — if directory-scoping protection
(`permissions.blockReadsOutsideWorkingDirectories`, see Stage 1 step 4) is enabled, an
env-var-prefixed command can never be verified against it and gets denied
unconditionally, on any tool, in any permission mode — one of the few checks nothing
can bypass (confirmed directly; see `references/gotchas.md`'s "`MSYS_NO_PATHCONV=1`
collides with directory-scoping protection"). Writing the path without a leading `/`
avoids the mangling outright, on the Bash tool or the PowerShell tool, so there's
nothing to remember about which tool to prefer either.

## Launch this skill in `dontAsk` permission mode, not `auto`

**`claude --plugin-dir "<path to this repo>" --permission-mode dontAsk`** — not
`--permission-mode auto`, and not whatever a session defaults to without an explicit
flag. This isn't a style preference: Claude Code's `auto` mode silently drops every
*wildcarded* `allowed-tools` rule the instant a session enters it (its own
documentation names `Bash(python*)`-style wildcarded interpreters explicitly), and
every rule this skill's `allowed-tools` frontmatter defines is wildcarded — there's
no way to express "run any `ffmpeg`/`tesseract`/`powershell` invocation with
whatever arguments this run needs" as a narrow, non-wildcarded rule. Under `auto`,
every Bash/PowerShell call in Stages 2/3/4/7/8/12/13 falls through to `auto` mode's
general-purpose classifier instead, which has no default-allow rule recognizing
DVD-ripping-specific commands as safe — and enough of them getting blocked trips
Claude Code's own "3 blocks in a row or 20 total" fallback, which then reverts the
*entire session* to full manual prompting for everything, indefinitely. This is a
real incident, not a theoretical concern — see `references/gotchas.md`'s
"`allowed-tools: Bash(powershell.exe *)` never matched anything" and its
auto-mode follow-up for the full history. `dontAsk` mode runs only from
`permissions.allow` rules (including wildcarded ones), the built-in read-only
command set, and nothing else — no classifier, nothing to trip.

## Stage 1 — Setup (first run on a machine, and a quick check every run)

1. Check whether `config/config.local.json` exists **at the plugin root — the
   `config/` directory that's a sibling of `skills/`, NOT
   `${CLAUDE_SKILL_DIR}/config/`.** (`${CLAUDE_SKILL_DIR}` resolves to
   `skills/dvd-autorip/`; a config file written there instead of the plugin root is a
   real, previously-encountered mistake — it silently creates a second, divergent
   config that the rest of this skill never reads, and won't necessarily be covered by
   `config/.gitignore` either.) If it exists, run
   `python ${CLAUDE_SKILL_DIR}/scripts/setup/validate_config.py <path>`. If it's
   missing or reports problems, **ask the user conversationally** for whatever's
   missing — this is a conversation, not a scripted prompt:
   - **Whether they have a Jellyfin server to write metadata to at all** —
     `media_server.type`, `"jellyfin"` or `"none"`. Default to `"jellyfin"` if they
     don't say otherwise (matches every config written before this field existed).
     `"none"` is the right answer for someone who just wants correctly-named,
     correctly-organized files with no media server involved at all, or who uses
     Plex instead — see `references/config-schema.md`'s `media_server.type` entry
     for why Plex users specifically are better served by `"none"` than by anything
     Plex-API-specific this skill doesn't have and can't test. Only ask the next
     bullet if the answer here is `"jellyfin"`.
   - Jellyfin base URL and an API key (Dashboard → Advanced → API Keys → +)
   - The Movies and Shows library folder paths — always needed, regardless of
     `media_server.type`
   - What to do with bonus/extra content (deleted scenes, featurettes, trailers) —
     keep it (filed into the standard Special Features folder convention, read by
     Jellyfin/Plex/etc. alike — not Jellyfin-specific, works the same regardless of
     `media_server.type`), discard it (deleted from staging once identified as bonus
     content, permanently), or be asked each run.
     Explain the tradeoff briefly (keep = more setup work on discs that have extras,
     discard = permanent, ask = no silent default either way) — see
     `references/bonus-content.md`. Default to `"ask"` if the user has no preference
     yet rather than guessing.
   - Anything else in `references/config-schema.md` that has no sensible default
   Write/update `config/config.local.json` (plugin root, not `${CLAUDE_SKILL_DIR}`)
   yourself once you have real values. Never commit this file, never echo the API key
   back in full once it's set.
2. Run `python ${CLAUDE_SKILL_DIR}/scripts/check_dependencies.py --json` **in the
   foreground, not backgrounded — it has its own hard internal ceiling (90s total,
   10s per external tool it shells out to) and normally finishes in well under a
   second, so there's no legitimate reason to background it and wait.** A real run
   backgrounded it anyway and then waited 12+ hours for a background shell task that
   never reported back — the actual bug was a Windows `subprocess` gap (a launched
   process's grandchild can inherit stdout/stderr pipe handles, so killing only the
   immediate PID left the read blocked past its own timeout indefinitely; fixed in
   the script itself by killing the whole process tree on timeout, plus the 90s
   watchdog as a backstop) — see `references/gotchas.md`'s "A dependency check that
   should finish in under a second waited 12 hours instead" for the full incident.
   If this or any other foreground call in this skill ever genuinely doesn't return,
   that's itself the signal something is wrong — check on it and investigate, don't
   silently keep waiting past what the step normally takes.

   For every check with `"found": false`, **offer to install it** rather than just relaying the
   hint and stopping — full flow (when to offer running the install command, when to
   hard stop, when a decline gets remembered vs. asked again next time) is in
   `references/dependency-install.md`. Short version:
   - **Required tool missing** (MakeMKV, ffmpeg/ffprobe, PowerShell/bash — see the
     check's `"required"` field): offer to install it, attempt it if they say yes,
     re-check afterward. **Hard stop if they decline, or if it's still missing after a
     genuine attempt** — none of the later stages work without it, and this never gets
     silently skipped on a future run either, it asks again every time.
   - **Optional tool missing** (Tesseract): first check `config.local.json`'s
     `setup.declined_optional_installs` — if this tool's key is already there, don't
     ask again, just note it's unavailable (OCR fallback in Stage 7 unavailable this
     session) and continue. Otherwise offer to install it; if they decline, **write
     its key into `setup.declined_optional_installs`** so future runs stop asking, and
     say so plainly.
   **For any check with `"found_via_fallback": true`** (currently only possible for
   `makemkv` and `tesseract` — check the field directly, don't try to infer this by
   eyeballing the `detail` path string), write that check's `resolved_path` into
   `config.local.json`'s matching field (`makemkv.path` / `tesseract.path`) right now:
   - `makemkv.path` — `Start-Process -FilePath` (used by the rip/discovery scripts)
     does not search the same locations `check_dependencies.py` does, so leaving this
     `null` on a machine where MakeMKV isn't literally on PATH will make Stage 3/4
     fail with a raw "file not found" error instead of working.
   - `tesseract.path` — Stage 7's OCR step invokes `tesseract` as a bare command by
     default; leaving this `null` on a machine where it's only reachable via
     `resolved_path` will make that invocation fail the same way. This is also why a
     genuinely-installed-but-off-PATH Tesseract used to get reported as fully missing
     and offered for (re-)install — `found_via_fallback` closes that gap; if it's
     `true`, don't offer to install it, it's already there.
3. **Skip this step entirely when `media_server.type` is `"none"`** — there's no
   server to reach, and none of Stages 8/9/12's Jellyfin-touching sub-steps run
   either (each says so at the point it applies). Otherwise, validate Jellyfin is
   actually reachable with the configured URL/key:
   `python ${CLAUDE_SKILL_DIR}/scripts/jellyfin_api.py <config path> GET System/Info`
   (no leading `/` — see "Running the bundled scripts" above for why).
   **Use this script for every Jellyfin API call in this skill, not just this one** —
   it already tries `Authorization: MediaBrowser Token=...` first and falls back to
   the older `X-Emby-Token` header on a 401 before giving up, which is the fix for a
   real incident where every call started 401ing hours into a session with no config
   change (see `references/gotchas.md`'s "Jellyfin API auth header can change across
   server versions" for the full incident) — routing every call through this one
   script means that fallback is always in effect, not just at first setup. It also
   hard-stops with a clear error on `/Items/RemoteSearch/Episode` instead of letting
   you discover the real 404 live (see Stage 9). On an `"error_type": "auth"` or
   `"connection"` result, tell the user plainly which one failed and offer to
   re-enter the value rather than failing silently mid-pipeline later — don't assume
   the key itself is bad without checking (regenerating it fixed nothing in the real
   incident; switching header form did).
4. **Offer directory-scoping protection**, a real Claude Code feature
   (`permissions.blockReadsOutsideWorkingDirectories`) this skill can turn on for
   itself rather than requiring the user to discover and configure it by hand. Run
   `python ${CLAUDE_SKILL_DIR}/scripts/setup/check_directory_scoping.py check
   --plugin-root <plugin root> --staging-path <staging.path, resolved — apply
   config-schema.md's default yourself if the field is unset, don't pass the literal
   string unresolved>`.
   **Check `"staging_path_in_scope"` every time, regardless of
   `"protection_enabled"`** — a real run had protection already on from an earlier
   session *and* staging genuinely out of scope at the same time, and caught it only
   because it checked both fields itself rather than short-circuiting on
   `protection_enabled` alone (see `references/gotchas.md`'s "Directory-scoping: a
   real safety feature, a global-vs-project-scope trap, and the staging-path
   gotcha"). Treat the two fields as independent:
   - **`protection_enabled: false`** — ask the user conversationally (once per
     machine, same spirit as the other Stage 1 questions) whether they want it on:
     explain plainly that it scopes Claude's file reads to this plugin's own
     directory tree for the session, catching an accidental read/write outside
     where it's expected — the exact failure mode a real run hit once (see the
     gotchas entry above for the full history, including why a *global* version of
     this setting is the wrong shape and caused its own, worse problem). If they
     decline, skip the rest of this step.
   - **`staging_path_in_scope: false`** (check this whether or not protection is
     already on) — `staging.path` is configured outside the plugin root, which
     would break (or is already breaking) every staging read/write the moment this
     protection is active: the setting has no per-directory nuance, it blocks
     anything outside the plugin root, unconditionally, in every permission mode,
     and staging is where most of this pipeline's actual file activity happens.
     Tell the user this plainly — don't silently enable the protection (or leave an
     already-enabled one silently broken) — and offer the real choices:
     - Move `staging.path` back under the plugin root (unset the field, or set it
       to a path inside the plugin root) — safe with no further action needed.
     - Keep `staging.path` where it is and always launch with an extra
       `--add-dir <staging.path>` flag going forward — tell them this explicitly,
       don't just fix the immediate problem and leave them to discover the flag is
       now required on every future launch.
     - Turn the protection off for now (only relevant if it was already on).
   - Only if the user wants protection on and staging is confirmed in scope (or
     they've just moved it there), run
     `python ${CLAUDE_SKILL_DIR}/scripts/setup/check_directory_scoping.py enable
     --plugin-root <plugin root>` — it merges into
     `<plugin root>/.claude/settings.local.json` rather than overwriting, so any
     other personal settings already there survive. This file lives inside the
     plugin's own folder tree deliberately (same principle as `config/
     config.local.json` — every skill dependency stays inside the skill, not
     scattered across the machine) and is already covered by this repo's own
     `.gitignore` — never commit it, it's personal-machine config, not something to
     ship to other installs of this plugin.
   - **Takes effect on next session start, not immediately** — same as any other
     settings-file change; tell the user plainly if they're mid-session.

Once config is valid and dependencies check out, this stage doesn't need repeating for
the rest of the session — just proceed to Stage 2/4 for each disc.

## Stage 2 — Suppress USB notifications (once per batch, parallel or not)

Windows: `scripts/platform/windows/toggle_usb_notifications.ps1 -Disable`. Linux/Mac:
`scripts/platform/linux/toggle_usb_notifications.sh --disable` — a documented no-op
there (suppresses a Windows-specific shell toast that has no Linux/Mac equivalent; the
script exists only so this stage is valid on every OS without a skill-level branch).
Re-enable (`-Enable` / `--enable`) only at the very end of the whole batch, as part of
Stage 12 — **before** Stage 13's completion sound and eject, not paired with them —
see "Ordering" in `references/parallel-ripping.md` for why this matters even on a
single-drive run.

## Stage 3 — Drive discovery (once per batch)

`drive_discovery.ps1` (Windows) / `drive_discovery.sh` (Linux/Mac) — **always pass
`-MakeMkvPath`/`--makemkv-path` from `config.local.json`'s `makemkv.path` when it's
set** (only omit it if that field is genuinely `null`, meaning check_dependencies.py
confirmed MakeMKV is directly on PATH). Never hardcode a disc index — always re-derive
the drive → index mapping fresh, since MakeMKV's own assignment isn't guaranteed stable
across runs. The result's `mediaLoaded` field tells you which drives actually have a
disc in them right now. The `driveLetter` field is a Windows drive letter (`"D:"`) on
Windows and a Unix device path (`"/dev/sr0"`) on Linux/Mac — same field name, OS-
appropriate value.

**Record the batch start time now**, so Stage 13's closing message can report total
run time:
`python ${CLAUDE_SKILL_DIR}/scripts/run_timer.py start --staging-path <staging.path>`
— writes into `staging.path`, same reasoning as `rip_processes.json`: a file
survives the whole batch reliably, don't rely on remembering a timestamp across
what can be an hours-long, multi-compaction session.

## Stage 4 — Rip

**Step 1, before calling `launch_rip` for any drive — scan the title list and decide
which titles are actually worth ripping.** This is a mandatory, ordered step, not
background context to keep in mind — two real runs skipped straight to ripping `all`
without doing this first: one wasted significant rip time on an oversized concat
title (caught and discarded afterward at classification); another wasted a full
duplicate feature-length rip on a multi-angle movie disc where the duplication was
already visible in the pre-rip title list, correctly noticed, and ripped anyway on
the reasoning that identification would sort it out afterward instead of excluding it
before ripping. Don't let either recur.

For each drive with `mediaLoaded: true`, run
`python ${CLAUDE_SKILL_DIR}/scripts/detect_exclusions.py --disc-index N
--makemkv-path <config's makemkv.path or omit if null>` (this runs `-r info disc:N`
itself — the per-disc info scan, distinct from `drive_discovery`'s `disc:9999`
enumeration — and applies both checks below in one pass) and check its result,
**in order**:

1. **Play-All/concat title (TV-shaped discs only)** — the script's
   `"exclude-concat"` verdicts implement `identification-technique.md`'s "Detecting
   a full-season 'concat' title before ripping it" method (TINFO field 25 + duration
   math, both signals required to agree). A single-title movie disc has nothing to
   detect here. **Always excluded once the script confirms it**, regardless of
   `bonus_content.handling`, including under `"keep"` — it's pure duplicate content
   (the real episodes are ripped separately in full), not a real "extra." A
   `"ambiguous-concat"` verdict (only one of the two signals matched) is not
   confirmed — apply `identification-technique.md`'s chapter-repetition check
   yourself before deciding.
2. **Multi-angle/duplicate-source titles (any disc)** — the script's
   `"exclude-duplicate"` verdicts implement `gotchas.md`'s "Multi-angle and
   duplicate-title discs" method (duplicate source-file name, near-identical
   duration, `SINFO` aspect-ratio/format check to rule out a genuine
   flipper/widescreen-vs-fullscreen disc rather than assume duplication from duration
   alone). MakeMKV's own dedup (`MSG:3027`) catches *angle*-level duplication
   automatically during the rip itself — it does **not** catch two separate *source*
   titles that both happen to be full duplicate encodes of the same content, which is
   exactly what this check is for. **Always excluded once the script confirms it**
   (keep one representative per duplicate group — the script already picks one),
   regardless of `bonus_content.handling` — same reasoning as the concat title.
   **`"ambiguous-duplicate"` is the expected, common result, not a sign anything's
   wrong** — the script's own `field_confidence_note` explains why (the source-file/
   format field numbers it uses aren't independently confirmed against a live
   MakeMKV instance the way the concat check's field is); treat an ambiguous result
   as "check the evidence yourself," never as "exclude it anyway" or "ignore it."
3. **Movie-shaped discs only, and only when `bonus_content.handling` is
   `"discard"`** — a movie disc only ever has *one* file surviving to placement, so
   ripping every non-feature title unconditionally (the way TV discs do) wastes far
   more, proportionally, than it does on a TV disc with many surviving episodes. When
   the disc's title-duration shape is unambiguous — one title clearly dominant
   (feature-length) and the rest clearly shorter (bonus-shaped: trailers, deleted
   scenes, featurettes) — restrict the rip to just that one dominant title. The
   script's `feature_length_title_ids` field (duration ≥ 50min) is a quick starting
   point for spotting the dominant title, not a replacement for this judgment call.
   **Do not
   guess when it's ambiguous**: if two or more titles are independently
   feature-length (a dual-cut release, a double-feature disc, anything where you
   can't tell which is "the" movie from duration alone), that's an identification
   question, not a pre-rip duration filter — rip all the feature-length candidates
   and let Stage 7 resolve it, same as always.
   - **`bonus_content.handling` is `"keep"` or `"ask"`**: do **not** apply this
     narrowing — rip every title (concat/duplicates aside) the same as a TV disc
     would. Bonus content on a movie disc can only become a real Jellyfin Special
     Feature (`"keep"`) or get a chance at manual review (`"ask"`) if it was actually
     ripped; skipping the rip here would silently defeat both settings. See
     `references/bonus-content.md`.
   - This narrowing is the one exception to "don't try to pre-emptively skip other
     bonus/extra content" below — it's justified specifically because a movie disc's
     single-surviving-file shape makes the waste asymmetric, and because
     `"discard"` guarantees those titles would never survive placement regardless of
     whether they're ripped.

**With more than one drive, issue these `detect_exclusions.py` calls as separate
`Bash`/`PowerShell` tool calls bundled together in one response, not one at a
time** — each drive's scan is independent of the others, and a real run issued them
one-at-a-time-waiting (drive 1's scan didn't start until drive 0's had fully
finished, costing real time for no reason) despite bundling genuinely independent
tool calls into one response being a valid, working pattern elsewhere in this skill
(see `identification-technique.md` for what genuinely bundled tool calls look like
versus sequential ones).

Once all three checks are done for a drive, decide its final title list:
- **Nothing to exclude**: proceed to rip `all`, as before.
- **Anything found above**: note every title id that should actually be ripped —
  this is what you pass to `launch_rip` via `-TitleIds`/`--title-ids` in step 3,
  instead of `all`.

**On a TV disc, don't try to pre-emptively skip genuine bonus/extra content**
(deleted scenes, featurettes) beyond the concat/duplicate checks above, even if
`bonus_content.handling` is `"discard"` — most of a TV disc's titles are real
episodes that should be ripped regardless, and there's no reliable pre-rip signal for
telling a short bonus title apart from a short episode the way there is for a concat
title or a confirmed duplicate encode; see `references/bonus-content.md`'s "Pre-rip
skipping" section for why ripping-then-discarding is still the right default there.
The movie-disc narrowing in check 3 above is the deliberate exception, not a
precedent for TV discs.

**Step 2**: decide a process-list file path for this batch —
`<staging.path>\rip_processes.json` (a fixed, deterministic name directly under the
configured staging root; don't improvise a different location or name per run).

**Step 3**: for each drive with `mediaLoaded: true`, call `launch_rip.ps1` (Windows) /
`launch_rip.sh` (Linux/Mac) with these exact flags — **the parameter name is
`-StagingRoot`/`--staging-root`, not `-StagingPath`/`--staging-path`, even though the
config field it comes from is named `staging.path`**; this mismatch has caused a real,
repeated wrong-flag-name error, so don't infer the flag name from the config field
name:
- `-DriveIndex`/`--drive-index` — the drive's index
- `-VolumeLabel`/`--volume-label` — the drive's volume label
- `-StagingRoot`/`--staging-root` — `staging.path` from config (**not** `-StagingPath`)
- `-MakeMkvPath`/`--makemkv-path` — same rule as Stage 3
- `-ProcessListPath`/`--process-list-path` — set to that same batch file path for
  every drive in the batch
- `-TitleIds`/`--title-ids` — **only when step 1 found something to exclude on this
  drive** (a concat title, confirmed duplicate-source titles, and/or — movie discs
  under `"discard"` only — non-feature titles narrowed out): every title id actually
  worth ripping, comma-separated (e.g. `"0,1,2,3"`). Leave this out entirely for a
  drive where step 1 found nothing to exclude — that's what ripping `all` still
  means. MakeMKV's CLI has
  no way to select multiple specific titles in one call (confirmed, not this
  pipeline's limitation — see `gotchas.md`), so under the hood this runs each given
  title sequentially within the same single detached process/PID `monitor_rips`
  already expects — nothing about the process-list/monitoring flow below changes
  because of this flag.

The script merges its own result into that file for you (a safe
JSON round-trip — PowerShell's `ConvertFrom-Json`/`ConvertTo-Json` on Windows, a
`json.load`/`json.dump` round trip via python3 on Linux/Mac — never string
concatenation) — **don't hand-build the JSON array yourself** from each call's stdout;
that's exactly the kind of manual JSON-construction this parameter exists to avoid, and
a volume label with an unusual character (a literal quote, say) could break a
hand-built version in a way the script's own merge logic doesn't need to worry about.
Each call launches detached and returns immediately with a PID.

Once every drive in the batch has been launched, run `monitor_rips.ps1
-ProcessListJson <same batch file path>` (Windows) / `monitor_rips.sh
--process-list-json <same batch file path>` (Linux/Mac) as a background/long-running
invocation — don't block synchronously waiting on it. It polls until every process
exits and reports each one's real outcome (parsed from the log, not just "did the
process exit"), **or until `-MaxTotalMinutes`/`--max-total-minutes` (default 360)
elapses, whichever comes first** — hang detection above flags a suspicious process
but keeps polling it forever on its own, so this ceiling is what actually stops the
wait (yours and the script's) from becoming unbounded if a process never exits. An
`outcome: "still_running_timeout"` entry means exactly that: still running (or
genuinely hung) when the ceiling hit, not killed, not resolved — surface it to the
user rather than treating a missing "success"/"failed" as either one. **Delete the process-list file once monitoring finishes** — it's a
working artifact for this one batch, not meant to persist in the staging root between
runs. (The Linux/Mac script deletes its own separate `<path>.results` working file
automatically; only the process-list file itself needs deleting by you.)

Running more than one drive at once: read `references/parallel-ripping.md` first — the
setup/memory/collision considerations there matter before you launch a second drive.

## Stage 5 — Root menu frame check (optional, cheap, before or after the rip)

See `references/gotchas.md` "Disc menu content is a real, underused identification
signal" for the exact technique — DVD menus often render the show name/season number
as literal on-screen text, which can save the rest of Stage 7 entirely for TV discs.

## Stage 6 — Classify movie vs. TV disc

Heuristic, not a hard rule: 1 title >50min → movie disc. Multiple titles in the
18-45min range → TV season disc. A disc that doesn't cleanly fit either shape is its
own outcome — "investigate further" — not a forced guess in either direction.

## Stage 7 — Identify

Read `references/identification-technique.md` now if you haven't already this
session — it has the full adaptive procedure (subtitle-type branching, when to OCR,
when to stop sampling, when frame extraction is primary vs. corroborating). Short
version: gather evidence live, cheapest-first, and stop the moment you're reasonably
confident — most titles don't need the whole toolbox.

Work through titles **one at a time, in order** — this stays your own reasoning the
whole way (there is no separate mechanized evidence-gathering step to wait on), and
naturally preserves the "verify every title independently, don't extrapolate a whole
disc's identity from one confirmed anchor" discipline in the gotchas doc.

Bundling several genuinely independent tool calls together in one response (e.g.
extracting different titles' subtitle streams concurrently) is fine when it's
actually useful. Never delegate any of this to a subagent (prohibited outright, see
this doc's intro) and never loop many commands inside one call (still sequential
under the hood despite looking batched, and can run long enough to hit the calling
tool's own timeout) — see the reference doc for both wrong forms shown concretely.

Once evidence is in hand, resolve each
title to a real TMDB id via
`python ${CLAUDE_SKILL_DIR}/scripts/jellyfin_api.py <config path> POST
Items/RemoteSearch/Movie` or `Series` (works with a zero-GUID `ItemId` — the same
search Jellyfin's own "Identify" dialog uses), cross-check against runtime, and check
embedded `CINFO`/`TINFO` disc metadata (from Stage 4/6's own disc info scan). **Hard
rule: a TV disc's content never spans seasons** — see the reference doc before
concluding otherwise.

**When `media_server.type` is `"none"`, skip the `RemoteSearch` call** — it exists to
get a real TMDB id for Stage 9's Jellyfin write, which doesn't happen in this mode
either. The identification itself doesn't need it: title/year (movies) or
show/season/episode (TV) still has to be established with the same confidence from
the same evidence (dialogue, subtitles, frame extraction) — a plain web search takes
`RemoteSearch`'s place as the cross-check corroborating that evidence, same as it
already does elsewhere in this stage. See `identification-technique.md`'s
"Identifying without a media server" for exactly what changes and what doesn't.

**For TV discs, build the Step 2b episode checklist (web search the season's
official disc breakdown) before working through titles** — it's a cheap cross-check
against wrong episode counts/numbers, and it's the primary defense against the
duplicate-encode mistake described next.

Output per title: proposed filename (per `library.naming` in config) + confirmed TMDB
id + confidence (high / needs-review) — **or**, when a title is confidently *not* the
main content but its evidence positively identifies what it actually is (a deleted
scene, a featurette, a trailer, etc.), classify it as bonus content with a subtype
guess instead of forcing it into high-confidence/needs-review. See
`references/bonus-content.md` for exactly where that line is — a title that's neither
confidently main content nor confidently bonus content is still "needs-review," not
bonus content by default.

**A title you suspect is a duplicate encode of another title on the same disc is
never a Stage 7 auto-discard, regardless of how confident the match looks.**
Classify it as needs-review and let Stage 11 resolve it with the user before
anything gets deleted — see `identification-technique.md`'s "Never permanently
delete a suspected duplicate encode without explicit confirmation" for why (a real
run deleted a genuine, distinct episode this way) and exactly what to check first.
No `config.local.json` setting, including `bonus_content.handling`, authorizes
skipping this — that setting governs bonus/extra content, not suspected duplicate
main-content encodes.

## Stage 8 — Place + scan

High-confidence main-content items: move into `{library.movies_path}` or
`{library.shows_path}\{Show}\Season N\` per the naming templates in config. **When
`media_server.type` is `"jellyfin"`**, follow with
`python ${CLAUDE_SKILL_DIR}/scripts/jellyfin_api.py <config path> POST Library/Refresh`
— Jellyfin will guess metadata via its own fuzzy match here, expected to sometimes be
wrong, corrected unconditionally next (Stage 9). **When `media_server.type` is
`"none"`, this stage is just the file move** — no refresh call, nothing to correct
afterward (Stage 9 doesn't run at all in this mode).

**Bonus/extra content classified in Stage 7** gets handled per
`config.local.json`'s `bonus_content.handling` (full mechanics in
`references/bonus-content.md`) — this part is unaffected by `media_server.type`, the
extras-folder convention `file_bonus_content.py` uses isn't Jellyfin-specific:
- `"discard"` — delete it from staging now, permanently.
- `"keep"` — run
  `python ${CLAUDE_SKILL_DIR}/scripts/file_bonus_content.py movie|tv ...` to file it
  into the real Special Features folders now (it creates a per-movie folder
  only for movies that actually have extras to keep — TV extras use the
  already-existing Season/Series folder structure, no restructuring needed — and
  never overwrites an existing destination), then include it in this stage's
  `Library/Refresh` **when `media_server.type` is `"jellyfin"`** (skip that part
  under `"none"`, same as above).
- `"ask"` — do neither yet. Leave it in staging; Stage 10 resolves it once, before
  eject, at the end of the whole run.

## Stage 9 — Auto-correct (always runs, unconditional, when `media_server.type` is `"jellyfin"`)

**Skip this entire stage when `media_server.type` is `"none"`** — there's no
Jellyfin item to correct, since Stage 8 never wrote one. Naming was already handled
by the naming templates at placement time, and Stage 7's identification confidence
is the only correctness guarantee that exists in this mode (see Stage 12 below for
what that means for verification).

**This stage's mechanism is different for movies/series than for individual
episodes — `POST Items/RemoteSearch/Episode` does not exist in Jellyfin's API
(confirmed: a live call to it 404s outright, there is no per-episode equivalent of the
Movie/Series/BoxSet search-and-apply endpoints).** Don't assume Stage 7's "resolve via
`RemoteSearch/.../Apply`" pattern is uniform across item types — `jellyfin_api.py`
hard-stops with a clear error if you call it against that path, rather than letting
you discover the 404 live.

- **Movies, Series (the show-level item), and BoxSets**: look up the newly-scanned item
  by path, then call `RemoteSearch/Apply` with the id already confirmed in Stage 7 —
  regardless of what Jellyfin's own scan guessed. Unconditional, not "only if wrong":
  this keeps the logic simple and means Jellyfin's own guess never needs evaluating at
  all.
- **Episodes**: there's no id to force-apply. This is actually lower-risk than it
  sounds — episode identity comes from deterministic season/episode numbering against
  an already-correctly-identified Series (Stage 7), not a fuzzy title search, so the
  class of error this pipeline exists to prevent (a *wrong show entirely*) mostly
  doesn't apply here. Correctness instead depends on (a) the filename's season/episode
  numbers being right and (b) Stage 8's `POST Library/Refresh` having actually run
  *after* the file was placed — a per-item `Refresh` does **not** pick up a
  renamed/newly-placed file the way a full library scan does. If verification (Stage
  12) still shows a wrong title after that, force re-derivation with
  `jellyfin_api.py <config path> POST
  "Items/{episodeId}/Refresh?Recursive=true&ReplaceAllMetadata=true&ImageRefreshMode=FullRefresh&MetadataRefreshMode=FullRefresh"`
  rather than reaching for a `RemoteSearch` call that doesn't exist for this item type.

## Stage 10 — Bonus/extra content decision (only when `bonus_content.handling` is `"ask"`)

Skip this stage entirely if `handling` is `"discard"` or `"keep"` (already resolved in
Stage 8) or if nothing was classified as bonus content this run. **Runs before
completion signal/eject, not after** — eject (Stage 13) is the real-world signal the
user is watching/listening for to know the run is genuinely finished and it's safe to
load the next discs (see "Ordering" in `references/parallel-ripping.md`); resolving
this stage after eject would send that signal while a real question was still
outstanding, which defeats the whole point of it being a hard physical cue. Full
mechanics in `references/bonus-content.md`'s "The `ask` flow" section: list what's
sitting in staging as bonus content (grouped by parent movie/show, with subtype +
evidence), ask the user keep-or-discard, execute it (a "keep" answer here uses the
same `file_bonus_content.py` call as Stage 8's `"keep"` bullet), and — **only if
`remember_choice` is `true`** — write their answer back into `config.local.json`'s
`bonus_content.handling` and tell them plainly that future runs won't ask again.

## Stage 11 — Needs-review resolution (only when Stage 7 held anything back)

Skip entirely if every title from Stage 7 resolved to a confident placement or
confirmed bonus content this run. **Runs before completion signal/eject (Stages
12-13), not after — same reasoning as Stage 10.** A real run left 3 titles
unidentified in staging and reported it only as a passing clause in its closing
summary; the user found out episodes were unaccounted-for by asking afterward, only
after eject had already fired the "you're done" signal (see `gotchas.md`'s "Needs-
review items were surfaced too late — after eject, not before" for the full incident
and why an earlier fix attempt — reporting it more prominently in the closing
message — still wasn't enough on its own). Eject must never fire while genuinely
unresolved content is sitting unaddressed without the user actively knowing about it.

1. Present each held title: filename/staging path, the evidence actually gathered (a
   one-line summary per piece, not a full re-dump), and why it wasn't confidently
   resolved — name a plausible-but-unconfirmed leading candidate if one exists,
   rather than just "no idea."
2. Ask the user what they want to do with it. This isn't a fixed keep/discard menu
   the way Stage 10 is — resolving an identification is open-ended, not binary. The
   realistic outcomes:
   - **The user gives you something that resolves it** (which episodes are actually
     missing from their collection, a correction, more context) — go finish
     identification and placement for that title now, same as any other title's
     Stage 7→8→9 path, before moving on to the next held title.
   - **The user says to leave it in staging for now** — a valid, explicit choice, not
     a default to silently fall into. Acknowledge it plainly; leave the file exactly
     where Stage 7 left it.
   - **The user says to discard it** — also valid (a duplicate, a fragment, not worth
     pursuing). Delete it from staging. This is a main-content decision, not a
     bonus-content one, so don't route it through `file_bonus_content.py`.
3. Execute whatever was decided, per title, before continuing to the next one.
4. Only once every held title has been addressed this way — resolved, explicitly
   deferred, or discarded — does the run proceed to Stage 12.

## Stage 12 — Verify, cleanup, and restore notifications

**When `media_server.type` is `"jellyfin"`**, confirm the correct match actually
landed — `python ${CLAUDE_SKILL_DIR}/scripts/jellyfin_api.py <config path> GET
Items?Ids={id}&Fields=ProviderIds,Overview` (or the per-item equivalent) and check
the id/title match what Stage 7 identified.
This is the real verification step referenced from Stage 9 above, not just "Stage 8's
write returned 200."

**When `media_server.type` is `"none"`, there is no equivalent API check — say so
plainly rather than silently skipping past it.** Stage 7's identification confidence
is the only guarantee that exists in this mode; nothing downstream cross-checks it
against a server the way the `jellyfin` path does. This is a genuinely weaker
guarantee, not a cosmetic difference — don't imply this stage "verified" anything for
a `"none"`-mode disc in the closing message.

Do this for **every** disc in the batch before moving on — Stage
10/Stage 11 (whichever applied) must also be fully resolved for all of them (see
"Ordering" in `references/parallel-ripping.md` — treating a disc as done, including
cleaning it up or ejecting it, before an outstanding bonus-content question or a
still-unaddressed needs-review title is resolved, sends a false "done" signal to the
user).

**Clean up this disc's staging working files, per disc, once its own verification
above is done — this is not optional, and it has no other trigger point anywhere else
in this pipeline.** A real installation went from 2026-08-28 to 2026-09-11 without a
single byte of staging cleanup ever happening — ~40 already-placed discs' worth of
`rip.log`/`rip.err.log`, a 155MB `evidence/` directory of OCR cue screenshots and
anchor frames, and leftover working files from the mechanized-evidence-gathering
system that was abandoned and deleted from the codebase on 2026-08-30 (see
`references/gotchas.md`'s "Mechanized evidence-gathering, tried and abandoned") were
all still sitting in staging two weeks later, none of it discovered until the user
asked directly. For each disc:
- Delete `<staging.path>/<volume_label>__drive<N>/` (the disc's own rip-log
  folder) — everything in it (`rip.log`, `rip.err.log`) has no value once every
  title from this disc has reached Stage 9/12 or been resolved by Stage 10/11.
- Delete `<staging.path>/evidence/<title_tag>/` for every title tag that came from
  this disc — Stage 7 evidence (OCR cues, anchor frames, extracted subtitles) has no
  reuse value once that title's identification is done; this is the same "single-use,
  never revisited" evidence category already covered in "Between batches" below, just
  triggered per-disc instead of per-session.
- **Never delete anything still legitimately in staging** — a title's raw rip file
  that Stage 11 hasn't resolved yet (the user chose to explicitly defer it), or a
  title still awaiting the user's answer to Stage 10's bonus-content question. Only
  clean up what this disc's own Stage 9-12/Stage 10-11 resolution actually finished —
  if anything from this disc is still open, its cleanup/eject was already blocked by
  the ordering rule above, so this case shouldn't arise, but don't clean up a title's
  evidence folder ahead of its own resolution regardless.

Once every disc in the batch has been verified and cleaned up, re-enable USB
notifications (`toggle_usb_notifications.ps1 -Enable` /
`toggle_usb_notifications.sh --enable`). **All of this — verification, cleanup, and
re-enabling notifications — must happen before Stage 13's completion sound and eject,
not after.** A real run got this backwards (discs were ejected before staging was
cleaned up and before notifications were turned back on), which is exactly the
ordering this stage exists to prevent — eject and the completion sound are the
signal that the whole run, including its own housekeeping, is finished, so nothing
housekeeping-related can still be pending when they fire.

## Stage 13 — Completion signal + eject

**The true last actions of the entire run — nothing scheduled after these should
ever still be pending.** Only start this stage once Stage 12 (verify, cleanup,
notifications-restore) has fully finished for **every** disc in the batch.

1. Play a completion sound so the user can step away during a rip without checking
   back constantly (PowerShell: `[System.Media.SystemSounds]::Asterisk.Play()`, no
   `Add-Type` needed).
2. Eject every drive: Windows `eject.ps1 -DriveLetter <letter>` per drive — verified
   via `Get-Volume`. Linux/Mac: `eject.sh --device <device path>` per drive —
   verified best-effort only (no cross-distro equivalent of `Get-Volume`; see
   `references/linux-mac-adapter.md`).

Eject is the literal last action of the run — nothing else (cleanup, notification
state, config writes) should happen after it.

**Never run this skill under `/loop` (or leave a `ScheduleWakeup` pending when it
finishes).** This pipeline is a one-shot batch task with a real, final completion
state — Stage 13 above — not an open-ended/recurring task. `/loop`'s dynamic wakeup
mechanism has no way to know the run already completed; it just re-enters the
conversation with the same prompt on schedule, and a real run of this happened: the
wakeup fired after Stage 13 had already reported "Done," found nothing new to do,
and instead re-ran Stage 12-style verification and even re-applied the series-level
provider match that a prior run had already made — unrequested Jellyfin writes with
no new disc behind them, the same failure class the "never spawn a subagent" section
above warns about (quietly exceeding the actual assignment once no one is watching
closely). If a wakeup ever resumes mid-run, or resumes at all after a closing message
has already been printed, **the first action of that turn — before writing any
explanation to the user — is to call `ScheduleWakeup({stop: true})`.** Writing a
text reply that says "nothing to do" is not enough and does not stop anything: a
real run did exactly that, twice, ~20 minutes apart, correctly declining to redo any
work but never calling `stop: true` either time — so the loop kept firing on its own
schedule regardless, each wakeup costing a full turn for no reason, indefinitely,
until a human noticed and stopped it manually. Recognizing there's nothing to do and
actually stopping the loop are two different actions; the first does not imply the
second, and both are required.

**Closing message**: once eject is done, run
`python ${CLAUDE_SKILL_DIR}/scripts/run_timer.py elapsed --staging-path
<staging.path>` and report its `"formatted"` value (e.g. `"2h 14m 03s"`) as part of
your closing comments to the user — it also deletes its own working file, same
reasoning as `rip_processes.json` not persisting between runs. This is a closing
chat message, not a pipeline action, so it doesn't conflict with eject being the
literal last pipeline action above. If Stage 11 ran and anything was explicitly
deferred to staging (the user's own choice, already discussed live at that point), a
brief one-line recap here is enough — it's a confirmation for the record at this
point, not the first time the user is hearing about it.

## Between batches — start a new session rather than chaining sets together

**Recommend a fresh Claude Code session per batch/set (e.g. per season, not per
disc), rather than running several sets back-to-back in one long-lived session.**
Once a set's discs are all placed and verified, that set's Stage 7 evidence
(extracted subtitle text, OCR frames, disc info scans) has no further use — but it
doesn't leave context on its own, and this pipeline's own pacing works against the
usual mitigations: prompt caching only offsets a long session's cost while requests
stay within its cache window, and a rip plus real-world disc-swapping time can
routinely exceed that between discs, let alone between whole sets; automatic
compaction avoids unbounded growth but costs a real summarization pass and is lossy
in a way that specifically works against this pipeline's own cross-disc consistency
checks (e.g. "cross-reference new evidence against what's already in the library" in
`identification-technique.md`), which need the actual prior evidence, not a
compacted summary of it. A session boundary per set is the one lever actually
available here — it isn't something SKILL.md can enforce, it's a practice to follow
when starting the next set.
