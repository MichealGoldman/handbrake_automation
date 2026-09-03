# Lint/Format/Type-Check Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every `.py` file in the repo passes `isort`, `black`, `pycodestyle`, `pydocstyle`, `pylint`, and `mypy` cleanly, runnable as a single `python lint.py` command, before any commit is made.

**Architecture:** A single orchestrator script (`lint.py`) runs `isort`/`black` as auto-fixers and `pycodestyle`/`pydocstyle`/`pylint`/`mypy` as check-only gates, against either the whole repo (default) or an explicit file list (for incremental work). Config lives in `pyproject.toml` (tools that support it: black, isort, pylint, mypy) and `setup.cfg` (tools that only support ini-style config: pycodestyle, pydocstyle). Existing modules get Google-style docstrings added to their public classes/functions/methods (private, underscore-prefixed helpers are exempt under `pydocstyle`'s convention and are left undocumented, per the project's normal no-comments default).

**Tech Stack:** Python 3, `pylint`, `pydocstyle`, `pycodestyle`, `black`, `isort`, `mypy` (all added via new `requirements-dev.txt`).

## Global Constraints

- Line length: 88 everywhere (black's default; pycodestyle/pylint configured to match).
- `isort` uses `profile = "black"` so import sorting and black formatting never disagree.
- `pydocstyle` convention: `google` — all docstrings use Google-style `Args:`/`Returns:`/`Raises:` sections.
- `mypy`: `check_untyped_defs = true`, `warn_unused_ignores = true`, `ignore_missing_imports = true` (no strict mode; no `types-*` stub packages added).
- Docstrings are required on every public (non-underscore-prefixed) module, class, and function — this intentionally overrides the assistant's general "no comments" default for this repo, because `pydocstyle` enforces it. Private (`_`-prefixed) helpers are not required to have docstrings and should not get them (matches existing code style).
- No git hook / CI enforcement — running `python lint.py` clean before every commit is manual discipline, not automated.
- The assistant never runs `git push`; commits only.
- This repo has no pytest suite. "Test" steps in this plan mean running `python lint.py` (optionally scoped to specific files) and checking its exit code / printed output — there is no other test harness to run.

---

### Task 1: Scaffolding — dev dependencies, tool config, and the `lint.py` orchestrator

**Files:**
- Create: `requirements-dev.txt`
- Create: `pyproject.toml`
- Create: `setup.cfg`
- Create: `lint.py`
- Modify: `config.py`, `convert.py`, `identify.py`, `main.py`, `models.py`, `naming.py`, `tmdb.py`, `tvdb.py` (auto-formatted only, by running the new tool — no manual edits in this task)

**Interfaces:**
- Produces: `lint.py`'s `main() -> int` entry point, invoked as `python lint.py [file ...]`. With no arguments it targets every top-level `*.py` file in the repo; with arguments it targets exactly those files. Exit code 0 = all check-only tools passed; 1 = at least one failed.

- [ ] **Step 1: Create `requirements-dev.txt`**

```
pylint>=3.0.0
pydocstyle>=6.3.0
pycodestyle>=2.11.0
black>=24.0.0
isort>=5.13.0
mypy>=1.8.0
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[tool.black]
line-length = 88

[tool.isort]
profile = "black"

[tool.pylint.format]
max-line-length = 88

[tool.pylint."messages control"]
disable = [
    "missing-module-docstring",
    "missing-class-docstring",
    "missing-function-docstring",
    "too-few-public-methods",
]

[tool.mypy]
check_untyped_defs = true
warn_unused_ignores = true
ignore_missing_imports = true
```

`missing-module-docstring`/`missing-class-docstring`/`missing-function-docstring` are disabled in pylint because `pydocstyle` already enforces docstring presence — running both risks two tools flagging the same missing docstring with different messages. `too-few-public-methods` is disabled because the codebase's plain dataclasses (`Config`, `ParsedName`, `MediaMatch`) and single-purpose exception classes (`ConfigError`, `ConversionError`, `TVDBAuthError`) are an intentional pattern, not a design smell.

- [ ] **Step 3: Create `setup.cfg`**

`pycodestyle` and `pydocstyle` predate `pyproject.toml` support and only read ini-style config (`setup.cfg`/`tox.ini`), so their settings can't live in `pyproject.toml` alongside the other four tools:

```ini
[pycodestyle]
max-line-length = 88

[pydocstyle]
convention = google
```

- [ ] **Step 4: Create `lint.py`**

```python
"""Runs isort and black (auto-fix), then pycodestyle, pydocstyle, pylint,
and mypy (check-only) across the project's Python files.

Usage:
    python lint.py               # lint every top-level .py file
    python lint.py foo.py bar.py  # lint only the given files
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

AUTOFIX_TOOLS = [
    ["isort"],
    ["black"],
]

CHECK_TOOLS = [
    ["pycodestyle"],
    ["pydocstyle"],
    ["pylint"],
    ["mypy"],
]


def _default_targets() -> list[str]:
    return sorted(str(p) for p in ROOT.glob("*.py"))


def _run(command: list[str], targets: list[str]) -> int:
    result = subprocess.run([*command, *targets], cwd=ROOT, check=False)
    return result.returncode


def main() -> int:
    """Auto-fix with isort/black, then run the check-only tools.

    Returns:
        0 if every check-only tool passed, 1 if any failed.
    """
    targets = sys.argv[1:] or _default_targets()

    for command in AUTOFIX_TOOLS:
        _run(command, targets)

    failed_tools = []
    for command in CHECK_TOOLS:
        print(f"\n=== {command[0]} ===")
        if _run(command, targets) != 0:
            failed_tools.append(command[0])

    if failed_tools:
        print(f"\nFAILED: {', '.join(failed_tools)}")
        return 1

    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Install dev dependencies**

Run: `pip install -r requirements-dev.txt`
Expected: all six tools install without error.

- [ ] **Step 6: Run the new tool for the first time**

Run: `python lint.py`
Expected: `isort`/`black` reformat the existing files in place (import order, line wrapping); `pycodestyle` passes or near-passes (formatting-only issues are now fixed); `pydocstyle` and `pylint` report missing-docstring findings on public classes/functions across all 8 existing modules — **this failure is expected at this stage**, it's what Tasks 2–6 fix. Exit code 1 is the correct outcome right now.

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt pyproject.toml setup.cfg lint.py config.py convert.py identify.py main.py models.py naming.py tmdb.py tvdb.py
git commit -m "Add lint/format/type-check tooling (lint.py, configs, dev deps)"
```

---

### Task 2: Docstrings for `config.py` and `models.py`

**Files:**
- Modify: `config.py` (`ConfigError` class, `Config` dataclass, `load_config` function)
- Modify: `models.py` (`MediaMatch` dataclass, `needs_review` property)

**Interfaces:**
- Consumes: nothing new — pure documentation additions, no signature changes.

- [ ] **Step 1: Add docstrings in `config.py`**

```python
class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""
```

```python
@dataclass(frozen=True)
class Config:
    """Fully resolved application configuration loaded from the environment.

    Attributes:
        source_dir: Root folder to recursively scan for .mkv files.
        dest_dir: Root of the Plex/Jellyfin-style output tree.
        handbrake_cli_path: Resolved path to the HandBrakeCLI executable.
        handbrake_preset: Exact HandBrake preset name to convert with.
        tmdb_api_key: API key used for movie lookups.
        tvdb_api_key: API key used for TV episode lookups.
    """

    source_dir: Path
    dest_dir: Path
    handbrake_cli_path: str
    handbrake_preset: str
    tmdb_api_key: str
    tvdb_api_key: str
```

```python
def load_config() -> Config:
    """Load and validate configuration from the environment.

    Returns:
        A fully resolved Config.

    Raises:
        ConfigError: If a required variable is missing, SOURCE_DIR does not
            exist, or HandBrakeCLI cannot be located.
    """
```

- [ ] **Step 2: Add docstrings in `models.py`**

```python
@dataclass(frozen=True)
class MediaMatch:
    """Best-match result from a TMDb/TVDB lookup, consumed by naming.py.

    Attributes:
        media_type: Either "movie" or "episode".
        title: Movie title, or series title for episodes.
        year: Movie release year, or series' first-air year.
        season: Season number, for episodes.
        episode: Episode number, for episodes.
        episode_title: Episode title, for episodes, if found.
        confidence: Match confidence score, 0-100.
        source: Which API produced the match ("tmdb", "tvdb", or "none").
    """

    media_type: str
    title: str
    year: Optional[int]
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_title: Optional[str] = None
    confidence: float = 0.0
    source: str = ""

    @property
    def needs_review(self) -> bool:
        """Return True if confidence is below CONFIDENCE_THRESHOLD."""
        return self.confidence < CONFIDENCE_THRESHOLD
```

Remove the now-redundant inline `#` comments on the `MediaMatch` fields (`# "movie" or "episode"`, etc.) — the Attributes section above documents them, so the inline comments would just be duplicated information.

- [ ] **Step 3: Verify just these two files**

Run: `python lint.py config.py models.py`
Expected: exit code 0, no output under any of the four check-tool sections.

- [ ] **Step 4: Commit**

```bash
git add config.py models.py
git commit -m "Add docstrings to config.py and models.py"
```

---

### Task 3: Docstrings for `convert.py` and `naming.py`

**Files:**
- Modify: `convert.py` (`ConversionError` class, `convert_file` function; add `check=False` to the existing `subprocess.run` call)
- Modify: `naming.py` (`build_dest_path` function)

**Interfaces:**
- Consumes: nothing new — pure documentation additions, plus one explicit-intent kwarg on an existing call.

- [ ] **Step 1: Add docstrings and fix `subprocess.run` in `convert.py`**

```python
class ConversionError(RuntimeError):
    """Raised when HandBrakeCLI exits with a non-zero status."""


def convert_file(
    source: Path, dest: Path, handbrake_cli_path: str, preset: str
) -> None:
    """Convert a single file with HandBrakeCLI.

    Args:
        source: Path to the source .mkv file.
        dest: Path to write the converted .mp4 to; parent directories are
            created as needed.
        handbrake_cli_path: Path to the HandBrakeCLI executable.
        preset: Exact HandBrake preset name to convert with.

    Raises:
        ConversionError: If HandBrakeCLI exits with a non-zero status. Any
            partial output file is deleted before raising.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = [
        handbrake_cli_path,
        "-i", str(source),
        "-o", str(dest),
        "--preset", preset,
    ]

    result = subprocess.run(args, capture_output=True, text=True, check=False)
```

`check=False` is added explicitly (previously omitted) — the code already handles the non-zero exit code itself via `result.returncode` below, so this documents that intent and satisfies pylint's `subprocess-run-check`.

- [ ] **Step 2: Add docstring in `naming.py`**

```python
def build_dest_path(match: MediaMatch, dest_root: Path) -> Path:
    """Build the Plex/Jellyfin-style destination path for a match.

    Low-confidence matches get REVIEW_MARKER appended to the filename (not
    the folder name) so they're easy to find after a run.

    Args:
        match: The identified media to build a path for.
        dest_root: Root of the destination tree.

    Returns:
        Full destination .mp4 path, e.g. "Movies/Title (Year)/Title
        (Year).mp4" or "TV Shows/Show/Season 01/Show - S01E02.mp4".
    """
```

- [ ] **Step 3: Verify just these two files**

Run: `python lint.py convert.py naming.py`
Expected: exit code 0.

- [ ] **Step 4: Commit**

```bash
git add convert.py naming.py
git commit -m "Add docstrings to convert.py and naming.py"
```

---

### Task 4: Docstrings for `identify.py` and `tmdb.py`

**Files:**
- Modify: `identify.py` (`ParsedName` dataclass, `parse_filename` function)
- Modify: `tmdb.py` (`search_movie` function)

**Interfaces:**
- Consumes: nothing new — pure documentation additions, no signature changes.

- [ ] **Step 1: Add docstring in `identify.py`**

```python
@dataclass(frozen=True)
class ParsedName:
    """Structured hints guessit extracted from a source filename.

    Attributes:
        media_type: Either "movie" or "episode".
        title: Best-guess movie or series title.
        year: Release year (movies) or first-air year (episodes), if found.
        season: Season number, for episodes.
        episode: Episode number, for episodes.
    """

    media_type: str
    title: str
    year: Optional[int]
    season: Optional[int]
    episode: Optional[int]


def parse_filename(path: Path) -> Optional[ParsedName]:
    """Parse a source filename into a ParsedName using guessit.

    Args:
        path: Source file path; only the filename is inspected.

    Returns:
        A ParsedName, or None if guessit could not extract a title.
    """
```

Remove the now-redundant inline `# "movie" or "episode"` comment on the `media_type` field.

- [ ] **Step 2: Add docstring in `tmdb.py`**

```python
def search_movie(
    title: str, year: Optional[int], api_key: str
) -> Optional[MediaMatch]:
    """Search TMDb for a movie and return the best fuzzy-matched result.

    Args:
        title: Movie title to search for.
        year: Release year, if known; scores an exact year match higher.
        api_key: TMDb API key.

    Returns:
        The best-matching MediaMatch, or None if TMDb returned no results.
    """
```

- [ ] **Step 3: Verify just these two files**

Run: `python lint.py identify.py tmdb.py`
Expected: exit code 0.

- [ ] **Step 4: Commit**

```bash
git add identify.py tmdb.py
git commit -m "Add docstrings to identify.py and tmdb.py"
```

---

### Task 5: Docstrings and type annotation for `tvdb.py`

**Files:**
- Modify: `tvdb.py` (`TVDBAuthError` class, `TVDBClient` class, `search_series` method, `get_episode_title` method, `search_episode` function)

**Interfaces:**
- Produces: `TVDBClient.search_series` gains an explicit return type,
  `Optional[tuple[str, str, Optional[int], float]]` — the
  `(series_id, matched_title, matched_year, confidence)` tuple it already
  returns, now annotated instead of left implicit.

- [ ] **Step 1: Add docstrings and the return-type annotation**

```python
class TVDBAuthError(RuntimeError):
    """Raised when TVDB login succeeds but returns no usable token."""
```

```python
class TVDBClient:
    """Minimal TVDB v4 client that lazily authenticates on first request."""

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._token: Optional[str] = None
```

```python
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
```

```python
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
```

```python
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
```

- [ ] **Step 2: Verify this file**

Run: `python lint.py tvdb.py`
Expected: exit code 0. If `mypy` flags the new tuple return type against how callers use it (e.g. in `main.py` or elsewhere), adjust the annotation to match actual usage rather than changing behavior.

- [ ] **Step 3: Commit**

```bash
git add tvdb.py
git commit -m "Add docstrings and return-type annotation to tvdb.py"
```

---

### Task 6: Docstring and pylint disable for `main.py`

**Files:**
- Modify: `main.py` (`main` function docstring; inline `pylint: disable` comment on the existing broad `except Exception:` in the identification step)

**Interfaces:**
- Consumes: nothing new — pure documentation additions plus one inline lint suppression with justification.

- [ ] **Step 1: Add docstring to `main()`**

```python
def main() -> int:
    """Run the full scan -> identify -> convert pipeline.

    Returns:
        0 if every file converted without failure, 2 if any file failed,
        1 if configuration could not be loaded.
    """
```

- [ ] **Step 2: Justify the broad exception catch**

```python
        try:
            match = _identify(source_path, tvdb_client, config)
        except Exception:  # pylint: disable=broad-exception-caught
            # One bad file must not abort the whole run.
            logging.exception("Identification failed for %s", source_path)
            failed += 1
            continue
```

- [ ] **Step 3: Verify this file**

Run: `python lint.py main.py`
Expected: exit code 0.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "Add docstring to main.py and justify broad exception catch"
```

---

### Task 7: Full-repo verification and documentation

**Files:**
- Modify: `CLAUDE.md` (new section documenting the dev workflow)

**Interfaces:**
- Consumes: `python lint.py` (Task 1), now expected to pass with no arguments across every file touched in Tasks 2–6.

- [ ] **Step 1: Run the full repo through the tool**

Run: `python lint.py`
Expected: exit code 0, `All checks passed.` printed. If anything still fails, fix it based on the tool's actual printed output (line-length stragglers, an unexpected pylint/mypy finding not covered above, etc.) before moving on — do not skip or silence a failure without understanding it first.

- [ ] **Step 2: Add a workflow section to `CLAUDE.md`**

Insert a new section (e.g. after "## Commands"):

```markdown
## Dev tooling

```
pip install -r requirements-dev.txt   # pylint, pydocstyle, pycodestyle, black, isort, mypy
python lint.py                        # auto-fixes (isort, black), then checks (pycodestyle, pydocstyle, pylint, mypy)
```

Every `.py` file must pass `python lint.py` cleanly before any commit — this
is manual discipline (there's no git hook or CI wired up), not an automated
gate. Docstrings are Google-style and required on every public module,
class, and function (`pydocstyle`-enforced); private (`_`-prefixed) helpers
don't need one. This project's assistant never runs `git push` — only the
user pushes.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Document lint.py workflow and docstring policy in CLAUDE.md"
```
