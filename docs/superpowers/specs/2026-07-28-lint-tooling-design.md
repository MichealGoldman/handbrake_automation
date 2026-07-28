# Lint / format / type-check enforcement — design

Date: 2026-07-28

## Problem

All Python files in this repo must pass `pylint`, `pydocstyle`, `pycodestyle`,
`black`, `isort`, and `mypy` before any commit is made. This is not enforced
by a git hook — it's a manual discipline the assistant follows: run every
check before running `git commit`, fix anything flagged, and only commit
once all six tools are clean. The user pushes code themselves; the assistant
never runs `git push`.

## Design

### Tooling

- New `requirements-dev.txt` (separate from runtime `requirements.txt`):
  `pylint`, `pydocstyle`, `pycodestyle`, `black`, `isort`, `mypy`.
- New `lint.py` at repo root, run as `python lint.py`. Order:
  1. `isort .` — auto-fix import ordering.
  2. `black .` — auto-fix formatting.
  3. `pycodestyle` — check-only.
  4. `pydocstyle` — check-only.
  5. `pylint` — check-only.
  6. `mypy` — check-only (type checking).
  Any check-only tool failing makes `lint.py` print that tool's output and
  exit non-zero. This is the single command run before every commit; a
  non-zero exit means fix and rerun until clean.

### Shared config (`pyproject.toml`)

- `[tool.black]`: default line length (88).
- `[tool.isort]`: `profile = "black"` so import formatting never fights
  black's formatting.
- `[tool.pylint]` / pycodestyle: `max-line-length = 88` to match black.
  Disable `missing-module-docstring`, `missing-class-docstring`,
  `missing-function-docstring` in pylint — docstring presence/content is
  pydocstyle's job; enforcing it in both risks conflicting messages for the
  same missing docstring.
- `[tool.pydocstyle]`: `convention = "google"` — all docstrings in this repo
  follow Google style (`Args:`, `Returns:`, `Raises:` sections).
- `[tool.mypy]`: standard strict-ish defaults for a small script project
  (check untyped defs, warn on unused ignores); no third-party stub
  requirements expected beyond what's already typed inline.

### Consequence for existing code

None of the 9 existing `.py` files currently have docstrings. Getting a clean
`python lint.py` run means adding Google-style docstrings to every module,
class, and public function across `config.py`, `convert.py`, `identify.py`,
`main.py`, `models.py`, `naming.py`, `tmdb.py`, `tvdb.py`, plus fixing
whatever `pylint`/`pycodestyle`/`mypy` flag once run for the first time
(unknown until the tools are actually run — line-length violations, missing
type annotations, etc.).

This overrides the general "default to no comments" instruction the
assistant otherwise follows — `CLAUDE.md` will state explicitly that
docstrings are required here because `pydocstyle` enforces them, so it's
clear this isn't a one-off deviation.

### Documentation (`CLAUDE.md`)

Add a section noting:
- `requirements-dev.txt` and how to install it.
- `python lint.py` must be run and pass clean before any commit.
- Docstrings are Google-style and required (pydocstyle-enforced), which is
  why this repo's Python files carry docstrings despite the assistant's
  default no-comments style.
- The assistant never runs `git push`; only the user pushes.

## Out of scope

- No git hook / CI enforcement — this is manual discipline, not automated
  gating.
- No decision yet on exact pylint/mypy strictness beyond the defaults above;
  specific disables/config tweaks will be added during implementation as
  real findings come up, not speculatively.
