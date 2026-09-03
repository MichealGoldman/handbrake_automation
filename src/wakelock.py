"""Keeps Windows awake for the duration of a long conversion run."""

from __future__ import annotations

import ctypes
import logging
import sys
from contextlib import contextmanager
from typing import Iterator

# SetThreadExecutionState flags. ES_CONTINUOUS makes the request stick until
# it's explicitly cleared, rather than resetting the idle timer just once.
# https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _set_state(flags: int) -> bool:
    try:
        func = ctypes.windll.kernel32.SetThreadExecutionState
        # 0x80000000 overflows a signed int, so the parameter has to be
        # declared unsigned explicitly.
        func.argtypes = [ctypes.c_uint]
        func.restype = ctypes.c_uint
        # Returns the previous state, or 0 if the request was refused.
        return bool(func(flags))
    except (AttributeError, OSError):
        return False


@contextmanager
def keep_awake() -> Iterator[bool]:
    """Stop the system sleeping while the wrapped block runs.

    HandBrake encodes for hours without any user input, and Windows' idle
    sleep timer ignores CPU load entirely -- left alone, the machine suspends
    part-way through a run and only advances while someone is at the desk.
    Only sleep is suppressed; the display is still free to blank.

    The request is dropped on the way out, including when the block raises,
    so an interrupted run never leaves sleep disabled behind it.

    On non-Windows platforms, or if the call is refused, this is a no-op and
    the run continues -- staying awake is a convenience, never a reason to
    abort.

    Yields:
        True if sleep is actually suppressed, False if the request failed or
        the platform doesn't support it.
    """
    held = False
    if sys.platform == "win32":
        held = _set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        if not held:
            logging.warning(
                "Could not suppress sleep; the machine may suspend mid-run."
            )

    try:
        yield held
    finally:
        if held:
            # Clear the request: back to the normal idle sleep timer.
            _set_state(_ES_CONTINUOUS)
