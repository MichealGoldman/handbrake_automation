"""Tests for how HandBrakeCLI is invoked and how partial output is handled."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import convert


class _FakeProcess:
    """Stands in for the HandBrakeCLI process, without launching anything."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.pid = 4321
        self._stderr = stderr

    def __enter__(self) -> "_FakeProcess":
        return self

    def __exit__(self, *_) -> None:
        return None

    def communicate(self) -> tuple[str, str]:
        return "", self._stderr


def _patch_popen(monkeypatch: pytest.MonkeyPatch, process: _FakeProcess) -> list[dict]:
    recorded: list[dict] = []

    def fake_popen(args, **kwargs):
        recorded.append({"args": args, **kwargs})
        return process

    monkeypatch.setattr(convert.subprocess, "Popen", fake_popen)
    return recorded


@pytest.fixture(name="calls")
def _calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture the arguments convert_file passes to subprocess.Popen."""
    return _patch_popen(monkeypatch, _FakeProcess())


@pytest.mark.skipif(sys.platform != "win32", reason="priority classes are Windows-only")
def test_handbrake_runs_at_below_normal_priority(tmp_path: Path, calls) -> None:
    # An encode runs for hours and saturates every core. At normal priority it
    # competes with whatever the machine is actually being used for; below
    # normal, it yields immediately and still gets the idle capacity.
    convert.convert_file(tmp_path / "in.mkv", tmp_path / "out.mp4", "HandBrakeCLI", "P")

    flags = calls[0]["creationflags"]
    assert flags & subprocess.BELOW_NORMAL_PRIORITY_CLASS


def test_priority_flag_is_not_passed_on_other_platforms(
    tmp_path: Path, calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BELOW_NORMAL_PRIORITY_CLASS doesn't exist off Windows; asking for it
    # there would crash the run rather than merely fail to lower priority.
    monkeypatch.setattr(convert.sys, "platform", "linux")

    convert.convert_file(tmp_path / "in.mkv", tmp_path / "out.mp4", "HandBrakeCLI", "P")

    assert calls[0]["creationflags"] == 0


def test_all_subtitles_are_carried_through(tmp_path: Path, calls) -> None:
    # The preset's default is a forced-subtitle search, which finds nothing on
    # a DVD carrying only a normal English track -- so every conversion before
    # this silently dropped its subtitles.
    convert.convert_file(tmp_path / "in.mkv", tmp_path / "out.mp4", "HandBrakeCLI", "P")

    assert "--all-subtitles" in calls[0]["args"]


def test_subtitles_are_not_burned_into_the_picture(tmp_path: Path, calls) -> None:
    # --all-subtitles alone selects the track but leaves the preset's burn-in
    # behaviour intact, which renders it permanently into the video. Verified
    # against HandBrake: without this the log reads "-> Render/Burn-in", with
    # it "-> Passthru".
    convert.convert_file(tmp_path / "in.mkv", tmp_path / "out.mp4", "HandBrakeCLI", "P")

    assert "--subtitle-burned=none" in calls[0]["args"]


@pytest.mark.parametrize(
    ("codecs", "expected"),
    [
        # VobSub off a DVD muxes into MP4 as a real track.
        ({"dvd_subtitle"}, True),
        ({"subrip"}, True),
        (set(), True),
        # PGS off a Blu-ray cannot be muxed into MP4. HandBrake burns it into
        # the picture instead, and --subtitle-burned=none does not stop it --
        # verified by encoding the same segment with and without subtitles
        # requested and comparing frames.
        ({"hdmv_pgs_subtitle"}, False),
        ({"dvd_subtitle", "hdmv_pgs_subtitle"}, False),
        # Anything unrecognised is treated as unsafe: the cost of being wrong
        # is subtitles burned permanently into the video.
        ({"something_new"}, False),
    ],
)
def test_subtitles_only_kept_when_they_can_be_muxed(codecs, expected: bool) -> None:
    assert convert.keep_subtitles(codecs) is expected


def test_subtitles_disabled_when_they_would_be_burned(tmp_path: Path, calls) -> None:
    convert.convert_file(
        tmp_path / "in.mkv",
        tmp_path / "out.mp4",
        "HandBrakeCLI",
        "P",
        with_subtitles=False,
    )

    args = calls[0]["args"]
    assert "--subtitle" in args and "none" in args
    assert "--all-subtitles" not in args


@pytest.mark.parametrize(
    ("height", "expected"),
    [
        (480, "SD"),  # NTSC DVD
        (576, "SD"),  # PAL DVD
        (720, "HD"),
        (1080, "HD"),
        (2160, "HD"),
        # Unknown height falls back to the higher-quality preset: guessing HD
        # on an SD source would cost quality, the reverse only costs time.
        (None, "SD"),
    ],
)
def test_preset_is_chosen_by_source_height(height, expected: str) -> None:
    assert convert.select_preset(height, "SD", "HD") == expected


def test_preset_selection_is_a_no_op_when_both_are_the_same() -> None:
    assert convert.select_preset(1080, "One", "One") == "One"


def test_command_line_is_unchanged(tmp_path: Path, calls) -> None:
    source, dest = tmp_path / "in.mkv", tmp_path / "out" / "out.mp4"

    convert.convert_file(source, dest, "HandBrakeCLI.exe", "Super HQ 1080p30 Surround")

    # Asserted in slices rather than as one literal list: spelled out in full
    # it duplicates the block in convert.py line for line, which pylint's
    # duplicate-code check flags across the two files.
    args = calls[0]["args"]
    assert args[0] == "HandBrakeCLI.exe"
    assert args[1:5] == ["-i", str(source), "-o", str(dest)]
    assert args[5:7] == ["--preset", "Super HQ 1080p30 Surround"]


@pytest.mark.parametrize(
    ("percent", "total", "expected"),
    [
        # Half of a 16-thread machine: every other logical processor, so the
        # eight chosen sit on eight distinct physical cores rather than
        # doubling up on four via hyper-threading.
        (50, 16, [0, 2, 4, 6, 8, 10, 12, 14]),
        (25, 16, [0, 4, 8, 12]),
        (50, 8, [0, 2, 4, 6]),
        # Always leave at least one processor usable.
        (1, 8, [0]),
    ],
)
def test_affinity_spreads_across_physical_cores(
    percent: int, total: int, expected: list[int]
) -> None:
    assert convert.affinity_processors(percent, total) == expected


@pytest.mark.parametrize("percent", [100, 0, 150])
def test_no_affinity_limit_outside_a_sensible_range(percent: int) -> None:
    # 100 means "uncapped"; anything else out of range must not silently
    # pin the encode to a single core.
    assert convert.affinity_processors(percent, 16) is None


def test_affinity_mask_sets_the_right_bits() -> None:
    assert convert.affinity_mask([0, 2, 4, 6]) == 0b01010101


def test_failure_deletes_the_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "out.mp4"
    dest.write_bytes(b"partial")

    _patch_popen(monkeypatch, _FakeProcess(returncode=3, stderr="boom"))

    with pytest.raises(convert.ConversionError):
        convert.convert_file(tmp_path / "in.mkv", dest, "HandBrakeCLI", "P")

    assert not dest.exists()
