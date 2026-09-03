"""Tests for the TVDB client's use of the retrying HTTP helper."""

from __future__ import annotations

import requests
from conftest import Installer

import tvdb

_LOGIN_OK = {"data": {"token": "tok"}}
_SEARCH_OK = {"data": [{"name": "Firefly", "year": "2002", "tvdb_id": "78874"}]}


# Login is a request like any other, so a stall on the very first call must
# not take the run's series lookups down with it.
def test_login_retries_transient_failure(fake_requests: Installer) -> None:
    transport = fake_requests(
        [requests.ConnectionError("reset"), _LOGIN_OK, _SEARCH_OK]
    )
    result = tvdb.TVDBClient("key").search_series("Firefly", 2002)
    assert result is not None
    assert result[0] == "78874"
    assert len(transport.calls) == 3


def test_search_series_retries_transient_failure(fake_requests: Installer) -> None:
    transport = fake_requests([_LOGIN_OK, requests.Timeout("t"), _SEARCH_OK])
    assert tvdb.TVDBClient("key").search_series("Firefly", 2002) is not None
    assert len(transport.calls) == 3
