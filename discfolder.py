"""Derives show/season/episode from ripped-disc folder and track names.

Handles libraries where the meaningful information lives in the parent folder
(e.g. ``BUFFY_S3_D1``) and the filename is only an opaque track id (e.g.
``C1_t00.mkv``) -- the case ``identify.parse_filename`` cannot handle, since it
inspects the filename alone.

Episode numbers are derived positionally: every track belonging to the same
show and season is ordered by (disc number, track number) and numbered
sequentially from 1. This is a heuristic -- discs routinely carry extras, so
the track count is verified against the season's real episode count before the
result is trusted (see ``tvdb.search_disc_episode``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Optional

# Episodes within one season run to near-uniform length, so a track far
# smaller than its group's median is a bonus feature, not an episode. Rips
# routinely carry featurettes, trailers, and menu loops alongside the
# episodes, and leaving them in shifts every subsequent episode number.
EXTRAS_SIZE_RATIO = 0.5

# "BUFFY_S3_D1", "Buffy Season 3 Disc 1", "SHOW.S03.D02" -> show/season/disc.
_FOLDER_RE = re.compile(
    r"^(?P<show>.+?)[ _.\-]+s(?:eason)?[ _.\-]*(?P<season>\d{1,2})"
    r"[ _.\-]+d(?:is[ck])?[ _.\-]*(?P<disc>\d{1,2})$",
    re.IGNORECASE,
)

# Trailing track id: "C1_t00" -> 0, "title_t12" -> 12.
_TRACK_RE = re.compile(r"t(?P<track>\d+)$", re.IGNORECASE)

_SEPARATORS = re.compile(r"[ _.\-]+")


@dataclass(frozen=True)
class DiscEpisode:
    """One ripped track, resolved to a positional episode within its season.

    Attributes:
        show: Show title recovered from the folder name.
        season: Season number recovered from the folder name.
        episode: Positional episode number, numbered from 1 across all discs
            of the same show and season.
        disc: Disc number the track came from.
        track: Track number within the disc.
        group_size: Total tracks found for this show and season, across every
            disc. Compared against the season's real episode count to decide
            whether the positional numbering can be trusted.
    """

    show: str
    season: int
    episode: int
    disc: int
    track: int
    group_size: int


def _clean_show(raw: str) -> str:
    name = _SEPARATORS.sub(" ", raw).strip()
    # Disc rips are routinely ALL CAPS ("BUFFY"); title-case reads better and
    # scores the same under rapidfuzz.
    if name.isupper():
        name = name.title()
    return name


def parse_disc_folder(folder_name: str) -> Optional[tuple[str, int, int]]:
    """Parse a ripped-disc folder name into show, season, and disc number.

    Args:
        folder_name: Bare folder name, e.g. "BUFFY_S3_D1".

    Returns:
        A (show, season, disc) tuple, or None if the name doesn't look like a
        season/disc folder.
    """
    match = _FOLDER_RE.match(folder_name.strip())
    if match is None:
        return None

    show = _clean_show(match.group("show"))
    if not show:
        return None

    return show, int(match.group("season")), int(match.group("disc"))


def parse_track_number(stem: str) -> Optional[int]:
    """Parse the trailing track number out of a ripped track filename.

    Args:
        stem: Filename without extension, e.g. "C1_t00".

    Returns:
        The track number, or None if the name has no trailing track id.
    """
    match = _TRACK_RE.search(stem)
    if match is None:
        return None
    return int(match.group("track"))


def _drop_extras(entries: list[tuple[int, int, Path]]) -> list[tuple[int, int, Path]]:
    """Drop tracks far smaller than the group median (bonus features)."""
    try:
        sizes = {entry[2]: entry[2].stat().st_size for entry in entries}
    except OSError:
        # Can't size the files; better to keep everything and let the
        # episode-count check flag the group than to drop a real episode.
        return list(entries)

    cutoff = median(sizes.values()) * EXTRAS_SIZE_RATIO
    kept = [entry for entry in entries if sizes[entry[2]] >= cutoff]

    # Never let the filter empty a group -- if every track looks like an
    # outlier the median is meaningless.
    return kept or list(entries)


def build_disc_plan(paths: list[Path]) -> dict[Path, DiscEpisode]:
    """Assign positional episode numbers to every ripped-disc track.

    Files whose parent folder isn't a recognized season/disc folder -- and
    tracks dropped as bonus features by the size check -- are left out of the
    result entirely, so the caller falls back to filename parsing for them.
    Nothing is discarded; an excluded file still gets converted, just via the
    review-flagged path.

    Args:
        paths: All source files found in the scan.

    Returns:
        A mapping of source path to its resolved DiscEpisode. Only files under
        a recognized season/disc folder appear.
    """
    # (show, season) -> list of (disc, track, path)
    groups: dict[tuple[str, int], list[tuple[int, int, Path]]] = {}

    for path in paths:
        parsed = parse_disc_folder(path.parent.name)
        if parsed is None:
            continue

        show, season, disc = parsed
        track = parse_track_number(path.stem)
        if track is None:
            # No track id to order by; positional numbering would be
            # arbitrary, so leave it to the filename-based path.
            continue

        groups.setdefault((show, season), []).append((disc, track, path))

    plan: dict[Path, DiscEpisode] = {}
    for (show, season), entries in groups.items():
        entries = _drop_extras(entries)
        entries.sort(key=lambda item: (item[0], item[1], item[2].name))
        group_size = len(entries)

        for index, (disc, track, path) in enumerate(entries, start=1):
            plan[path] = DiscEpisode(
                show=show,
                season=season,
                episode=index,
                disc=disc,
                track=track,
                group_size=group_size,
            )

    return plan
