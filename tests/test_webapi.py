"""Tests for the shared HTTP helper's transient-failure retry behaviour."""

from __future__ import annotations

import pytest
import requests
from conftest import Installer

from webapi import MAX_REQUEST_ATTEMPTS, RETRY_BACKOFF_SECONDS, RequestError, request

# --- a transient failure must not lose the request -------------------------


@pytest.mark.parametrize(
    "first_failure",
    [
        requests.Timeout("read timed out"),
        requests.ConnectionError("connection reset"),
    ],
)
def test_retries_transient_failure_then_succeeds(
    fake_requests: Installer, first_failure: Exception
) -> None:
    transport = fake_requests([first_failure, 200])
    assert request("GET", "https://example.test/x").status_code == 200
    assert len(transport.calls) == 2


def test_retries_server_error(fake_requests: Installer) -> None:
    transport = fake_requests([503, 200])
    assert request("GET", "https://example.test/x").status_code == 200
    assert len(transport.calls) == 2


# --- but a real answer, and a persistent failure, must not loop ------------


def test_does_not_retry_client_error(fake_requests: Installer) -> None:
    # 404 is the API answering "no such thing" -- retrying cannot change it.
    transport = fake_requests([404, 200])
    assert request("GET", "https://example.test/x").status_code == 404
    assert len(transport.calls) == 1


def test_gives_up_after_max_attempts(fake_requests: Installer) -> None:
    transport = fake_requests([requests.Timeout("t")] * MAX_REQUEST_ATTEMPTS)
    with pytest.raises(requests.Timeout):
        request("GET", "https://example.test/x")
    assert len(transport.calls) == MAX_REQUEST_ATTEMPTS


def test_gives_up_after_persistent_server_error(fake_requests: Installer) -> None:
    transport = fake_requests([500] * MAX_REQUEST_ATTEMPTS)
    with pytest.raises(RequestError):
        request("GET", "https://example.test/x")
    assert len(transport.calls) == MAX_REQUEST_ATTEMPTS


def test_backoff_grows_between_attempts(fake_requests: Installer) -> None:
    transport = fake_requests([requests.Timeout("t"), requests.Timeout("t"), 200])
    request("GET", "https://example.test/x")
    assert transport.sleeps == [RETRY_BACKOFF_SECONDS, RETRY_BACKOFF_SECONDS * 2]
