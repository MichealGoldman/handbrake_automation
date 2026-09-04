"""Minimal TVDB v4 client: search for a series and look up an episode title."""

from __future__ import annotations

from typing import Optional

from rapidfuzz import fuzz

import webapi
from discfolder import DiscEpisode
from models import MediaMatch

BASE_URL = "https://api4.thetvdb.com/v4"
YEAR_MATCH_BONUS = 5.0
MAX_EPISODE_PAGES = 10
# Episode not found under this series/season/episode number combo -> can't
# trust the season/episode numbering (TVDB has multiple episode orderings),
# so cap confidence below the review threshold even if the series match was good.
NO_EPISODE_TITLE_CONFIDENCE_CAP = 60.0
# How many title matches to weigh against the rip's own shape before settling
# on a series. A disc folder carries no year or country, so remakes collide:
# "BEING_HUMAN_S2_D1" scores 100 against the UK original and slightly less
# against "Being Human (US)", which is the one on the disc.
MAX_SERIES_CANDIDATES = 5


class TVDBAuthError(RuntimeError):
    """Raised when TVDB login succeeds but returns no usable token."""


def _login(api_key: str) -> str:
    resp = webapi.request("POST", f"{BASE_URL}/login", json={"apikey": api_key})
    resp.raise_for_status()
    token = resp.json().get("data", {}).get("token")
    if not token:
        raise TVDBAuthError("TVDB login succeeded but no token was returned")
    return token


class TVDBClient:
    """Minimal TVDB v4 client that lazily authenticates on first request."""

    def __init__(self, api_key: str):
        """Store the API key; the bearer token is fetched on first request."""
        self._api_key = api_key
        self._token: Optional[str] = None

    def _headers(self) -> dict:
        if self._token is None:
            self._token = _login(self._api_key)
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = webapi.request(
            "GET", f"{BASE_URL}{path}", headers=self._headers(), params=params
        )
        resp.raise_for_status()
        return resp.json()

    def search_series(
        self, title: str, year: Optional[int]
    ) -> Optional[tuple[str, str, Optional[int], float]]:
        """Search TVDB for a series and return the best fuzzy-matched result.

        Args:
            title: Series title to search for.
            year: First-air year, if known; scores an exact year match
                higher.

        Returns:
            A (series_id, matched_title, matched_year, confidence) tuple,
            or None if TVDB returned no results.
        """
        data = self._get("/search", params={"query": title, "type": "series"}).get(
            "data", []
        )
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

    def rank_series(
        self, title: str, year: Optional[int]
    ) -> list[tuple[str, str, Optional[int], float]]:
        """Rank every series matching a title, best fuzzy score first.

        Args:
            title: Series title to search for.
            year: First-air year, if known; scores an exact year match higher.

        Returns:
            Up to MAX_SERIES_CANDIDATES (series_id, title, year, confidence)
            tuples, best first. Empty if TVDB returned nothing.
        """
        data = self._get("/search", params={"query": title, "type": "series"}).get(
            "data", []
        )

        ranked = []
        for candidate in data:
            series_id = candidate.get("tvdb_id") or candidate.get("id")
            if series_id is None:
                continue
            candidate_title = candidate.get("name") or ""
            score = fuzz.WRatio(title, candidate_title)
            raw_year = candidate.get("year")
            candidate_year = int(raw_year) if raw_year else None
            if year and candidate_year == year:
                score += YEAR_MATCH_BONUS
            ranked.append(
                (str(series_id), candidate_title, candidate_year, min(score, 100.0))
            )

        ranked.sort(key=lambda item: item[3], reverse=True)
        return ranked[:MAX_SERIES_CANDIDATES]

    def get_season_episodes(self, series_id: str, season: int) -> list[dict]:
        """Collect every episode of one season, ordered by episode number.

        Args:
            series_id: TVDB series id, as returned by search_series.
            season: Season number to collect.

        Returns:
            The season's episodes sorted by episode number; empty if the
            season wasn't found within MAX_EPISODE_PAGES pages.
        """
        found: list[dict] = []
        page = 0
        while page < MAX_EPISODE_PAGES:
            payload = self._get(
                f"/series/{series_id}/episodes/default", params={"page": page}
            )
            for ep in payload.get("data", {}).get("episodes", []):
                if ep.get("seasonNumber") == season and ep.get("number") is not None:
                    found.append(ep)
            if not payload.get("links", {}).get("next"):
                break
            page += 1

        found.sort(key=lambda ep: ep["number"])
        return found

    def get_episode_title(
        self, series_id: str, season: int, episode: int
    ) -> Optional[str]:
        """Look up an episode's title by paging through a series' episodes.

        Args:
            series_id: TVDB series id, as returned by search_series.
            season: Season number to look for.
            episode: Episode number to look for.

        Returns:
            The episode's title, or None if no matching season/episode was
            found within MAX_EPISODE_PAGES pages.
        """
        page = 0
        while page < MAX_EPISODE_PAGES:
            payload = self._get(
                f"/series/{series_id}/episodes/default", params={"page": page}
            )
            episodes = payload.get("data", {}).get("episodes", [])
            for ep in episodes:
                if ep.get("seasonNumber") == season and ep.get("number") == episode:
                    return ep.get("name")
            if not payload.get("links", {}).get("next"):
                break
            page += 1
        return None


def search_disc_episode(
    client: TVDBClient, disc_episode: DiscEpisode
) -> Optional[MediaMatch]:
    """Resolve a positionally-numbered disc track against TVDB.

    The positional numbering from discfolder is only trustworthy when the
    number of ripped tracks matches the season's real episode count. When the
    counts disagree -- extras on the disc, a missing rip, a split two-parter --
    the mapping could be off by any amount, so confidence is capped below the
    review threshold and the episode title is left off rather than guessed.

    Args:
        client: Authenticated TVDB client.
        disc_episode: Positional assignment produced by
            discfolder.build_disc_plan.

    Returns:
        A MediaMatch for the episode, or None if no series match was found.
    """
    candidates = client.rank_series(disc_episode.show, None)
    if not candidates:
        return None

    # A disc folder names no year and no country, so a remake scores as well
    # as the original -- "Being Human" is an exact hit on the 2009 UK series
    # when the discs are the 2011 US one. The rip's own shape breaks the tie:
    # prefer the candidate whose season is as long as the numbering implies.
    # Falling back to the best title match keeps a single-candidate search,
    # and an unverifiable one, behaving exactly as before.
    chosen = None
    for candidate in candidates:
        season_episodes = client.get_season_episodes(candidate[0], disc_episode.season)
        if len(season_episodes) == disc_episode.implied_season_length:
            chosen = (candidate, season_episodes)
            break

    if chosen is None:
        best = candidates[0]
        chosen = (best, client.get_season_episodes(best[0], disc_episode.season))

    (_series_id, matched_title, matched_year, confidence), episodes = chosen

    counts_agree = (
        bool(episodes) and len(episodes) == disc_episode.implied_season_length
    )
    episode_title = None

    if counts_agree:
        # Look the number up rather than indexing: disc-anchored numbering
        # skips the episodes whose tracks were never ripped.
        match = next(
            (e for e in episodes if e.get("number") == disc_episode.episode), None
        )
        episode_title = match.get("name") if match else None
    else:
        # Track count and episode count disagree -- the positional mapping is
        # unreliable, so flag rather than assert a title.
        confidence = min(confidence, NO_EPISODE_TITLE_CONFIDENCE_CAP)

    return MediaMatch(
        media_type="episode",
        title=matched_title,
        year=matched_year,
        season=disc_episode.season,
        episode=disc_episode.episode,
        episode_title=episode_title,
        confidence=confidence,
        source="tvdb",
    )


def search_episode(
    client: TVDBClient, title: str, year: Optional[int], season: int, episode: int
) -> Optional[MediaMatch]:
    """Search TVDB for a series and resolve one episode's title.

    If no matching episode is found, confidence is capped below the review
    threshold even if the series match itself was good.

    Args:
        client: Authenticated TVDB client.
        title: Series title to search for.
        year: First-air year, if known.
        season: Season number to look up.
        episode: Episode number to look up.

    Returns:
        A MediaMatch for the episode, or None if no series match was found.
    """
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
