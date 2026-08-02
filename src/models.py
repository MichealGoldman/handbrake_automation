"""Shared result type produced by the TMDb/TVDB clients and consumed by naming.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CONFIDENCE_THRESHOLD = 85.0


@dataclass(frozen=True)
class MediaMatch:
    """Best-match result from a TMDb/TVDB lookup, consumed by naming.py.

    Attributes:
        media_type: Either "movie" or "episode".
        title: Movie title, or series title for episodes.
        year: Movie release year, or series' first-air year.
        season: Season number, for episodes.
        episode: Episode number, for episodes.
        episode_title: Episode title, for episodes, if found.
        confidence: Match confidence score, 0-100.
        source: Which API produced the match ("tmdb", "tvdb", or "none").
    """

    media_type: str
    title: str
    year: Optional[int]
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_title: Optional[str] = None
    confidence: float = 0.0
    source: str = ""

    @property
    def needs_review(self) -> bool:
        """Return True if confidence is below CONFIDENCE_THRESHOLD."""
        return self.confidence < CONFIDENCE_THRESHOLD
