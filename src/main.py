"""Recursively finds, identifies, and converts .mkv files to Plex/Jellyfin .mp4s.

Scans SOURCE_DIR for .mkv files, identifies each one via TMDb/TVDB, and
converts it with HandBrakeCLI into DEST_DIR's Plex/Jellyfin-style folder
structure.

Usage:
    python src/main.py
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Iterator

import tmdb
from config import Config, ConfigError, load_config
from convert import (
    ConversionError,
    convert_file,
    is_complete_output,
    keep_subtitles,
    probe_height,
    probe_subtitle_codecs,
    remove_partial_output,
    select_preset,
)
from discfolder import (
    AMBIGUOUS_FEATURE_CONFIDENCE,
    MovieRip,
    RipPlan,
    build_rip_plan,
)
from identify import parse_filename
from models import MediaMatch
from naming import DONE_PREFIX, REVIEW_PREFIX, SKIP_PREFIX, build_dest_path
from tvdb import TVDBClient, search_disc_episode, search_episode
from wakelock import keep_awake

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# How often to log a "still encoding" line during a single conversion. Long
# enough not to clutter the log, short enough to tell a working encode from a
# hung one.
HEARTBEAT_INTERVAL = 600.0


@dataclass
class _RunStats:
    """Per-run tallies, reported in the closing summary line."""

    converted: int = 0
    skipped_existing: int = 0
    skipped_extras: int = 0
    repaired: int = 0
    failed: int = 0
    tagged: int = 0
    flagged: list[Path] = field(default_factory=list)


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@contextmanager
def _heartbeat(label: str) -> Iterator[None]:
    # HandBrake's own output is captured, so a multi-hour encode is otherwise a
    # silent gap in the log with no way to tell progress from a hang.
    stop = threading.Event()
    started = time.monotonic()

    def tick() -> None:
        # wait() returns True once stop is set, so the loop ends promptly on
        # the way out instead of sleeping out the rest of the interval.
        while not stop.wait(HEARTBEAT_INTERVAL):
            logging.info(
                "Still encoding %s (%s elapsed)",
                label,
                _format_duration(time.monotonic() - started),
            )

    thread = threading.Thread(target=tick, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def _log_summary(stats: _RunStats, elapsed: float) -> None:
    """Log the run totals, then every destination flagged for review."""
    logging.info(
        "Done in %s. Converted: %d, skipped (already existed): %d, "
        "skipped (disc extras): %d, re-converted (partial output): %d, "
        "failed: %d, tagged: %d, flagged for review: %d",
        _format_duration(elapsed),
        stats.converted,
        stats.skipped_existing,
        stats.skipped_extras,
        stats.repaired,
        stats.failed,
        stats.tagged,
        len(stats.flagged),
    )
    if stats.flagged:
        logging.info("Flagged for review:")
        for path in stats.flagged:
            logging.info("  %s", path)


def _setup_logging() -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def _outcome_prefix(match: MediaMatch) -> str:
    return REVIEW_PREFIX if match.needs_review else DONE_PREFIX


def _tag_source(path: Path, prefix: str) -> bool:
    """Rename a processed source file in place with a DONE_/REVIEW_/SKIP_ prefix."""
    if path.name.startswith((DONE_PREFIX, REVIEW_PREFIX, SKIP_PREFIX)):
        # Already tagged by an earlier run; never re-tag or switch prefix.
        return False

    try:
        path.rename(path.with_name(prefix + path.name))
    except OSError:
        # The conversion already succeeded; only the cosmetic tag failed, so
        # this must not affect the run's pass/fail counting or exit code.
        logging.warning("Could not tag source file: %s", path, exc_info=True)
        return False

    return True


def _identify_movie_rip(movie_rip: MovieRip, config: Config) -> MediaMatch:
    # No year: disc folder names don't carry one.
    match = tmdb.search_movie(movie_rip.title, None, config.tmdb_api_key)

    if match is None:
        return MediaMatch(
            media_type="movie",
            title=movie_rip.title,
            year=None,
            confidence=0.0,
            source="none",
        )

    if movie_rip.ambiguous:
        # Another track was nearly as large, so the feature pick is a guess.
        # Cap rather than assert, matching how an untrustworthy episode
        # mapping is handled.
        return replace(
            match,
            confidence=min(match.confidence, AMBIGUOUS_FEATURE_CONFIDENCE),
        )

    return match


def _identify(
    path: Path,
    tvdb_client: TVDBClient,
    config: Config,
    plan: RipPlan,
) -> MediaMatch:
    # Ripped-disc tracks carry no usable title in the filename; their show,
    # season, and episode order come from the folder name instead.
    disc_episode = plan.episodes.get(path)
    if disc_episode is not None:
        match = search_disc_episode(tvdb_client, disc_episode)
        if match is not None:
            return match

        return MediaMatch(
            media_type="episode",
            title=disc_episode.show,
            year=None,
            season=disc_episode.season,
            episode=disc_episode.episode,
            episode_title=None,
            confidence=0.0,
            source="none",
        )

    # Same problem on a movie disc, minus the numbering: the folder name is
    # the only place the title survives.
    movie_rip = plan.movies.get(path)
    if movie_rip is not None:
        return _identify_movie_rip(movie_rip, config)

    parsed = parse_filename(path)

    if parsed is None:
        return MediaMatch(
            media_type="movie",
            title=path.stem,
            year=None,
            confidence=0.0,
            source="none",
        )

    if parsed.media_type == "movie":
        match = tmdb.search_movie(parsed.title, parsed.year, config.tmdb_api_key)
    elif parsed.season is not None and parsed.episode is not None:
        match = search_episode(
            tvdb_client, parsed.title, parsed.year, parsed.season, parsed.episode
        )
    else:
        match = None

    if match is not None:
        return match

    # No API match at all: fall back to the guessit-parsed name so the file
    # still gets converted, just flagged for manual review.
    return MediaMatch(
        media_type=parsed.media_type,
        title=parsed.title,
        year=parsed.year,
        season=parsed.season,
        episode=parsed.episode,
        episode_title=None,
        confidence=0.0,
        source="none",
    )


def _process_file(
    source_path: Path,
    tvdb_client: TVDBClient,
    config: Config,
    plan: RipPlan,
    stats: _RunStats,
) -> None:
    """Identify, convert, and tag one source file, recording the outcome."""
    # Checked before identification so a track that won't be encoded costs no
    # API call either.
    movie_rip = plan.movies.get(source_path)
    if movie_rip is not None and not movie_rip.is_feature:
        logging.info("Skipping extra: %s", source_path)
        stats.skipped_extras += 1
        if _tag_source(source_path, SKIP_PREFIX):
            stats.tagged += 1
        return

    try:
        match = _identify(source_path, tvdb_client, config, plan)
    except Exception:  # pylint: disable=broad-exception-caught
        # One bad file must not abort the whole run.
        logging.exception("Identification failed for %s", source_path)
        stats.failed += 1
        return

    dest_path = build_dest_path(match, config.dest_dir)

    if dest_path.exists():
        if is_complete_output(source_path, dest_path):
            logging.info("Skipping (already exists): %s", dest_path)
            stats.skipped_existing += 1
            # Self-healing: files converted before tagging existed (or by a
            # run interrupted after conversion) get tagged here, so no
            # separate backfill step is needed.
            if _tag_source(source_path, _outcome_prefix(match)):
                stats.tagged += 1
            return

        # Truncated leftover from an interrupted run. Left in place it would
        # satisfy the check above on every future run, so the episode would
        # stay broken forever -- discard it and encode again.
        logging.warning(
            "Destination exists but is too small to be a finished encode; "
            "re-converting: %s",
            dest_path,
        )
        remove_partial_output(dest_path)
        stats.repaired += 1

    if match.needs_review:
        stats.flagged.append(dest_path)
        logging.warning(
            "Low-confidence match (%.0f%%), flagged for review: %s",
            match.confidence,
            dest_path,
        )

    height = probe_height(source_path, config.handbrake_cli_path)
    preset = select_preset(height, config.handbrake_preset, config.handbrake_preset_hd)
    if preset != config.handbrake_preset:
        logging.info("Source is %sp, using preset: %s", height, preset)

    codecs = probe_subtitle_codecs(source_path, config.handbrake_cli_path)
    subtitles = keep_subtitles(codecs)
    if not subtitles:
        logging.info(
            "Subtitles disabled: %s cannot be muxed into MP4 and would be "
            "burned into the picture",
            ", ".join(sorted(codecs)),
        )

    started = time.monotonic()
    try:
        with _heartbeat(source_path.name):
            convert_file(
                source_path,
                dest_path,
                config.handbrake_cli_path,
                preset,
                config.cpu_percent,
                subtitles,
            )
    except ConversionError:
        logging.exception(
            "Conversion failed after %s for %s",
            _format_duration(time.monotonic() - started),
            source_path,
        )
        stats.failed += 1
        return

    logging.info(
        "Converted in %s -> %s",
        _format_duration(time.monotonic() - started),
        dest_path,
    )
    stats.converted += 1
    if _tag_source(source_path, _outcome_prefix(match)):
        stats.tagged += 1


def main() -> int:
    """Run the full scan -> identify -> convert pipeline.

    Returns:
        0 if every file converted without failure, 2 if any file failed,
        1 if configuration could not be loaded.
    """
    log_path = _setup_logging()

    try:
        config = load_config()
    except ConfigError as e:
        logging.error(str(e))
        return 1

    logging.info("Source: %s", config.source_dir)
    logging.info("Destination: %s", config.dest_dir)
    logging.info("Preset: %s", config.handbrake_preset)
    logging.info("Log file: %s", log_path)

    source_files = sorted(config.source_dir.rglob("*.mkv"))
    logging.info("Found %d .mkv file(s)", len(source_files))

    plan = build_rip_plan(source_files, config.source_dir)
    features = sum(1 for rip in plan.movies.values() if rip.is_feature)
    extras = len(plan.movies) - features
    if plan.episodes or plan.movies:
        logging.info(
            "%d episode track(s) and %d movie feature(s) resolved from folder "
            "names; %d extra track(s) will be skipped",
            len(plan.episodes),
            features,
            extras,
        )

    tvdb_client = TVDBClient(config.tvdb_api_key)

    stats = _RunStats()
    run_started = time.monotonic()

    with keep_awake() as awake:
        if awake:
            logging.info("Sleep suppressed until the run finishes")

        for index, source_path in enumerate(source_files, start=1):
            logging.info(
                "Processing [%d/%d]: %s", index, len(source_files), source_path
            )
            _process_file(source_path, tvdb_client, config, plan, stats)

    _log_summary(stats, time.monotonic() - run_started)

    return 0 if stats.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
