"""Shared result type produced by the TMDb/TVDB clients and consumed by naming.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CONFIDENCE_THRESHOLD = 85.0


@dataclass(frozen=True)
class MediaMatch:
    media_type: str  # "movie" or "episode"
    title: str  # movie title, or series title for episodes
    year: Optional[int]  # movie release year, or series' first-air year
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_title: Optional[str] = None
    confidence: float = 0.0
    source: str = ""

    @property
    def needs_review(self) -> bool:
        return self.confidence < CONFIDENCE_THRESHOLD
