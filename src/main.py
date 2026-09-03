r"""Recursively finds, identifies, and converts .mkv files to Plex/Jellyfin .mp4s.

Scans a source folder for .mkv files, identifies each one via TMDb/TVDB, and
converts it with HandBrakeCLI into DEST_DIR's Plex/Jellyfin-style folder
structure.

Usage:
    python src/main.py "C:\\Video"   # scan the folder given
    python src/main.py               # fall back to SOURCE_DIR in .env
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

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
from naming import (
    DONE_PREFIX,
    REVIEW_PREFIX,
    SKIP_PREFIX,
    TAG_PREFIXES,
    build_dest_path,
    folder_prefix,
)
from timefmt import format_duration as _format_duration
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
    folders_tagged: int = 0
    flagged: list[Path] = field(default_factory=list)


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
        "failed: %d, tagged: %d, folders tagged: %d, flagged for review: %d",
        _format_duration(elapsed),
        stats.converted,
        stats.skipped_existing,
        stats.skipped_extras,
        stats.repaired,
        stats.failed,
        stats.tagged,
        stats.folders_tagged,
        len(stats.flagged),
    )
    if stats.flagged:
        logging.info("Flagged for review:")
        for path in stats.flagged:
            logging.info("  %s", path)


def _log_rip_plan(plan: RipPlan) -> None:
    """Log what the disc-folder pass resolved, when it resolved anything."""
    if not plan.episodes and not plan.movies:
        return

    features = sum(1 for rip in plan.movies.values() if rip.is_feature)
    logging.info(
        "%d episode track(s) and %d movie feature(s) resolved from folder "
        "names; %d extra track(s) will be skipped",
        len(plan.episodes),
        features,
        len(plan.movies) - features,
    )


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


def _tag_folder(folder: Path) -> bool:
    """Rename one finished source folder with the prefix its tracks earned."""
    if folder.name.startswith(TAG_PREFIXES):
        # Already tagged by an earlier run; never re-tag or switch prefix.
        return False

    prefix = folder_prefix(track.name for track in folder.glob("*.mkv"))
    if prefix is None:
        return False

    try:
        folder.rename(folder.with_name(prefix + folder.name))
    except OSError:
        # Same reasoning as _tag_source: the conversions already succeeded, so
        # a cosmetic rename must not fail the run.
        logging.warning("Could not tag source folder: %s", folder, exc_info=True)
        return False

    return True


def _count_pending(source_files: Iterable[Path], source_dir: Path) -> Counter[Path]:
    """Count the queued tracks under each folder between them and the scan root.

    Args:
        source_files: Every file the run is about to process.
        source_dir: Scan root. Excluded from the counts -- it is never renamed.

    Returns:
        A count per folder, of tracks queued anywhere beneath it.
    """
    pending: Counter[Path] = Counter()

    for path in source_files:
        folder = path.parent
        # Bounded by the scan root on both sides: a path that somehow sits
        # outside it contributes nothing, so no folder above SOURCE_DIR can
        # ever reach zero and become a rename candidate.
        while folder != source_dir and source_dir in folder.parents:
            pending[folder] += 1
            folder = folder.parent

    return pending


def _tag_finished_folders(path: Path, source_dir: Path, pending: Counter[Path]) -> int:
    """Tag the folders whose last queued track has just been handled.

    Folders are tagged as the run goes rather than in a pass at the end, so an
    interrupted run leaves the same marks a completed one would; a power cut
    mid-run on 2026-09-03 lost every folder tag while the file tags survived.

    The pending count is what makes mid-run renaming safe. The loop holds
    absolute paths collected up front, so a folder may only be renamed once
    nothing beneath it is still queued -- renaming it any earlier would break
    the path of every track still waiting inside it.

    Args:
        path: The source file just processed.
        source_dir: Scan root, never renamed itself.
        pending: Per-folder queued-track counts, decremented in place.

    Returns:
        How many folders were renamed.
    """
    tagged = 0
    folder = path.parent

    # Deepest first, so a child is renamed before its parent: the reverse
    # invalidates the child's path and would silently skip it.
    while folder != source_dir and source_dir in folder.parents:
        pending[folder] -= 1
        if pending[folder] == 0 and _tag_folder(folder):
            tagged += 1
        folder = folder.parent

    return tagged


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


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a folder for .mkv files, identify each one, and convert it "
            "into the Plex/Jellyfin-style tree under DEST_DIR."
        )
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=None,
        help=(
            "Folder to scan recursively for .mkv files. Overrides SOURCE_DIR "
            "from .env, which is only used when this is omitted."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the full scan -> identify -> convert pipeline.

    Args:
        argv: Command-line arguments, defaulting to the process's own. The
            first positional argument, if given, is the folder to scan.

    Returns:
        0 if every file converted without failure, 2 if any file failed,
        1 if configuration could not be loaded.
    """
    args = _parse_args(argv)
    log_path = _setup_logging()

    try:
        config = load_config(args.source)
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
    _log_rip_plan(plan)

    tvdb_client = TVDBClient(config.tvdb_api_key)

    stats = _RunStats()
    run_started = time.monotonic()
    pending = _count_pending(source_files, config.source_dir)

    with keep_awake() as awake:
        if awake:
            logging.info("Sleep suppressed until the run finishes")

        for index, source_path in enumerate(source_files, start=1):
            logging.info(
                "Processing [%d/%d]: %s", index, len(source_files), source_path
            )
            _process_file(source_path, tvdb_client, config, plan, stats)
            stats.folders_tagged += _tag_finished_folders(
                source_path, config.source_dir, pending
            )

    _log_summary(stats, time.monotonic() - run_started)

    return 0 if stats.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
