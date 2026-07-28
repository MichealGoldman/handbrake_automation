"""Builds Plex/Jellyfin-style destination paths from a MediaMatch."""

from __future__ import annotations

import re
from pathlib import Path

from models import MediaMatch

REVIEW_MARKER = " --needs name review--"
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')


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
        Full destination .mp4 path, e.g. "Movies/Title (Year)/Title
        (Year).mp4" or "TV Shows/Show/Season 01/Show - S01E02.mp4".
    """
    title = _sanitize(match.title)

    if match.media_type == "movie":
        folder_name = f"{title} ({match.year})" if match.year else title
        filename = folder_name
        if match.needs_review:
            filename += REVIEW_MARKER
        return dest_root / "Movies" / folder_name / f"{filename}.mp4"

    season = match.season or 0
    episode = match.episode or 0
    season_folder = f"Season {season:02d}"

    filename = f"{title} - S{season:02d}E{episode:02d}"
    if match.episode_title:
        filename += f" - {_sanitize(match.episode_title)}"
    if match.needs_review:
        filename += REVIEW_MARKER

    return dest_root / "TV Shows" / title / season_folder / f"{filename}.mp4"
