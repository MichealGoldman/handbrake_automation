"""Shared fixtures, chiefly a scripted stand-in for the HTTP transport.

Every API client reaches the network through webapi.request, so faking
webapi's transport in one place covers tmdb, tvdb, and the retry helper
itself -- and keeps the three test modules from each carrying a copy.
"""

from __future__ import annotations

from typing import Callable, Optional, Union

import pytest
import requests

import webapi

# An outcome is what the transport does on one call: raise the exception,
# return that bare status code, or return 200 carrying that JSON payload.
Outcome = Union[Exception, int, dict]


class FakeResponse:
    """The parts of requests.Response the API clients actually touch."""

    def __init__(self, status_code: int, payload: Optional[dict] = None):
        """Store the status code and the body json() should return."""
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        """Raise requests.HTTPError for a 4xx or 5xx status, as requests does.

        Raises:
            requests.HTTPError: If the status code is 400 or above.
        """
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self) -> dict:
        """Return the scripted response body.

        Returns:
            The payload this response was built with.
        """
        return self._payload


class FakeTransport:
    """Replays a scripted list of outcomes, one per request, recording calls."""

    def __init__(self, outcomes: list[Outcome]):
        """Script the outcomes to serve, in order, and start empty records."""
        self._outcomes = outcomes
        self.calls: list[tuple[str, str]] = []
        self.sleeps: list[float] = []

    def __call__(self, method: str, url: str, **_kwargs: object) -> FakeResponse:
        """Record the call and serve the next scripted outcome.

        Args:
            method: HTTP method the client asked for.
            url: URL the client asked for.
            **_kwargs: Ignored; accepted so the signature matches requests.

        Returns:
            The scripted response for this call.

        Raises:
            Exception: The scripted exception for this call, if it is one.
        """
        self.calls.append((method, url))
        outcome = self._outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return FakeResponse(outcome)
        return FakeResponse(200, outcome)


Installer = Callable[[list[Outcome]], FakeTransport]


@pytest.fixture(name="fake_requests")
def fixture_fake_requests(monkeypatch: pytest.MonkeyPatch) -> Installer:
    """Return an installer for a scripted, non-sleeping HTTP transport.

    Call it with one outcome per expected request -- an exception to raise,
    an int status code, or a dict served as a 200 JSON body. Backoff sleeps
    are recorded instead of taken, so retry tests run instantly.

    Returns:
        A function taking the outcome list and returning the FakeTransport,
        whose ``calls`` and ``sleeps`` record what happened.
    """

    def _install(outcomes: list[Outcome]) -> FakeTransport:
        transport = FakeTransport(outcomes)
        monkeypatch.setattr(webapi.requests, "request", transport)
        monkeypatch.setattr(webapi.time, "sleep", transport.sleeps.append)
        return transport

    return _install
