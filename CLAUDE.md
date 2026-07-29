# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python script that recursively scans a source folder for `.mkv` files, identifies each one as a movie or TV episode (via TMDb/TVDB lookups), and converts it with `HandBrakeCLI` into an `.mp4` written into a Jellyfin-style destination tree (`movies\Title (Year)\...` / `shows\Show\Season NN\...`).

## Commands

```
pip install -r requirements.txt   # guessit, requests, python-dotenv, rapidfuzz
cp .env.example .env              # then fill in the values below
python main.py                    # run the full scan -> identify -> convert pipeline
```

There is no test suite or build step configured in this repo yet.

Required `.env` values (see `.env.example`): `SOURCE_DIR`, `DEST_DIR`, `HANDBRAKE_CLI_PATH` (blank = search PATH), `HANDBRAKE_PRESET` (must exactly match a name from `HandBrakeCLI --preset-list`), `TMDB_API_KEY`, `TVDB_API_KEY`.

## Dev tooling

```
pip install -r requirements-dev.txt   # pylint, pydocstyle, pycodestyle, black, isort, mypy
python lint.py                        # auto-fixes (isort, black), then checks (pycodestyle, pydocstyle, pylint, mypy)
```

Every `.py` file must pass `python lint.py` cleanly before any commit — this is manual discipline (there's no git hook or CI wired up), not an automated gate. Docstrings are Google-style and required on every public module, class, and function (`pydocstyle`-enforced); private (`_`-prefixed) helpers don't need one. This project's assistant never runs `git push` — only the user pushes.

## Architecture

Pipeline, orchestrated by `main.py`, one file processed at a time (sequential by design — HandBrake already saturates all CPU cores per job):

1. `config.py` — loads and validates `.env` into a frozen `Config`; resolves `HandBrakeCLI` path (explicit `HANDBRAKE_CLI_PATH` or PATH lookup via `shutil.which`).
2. `discfolder.py` — handles ripped-disc libraries, where the filename is an opaque track id and the real information is in the parent folder. `build_disc_plan()` runs once over the whole file list *before* the per-file loop, because episode numbers are positional and can only be derived from the group: it matches folders like `BUFFY_S3_D1` (show + season + disc) and filenames ending in a `t##` track id, groups every track by (show, season), drops bonus features via a size check (`_drop_extras()` — any track under `EXTRAS_SIZE_RATIO` × the group median, since episodes in a season run to near-uniform length and one stray featurette shifts every subsequent episode number), sorts by (disc, track), and numbers them sequentially from 1. Returns a `dict[Path, DiscEpisode]` containing *only* the files it recognized — everything else is left to `identify.py`. `DiscEpisode.group_size` carries the total track count for the group, which is what makes the count check in step 4 possible.
3. `identify.py` — `parse_filename()` runs `guessit` on the filename only (not the path) to produce a `ParsedName`: media type (`movie`/`episode`), title, year, season, episode. Returns `None` if guessit can't extract a title.
4. `tmdb.py` / `tvdb.py` — take the `ParsedName` fields and query the respective API for the best fuzzy match (`rapidfuzz.fuzz.WRatio` on title, small bonus for year match), returning a `models.MediaMatch`. `TVDBClient` handles its own v4 auth (POST `/login` → bearer token, lazily fetched on first request). TVDB episode lookup pages through `/series/{id}/episodes/default` to find the season/episode; if no matching episode is found, confidence is capped below the review threshold even if the series match itself was good. `search_disc_episode()` is the disc-rip counterpart: it resolves the series, then compares `get_season_episodes()`'s count against `DiscEpisode.group_size` — equal counts mean the positional mapping lines up, so it attaches the real episode title; unequal counts mean the mapping is off by an unknown amount, so confidence is capped and no title is attached rather than asserting a wrong one.
5. `models.MediaMatch` — the shared result type; `needs_review` is `confidence < CONFIDENCE_THRESHOLD` (85, defined in `models.py`).
6. `naming.py` — `build_dest_path()` turns a `MediaMatch` into the final Jellyfin-style path, under the `MOVIES_FOLDER` / `SHOWS_FOLDER` top-level names (`movies` / `shows`, lowercase to match the existing library tree — the conventional `Movies`/`TV Shows` would build a second library alongside the real one). Low-confidence matches get `" --needs name review--"` appended to the filename (not the folder name) so they're easy to grep for after a run but still land in a reasonable folder.
7. `convert.py` — `convert_file()` shells out to `HandBrakeCLI -i <src> -o <dest> --preset <preset>`; deletes a partial output file if the process exits non-zero *or* if the run is interrupted (Ctrl-C reaches HandBrake too, so it dies mid-write). `is_complete_output()` is the second half of that defense: it judges an *existing* destination by size against its source (`MIN_OUTPUT_SIZE_RATIO`, 10% — far below any plausible encode ratio, so it catches near-empty truncation without second-guessing compression). The cleanup handler only protects runs that die where Python can catch it; the size check repairs whatever slipped through, including stubs left by older versions of the code.
8. `main.py` — ties it together: finds all `*.mkv` under `SOURCE_DIR`, builds the disc plan once, then per file calls `_identify()` (disc plan first, then `parse_filename()` + API lookup; falls back to the raw parsed name at confidence 0 if nothing matches at all — so a file always gets *some* destination path rather than being silently dropped), skips conversion if the destination file already exists *and* passes `convert.is_complete_output()` — a destination that exists but is too small is a partial from an interrupted run, so it's deleted and re-encoded rather than skipped (counted separately as `repaired`). Converts, and logs to both stdout and `logs/run_<timestamp>.log` (gitignored). Run tallies live in `_RunStats`, reported by `_log_summary()`.

   The conversion loop runs inside `wakelock.keep_awake()`, which holds a `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` request for the duration. Windows' idle sleep timer keys off user input, not CPU load, so a multi-hour encode would otherwise be suspended the first time nobody touches the machine for the idle timeout. Only sleep is suppressed — the display still blanks. The request is released in a `finally`, so an interrupted run never leaves sleep disabled behind it, and a failed or non-Windows request is logged and ignored rather than aborting the run.

   After a file is handled, `_tag_source()` renames the **source** in place with a `DONE_` or `REVIEW_` prefix (mirroring `naming.REVIEW_MARKER` on the destination side), so `SOURCE_DIR` can be read on its own to see what's been processed. Source files are never moved or deleted — only renamed. Tagging also runs on the "already exists" path, so a run interrupted after conversion self-heals on the next pass with no backfill step. A failed rename is logged and ignored: the conversion already succeeded, so a cosmetic tag must not affect the exit code. `identify.parse_filename()` strips the prefix before running guessit, and `discfolder`'s track regex matches on the trailing `t##`, so re-scanning a tagged library identifies identically.

## Known limitations

`identify.py` only parses the **filename**, not the parent folder. It assumes filenames carry a usable title/year/SxxExx (typical scene-release naming). Ripped-disc libraries, where the meaningful info is in the folder name and the filename is just an opaque track id (e.g. `BUFFY_S3_D1\C1_t00.mkv`), are handled separately by `discfolder.py` (step 2 above) — but only when the folder matches the season/disc pattern *and* the filename ends in a `t##` track id. Anything else still falls through to `parse_filename()` and, failing that, the review-flagged fallback.

Positional episode numbering is a heuristic, and its accuracy depends on the track count matching the season's real episode count. The size filter removes the common cause of a mismatch (bonus features), but when the counts still disagree the pipeline can tell you *that* the mapping is wrong and not *where* — it flags every file in the group at confidence 60 with no episode titles, rather than guessing which track is spurious. A missing rip, a duplicated track, or an extra that happens to be episode-length all land here and need a human.

The size filter cuts on relative size, not content, so it only catches extras that are markedly shorter than an episode. A feature-length documentary or a "play all" track spanning a whole disc is longer than the median and survives the filter — the count check then flags the group. It also assumes uniform episode length within a season; a season mixing 22- and 44-minute episodes could see the short ones dropped (they'd still convert, just via the review path).

Episode *order* within a disc is assumed to follow track number. Discs that lay tracks down out of broadcast order will number confidently and wrongly — the count check passes, so nothing gets flagged.
