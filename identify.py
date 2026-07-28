"""Parses messy source filenames into structured hints using guessit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from guessit import guessit


@dataclass(frozen=True)
class ParsedName:
    """Structured hints guessit extracted from a source filename.

    Attributes:
        media_type: Either "movie" or "episode".
        title: Best-guess movie or series title.
        year: Release year (movies) or first-air year (episodes), if found.
        season: Season number, for episodes.
        episode: Episode number, for episodes.
    """

    media_type: str
    title: str
    year: Optional[int]
    season: Optional[int]
    episode: Optional[int]


def parse_filename(path: Path) -> Optional[ParsedName]:
    """Parse a source filename into a ParsedName using guessit.

    Args:
        path: Source file path; only the filename is inspected.

    Returns:
        A ParsedName, or None if guessit could not extract a title.
    """
    guess = guessit(path.name)

    title = guess.get("title")
    if not title:
        return None

    guess_type = guess.get("type")
    season = guess.get("season")
    episode = guess.get("episode")

    # guessit reports lists when it finds multiple season/episode numbers
    # (e.g. multi-episode files); take the first as our best guess.
    if isinstance(season, list):
        season = season[0] if season else None
    if isinstance(episode, list):
        episode = episode[0] if episode else None

    if guess_type == "episode" or (season is not None and episode is not None):
        return ParsedName(
            media_type="episode",
            title=str(title),
            year=guess.get("year"),
            season=int(season) if season is not None else None,
            episode=int(episode) if episode is not None else None,
        )

    return ParsedName(
        media_type="movie",
        title=str(title),
        year=guess.get("year"),
        season=None,
        episode=None,
    )
