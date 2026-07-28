"""Runs HandBrakeCLI to convert a single file."""
from __future__ import annotations

import subprocess
from pathlib import Path


class ConversionError(RuntimeError):
    pass


def convert_file(source: Path, dest: Path, handbrake_cli_path: str, preset: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = [
        handbrake_cli_path,
        "-i", str(source),
        "-o", str(dest),
        "--preset", preset,
    ]

    result = subprocess.run(args, capture_output=True, text=True)

    if result.returncode != 0:
        # Don't leave a partial/corrupt .mp4 behind on failure.
        if dest.exists():
            dest.unlink()
        raise ConversionError(
            f"HandBrakeCLI exited with code {result.returncode} for {source}:\n{result.stderr[-4000:]}"
        )
