"""Tests for parsing progress out of a run log."""

from __future__ import annotations

from datetime import datetime

from status import parse_log

_FINISHED = """\
2026-08-07 19:38:42,417 [INFO] Found 2 .mkv file(s)
2026-08-07 19:38:42,421 [INFO] Processing [1/2]: E:\\Video\\A\\a_t00.mkv
2026-08-07 20:06:54,000 [INFO] Converted in 28m 12s -> E:\\media\\Movies\\A\\A.mp4
2026-08-07 20:06:54,001 [INFO] Processing [2/2]: E:\\Video\\B\\b_t00.mkv
2026-08-07 20:10:00,000 [WARNING] Low-confidence match (40%), \
flagged for review: E:\\media\\Movies\\B\\B --needs name review--.mp4
2026-08-07 20:36:54,000 [INFO] Converted in 30m 00s -> E:\\media\\Movies\\B\\B.mp4
2026-08-07 20:36:54,001 [INFO] Done in 58m 12s. Converted: 2, \
skipped (already existed): 0, failed: 0, tagged: 2, flagged for review: 1
"""


def test_finished_run_is_reported_as_finished() -> None:
    # The closing summary carries "Done in ..." and "flagged for review: 1" on
    # one line. Matching the flagged pattern first consumed the line, so a
    # finished run kept reporting as running.
    status = parse_log(_FINISHED)

    assert status.finished == "58m 12s"
    assert status.state(datetime(2026, 8, 7, 21, 0, 0)) == "finished"


def test_the_flagged_tally_is_not_counted_as_a_flagged_title() -> None:
    status = parse_log(_FINISHED)

    assert status.flagged == ["B --needs name review--"]


def test_progress_and_totals() -> None:
    status = parse_log(_FINISHED)

    assert (status.total, status.converted, status.processed) == (2, 2, 2)
    assert status.durations == [28 * 60 + 12, 30 * 60]


def test_a_running_run_reports_the_file_in_flight() -> None:
    partial = "\n".join(_FINISHED.splitlines()[:4])

    status = parse_log(partial)

    assert status.finished is None
    assert status.current is not None
    label, since = status.current
    assert label == "[2/2] b_t00.mkv"
    assert since == datetime(2026, 8, 7, 20, 6, 54)
    assert status.state(datetime(2026, 8, 7, 20, 10, 0)) == "running"


def test_a_silent_run_is_reported_as_stalled() -> None:
    # The heartbeat fires every 10 minutes, so a longer gap is a hang rather
    # than a slow encode.
    partial = "\n".join(_FINISHED.splitlines()[:4])

    status = parse_log(partial)

    assert status.state(datetime(2026, 8, 7, 21, 30, 0)) == "STALLED?"
