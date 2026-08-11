"""Tests for deciding a source folder's completion prefix."""

from __future__ import annotations

from pathlib import Path

from main import _tag_folders
from naming import DONE_PREFIX, REVIEW_PREFIX, SKIP_PREFIX, folder_prefix


def test_every_track_done_marks_the_folder_done() -> None:
    assert folder_prefix(["DONE_a_t00.mkv", "DONE_b_t01.mkv"]) == DONE_PREFIX


def test_one_untagged_track_leaves_the_folder_unmarked() -> None:
    # A folder half-converted by an interrupted run must stay unmarked, or the
    # remaining track would look handled and never be picked up again.
    assert folder_prefix(["DONE_a_t00.mkv", "b_t01.mkv"]) is None


def test_review_wins_over_done() -> None:
    # A mixed folder keeps standing out: the point of the marker is to find
    # what still needs a human, so the weaker outcome must not hide it.
    assert folder_prefix(["DONE_a_t00.mkv", "REVIEW_b_t01.mkv"]) == REVIEW_PREFIX


def test_a_folder_of_only_extras_is_marked_skip() -> None:
    # Nothing was converted, so claiming DONE_ would assert something false.
    assert folder_prefix(["SKIP_a_t00.mkv", "SKIP_b_t01.mkv"]) == SKIP_PREFIX


def test_skipped_extras_do_not_stop_a_folder_being_done() -> None:
    assert folder_prefix(["DONE_a_t00.mkv", "SKIP_b_t01.mkv"]) == DONE_PREFIX


def test_an_empty_folder_is_not_marked() -> None:
    # No tracks means nothing was processed; E:\Video holds folders like this.
    assert folder_prefix([]) is None


def _mkv(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    track = folder / name
    track.write_bytes(b"x")
    return track


def test_a_finished_folder_is_renamed(tmp_path: Path) -> None:
    _mkv(tmp_path / "CASINO", "DONE_B1_t00.mkv")

    assert _tag_folders(tmp_path) == 1
    assert (tmp_path / "DONE_CASINO").is_dir()


def test_an_unfinished_folder_is_left_alone(tmp_path: Path) -> None:
    _mkv(tmp_path / "CASINO", "B1_t00.mkv")

    assert _tag_folders(tmp_path) == 0
    assert (tmp_path / "CASINO").is_dir()


def test_an_already_tagged_folder_is_not_tagged_twice(tmp_path: Path) -> None:
    _mkv(tmp_path / "DONE_CASINO", "DONE_B1_t00.mkv")

    assert _tag_folders(tmp_path) == 0
    assert not (tmp_path / "DONE_DONE_CASINO").exists()


def test_nested_folders_are_renamed_deepest_first(tmp_path: Path) -> None:
    # Renaming the parent first would invalidate the child's path, so the
    # child would be silently missed.
    _mkv(tmp_path / "BOXSET", "DONE_a_t00.mkv")
    _mkv(tmp_path / "BOXSET" / "DISC2", "DONE_b_t00.mkv")

    assert _tag_folders(tmp_path) == 2
    assert (tmp_path / "DONE_BOXSET" / "DONE_DISC2").is_dir()


def test_the_scan_root_is_never_renamed(tmp_path: Path) -> None:
    _mkv(tmp_path, "DONE_loose_t00.mkv")

    assert _tag_folders(tmp_path) == 0
    assert tmp_path.is_dir()
