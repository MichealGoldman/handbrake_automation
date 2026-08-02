"""Tests for folder-name based resolution of ripped disc tracks."""

from __future__ import annotations

from pathlib import Path

import pytest

from discfolder import (
    DEFAULT_DISC,
    DEFAULT_SEASON,
    build_disc_plan,
    build_movie_plan,
    parse_disc_folder,
)


def _track(folder: Path, name: str, size: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"\0" * size)
    return path


# --- parse_disc_folder: the disc number is optional -------------------------


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("BUFFY_S3_D1", ("Buffy", 3, 1)),
        ("BUFFY_S5_D2", ("Buffy", 5, 2)),
        ("Buffy Season 3 Disc 1", ("Buffy", 3, 1)),
        ("SHOW.S03.D02", ("Show", 3, 2)),
        # Season with no disc -- the case that used to fall through entirely.
        ("THE_IT_CROWD_SEASON_4", ("The It Crowd", 4, DEFAULT_DISC)),
        ("THE_IT_CROWD_SEASON_1", ("The It Crowd", 1, DEFAULT_DISC)),
        ("Some Show S2", ("Some Show", 2, DEFAULT_DISC)),
    ],
)
def test_parse_disc_folder_matches(folder: str, expected: tuple[str, int, int]) -> None:
    assert parse_disc_folder(folder) == expected


# --- parse_disc_folder: the season is optional when a disc is spelled out ----


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        # A one-season show ripped per disc names no season at all.
        ("FIREFLY- DISC 1", ("Firefly", DEFAULT_SEASON, 1)),
        ("FIREFLY- DISC 3", ("Firefly", DEFAULT_SEASON, 3)),
        ("Some Show Disc 2", ("Some Show", DEFAULT_SEASON, 2)),
        ("SOME_SHOW_DISK_2", ("Some Show", DEFAULT_SEASON, 2)),
    ],
)
def test_parse_disc_folder_defaults_season_when_only_a_disc_is_named(
    folder: str, expected: tuple[str, int, int]
) -> None:
    assert parse_disc_folder(folder) == expected


@pytest.mark.parametrize(
    "folder",
    [
        # Only the spelled-out word counts without a season. A bare "D2" is
        # how too many movie folders end for it to mean "disc" on its own.
        "SOME SHOW D2",
        "APOLLO 13 D2",
    ],
)
def test_parse_disc_folder_needs_the_word_disc_when_no_season_is_named(
    folder: str,
) -> None:
    assert parse_disc_folder(folder) is None


def test_discs_of_a_season_less_show_share_one_episode_run(tmp_path: Path) -> None:
    # The Firefly regression: three sibling disc folders must number as one
    # season, not restart at episode 1 on every disc.
    first = _track(tmp_path / "FIREFLY- DISC 1", "FIREFLY- DISC 1_t01.mkv", 100)
    second = _track(tmp_path / "FIREFLY- DISC 2", "FIREFLY- DISC 2_t00.mkv", 100)
    third = _track(tmp_path / "FIREFLY- DISC 3", "FIREFLY- DISC 3_t00.mkv", 100)

    plan = build_disc_plan([first, second, third])

    assert [plan[path].episode for path in (first, second, third)] == [1, 2, 3]
    assert {plan[path].show for path in (first, second, third)} == {"Firefly"}
    assert {plan[path].season for path in (first, second, third)} == {DEFAULT_SEASON}
    assert plan[first].group_size == 3


def test_season_less_disc_folder_is_not_claimed_as_a_movie(tmp_path: Path) -> None:
    folder = tmp_path / "FIREFLY- DISC 2"
    tracks = [_track(folder, f"FIREFLY- DISC 2_t0{i}.mkv", 100) for i in range(5)]

    assert not build_movie_plan(tracks, tmp_path)


@pytest.mark.parametrize(
    "folder",
    [
        # Movie folders that must never be read as a season, or the optional
        # disc number would have turned films into TV.
        "SE7EN",
        "TOY STORY 2",
        "DIE HARD 2",
        "THE MATRIX RELOADED",
        "ALIENS 2",
        "FIGHT CLUB",
        "ZOMBIES ANONYMOUS",
        "OFFICESPACE",
    ],
)
def test_parse_disc_folder_rejects_movie_folders(folder: str) -> None:
    assert parse_disc_folder(folder) is None


# --- build_movie_plan ------------------------------------------------------


def test_single_track_folder_is_the_feature(tmp_path: Path) -> None:
    track = _track(tmp_path / "FIGHT CLUB", "D1_t00.mkv", 100)

    plan = build_movie_plan([track], tmp_path)

    assert plan[track].title == "Fight Club"
    assert plan[track].is_feature is True
    assert plan[track].ambiguous is False


def test_largest_track_is_the_feature(tmp_path: Path) -> None:
    folder = tmp_path / "NEAR DARK"
    feature = _track(folder, "B1_t00.mkv", 1000)
    extra_a = _track(folder, "B2_t01.mkv", 100)
    extra_b = _track(folder, "B3_t02.mkv", 50)

    plan = build_movie_plan([feature, extra_a, extra_b], tmp_path)

    assert plan[feature].is_feature is True
    assert plan[extra_a].is_feature is False
    assert plan[extra_b].is_feature is False
    assert plan[feature].ambiguous is False


def test_season_disc_folder_is_not_claimed_as_a_movie(tmp_path: Path) -> None:
    track = _track(tmp_path / "BUFFY_S3_D1", "C1_t00.mkv", 100)

    assert not build_movie_plan([track], tmp_path)


def test_season_without_disc_is_not_claimed_as_a_movie(tmp_path: Path) -> None:
    # The regression that motivated the change: this folder matches no disc
    # pattern before the fix, so the movie path would have swallowed a whole
    # TV season and thrown all but one episode away as extras.
    folder = tmp_path / "THE_IT_CROWD_SEASON_4"
    tracks = [_track(folder, f"A{i}_t0{i}.mkv", 100) for i in range(1, 7)]

    assert not build_movie_plan(tracks, tmp_path)


def test_file_directly_in_source_dir_is_not_claimed(tmp_path: Path) -> None:
    track = _track(tmp_path, "t00.mkv", 100)

    assert not build_movie_plan([track], tmp_path)


def test_near_equal_sizes_are_ambiguous(tmp_path: Path) -> None:
    folder = tmp_path / "DOUBLE FEATURE"
    first = _track(folder, "B1_t00.mkv", 1000)
    second = _track(folder, "B2_t01.mkv", 990)

    plan = build_movie_plan([first, second], tmp_path)

    assert plan[first].is_feature is True
    assert plan[first].ambiguous is True
    assert plan[second].ambiguous is True


def test_folder_without_track_ids_is_ignored(tmp_path: Path) -> None:
    track = _track(tmp_path / "SOME MOVIE", "some.movie.1999.mkv", 100)

    assert not build_movie_plan([track], tmp_path)
