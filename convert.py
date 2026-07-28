"""Runs HandBrakeCLI to convert a single file."""

from __future__ import annotations

import subprocess
from pathlib import Path


class ConversionError(RuntimeError):
    """Raised when HandBrakeCLI exits with a non-zero status."""


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
            partial output file is deleted before raising.
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

    result = subprocess.run(args, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        # Don't leave a partial/corrupt .mp4 behind on failure.
        if dest.exists():
            dest.unlink()
        raise ConversionError(
            f"HandBrakeCLI exited with code {result.returncode} for {source}:\n"
            f"{result.stderr[-4000:]}"
        )
