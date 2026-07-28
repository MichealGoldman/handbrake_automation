"""Parses messy source filenames into structured hints using guessit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from guessit import guessit


@dataclass(frozen=True)
class ParsedName:
    media_type: str  # "movie" or "episode"
    title: str
    year: Optional[int]
    season: Optional[int]
    episode: Optional[int]


def parse_filename(path: Path) -> Optional[ParsedName]:
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
