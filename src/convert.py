"""Runs HandBrakeCLI to convert a single file."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Rights needed to retarget a running process onto a subset of processors.
PROCESS_SET_INFORMATION = 0x0200


class ConversionError(RuntimeError):
    """Raised when HandBrakeCLI exits with a non-zero status."""


# A finished encode is broadly comparable in size to its source -- these
# presets land somewhere around half of the original. This threshold sits far
# below any plausible encode ratio on purpose: it exists to catch near-empty
# truncation, not to second-guess how well a preset compressed.
MIN_OUTPUT_SIZE_RATIO = 0.10

# Tallest source still treated as standard definition. PAL DVD is 576 lines,
# NTSC 480; anything above came off a Blu-ray or a download.
SD_MAX_HEIGHT = 576

# "+ size: 1916x820" in HandBrake's scan output.
_SIZE_RE = re.compile(r"\+ size: (\d+)x(\d+)")


def probe_height(source: Path, handbrake_cli_path: str) -> Optional[int]:
    """Read a source's picture height by scanning it with HandBrake.

    Uses HandBrake rather than ffprobe so the pipeline keeps its single
    external dependency. The scan costs a few seconds against an encode that
    runs for minutes to hours.

    Args:
        source: File to scan.
        handbrake_cli_path: Path to the HandBrakeCLI executable.

    Returns:
        Height in lines, or None if the scan failed or reported no size --
        callers fall back rather than treating unknown as a value.
    """
    try:
        result = subprocess.run(
            [handbrake_cli_path, "-i", str(source), "--scan", "-t", "1"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            creationflags=_priority_flags(),
        )
    except OSError:
        return None

    found = _SIZE_RE.search(result.stdout + result.stderr)
    return int(found.group(2)) if found else None


def select_preset(height: Optional[int], sd_preset: str, hd_preset: str) -> str:
    """Pick the preset appropriate to a source's resolution.

    A slow, near-transparent preset costs minutes on a DVD and hours on a
    Blu-ray, for a difference that is hard to see -- so HD sources get the
    cheaper preset and SD keeps the expensive one.

    Args:
        height: Source picture height, or None if it couldn't be determined.
        sd_preset: Preset for standard-definition sources.
        hd_preset: Preset for anything taller than SD_MAX_HEIGHT.

    Returns:
        The preset name to convert with. An unknown height yields the SD
        preset: guessing HD on an SD source would cost quality, whereas the
        reverse only costs time.
    """
    if height is None or height <= SD_MAX_HEIGHT:
        return sd_preset
    return hd_preset


def _priority_flags() -> int:
    """Return the creation flags that run HandBrake below normal priority.

    x264 scales across every core it can see, so an encode saturates the
    machine for hours. Below-normal priority makes it yield the instant
    anything else wants CPU, while still soaking up whatever is idle -- so the
    machine stays usable and the encode costs close to nothing in wall time.
    A hard ceiling (CPU affinity) would instead cap throughput even when
    nothing else is running.

    Returns:
        The Windows priority-class flag, or 0 on platforms without one.
    """
    if sys.platform != "win32":
        return 0
    return subprocess.BELOW_NORMAL_PRIORITY_CLASS


def affinity_processors(percent: int, total: int) -> Optional[list[int]]:
    """Choose which logical processors HandBrake is allowed to run on.

    Processors are picked at an even stride across the whole range rather than
    as the first N. On a hyper-threaded machine the first 8 of 16 are only
    four physical cores sharing eight threads, which costs far more throughput
    than the 50% the number implies; striding lands one per physical core.

    Args:
        percent: Share of the machine to allow, 1..100.
        total: Number of logical processors available.

    Returns:
        Processor indices to pin to, or None for no restriction -- which
        covers 100% and any value outside the sensible range, so a bad number
        never silently pins an encode to one core.
    """
    if not 1 <= percent < 100:
        return None

    count = max(1, round(total * percent / 100))
    return [round(index * total / count) for index in range(count)]


def affinity_mask(processors: list[int]) -> int:
    """Pack processor indices into a Windows affinity bitmask.

    Args:
        processors: Logical processor indices.

    Returns:
        The bitmask with one bit set per processor.
    """
    mask = 0
    for processor in processors:
        mask |= 1 << processor
    return mask


def _apply_affinity(process: subprocess.Popen, percent: int) -> None:
    """Pin a running process to a subset of the machine's processors.

    A failure here is logged by the caller's own error path rather than
    raised: the encode itself is fine uncapped, so losing the cap must not
    lose the conversion.
    """
    processors = affinity_processors(percent, os.cpu_count() or 1)
    if processors is None or sys.platform != "win32":
        return

    try:
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_SET_INFORMATION, False, process.pid
        )
        if handle:
            ctypes.windll.kernel32.SetProcessAffinityMask(
                handle, affinity_mask(processors)
            )
            ctypes.windll.kernel32.CloseHandle(handle)
    except OSError:
        pass


def remove_partial_output(dest: Path) -> None:
    """Delete a half-written output file, ignoring any failure to do so.

    Args:
        dest: Path to the partial output file to remove.
    """
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass


def is_complete_output(source: Path, dest: Path) -> bool:
    """Report whether an existing destination looks like a finished encode.

    An interrupted run leaves a truncated .mp4 behind. Judged on existence
    alone it would pass for a finished conversion and be skipped by every
    later run, so the episode would never be repaired.

    Args:
        source: Source file the destination was encoded from.
        dest: Existing destination file to judge.

    Returns:
        True if dest is plausibly complete, False if it is small enough
        relative to source to be a partial write. Sizes that can't be read
        return True, so an unrelated I/O error never forces a re-encode.
    """
    try:
        return dest.stat().st_size >= source.stat().st_size * MIN_OUTPUT_SIZE_RATIO
    except OSError:
        return True


def convert_file(
    source: Path,
    dest: Path,
    handbrake_cli_path: str,
    preset: str,
    cpu_percent: int = 100,
) -> None:
    """Convert a single file with HandBrakeCLI.

    Args:
        source: Path to the source .mkv file.
        dest: Path to write the converted .mp4 to; parent directories are
            created as needed.
        handbrake_cli_path: Path to the HandBrakeCLI executable.
        preset: Exact HandBrake preset name to convert with.
        cpu_percent: Share of the machine's logical processors to allow,
            1..100. 100 (the default) leaves the encode uncapped.

    HandBrake is run at below-normal priority so a multi-hour encode doesn't
    make the machine unusable (see ``_priority_flags``), and pinned to a
    subset of processors when ``cpu_percent`` asks for a hard ceiling.

    Raises:
        ConversionError: If HandBrakeCLI exits with a non-zero status. Any
            partial output file is deleted before raising -- as it also is if
            the run is interrupted (Ctrl-C), since a truncated .mp4 left at the
            destination would be mistaken for a finished conversion and skipped
            by the next run.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = [
        handbrake_cli_path,
        "-i",
        str(source),
        "-o",
        str(dest),
        "--preset",
        preset,
        # The presets default to a forced-subtitle search, which finds nothing
        # on a disc carrying only a normal full subtitle track -- so without
        # this every conversion silently discards its subtitles.
        "--all-subtitles",
        # ...but selecting the track is not enough. The presets also carry a
        # burn-in behaviour, so --all-subtitles on its own renders the
        # subtitles permanently into the picture: HandBrake logs
        # "-> Render/Burn-in" and the output has no subtitle stream at all.
        # With this it logs "-> Passthru" and muxes a selectable track.
        "--subtitle-burned=none",
    ]

    try:
        with subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_priority_flags(),
        ) as process:
            # Set once the process exists but long before the encode proper
            # starts -- HandBrake spends its first moments scanning the title.
            _apply_affinity(process, cpu_percent)
            _, stderr = process.communicate()
            returncode = process.returncode
    except BaseException:
        # Ctrl-C reaches HandBrake too, so it dies mid-write; clean up before
        # letting the interrupt through.
        remove_partial_output(dest)
        raise

    if returncode != 0:
        # Don't leave a partial/corrupt .mp4 behind on failure.
        remove_partial_output(dest)
        raise ConversionError(
            f"HandBrakeCLI exited with code {returncode} for {source}:\n"
            f"{(stderr or '')[-4000:]}"
        )
