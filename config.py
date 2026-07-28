"""Loads settings from .env into a single Config object."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def _resolve_handbrake_cli(configured_path: str) -> str:
    if configured_path:
        if not Path(configured_path).is_file():
            raise ConfigError(
                f"HANDBRAKE_CLI_PATH does not point to a file: {configured_path}"
            )
        return configured_path

    found = shutil.which("HandBrakeCLI")
    if found:
        return found

    raise ConfigError(
        "HandBrakeCLI.exe not found on PATH and HANDBRAKE_CLI_PATH is not set in .env. "
        "Install HandBrakeCLI from https://handbrake.fr/downloads2.php and set its "
        "path."
    )


@dataclass(frozen=True)
class Config:
    """Fully resolved application configuration loaded from the environment.

    Attributes:
        source_dir: Root folder to recursively scan for .mkv files.
        dest_dir: Root of the Plex/Jellyfin-style output tree.
        handbrake_cli_path: Resolved path to the HandBrakeCLI executable.
        handbrake_preset: Exact HandBrake preset name to convert with.
        tmdb_api_key: API key used for movie lookups.
        tvdb_api_key: API key used for TV episode lookups.
    """

    source_dir: Path
    dest_dir: Path
    handbrake_cli_path: str
    handbrake_preset: str
    tmdb_api_key: str
    tvdb_api_key: str


def load_config() -> Config:
    """Load and validate configuration from the environment.

    Returns:
        A fully resolved Config.

    Raises:
        ConfigError: If a required variable is missing, SOURCE_DIR does not
            exist, or HandBrakeCLI cannot be located.
    """
    source_dir = Path(_require("SOURCE_DIR"))
    dest_dir = Path(_require("DEST_DIR"))

    if not source_dir.is_dir():
        raise ConfigError(
            f"SOURCE_DIR does not exist or is not a directory: {source_dir}"
        )

    handbrake_preset = (
        os.environ.get("HANDBRAKE_PRESET", "").strip() or "Super HQ 1080p30 Surround"
    )
    handbrake_cli_path = _resolve_handbrake_cli(
        os.environ.get("HANDBRAKE_CLI_PATH", "").strip()
    )

    return Config(
        source_dir=source_dir,
        dest_dir=dest_dir,
        handbrake_cli_path=handbrake_cli_path,
        handbrake_preset=handbrake_preset,
        tmdb_api_key=_require("TMDB_API_KEY"),
        tvdb_api_key=_require("TVDB_API_KEY"),
    )
