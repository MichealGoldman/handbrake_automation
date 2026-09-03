"""Tests for the TMDb client's use of the retrying HTTP helper."""

from __future__ import annotations

import requests
from conftest import Installer

import tmdb

_ONE_RESULT = {"results": [{"title": "Alien", "release_date": "1979-05-25"}]}


# A movie lookup must survive a transient stall the same way an episode
# lookup does: the cost of losing one is a file skipped for the whole run.
def test_search_movie_retries_transient_failure(fake_requests: Installer) -> None:
    transport = fake_requests([requests.Timeout("t"), _ONE_RESULT])
    match = tmdb.search_movie("Alien", 1979, "key")
    assert match is not None
    assert match.title == "Alien"
    assert len(transport.calls) == 2
