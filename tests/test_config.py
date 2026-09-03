"""Tests for parsing configuration out of the environment."""

from __future__ import annotations

from pathlib import Path

import pytest

import config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("50", 50),
        ("  75  ", 75),
        ("100", 100),
        # Unset or blank means uncapped.
        ("", 100),
    ],
)
def test_cpu_percent_parses(
    raw: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HANDBRAKE_CPU_PERCENT", raw)

    assert config.load_cpu_percent() == expected


@pytest.mark.parametrize("raw", ["0", "-10", "101", "half", "50%"])
def test_cpu_percent_rejects_nonsense(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A typo here would silently change how long every encode takes, so it
    # fails loudly at startup rather than being coerced to something.
    monkeypatch.setenv("HANDBRAKE_CPU_PERCENT", raw)

    with pytest.raises(config.ConfigError, match="HANDBRAKE_CPU_PERCENT"):
        config.load_cpu_percent()


@pytest.fixture(name="env")
def env_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set every required variable except SOURCE_DIR, which each test owns."""
    cli = tmp_path / "HandBrakeCLI.exe"
    cli.touch()
    monkeypatch.setenv("HANDBRAKE_CLI_PATH", str(cli))
    monkeypatch.setenv("DEST_DIR", str(tmp_path / "dest"))
    monkeypatch.setenv("TMDB_API_KEY", "tmdb-key")
    monkeypatch.setenv("TVDB_API_KEY", "tvdb-key")
    return tmp_path


def test_source_argument_overrides_env(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The source moves between runs; whatever the caller names wins over the
    # value left in .env, which is routinely stale.
    stale = env / "stale"
    stale.mkdir()
    current = env / "current"
    current.mkdir()
    monkeypatch.setenv("SOURCE_DIR", str(stale))

    assert config.load_config(current).source_dir == current


def test_source_falls_back_to_env(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = env / "configured"
    configured.mkdir()
    monkeypatch.setenv("SOURCE_DIR", str(configured))

    assert config.load_config().source_dir == configured


@pytest.mark.usefixtures("env")
def test_missing_source_names_both_ways(monkeypatch: pytest.MonkeyPatch) -> None:
    # With neither supplied the run would scan nothing and report success, so
    # the error has to point at both places a source can come from.
    monkeypatch.delenv("SOURCE_DIR", raising=False)

    with pytest.raises(config.ConfigError) as excinfo:
        config.load_config()

    message = str(excinfo.value)
    assert "command line" in message
    assert "SOURCE_DIR" in message


def test_source_argument_must_exist(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo'd path must fail at startup rather than scanning zero files.
    monkeypatch.delenv("SOURCE_DIR", raising=False)

    with pytest.raises(config.ConfigError, match="does not exist"):
        config.load_config(env / "typo")


def test_source_from_env_must_exist(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DIR", str(env / "gone"))

    with pytest.raises(config.ConfigError, match="does not exist"):
        config.load_config()
