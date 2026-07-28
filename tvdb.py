"""Minimal TVDB v4 client: search for a series and look up an episode title."""
from __future__ import annotations

from typing import Optional

import requests
from rapidfuzz import fuzz

from models import MediaMatch

BASE_URL = "https://api4.thetvdb.com/v4"
YEAR_MATCH_BONUS = 5.0
MAX_EPISODE_PAGES = 10
# Episode not found under this series/season/episode number combo -> can't
# trust the season/episode numbering (TVDB has multiple episode orderings),
# so cap confidence below the review threshold even if the series match was good.
NO_EPISODE_TITLE_CONFIDENCE_CAP = 60.0


class TVDBAuthError(RuntimeError):
    pass


def _login(api_key: str) -> str:
    resp = requests.post(f"{BASE_URL}/login", json={"apikey": api_key}, timeout=15)
    resp.raise_for_status()
    token = resp.json().get("data", {}).get("token")
    if not token:
        raise TVDBAuthError("TVDB login succeeded but no token was returned")
    return token


class TVDBClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._token: Optional[str] = None

    def _headers(self) -> dict:
        if self._token is None:
            self._token = _login(self._api_key)
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = requests.get(f"{BASE_URL}{path}", headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def search_series(self, title: str, year: Optional[int]):
        """Returns (series_id, matched_title, matched_year, confidence) for the best match."""
        data = self._get("/search", params={"query": title, "type": "series"}).get("data", [])
        if not data:
            return None

        best = None
        best_score = -1.0
        for candidate in data:
            candidate_title = candidate.get("name") or ""
            score = fuzz.WRatio(title, candidate_title)
            candidate_year_raw = candidate.get("year")
            candidate_year = int(candidate_year_raw) if candidate_year_raw else None
            if year and candidate_year == year:
                score += YEAR_MATCH_BONUS
            if score > best_score:
                best_score = score
                best = (candidate, candidate_title, candidate_year)

        if best is None:
            return None

        candidate, candidate_title, candidate_year = best
        series_id = candidate.get("tvdb_id") or candidate.get("id")
        if series_id is None:
            return None
        return str(series_id), candidate_title, candidate_year, min(best_score, 100.0)

    def get_episode_title(self, series_id: str, season: int, episode: int) -> Optional[str]:
        page = 0
        while page < MAX_EPISODE_PAGES:
            payload = self._get(f"/series/{series_id}/episodes/default", params={"page": page})
            episodes = payload.get("data", {}).get("episodes", [])
            for ep in episodes:
                if ep.get("seasonNumber") == season and ep.get("number") == episode:
                    return ep.get("name")
            if not payload.get("links", {}).get("next"):
                break
            page += 1
        return None


def search_episode(
    client: TVDBClient, title: str, year: Optional[int], season: int, episode: int
) -> Optional[MediaMatch]:
    series_match = client.search_series(title, year)
    if series_match is None:
        return None

    series_id, matched_title, matched_year, confidence = series_match
    episode_title = client.get_episode_title(series_id, season, episode)
    if episode_title is None:
        confidence = min(confidence, NO_EPISODE_TITLE_CONFIDENCE_CAP)

    return MediaMatch(
        media_type="episode",
        title=matched_title,
        year=matched_year,
        season=season,
        episode=episode,
        episode_title=episode_title,
        confidence=confidence,
        source="tvdb",
    )
