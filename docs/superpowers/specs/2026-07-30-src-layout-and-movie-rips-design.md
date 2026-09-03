# Movie disc rips + `src/` layout — design

Date: 2026-07-30
Status: awaiting review

## Problem

In the run of 2026-07-29 (`logs/run_20260729_162802.log`) all 22 Buffy episodes were
identified correctly and every one of the 17 movie tracks failed.

`discfolder.build_disc_plan()` only claims a file when its parent folder matches
`_FOLDER_RE`, which requires **both** a season and a disc number (`BUFFY_S4_D1`). Movie
rip folders — `FIGHT CLUB`, `HOT FUZZ`, `NEAR_DARK`, `ZOMBIES_ANONYMOUS` — have no
season, so they are never claimed. They fall through to `identify.parse_filename()`,
which by design inspects the filename alone. The filename is an opaque track id
(`D1_t00.mkv`), so guessit returns a title of `"D1 t00"`, the TMDb lookup fails, and the
file is written to `movies\D1 t00\D1 t00 --needs name review--.mp4` at confidence 0.

Two further failures follow from the same cause:

- **Path collisions.** The destination is derived from the filename, and track ids repeat
  across folders. `B1_t00.mkv` occurs in `OFFICESPACE`, `Party Monster`, `WATCHMEN`, and
  `ZOMBIES_ANONYMOUS`; all four resolve to `movies\B1 t00\`. Only the first was encoded —
  the other three hit the "already exists" branch, were counted as skipped, and were then
  tagged `REVIEW_` in the source, so they appear processed while having produced nothing.
  Five movies were lost this way: Party Monster, Shaun of the Dead, Watchmen, Zombies
  Anonymous, and the Hitchhiker's feature.
- **Misclassification.** guessit read the `E1` in `HOT FUZZ\E1_t00.mkv` as "episode 1", so
  a movie was filed under `shows\t00\Season 00\`.

## Scope

In scope: recognising movie rip folders, and moving the source files under `src/`.

Out of scope: repairing the existing library. The 12 misnamed outputs, the 5 unconverted
movies, and the `REVIEW_` tags on their sources are being cleaned up by hand by the user.
No migration or backfill script is part of this work.

## Part 1 — `src/` layout

A plain directory, not a package. Running `python src/main.py` places `src/` at
`sys.path[0]`, so every existing flat import (`from naming import ...`) keeps resolving
with no changes.

```
handbrake_automation/
├── lint.py                 <- stays at root; it is a dev entry point
├── pyproject.toml
├── setup.cfg
├── requirements.txt
├── requirements-dev.txt
├── .env
├── logs/
├── docs/
├── tests/
│   └── test_discfolder.py
└── src/
    ├── main.py
    ├── config.py
    ├── convert.py
    ├── discfolder.py
    ├── identify.py
    ├── models.py
    ├── naming.py
    ├── tmdb.py
    ├── tvdb.py
    └── wakelock.py
```

Three call sites break on the move and must change with it:

1. **`main.py:38`** — `LOG_DIR = Path(__file__).parent / "logs"` is anchored to the source
   file, so logs would silently start landing in `src/logs/`. Becomes
   `Path(__file__).resolve().parent.parent / "logs"`, preserving `logs/` at the repo root.
2. **`lint.py:33`** — `ROOT.glob("*.py")` would match only `lint.py` itself and quietly
   stop checking the codebase. Becomes `lint.py` + `src/*.py` + `tests/*.py`.
3. **`config.py:12`** — bare `load_dotenv()` happens to survive, because it walks up from
   `config.py`'s own directory and finds `.env` one level above. Made explicit anyway:
   `load_dotenv(Path(__file__).resolve().parent.parent / ".env")`, so the behaviour does
   not rest on a search rule.

`pyproject.toml` and `setup.cfg` contain no paths and need no change for the move.
`.gitignore` already ignores `logs/` at any depth.

`CLAUDE.md` and `README.md` must have `python main.py` updated to `python src/main.py`.

## Part 2 — movie rip recognition

### New in `discfolder.py`

```python
# A track this close in size to the largest could be the feature itself -- a
# second cut, a "play all" track, or a double feature. Size cannot separate them.
FEATURE_MARGIN_RATIO = 0.9

# Matches the cap tvdb.search_disc_episode already applies to an untrustworthy
# positional mapping: below CONFIDENCE_THRESHOLD, so the file is flagged.
AMBIGUOUS_FEATURE_CONFIDENCE = 60.0

@dataclass(frozen=True)
class MovieRip:
    title: str        # recovered from the folder name
    is_feature: bool  # True for the one track chosen as the main feature
    ambiguous: bool   # another track is too close in size to be sure

@dataclass(frozen=True)
class RipPlan:
    episodes: dict[Path, DiscEpisode]
    movies: dict[Path, MovieRip]

def build_movie_plan(paths: list[Path], source_dir: Path) -> dict[Path, MovieRip]: ...
def build_rip_plan(paths: list[Path], source_dir: Path) -> RipPlan: ...
```

`build_rip_plan()` is the single entry point `main` calls, replacing its current
`build_disc_plan()` call; it invokes `build_disc_plan()` and `build_movie_plan()` in turn
and returns both results. The two builders stay separately callable so each can be tested
on its own.

`RipPlan` holds two separately typed dicts, deliberately not a `DiscEpisode | MovieRip`
union — the lookups stay type-safe and no `isinstance` branching is introduced. Its only
purpose is to spare `main` from threading two parameters through `_identify()` and
`_process_file()`, which would push the latter to six arguments and trip pylint.

### Recognition rule

Paths are grouped by parent folder. A folder is a movie rip when all three hold:

1. It is not `SOURCE_DIR` itself. A stray `t00.mkv` at the root has no folder name to
   recover a title from.
2. `parse_disc_folder(folder.name) is None` — it is not a TV season/disc folder. Reusing
   the existing TV regex as the negative test makes the two paths mutually exclusive by
   construction, so no file can ever be claimed by both.
3. At least one file in it has a trailing `t##` track id.

Only the `t##` files enter the plan. Anything else in the folder still falls through to
`parse_filename()`, matching how `build_disc_plan()` already behaves.

The title is `_clean_show(folder.name)`, the existing helper: `NEAR_DARK` → "Near Dark",
`ZOMBIES_ANONYMOUS` → "Zombies Anonymous".

### Choosing the feature

The largest track by file size, with two refinements:

- **A single-track folder needs no `stat()` at all** — that track is the feature by
  definition. This covers every movie folder in the current library except Hitchhiker's.
- **An `OSError` while sizing a multi-track folder means the folder is not claimed.** It
  falls through to the existing path rather than guessing, mirroring how `_drop_extras()`
  refuses to act on files it cannot size.

If the second-largest track is at least `FEATURE_MARGIN_RATIO` of the largest, the choice
cannot be trusted. The largest is still selected and still converted, but `ambiguous=True`
caps confidence at 60 — the value `tvdb.search_disc_episode()` already uses for an
untrustworthy mapping — so it lands with the `--needs name review--` marker. Nothing is
lost, since the runner-up remains in the source untouched. The Hitchhiker's folder is
4639 MB against 451 MB, so it is unambiguous.

### Identification

`_identify()` gains one branch, between the disc-plan check and `parse_filename()`:

```python
movie_rip = plan.movies.get(path)
if movie_rip is not None:
    match = tmdb.search_movie(movie_rip.title, None, config.tmdb_api_key)
    if match is not None:
        if movie_rip.ambiguous:
            match = replace(
                match,
                confidence=min(match.confidence, AMBIGUOUS_FEATURE_CONFIDENCE),
            )
        return match
    return MediaMatch(
        media_type="movie", title=movie_rip.title,
        year=None, confidence=0.0, source="none",
    )
```

`tmdb.search_movie()` is reused unchanged, with `year=None` since disc folder names carry
no year.

### Skipping extras

At the top of `_process_file()`, before `_identify()`, so a track that will not be encoded
costs no TMDb call either:

```python
movie_rip = plan.movies.get(source_path)
if movie_rip is not None and not movie_rip.is_feature:
    logging.info("Skipping extra: %s", source_path)
    stats.skipped_extras += 1
    if _tag_source(source_path, SKIP_PREFIX):
        stats.tagged += 1
    return
```

### Tagging

A third prefix, `SKIP_`, is added to `naming.py`, to `identify._strip_processed_tag()`, and
to the `startswith` guard in `_tag_source()`. Without it a skipped extra would have to be
tagged `DONE_`, which would assert it had been converted.

`_tag_source(path, needs_review: bool)` becomes `_tag_source(path, prefix: str)`. Three
outcomes do not fit a boolean, and the two existing call sites become explicit about which
tag they mean.

### Stats and logging

`_RunStats` gains `skipped_extras`, reported by `_log_summary()`. The startup line reports
both kinds, e.g. `22 episode track(s) and 10 movie feature(s) resolved from folder names;
7 extra track(s) will be skipped.`

### Consequences

- Destinations derive from the folder name, which is unique per movie, so the collisions
  cannot recur.
- The movie path never consults guessit, so `E1` can never again be read as "episode 1".
- Existing `REVIEW_` tags on the sources do not interfere: `_strip_processed_tag()` removes
  them before guessit, the track regex matches the trailing `t##` regardless of prefix, and
  folder names were never touched.

## Testing

`tests/test_discfolder.py`, using pytest and `tmp_path`. `build_movie_plan()` is pure and
filesystem-only, with no network calls, which makes it the natural seam. Six cases:

1. Single-track folder — the one track is the feature.
2. Multi-track folder — the largest is the feature, the rest are not.
3. A season/disc folder is *not* claimed as a movie rip.
4. A file directly in `SOURCE_DIR` is not claimed.
5. Near-equal sizes set `ambiguous=True`.
6. A folder with no `t##` files is ignored entirely.

Requires `pytest>=8.0` in `requirements-dev.txt` and `[tool.pytest.ini_options] pythonpath
= ["src"]` in `pyproject.toml`. pydocstyle's default `match` already excludes `test_*.py`,
and `missing-function-docstring` is already disabled in the pylint config, so `python
lint.py` stays clean without further exclusions.

## Commit sequence

1. **`src/` layout** — the file move plus the three call-site fixes and the doc updates.
   Pure restructuring, no behaviour change. Verified by `python lint.py` passing and
   `python src/main.py` producing an identical startup log against the real source tree.
2. **Movie rip recognition** — `MovieRip`, `RipPlan`, `build_movie_plan()`, the `_identify`
   branch, extras skipping, `SKIP_` tagging, stats, and the tests.

Keeping them separate means the feature diff is not buried in a 10-file rename.

## Assumptions

- **Tests are included.** Raised twice during design without a decision either way, so
  included on the grounds that the cost is one config line. Strike at review if unwanted.
- **The ambiguous-feature path is included.** No folder in the current library triggers it;
  it is roughly six lines and follows the codebase's existing preference for flagging an
  untrustworthy result over asserting a wrong one. Also droppable as YAGNI.
- `OFFICESPACE` cleans to `"Officespace"`, and `fuzz.WRatio` is expected to score that
  above the threshold of 85 against TMDb's `"Office Space"`. To be verified against the
  live API during implementation rather than assumed. If it scores lower the folder flags
  for review, which is an acceptable outcome either way.
