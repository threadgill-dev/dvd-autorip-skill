# The formalized closing report (Stage 13)

A real batch can run for hours, span multiple disc swaps, and survive one or more
context compactions. Before this feature, Stage 13's closing message was whatever
Claude happened to still remember at that point — runtime came from
`run_timer.py`, but what actually got ripped/placed/excluded per disc, and anything
that went sideways along the way, depended on what was still in context, not on a
durable record. `run_report.py` fixes that the same way `rip_processes.json` and
`run_timer.py`'s own working file already do for their own concerns: a file in
`staging.path` that every stage writes to as things happen, read back once at the
very end.

## The four calls

- **`start`** — Stage 3, same call site as `run_timer.py start`. Creates an empty
  accumulator (`<staging.path>/run_report.json`).
- **`add-disc`** — Stage 12, once per disc, right before that disc's own staging
  cleanup (the filenames/details being recorded live in exactly the files that
  cleanup is about to delete — record first). One JSON object per disc:
  ```json
  {
    "volume_label": "ANGEL_S5D5",
    "drive_index": 1,
    "placed": [{"filename": "Angel - S05E16 - Shells.mkv"}],
    "bonus": [{"filename": "Angel Unbound.mkv", "handling": "kept", "subtype": "featurette"}],
    "excluded_pre_rip": [{"title_id": 3, "reason": "concat/Play-All"}],
    "needs_review": [{"description": "Title 5 duration mismatch vs disc menu claim", "resolution": "deferred"}]
  }
  ```
  Every key besides `volume_label` is optional — omit whatever doesn't apply to this
  disc rather than sending an empty list. Pull `placed`/`bonus` from what Stage 8/9
  actually did, `excluded_pre_rip` from Stage 4 step 1's exclusion list, and
  `needs_review` from how Stage 11 resolved each held title (`"resolved"`,
  `"deferred"`, or `"discarded"` — the three outcomes Stage 11 already defines).
- **`add-issue`** — anywhere, any time from Stage 3 onward, whenever something in
  the run didn't go the way it should have and is worth the user seeing in the
  closing report even though the run continued past it. This is a judgment call
  each time, the same discipline as everything else in Stages 6-11 — there's no
  fixed list of what qualifies and no mechanized trigger for it. Examples worth
  logging: a dependency install that failed and was worked around, a Jellyfin auth
  fallback (`X-Emby-Token` after a 401), a disc read needing the salvage procedure,
  an unexpected retry, a `check_directory_scoping.py` mismatch. Examples **not**
  worth logging: normal, expected outcomes already covered by `add-disc`'s own
  fields (a confirmed concat exclusion, a routine bonus-content decision) — this is
  for things that were surprising or went wrong, not a duplicate log of normal
  pipeline behavior.
- **`render`** — Stage 13, after `run_timer.py elapsed`. Reads the accumulator,
  formats it into a plain-text report, deletes the accumulator file (same
  end-of-batch cleanup reasoning as `run_timer.py`'s own file), and returns it as
  `"report"`. **Relay this string to the user verbatim as the closing message** —
  it already is the formalized summary; don't re-narrate it in different words on
  top of it.

## What the rendered report looks like

```
=== DVD Autorip -- Run Summary ===
Runtime: 2h 14m 03s
Discs processed: 2

--- ANGEL_S5D5 (drive 1) ---
Placed (2):
  - Angel - S05E16 - Shells.mkv
  - Angel - S05E17 - Underneath.mkv
Bonus content: none
Excluded pre-rip (1):
  - title 3: concat/Play-All
Needs review: none

--- ANGEL_S5D6 (drive 0) ---
Placed (1):
  - Angel - S05E18 - Origin.mkv
Bonus content (1):
  - Angel Unbound.mkv [featurette] -- kept
Excluded pre-rip: none
Needs review (1):
  - Title 5 duration mismatch vs disc menu claim -- deferred

=== Issues (1) ===
[warning] Stage 1: Jellyfin auth required header fallback (X-Emby-Token) after initial 401
```

A run with nothing to flag renders `"No hiccups or errors this run."` instead of an
`Issues` section — the absence of that section is itself the signal, not something
to call out further.

## Why per-disc, not per-title

Placement/bonus/exclusion/needs-review entries are grouped by disc, matching how
every other stage in this pipeline already reasons about a batch (Stage 4's
exclusion list is per-drive, Stage 12's cleanup is per-disc) — a title-by-title
report across a multi-disc season would be harder to scan for "did this disc come
out right" than the per-disc grouping the rest of the pipeline already uses.
