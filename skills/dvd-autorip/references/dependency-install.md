# Dependency install offers (Stage 1)

What Stage 1 does with a missing tool from `check_dependencies.py --json`'s report,
beyond just relaying the install hint. Use `--json` here, not the human-readable
output — this flow is driven off each check's structured `key`/`required`/`found`/
`installable`/`install_hint` fields, not parsed prose.

## The flow, per missing check (`found: false`)

**Required tool** (`required: true`):
1. Always ask — required-tool declines are never remembered, every future run asks
   again (see "Why required declines aren't persisted" below).
2. If `installable: true`, offer to run the exact command in `install_hint` (adapting
   it if the hint names a specific package manager that isn't actually the right one
   for this machine — e.g. `apt` shown as the common case for a Linux hint doesn't
   apply on Fedora/Arch; use judgment, don't blindly run the literal command on a
   distro it doesn't match). If `installable: false` (no single runnable command
   exists — e.g. MakeMKV, Python itself), just relay the instructions and ask the
   user to handle it, then continue once they say they're ready.
3. Running the install command goes through the normal tool-permission flow like any
   other command this skill runs — no separate confirmation needed beyond the
   conversational ask itself.
4. After an install attempt (or the user confirming they did it manually), re-run
   `check_dependencies.py` to confirm it actually worked before proceeding — don't
   assume success from the install command's exit code alone.
5. **If the user declines outright, or the tool is still missing after a genuine
   attempt: hard stop.** Explain plainly why the run can't proceed without it. Don't
   continue to Stage 2 with a required tool missing.

**Optional tool** (`required: false`, currently only `tesseract`):
1. First check `config.local.json`'s `setup.declined_optional_installs` — **if this
   check's `key` is already in that list, skip asking entirely.** Just note it's
   unavailable (same as the existing "missing optional tool" behavior) and continue.
   This is what makes the decline actually stick across runs instead of re-asking
   every time.
2. Otherwise, if `installable: true`, ask if they want it installed now.
   - Yes: run the command, re-check, report the outcome either way — not a hard stop
     regardless of success (it's optional), just tell the user plainly if it didn't
     work.
   - No: **write this check's `key` into `config.local.json`'s
     `setup.declined_optional_installs`** (append, don't overwrite the array), tell
     the user plainly that this preference is saved and future runs won't ask again,
     and mention how to reverse it: remove the key from the array themselves, or ask
     Claude to remove it. (Asking Claude to install the tool later does **not** by
     itself clear the decline — the array entry has to actually be removed first, or
     this check keeps getting silently skipped.)
   - If `installable: false`, there's no command to offer running — just relay the
     limitation as before. Still worth asking "want me to stop mentioning this each
     run?" and recording the decline the same way if they say yes, for consistency,
     even though no install was actually offered.
3. Continue to Stage 2 either way — a missing optional tool never blocks the run.

## Why required declines aren't persisted, but optional ones are

A required tool missing means the pipeline **cannot function at all** — there's no
useful "keep declining silently" state to remember, since every future run would hit
the exact same hard stop regardless. Asking every time isn't friction, it's the only
option, and it gives the user a fresh chance to say yes once they're actually ready
(e.g. they installed it manually between sessions and just haven't told Claude).

A missing optional tool degrades one specific fallback path (OCR) but the pipeline
works fine without it — repeatedly asking about something the user has already said
no to is pure friction with no corresponding benefit, so remembering the decision is
the right default. `bonus_content.remember_choice` (see `bonus-content.md`) is the
same pattern applied to a different setting, for the same reason.

## Example exchange (optional tool decline)

> Claude: Tesseract OCR isn't installed — this only affects discs with image-only
> subtitles, where the pipeline falls back to frame-based identification instead
> (weaker but still usable). Want me to install it now (`winget install --id
> UB-Mannheim.TesseractOCR`)?
>
> User: no, don't bother
>
> Claude: Got it — I've noted that in your config so I won't ask again on future
> runs. If you change your mind later, just tell me to install it, or remove
> `"tesseract"` from `setup.declined_optional_installs` in `config.local.json`
> yourself.
