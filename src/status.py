"""Reports progress of the most recent conversion run, running or finished.

Reads the newest ``logs/run_*.log`` rather than talking to the running
process, so it works from any shell, survives the session that launched the
run, and costs nothing while a multi-hour encode is in flight.

Usage:
    python src/status.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from timefmt import format_duration

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# main.py's heartbeat fires every 600s, so a longer silence than this means the
# run is not merely between log lines.
STALLED_AFTER = 900.0

_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}")
_FOUND_RE = re.compile(r"Found (\d+) \.mkv file\(s\)")
_PROCESSING_RE = re.compile(r"Processing \[(\d+)/(\d+)\]: (.+)$")
_CONVERTED_RE = re.compile(r"Converted in (.+?) -> ")
_FLAGGED_RE = re.compile(r"flagged for review: (.+)$")
_DONE_RE = re.compile(r"Done in ([^.]+)\.")
_DURATION_RE = re.compile(r"(?:(\d+)h )?(?:(\d+)m )?(\d+)s")


def _parse_time(line: str) -> Optional[datetime]:
    found = _TIMESTAMP_RE.match(line)
    if found is None:
        return None
    return datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S")


def _parse_duration(text: str) -> float:
    found = _DURATION_RE.search(text)
    if found is None:
        return 0.0
    hours, minutes, seconds = (int(part) if part else 0 for part in found.groups())
    return hours * 3600 + minutes * 60 + seconds


@dataclass(frozen=True)
class RunStatus:
    """Everything the status line needs, parsed out of one run log.

    Attributes:
        total: Files the run set out to process.
        durations: Encode time of each completed conversion, in seconds.
        skipped: Files skipped, whether already present or disc extras.
        failed: Files that failed to identify or convert.
        flagged: Destination names flagged as low-confidence matches.
        current: The file being encoded and when it started, or None if the
            run is between files or over.
        span: First and last timestamps seen in the log.
        finished: The run's own total duration, or None if still going.
    """

    total: int
    durations: list[float]
    skipped: int
    failed: int
    flagged: list[str]
    current: Optional[tuple[str, datetime]]
    span: tuple[Optional[datetime], Optional[datetime]]
    finished: Optional[str]

    @property
    def converted(self) -> int:
        """Number of files converted so far."""
        return len(self.durations)

    @property
    def processed(self) -> int:
        """Number of files finished with, by any outcome."""
        return self.converted + self.skipped + self.failed

    def state(self, now: datetime) -> str:
        """Describe whether the run is finished, running, or apparently stuck.

        Args:
            now: Current time, passed in rather than read so the result is
                reproducible under test.

        Returns:
            One of "finished", "running", or "STALLED?".
        """
        if self.finished is not None:
            return "finished"
        last = self.span[1]
        if last is not None and (now - last).total_seconds() > STALLED_AFTER:
            return "STALLED?"
        return "running"


def latest_log() -> Optional[Path]:
    """Find the most recent run log.

    Returns:
        Path to the newest ``logs/run_*.log``, or None if there are none.
    """
    logs = sorted(LOG_DIR.glob("run_*.log"))
    return logs[-1] if logs else None


def parse_log(text: str) -> RunStatus:
    """Extract a RunStatus from the contents of a run log.

    Args:
        text: Full text of a ``logs/run_*.log`` file.

    Returns:
        The parsed RunStatus.
    """
    counts = {"skipped": 0, "failed": 0}
    state: dict = {"total": 0, "current": None, "finished": None}
    durations: list[float] = []
    flagged: list[str] = []
    started: Optional[datetime] = None
    last: Optional[datetime] = None

    for line in text.splitlines():
        stamp = _parse_time(line)
        if stamp is not None:
            started = started or stamp
            last = stamp
        _read_line(line, stamp, counts, state, durations, flagged)

    return RunStatus(
        total=state["total"],
        durations=durations,
        skipped=counts["skipped"],
        failed=counts["failed"],
        flagged=flagged,
        current=state["current"],
        span=(started, last),
        finished=state["finished"],
    )


def _read_line(
    line: str,
    stamp: Optional[datetime],
    counts: dict,
    state: dict,
    durations: list[float],
    flagged: list[str],
) -> None:
    """Fold one log line into the accumulators parse_log is building."""
    found = _FOUND_RE.search(line)
    if found:
        state["total"] = int(found.group(1))
        return

    found = _PROCESSING_RE.search(line)
    if found and stamp is not None:
        name = Path(found.group(3)).name
        state["current"] = (f"[{found.group(1)}/{found.group(2)}] {name}", stamp)
        return

    found = _CONVERTED_RE.search(line)
    if found:
        durations.append(_parse_duration(found.group(1)))
        state["current"] = None
        return

    # Checked before the flagged line: the closing summary carries both "Done
    # in ..." and a "flagged for review: <count>" tally, so matching the
    # flagged pattern first would swallow the line, leave the run looking like
    # it was still going, and file the count itself as a flagged title.
    found = _DONE_RE.search(line)
    if found:
        state["finished"] = found.group(1)
        state["current"] = None
        return

    found = _FLAGGED_RE.search(line)
    if found:
        flagged.append(Path(found.group(1)).stem)
        return

    if "Skipping" in line:
        counts["skipped"] += 1
    elif "[ERROR]" in line:
        counts["failed"] += 1


def render(status: RunStatus, log_name: str, now: datetime) -> str:
    """Format a RunStatus as the compact block printed to stdout.

    Args:
        status: Parsed run status.
        log_name: Filename of the log it came from.
        now: Current time, used for the in-flight file's elapsed and the ETA.

    Returns:
        The multi-line status block.
    """
    started, _ = status.span
    elapsed = (now - started).total_seconds() if started else 0.0
    remaining = max(0, status.total - status.processed)

    out = [
        f"Log:      {log_name}  [{status.state(now)}]",
        f"Progress: {status.processed}/{status.total} done, {remaining} left"
        f"   (converted {status.converted}, skipped {status.skipped},"
        f" failed {status.failed})",
    ]

    if status.current is not None:
        label, since = status.current
        so_far = format_duration((now - since).total_seconds())
        out.append(f"Current:  {label}  ({so_far} so far)")

    out.append(f"Elapsed:  {format_duration(elapsed)}")

    if status.finished is not None:
        out.append(f"Finished: run took {status.finished}")
    elif status.durations:
        average = sum(status.durations) / len(status.durations)
        out.append(f"Avg/file: {format_duration(average)}")
        out.append(f"ETA:      ~{format_duration(average * remaining)} remaining")

    if status.flagged:
        out.append(f"Flagged for review ({len(status.flagged)}):")
        out.extend(f"  - {name}" for name in status.flagged)

    return "\n".join(out)


def main() -> int:
    """Print the status of the newest run log.

    Returns:
        0 on success, 1 if no run log could be found.
    """
    log_path = latest_log()
    if log_path is None:
        print(f"No run logs found in {LOG_DIR}")
        return 1

    status = parse_log(log_path.read_text(encoding="utf-8", errors="replace"))
    print(render(status, log_path.name, datetime.now()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
