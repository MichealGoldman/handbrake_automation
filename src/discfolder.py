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

from naming import TAG_PREFIXES

# Episodes within one season run to near-uniform length, so a track far
# smaller than its group's median is a bonus feature, not an episode. Rips
# routinely carry featurettes, trailers, and menu loops alongside the
# episodes, and leaving them in shifts every subsequent episode number.
EXTRAS_SIZE_RATIO = 0.5

# Disc number assumed for a season folder that names no disc.
DEFAULT_DISC = 1

# Season assumed for a disc folder that names no season. A show that ran for
# one season is routinely ripped as bare discs ("FIREFLY- DISC 1"), with the
# season implicit because there was never a second one to distinguish.
DEFAULT_SEASON = 1

# A track this close in size to the largest could be the feature itself -- a
# second cut, a "play all" track, or a double feature. Size cannot separate
# them, so the pick is flagged rather than trusted.
FEATURE_MARGIN_RATIO = 0.9

# Matches the cap tvdb.search_disc_episode applies to an untrustworthy
# positional mapping: below CONFIDENCE_THRESHOLD, so the file is flagged.
AMBIGUOUS_FEATURE_CONFIDENCE = 60.0

# "BUFFY_S3_D1", "Buffy Season 3 Disc 1", "SHOW.S03.D02" -> show/season/disc.
# The disc part is optional: a whole season ripped into one folder
# ("THE_IT_CROWD_SEASON_4") is just a season whose only disc is DEFAULT_DISC.
# Requiring it meant such folders matched neither this pattern nor anything
# else, so they fell through to filename parsing and collided on track ids.
# MakeMKV names a disc folder after the disc label, which brackets the season
# and disc ("Battlestar Galactica- Season 1 (Disc 2)"). Either segment may
# therefore be introduced by a bracket instead of a plain separator, and closed
# by one. Both halves need this: making only the disc bracket-aware left
# "Show [Season 3] [Disc 4]" matching the disc-only pattern, which silently
# reported DEFAULT_SEASON instead of the season the folder actually names.
_SEG_LEAD = r"(?:[ _.\-]*[(\[][ _.\-]*|[ _.\-]+)"
_SEG_TAIL = r"[ _.\-]*[)\]]?"

# "DVD" is accepted alongside "DISC"/"DISK" and bare "D" here because the
# season is stated explicitly ("BEING_HUMAN_SEASON1_DVD2"); without it the
# folder matched nothing, fell through to the movie-rip path, and every track
# but the largest was dropped as an extra.
_FOLDER_RE = re.compile(
    r"^(?P<show>.+?)"
    + _SEG_LEAD
    + r"s(?:eason)?[ _.\-]*(?P<season>\d{1,2})"
    + _SEG_TAIL
    + r"(?:"
    + _SEG_LEAD
    + r"d(?:is[ck]|vd)?[ _.\-]*(?P<disc>\d{1,2})"
    + _SEG_TAIL
    + r")?$",
    re.IGNORECASE,
)

# "FIREFLY- DISC 1" -> show/disc, with the season left implicit. Only a
# spelled-out word is accepted here -- "DISC", "DISK" or "DVD": with no season
# to corroborate it, a bare trailing "D2" ends far too many movie folders to
# read as a disc number.
_DISC_ONLY_RE = re.compile(
    r"^(?P<show>.+?)"
    + _SEG_LEAD
    + r"(?:dis[ck]|dvd)[ _.\-]*(?P<disc>\d{1,2})"
    + _SEG_TAIL
    + r"$",
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


@dataclass(frozen=True)
class MovieRip:
    """One ripped track from a movie disc folder.

    Attributes:
        title: Movie title recovered from the folder name.
        is_feature: True for the single track chosen as the main feature; the
            rest of the folder is bonus material and is not converted.
        ambiguous: True when another track is close enough in size that the
            feature choice cannot be trusted.
    """

    title: str
    is_feature: bool
    ambiguous: bool


@dataclass(frozen=True)
class RipPlan:
    """Both halves of the ripped-disc plan, resolved before the per-file loop.

    Deliberately two separately typed mappings rather than one dict of a
    ``DiscEpisode | MovieRip`` union, so lookups stay type-safe and no
    isinstance branching is needed at the call site.

    Attributes:
        episodes: TV tracks resolved to positional episodes.
        movies: Movie tracks resolved from their folder name.
    """

    episodes: dict[Path, DiscEpisode]
    movies: dict[Path, MovieRip]


def _clean_show(raw: str) -> str:
    # A finished folder carries the same DONE_/REVIEW_/SKIP_ prefix its tracks
    # do. Left on, it would become part of the title ("Done Casino") the next
    # time the library is scanned.
    for prefix in TAG_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break

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
        season/disc folder. A folder naming no disc ("THE_IT_CROWD_SEASON_4")
        yields DEFAULT_DISC; one naming only a disc ("FIREFLY- DISC 1") yields
        DEFAULT_SEASON.
    """
    name = folder_name.strip()

    match = _FOLDER_RE.match(name)
    if match is not None:
        show = _clean_show(match.group("show"))
        disc = match.group("disc")
        season = int(match.group("season"))
        return (show, season, int(disc) if disc else DEFAULT_DISC) if show else None

    match = _DISC_ONLY_RE.match(name)
    if match is None:
        return None

    show = _clean_show(match.group("show"))
    return (show, DEFAULT_SEASON, int(match.group("disc"))) if show else None


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


def build_movie_plan(paths: list[Path], source_dir: Path) -> dict[Path, MovieRip]:
    """Recover movie titles from the folders their discs were ripped into.

    A folder counts as a movie rip when it isn't the scan root, isn't a
    season/disc folder, and holds at least one track with a trailing track id.
    Using ``parse_disc_folder`` as the negative test keeps the TV and movie
    paths mutually exclusive by construction, so no file can be claimed twice.

    The feature is the largest track. A folder holding one track needs no
    sizing at all; if sizing a multi-track folder fails the folder is left
    unclaimed rather than guessed at.

    Args:
        paths: All source files found in the scan.
        source_dir: Scan root. A track sitting directly in it has no folder
            name to recover a title from.

    Returns:
        A mapping of source path to its resolved MovieRip, covering every
        track of each recognized folder -- extras included, flagged
        ``is_feature=False`` so the caller can skip them.
    """
    groups: dict[Path, list[Path]] = {}

    for path in paths:
        folder = path.parent
        if folder == source_dir:
            continue
        if parse_disc_folder(folder.name) is not None:
            continue
        if parse_track_number(path.stem) is None:
            continue
        groups.setdefault(folder, []).append(path)

    plan: dict[Path, MovieRip] = {}
    for folder, tracks in groups.items():
        title = _clean_show(folder.name)
        if not title:
            continue

        if len(tracks) == 1:
            plan[tracks[0]] = MovieRip(title=title, is_feature=True, ambiguous=False)
            continue

        try:
            sizes = {track: track.stat().st_size for track in tracks}
        except OSError:
            # Can't tell the feature from the extras, so claiming the folder
            # would mean guessing which track to convert. Leave it to the
            # filename-based path instead.
            continue

        ordered = [
            track
            for track, _ in sorted(
                sizes.items(), key=lambda item: (-item[1], item[0].name)
            )
        ]
        feature = ordered[0]
        ambiguous = sizes[ordered[1]] >= sizes[feature] * FEATURE_MARGIN_RATIO

        for track in ordered:
            plan[track] = MovieRip(
                title=title,
                is_feature=track == feature,
                ambiguous=ambiguous,
            )

    return plan


def build_rip_plan(paths: list[Path], source_dir: Path) -> RipPlan:
    """Resolve every ripped-disc track, TV and movie alike, in one pass.

    Args:
        paths: All source files found in the scan.
        source_dir: Scan root.

    Returns:
        A RipPlan holding both mappings. The two builders stay separately
        callable so each can be tested on its own.
    """
    return RipPlan(
        episodes=build_disc_plan(paths),
        movies=build_movie_plan(paths, source_dir),
    )
