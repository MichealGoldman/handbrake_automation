# Progress — paused 2026-07-28, resuming this afternoon

## Done
- Full pipeline built: `config.py`, `identify.py`, `tmdb.py`, `tvdb.py`, `models.py`, `naming.py`, `convert.py`, `main.py` (see `CLAUDE.md` for architecture).
- `HandBrakeCLI.exe` found and confirmed working at `C:\Program Files (x86)\handbrakecli\HandBrakeCLI.exe`.
- Preset name confirmed exact: `Super HQ 1080p30 Surround` (via `--preset-list`).
- `.env` fully filled in (`SOURCE_DIR=C:\video`, `DEST_DIR=C:\Users\user\Videos`, `HANDBRAKE_CLI_PATH`, `HANDBRAKE_PRESET`, `TMDB_API_KEY`, `TVDB_API_KEY`) and loads cleanly via `config.load_config()`.
- Smoke-tested `identify.py` + `naming.py` on fake filenames (scene-release style like `The.Matrix.1999...mkv`) — parsing, TMDb/TVDB-style matching, and Plex-style path building all work, including the `--needs name review--` low-confidence flag.
- None of this has touched real files yet — no conversions have been run, nothing written to `C:\Users\user\Videos`.

## Blocker / open design problem (task #8, not started)
Inspected the real `C:\video` library: it's ripped-DVD content, not scene releases.
```
C:\video\BUFFY_S3_D1\C1_t00.mkv
C:\video\BUFFY_S3_D1\D1_t01.mkv
...
C:\video\BUFFY_SEASON2_DISC1..6\   (contents not yet inspected — user paused before we could look)
```
The filenames (`C1_t00.mkv`) carry **no title/episode info at all** — only the parent folder encodes show + season + disc (`BUFFY_S3_D1` = Buffy, season 3, disc 1). Two folder-naming conventions are already visible for the same show (`BUFFY_S3_D1` vs `BUFFY_SEASON2_DISC1`), so any folder parser needs to handle both.

`identify.py` currently only parses the filename via `guessit` — it will fail to extract anything useful for this library and everything will fall through to the low-confidence fallback path (still converts, but flagged for review with no real title/episode).

## Next steps when we resume
1. Finish inspecting `C:\video` (esp. the `BUFFY_SEASON2_DISC*` folders) to confirm the naming patterns in play.
2. Design folder-based identification: parse show + season (+ disc) from the parent folder, and figure out how to map disc + track-in-disc-order to actual episode numbers (likely: sort files within a disc, use TVDB's per-season episode count to sanity-check the total across all discs for that season, flag the whole season for review if the count doesn't line up).
3. Decide whether to test against the live `C:\video` tree or a scratch copy, since the user is actively using these files.
4. Once identify logic is updated, do a real end-to-end run (task #8) on a small subset before letting it loose on everything.

## Repo state
Not committed — `git status` shows `CLAUDE.md`, `config.py`, `convert.py`, `identify.py`, `main.py`, `models.py`, `naming.py`, `requirements.txt`, `tmdb.py`, `tvdb.py`, `.env.example` untracked, plus a modified `.gitignore` (added `logs/`). `.env` is correctly git-ignored, not shown. Nothing has been committed yet — waiting on your go-ahead.
