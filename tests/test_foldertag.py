"""Tests for deciding a source folder's completion prefix."""

from __future__ import annotations

from pathlib import Path

from main import _count_pending, _tag_finished_folders
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


def _run(root: Path, tracks: list[Path]) -> int:
    """Walk a track list the way main()'s loop does, tagging folders as it goes.

    Each track is assumed already renamed by _tag_source, which is the state
    _tag_finished_folders sees when the loop calls it.
    """
    pending = _count_pending(tracks, root)
    return sum(_tag_finished_folders(track, root, pending) for track in tracks)


def test_a_finished_folder_is_renamed(tmp_path: Path) -> None:
    track = _mkv(tmp_path / "CASINO", "DONE_B1_t00.mkv")

    assert _run(tmp_path, [track]) == 1
    assert (tmp_path / "DONE_CASINO").is_dir()


def test_an_unfinished_folder_is_left_alone(tmp_path: Path) -> None:
    # The track never got a tag -- it failed, so the folder isn't finished.
    track = _mkv(tmp_path / "CASINO", "B1_t00.mkv")

    assert _run(tmp_path, [track]) == 0
    assert (tmp_path / "CASINO").is_dir()


def test_an_already_tagged_folder_is_not_tagged_twice(tmp_path: Path) -> None:
    track = _mkv(tmp_path / "DONE_CASINO", "DONE_B1_t00.mkv")

    assert _run(tmp_path, [track]) == 0
    assert not (tmp_path / "DONE_DONE_CASINO").exists()


def test_a_folder_is_tagged_as_soon_as_its_last_track_is_done(tmp_path: Path) -> None:
    # The point of tagging during the run: the folder is marked the moment it
    # is finished, so an interrupted run keeps the marks it earned.
    folder = tmp_path / "BUFFY_S3_D1"
    first = _mkv(folder, "DONE_C1_t00.mkv")
    second = _mkv(folder, "DONE_C1_t01.mkv")
    pending = _count_pending([first, second], tmp_path)

    assert _tag_finished_folders(first, tmp_path, pending) == 0
    assert folder.is_dir(), "renaming with a track still queued breaks its path"

    assert _tag_finished_folders(second, tmp_path, pending) == 1
    assert (tmp_path / "DONE_BUFFY_S3_D1").is_dir()


def test_a_parent_waits_for_its_subfolders(tmp_path: Path) -> None:
    # The parent holds a finished track of its own, but renaming it now would
    # invalidate the queued path inside DISC2.
    boxset = tmp_path / "BOXSET"
    loose = _mkv(boxset, "DONE_a_t00.mkv")
    nested = _mkv(boxset / "DISC2", "DONE_b_t00.mkv")
    pending = _count_pending([loose, nested], tmp_path)

    assert _tag_finished_folders(loose, tmp_path, pending) == 0
    assert nested.exists()

    # Deepest first, so the child is renamed before the parent moves under it.
    assert _tag_finished_folders(nested, tmp_path, pending) == 2
    assert (tmp_path / "DONE_BOXSET" / "DONE_DISC2").is_dir()


def test_the_scan_root_is_never_renamed(tmp_path: Path) -> None:
    track = _mkv(tmp_path, "DONE_loose_t00.mkv")

    assert _run(tmp_path, [track]) == 0
    assert tmp_path.is_dir()
