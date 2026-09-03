"""Loads settings from .env into a single Config object."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def load_cpu_percent() -> int:
    """Read the share of the machine's CPUs HandBrake is allowed to use.

    Returns:
        A percentage from 1 to 100. 100 -- the default when unset -- means
        uncapped, and is what every run before this option did.

    Raises:
        ConfigError: If the value isn't a whole number in 1..100. A typo here
            would quietly change how long every encode takes, so it fails at
            startup rather than being coerced into something plausible.
    """
    raw = os.environ.get("HANDBRAKE_CPU_PERCENT", "").strip()
    if not raw:
        return 100

    try:
        percent = int(raw)
    except ValueError as error:
        raise ConfigError(
            f"HANDBRAKE_CPU_PERCENT must be a whole number from 1 to 100, got {raw!r}"
        ) from error

    if not 1 <= percent <= 100:
        raise ConfigError(f"HANDBRAKE_CPU_PERCENT must be from 1 to 100, got {percent}")
    return percent


def _resolve_source_dir(override: Optional[Path]) -> Path:
    # The source folder moves between runs -- it has been C:\video, E:\Video,
    # and C:\Video -- while .env keeps whatever it was told last. A stale
    # SOURCE_DIR pointing at an emptied folder is the worst failure available
    # here: the scan finds nothing, every tally reads zero, and the run exits
    # 0 as though it had succeeded. So the caller's value always wins, and
    # .env is only the fallback.
    if override is not None:
        source_dir = override
    else:
        configured = os.environ.get("SOURCE_DIR", "").strip()
        if not configured:
            raise ConfigError(
                "No source folder given. Pass one on the command line "
                '(python src/main.py "C:\\Video") or set SOURCE_DIR in .env.'
            )
        source_dir = Path(configured)

    if not source_dir.is_dir():
        raise ConfigError(
            f"Source folder does not exist or is not a directory: {source_dir}"
        )
    return source_dir


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
        source_dir: Root folder to recursively scan for .mkv files, taken
            from the command line when given and from SOURCE_DIR otherwise.
        dest_dir: Root of the Plex/Jellyfin-style output tree.
        handbrake_cli_path: Resolved path to the HandBrakeCLI executable.
        handbrake_preset: Preset for standard-definition sources, and the
            fallback when a source's resolution can't be determined.
        handbrake_preset_hd: Preset for sources taller than SD. Defaults to
            handbrake_preset, so leaving it unset keeps one preset for
            everything.
        tmdb_api_key: API key used for movie lookups.
        tvdb_api_key: API key used for TV episode lookups.
        cpu_percent: Share of the machine's logical processors HandBrake may
            run on, 1..100. 100 is uncapped.
    """

    source_dir: Path
    dest_dir: Path
    handbrake_cli_path: str
    handbrake_preset: str
    handbrake_preset_hd: str
    tmdb_api_key: str
    tvdb_api_key: str
    cpu_percent: int = 100


def load_config(source_override: Optional[Path] = None) -> Config:
    """Load and validate configuration from the environment.

    Args:
        source_override: Source folder supplied by the caller, normally from
            the command line. Takes precedence over SOURCE_DIR in .env; when
            omitted, SOURCE_DIR is used instead.

    Returns:
        A fully resolved Config.

    Raises:
        ConfigError: If a required variable is missing, no source folder was
            given by either route, the source folder does not exist, or
            HandBrakeCLI cannot be located.
    """
    source_dir = _resolve_source_dir(source_override)
    dest_dir = Path(_require("DEST_DIR"))

    handbrake_preset = (
        os.environ.get("HANDBRAKE_PRESET", "").strip() or "Super HQ 1080p30 Surround"
    )
    handbrake_preset_hd = (
        os.environ.get("HANDBRAKE_PRESET_HD", "").strip() or handbrake_preset
    )
    handbrake_cli_path = _resolve_handbrake_cli(
        os.environ.get("HANDBRAKE_CLI_PATH", "").strip()
    )

    return Config(
        source_dir=source_dir,
        dest_dir=dest_dir,
        handbrake_cli_path=handbrake_cli_path,
        handbrake_preset=handbrake_preset,
        handbrake_preset_hd=handbrake_preset_hd,
        tmdb_api_key=_require("TMDB_API_KEY"),
        tvdb_api_key=_require("TVDB_API_KEY"),
        cpu_percent=load_cpu_percent(),
    )
