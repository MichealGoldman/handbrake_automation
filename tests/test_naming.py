"""Tests for building Jellyfin-style destination paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from models import MediaMatch
from naming import MOVIES_FOLDER, REVIEW_MARKER, SHOWS_FOLDER, build_dest_path

ROOT = Path(r"E:\media")


def _movie(
    title: str = "Fight Club", year: int | None = 1999, confidence: float = 95.0
):
    return MediaMatch(
        media_type="movie",
        title=title,
        year=year,
        confidence=confidence,
        source="tmdb",
    )


def _episode(confidence: float = 95.0, episode_title: str | None = "The Work Outing"):
    return MediaMatch(
        media_type="episode",
        title="The IT Crowd",
        year=2006,
        season=2,
        episode=1,
        episode_title=episode_title,
        confidence=confidence,
        source="tvdb",
    )


def test_library_folders_match_the_tree_on_disk() -> None:
    # The library lives at E:\media\Movies and E:\media\Shows. Writing
    # differently-named top-level folders would build a second library
    # alongside the real one.
    assert MOVIES_FOLDER == "Movies"
    assert SHOWS_FOLDER == "Shows"


def test_movie_path() -> None:
    path = build_dest_path(_movie(), ROOT)

    assert path == ROOT / "Movies" / "Fight Club (1999)" / "Fight Club (1999).mp4"


def test_movie_without_a_year_omits_the_bracket() -> None:
    path = build_dest_path(_movie(title="Officespace", year=None), ROOT)

    assert path == ROOT / "Movies" / "Officespace" / "Officespace.mp4"


def test_episode_path() -> None:
    path = build_dest_path(_episode(), ROOT)

    assert path == (
        ROOT
        / "Shows"
        / "The IT Crowd"
        / "Season 02"
        / "The IT Crowd - S02E01 - The Work Outing.mp4"
    )


def test_episode_without_a_title() -> None:
    path = build_dest_path(_episode(episode_title=None), ROOT)

    assert path.name == "The IT Crowd - S02E01.mp4"


@pytest.mark.parametrize("build", [_movie, _episode])
def test_low_confidence_marks_the_filename_not_the_folder(build) -> None:
    path = build_dest_path(build(confidence=40.0), ROOT)

    assert REVIEW_MARKER in path.name
    assert REVIEW_MARKER not in path.parent.name


def test_illegal_characters_are_stripped() -> None:
    path = build_dest_path(_movie(title="Face/Off: Reloaded?"), ROOT)

    assert path.parent.name == "FaceOff Reloaded (1999)"
