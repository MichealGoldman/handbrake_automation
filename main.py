"""Recursively finds, identifies, and converts .mkv files to Plex/Jellyfin .mp4s.

Scans SOURCE_DIR for .mkv files, identifies each one via TMDb/TVDB, and
converts it with HandBrakeCLI into DEST_DIR's Plex/Jellyfin-style folder
structure.

Usage:
    python main.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import tmdb
from config import Config, ConfigError, load_config
from convert import ConversionError, convert_file
from identify import parse_filename
from models import MediaMatch
from naming import build_dest_path
from tvdb import TVDBClient, search_episode

LOG_DIR = Path(__file__).parent / "logs"


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


def _identify(path: Path, tvdb_client: TVDBClient, config: Config) -> MediaMatch:
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

    tvdb_client = TVDBClient(config.tvdb_api_key)

    converted = 0
    skipped_existing = 0
    failed = 0
    flagged: list[Path] = []

    for source_path in source_files:
        logging.info("Processing: %s", source_path)

        try:
            match = _identify(source_path, tvdb_client, config)
        except Exception:  # pylint: disable=broad-exception-caught
            # One bad file must not abort the whole run.
            logging.exception("Identification failed for %s", source_path)
            failed += 1
            continue

        dest_path = build_dest_path(match, config.dest_dir)

        if dest_path.exists():
            logging.info("Skipping (already exists): %s", dest_path)
            skipped_existing += 1
            continue

        if match.needs_review:
            flagged.append(dest_path)
            logging.warning(
                "Low-confidence match (%.0f%%), flagged for review: %s",
                match.confidence,
                dest_path,
            )

        try:
            convert_file(
                source_path,
                dest_path,
                config.handbrake_cli_path,
                config.handbrake_preset,
            )
        except ConversionError:
            logging.exception("Conversion failed for %s", source_path)
            failed += 1
            continue

        logging.info("Converted -> %s", dest_path)
        converted += 1

    logging.info(
        "Done. Converted: %d, skipped (already existed): %d, failed: %d, "
        "flagged for review: %d",
        converted,
        skipped_existing,
        failed,
        len(flagged),
    )
    if flagged:
        logging.info("Flagged for review:")
        for path in flagged:
            logging.info("  %s", path)

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
