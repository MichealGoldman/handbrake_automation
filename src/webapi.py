"""Shared HTTP helper: one retrying request path for every API client.

Both metadata clients (tmdb, tvdb) reach the network on the critical path of a
run. A single dropped request silently skips a file for the whole run --
main.py logs the failure and moves on, so that episode is never converted. One
transient Wi-Fi stall cost an episode on 2026-08-31. Rather than each client
growing its own retry loop, they share this one.
"""

from __future__ import annotations

import time

import requests

REQUEST_TIMEOUT = 15
MAX_REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0


class RequestError(RuntimeError):
    """Raised when an API cannot be reached after retrying."""


def request(method: str, url: str, **kwargs: object) -> requests.Response:
    """Make an HTTP request, retrying transient failures with backoff.

    Only transient failures are retried: connection and timeout errors, and
    5xx responses. A 4xx is a real answer from the API (bad key, unknown id)
    and is returned as-is -- retrying cannot change it.

    Args:
        method: HTTP method, e.g. "GET" or "POST".
        url: Full request URL.
        **kwargs: Passed through to requests.request (params, headers, json).

    Returns:
        The response, for any status below 500.

    Raises:
        RequestError: If every attempt got a 5xx response.
        requests.RequestException: The last connection or timeout error, if
            every attempt failed to reach the server.
    """
    last_error: Exception = RequestError(f"no attempt made for {url}")
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            resp = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
        else:
            if resp.status_code < 500:
                return resp
            last_error = RequestError(f"{resp.status_code} from {url}")
        if attempt + 1 < MAX_REQUEST_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise last_error
