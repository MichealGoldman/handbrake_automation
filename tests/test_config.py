"""Tests for parsing configuration out of the environment."""

from __future__ import annotations

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
