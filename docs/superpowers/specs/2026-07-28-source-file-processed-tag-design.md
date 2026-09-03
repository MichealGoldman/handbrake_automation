# Source-file "processed" tag — design

Date: 2026-07-28

## Problem

`main.py` currently decides whether to (re)convert a source file purely by
checking if the computed destination path already exists
(`naming.build_dest_path()` + `Path.exists()`). There is no signal on the
*source* side that a file has already been handled. The user wants to be able
to look at `SOURCE_DIR` directly (e.g. `C:\video`) and tell, file by file,
which ones have already been run through the pipeline — independent of what's
sitting in `DEST_DIR` — as groundwork for eventually archiving/moving
processed source files by hand.

## Design

### Tag scheme

Two filename prefixes, applied to the source `.mkv` file in place (same
folder, rename only — never moved, never deleted):

- `DONE_` — converted successfully, confidence at or above the review
  threshold.
- `REVIEW_` — converted successfully, but the match was low-confidence
  (`MediaMatch.needs_review`) — mirrors the existing `--needs name review--`
  suffix already applied to the *destination* filename in `naming.py`.

The two prefix constants are defined in `naming.py`, next to the existing
`--needs name review--` convention, and imported by `identify.py` and
`main.py`.

A source file that already carries either prefix is never re-tagged or
switched to the other prefix.

### Where tagging happens (`main.py`)

1. **After a successful `convert_file()` call** — rename the source file with
   `DONE_` or `REVIEW_` based on `match.needs_review`.
2. **In the existing "skip — dest already exists" branch** — if the source
   file isn't already prefixed, tag it there too. This makes the tag
   self-healing: files converted by a prior run (before this feature existed,
   or a run that was interrupted after conversion but before tagging) get
   tagged the next time `main.py` sees them, with no separate backfill step.

A rename failure (e.g. file locked/in use) is logged as a warning and does
not affect the run's pass/fail counting or exit code — the conversion itself
already succeeded; only the cosmetic tag failed.

`main.py`'s final summary log line gains a `tagged` count alongside the
existing converted/skipped/failed/flagged counts.

### Identification impact (`identify.py`)

`parse_filename()` runs `guessit` on the filename only. If a file is
re-scanned after already being tagged (e.g. `DONE_The.Matrix.1999.mkv`), the
prefix must be stripped before handing the name to `guessit`, so the tagging
mechanism never degrades identification quality on a later run. Fix: at the
top of `parse_filename`, strip a recognized `DONE_`/`REVIEW_` prefix from
`path.name` before parsing.

### Documentation

`CLAUDE.md`'s architecture section currently states "Source `.mkv` files are
never moved or deleted." That remains true, but source files are now renamed
in place after processing — the doc needs a one-line clarification so it
doesn't read as stale.

## Out of scope

- No central manifest/database of processed files — the tag lives entirely
  on the filename.
- No change to the existing dest-exists skip logic — it remains the primary
  mechanism deciding whether to convert; the source tag is a secondary,
  informational marker plus a hook for future manual archiving.
- No automatic move/archive of tagged source files — that's a possible
  future step, not part of this change.
