"""Formats elapsed seconds the one way the logs and the status report use."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """Render a number of seconds as a compact duration string.

    Shared by main.py, which writes these into the run log, and status.py,
    which reads them back out -- so the two can never drift apart.

    Args:
        seconds: Duration to format.

    Returns:
        A string like "1h 02m 53s", "28m 12s", or "45s".
    """
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
