# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python script that recursively scans a source folder for `.mkv` files, identifies each one as a movie or TV episode (via TMDb/TVDB lookups), and converts it with `HandBrakeCLI` into an `.mp4` written into a Plex/Jellyfin-style destination tree (`Movies\Title (Year)\...` / `TV Shows\Show\Season NN\...`).

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
2. `identify.py` — `parse_filename()` runs `guessit` on the filename only (not the path) to produce a `ParsedName`: media type (`movie`/`episode`), title, year, season, episode. Returns `None` if guessit can't extract a title.
3. `tmdb.py` / `tvdb.py` — take the `ParsedName` fields and query the respective API for the best fuzzy match (`rapidfuzz.fuzz.WRatio` on title, small bonus for year match), returning a `models.MediaMatch`. `TVDBClient` handles its own v4 auth (POST `/login` → bearer token, lazily fetched on first request). TVDB episode lookup pages through `/series/{id}/episodes/default` to find the season/episode; if no matching episode is found, confidence is capped below the review threshold even if the series match itself was good.
4. `models.MediaMatch` — the shared result type; `needs_review` is `confidence < CONFIDENCE_THRESHOLD` (85, defined in `models.py`).
5. `naming.py` — `build_dest_path()` turns a `MediaMatch` into the final Plex-style path. Low-confidence matches get `" --needs name review--"` appended to the filename (not the folder name) so they're easy to grep for after a run but still land in a reasonable folder.
6. `convert.py` — `convert_file()` shells out to `HandBrakeCLI -i <src> -o <dest> --preset <preset>`; deletes a partial output file if the process exits non-zero.
7. `main.py` — ties it together: finds all `*.mkv` under `SOURCE_DIR`, calls `_identify()` (falls back to the raw `ParsedName`, confidence 0, if the API lookup finds nothing at all — so a file always gets *some* destination path rather than being silently dropped), skips conversion if the destination file already exists, converts, and logs to both stdout and `logs/run_<timestamp>.log` (gitignored). Source `.mkv` files are never moved or deleted.

## Known limitation

`identify.py` only parses the **filename**, not the parent folder. It assumes filenames carry a usable title/year/SxxExx (typical scene-release naming). It does *not* yet handle ripped-disc libraries where the meaningful info (show + season + disc number) is in the folder name and the filename is just an opaque track id (e.g. `BUFFY_S3_D1\C1_t00.mkv`) — those will fail to parse and fall through to the review-flagged fallback path. Extending `identify.py`/`main.py` to also read the parent folder and derive episode order from disc/track sequence is a known open item, not yet implemented.
