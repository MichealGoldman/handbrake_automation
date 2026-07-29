"""Runs HandBrakeCLI to convert a single file."""

from __future__ import annotations

import subprocess
from pathlib import Path


class ConversionError(RuntimeError):
    """Raised when HandBrakeCLI exits with a non-zero status."""


# A finished encode is broadly comparable in size to its source -- these
# presets land somewhere around half of the original. This threshold sits far
# below any plausible encode ratio on purpose: it exists to catch near-empty
# truncation, not to second-guess how well a preset compressed.
MIN_OUTPUT_SIZE_RATIO = 0.10


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
    source: Path, dest: Path, handbrake_cli_path: str, preset: str
) -> None:
    """Convert a single file with HandBrakeCLI.

    Args:
        source: Path to the source .mkv file.
        dest: Path to write the converted .mp4 to; parent directories are
            created as needed.
        handbrake_cli_path: Path to the HandBrakeCLI executable.
        preset: Exact HandBrake preset name to convert with.

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
    ]

    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except BaseException:
        # Ctrl-C reaches HandBrake too, so it dies mid-write; clean up before
        # letting the interrupt through.
        remove_partial_output(dest)
        raise

    if result.returncode != 0:
        # Don't leave a partial/corrupt .mp4 behind on failure.
        remove_partial_output(dest)
        raise ConversionError(
            f"HandBrakeCLI exited with code {result.returncode} for {source}:\n"
            f"{result.stderr[-4000:]}"
        )
