# Potential enhancements

Ideas that aren't built, aren't scheduled, and shouldn't be assumed to work until
someone actually validates them against a real disc — this is a backlog of honest
gaps and proposals, not a roadmap with commitments. See also GitHub Issues/
Discussions for anything a user wants to track more actively (comments, status,
cross-referencing a PR) — this file is for lower-friction logging of ideas that
came up before they're worth a full issue.

## Play-all-only TV/Blu-ray discs (no separate per-episode titles) aren't detected or split

**Raised via community feedback** after posting this project publicly, and confirmed
as a real gap by reading the actual detection code (not just the docs) — not
speculative.

**The problem**: `detect_exclusions.py`'s concat-title detection (see
`identification-technique.md`'s "Detecting a full-season 'concat' title before
ripping it") works by clustering titles on the disc by similar duration, then
checking whether a candidate concat title's duration equals the *sum* of one of
those clusters. That comparison is entirely disc-internal — it requires separate,
individual-episode-shaped titles to be present on the same disc to build a cluster
against. **A disc that only authors a play-all title, with no individual episode
titles at all, has nothing to cluster against — the concat check can't fire, not
even as `"ambiguous-concat"`.** It falls through as an ordinary, unflagged single
title. Reportedly common on DVD, occasional on Blu-ray.

It compounds from there: SKILL.md's Stage 6 classification heuristic is "1 title
>50min → movie disc." A play-all title spanning a whole season (e.g. 4 episodes ×
~22min ≈ 88 minutes) would trigger that threshold and likely get misclassified as a
movie disc outright, rather than recognized as a multi-episode blob needing to be
split.

**Proposed direction (from community feedback, reviewed but not implemented)**: use
MKVToolNix's `mkvmerge --split chapters:<N,N,...>` to losslessly split the single
ripped title at chapter boundaries once it's recognized as a play-all blob —
purpose-built for exactly this (MKV-native, remux only, no re-encode), and a
cleaner primitive for the job than hand-composing ffmpeg segment cuts from
ffprobe-extracted chapter timestamps. The splitting mechanics are the easy part;
what's still unsolved:

1. **Detection has to happen post-rip, not pre-rip** — there's no cluster to compare
   against before ripping for this specific case, unlike the existing concat check.
   Stage 6 (or a new step near it) would need a heuristic for "single long title
   whose duration divides plausibly into N episode-length chunks" to even trigger
   the split path.
2. **Which chapters are actual episode boundaries is a real sub-problem.**
   `--split chapters:all` would almost certainly over-split — a real episode
   typically has multiple chapters of its own. Needs a heuristic (e.g. chapter
   timestamps landing at roughly-equal, plausible-episode-length intervals) before
   calling `mkvmerge`, not a blind full split.
3. **Unverified assumption**: that play-all-authored titles reliably carry a chapter
   mark at every episode boundary. Plausible given how DVD authoring generally
   works, but this project's own norm has been not to trust an assumption about
   disc structure until it's checked against a real disc — hasn't been done yet.
4. **New optional dependency**: `mkvmerge`/MKVToolNix isn't currently checked for at
   all. Would need the same treatment as Tesseract — `check_dependencies.py`
   detection with fallback install-location checks, an optional `mkvmerge.path`
   config field, install-offer flow, README/config-schema documentation — and
   should stay optional, since most runs won't hit this case.
5. Splitting produces N files with no identity yet — Stage 7 still has to run full
   per-file identification on each one afterward. This unblocks the mechanical
   problem, not the identification work.

**Not started.** Needs a real play-all-only disc to validate the chapter-boundary
assumption before any of this is built for real, consistent with how everything
else in this pipeline got built.
