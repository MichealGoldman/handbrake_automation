"""Minimal TMDb client: search for a movie and return the best fuzzy match."""
from __future__ import annotations

from typing import Optional

import requests
from rapidfuzz import fuzz

from models import MediaMatch

BASE_URL = "https://api.themoviedb.org/3"
YEAR_MATCH_BONUS = 5.0


def search_movie(title: str, year: Optional[int], api_key: str) -> Optional[MediaMatch]:
    params = {"api_key": api_key, "query": title, "include_adult": "false"}
    if year:
        params["year"] = year

    resp = requests.get(f"{BASE_URL}/search/movie", params=params, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return None

    best_result = None
    best_title = ""
    best_year: Optional[int] = None
    best_score = -1.0

    for result in results:
        candidate_title = result.get("title") or result.get("original_title") or ""
        score = fuzz.WRatio(title, candidate_title)

        release_date = result.get("release_date") or ""
        candidate_year = int(release_date[:4]) if release_date[:4].isdigit() else None
        if year and candidate_year == year:
            score += YEAR_MATCH_BONUS

        if score > best_score:
            best_score = score
            best_result = result
            best_title = candidate_title
            best_year = candidate_year

    if best_result is None:
        return None

    return MediaMatch(
        media_type="movie",
        title=best_title,
        year=best_year,
        confidence=min(best_score, 100.0),
        source="tmdb",
    )
