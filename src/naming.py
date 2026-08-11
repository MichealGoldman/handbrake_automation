"""Builds Plex/Jellyfin-style destination paths from a MediaMatch."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from models import MediaMatch

REVIEW_MARKER = " --needs name review--"

# Top-level library folders created under DEST_DIR. These must match the
# Jellyfin tree exactly -- writing a differently-named folder into the library
# builds a second, parallel one sitting next to the real one. The library moved
# to E:\media in Aug 2026 and uses "Movies"/"Shows"; it previously used
# lowercase. Windows compares paths case-insensitively so the difference is
# cosmetic there, but it is not on a Linux Jellyfin host reading the same tree.
MOVIES_FOLDER = "Movies"
SHOWS_FOLDER = "Shows"

# Prefixes applied to the *source* file after processing, so SOURCE_DIR can be
# read on its own to see what's been handled. DONE_ = converted with a
# confident match; REVIEW_ = converted, but the match needs checking (the
# source-side mirror of REVIEW_MARKER); SKIP_ = deliberately not converted,
# being bonus material on a movie disc. SKIP_ has to be its own prefix --
# tagging an extra DONE_ would assert it had been converted.
DONE_PREFIX = "DONE_"
REVIEW_PREFIX = "REVIEW_"
SKIP_PREFIX = "SKIP_"
TAG_PREFIXES = (DONE_PREFIX, REVIEW_PREFIX, SKIP_PREFIX)
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')


def folder_prefix(filenames: Iterable[str]) -> Optional[str]:
    """Decide the prefix a source folder has earned, if any.

    The same three prefixes are applied to a whole folder once every track
    inside it has been handled, so SOURCE_DIR can be skimmed a directory at a
    time rather than file by file.

    Args:
        filenames: Names of every source file directly in the folder.

    Returns:
        REVIEW_PREFIX if any track needs review, DONE_PREFIX if any converted
        cleanly, SKIP_PREFIX if the folder held nothing but extras, or None
        when the folder is empty or still holds an untagged track.
    """
    seen = set()
    for name in filenames:
        prefix = next((p for p in TAG_PREFIXES if name.startswith(p)), None)
        if prefix is None:
            # Still unprocessed, so the folder as a whole isn't finished.
            return None
        seen.add(prefix)

    # Review beats done beats skip: the marker exists to surface what still
    # needs a human, so the weaker outcome must never mask the stronger.
    for prefix in (REVIEW_PREFIX, DONE_PREFIX, SKIP_PREFIX):
        if prefix in seen:
            return prefix

    return None


def _sanitize(text: str) -> str:
    cleaned = _ILLEGAL_CHARS.sub("", text)
    return cleaned.strip(" .")


def build_dest_path(match: MediaMatch, dest_root: Path) -> Path:
    """Build the Plex/Jellyfin-style destination path for a match.

    Low-confidence matches get REVIEW_MARKER appended to the filename (not
    the folder name) so they're easy to find after a run.

    Args:
        match: The identified media to build a path for.
        dest_root: Root of the destination tree.

    Returns:
        Full destination .mp4 path, e.g. "movies/Title (Year)/Title
        (Year).mp4" or "shows/Show/Season 01/Show - S01E02.mp4".
    """
    title = _sanitize(match.title)

    if match.media_type == "movie":
        folder_name = f"{title} ({match.year})" if match.year else title
        filename = folder_name
        if match.needs_review:
            filename += REVIEW_MARKER
        return dest_root / MOVIES_FOLDER / folder_name / f"{filename}.mp4"

    season = match.season or 0
    episode = match.episode or 0
    season_folder = f"Season {season:02d}"

    filename = f"{title} - S{season:02d}E{episode:02d}"
    if match.episode_title:
        filename += f" - {_sanitize(match.episode_title)}"
    if match.needs_review:
        filename += REVIEW_MARKER

    return dest_root / SHOWS_FOLDER / title / season_folder / f"{filename}.mp4"
