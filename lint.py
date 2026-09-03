"""Auto-fixes and checks the project's .py files.

Runs isort and black (auto-fix), then pycodestyle, pydocstyle, pylint, and
mypy (check-only) across lint.py itself plus src/ and tests/.

Usage:
    python lint.py                # lint lint.py, src/*.py, and tests/*.py
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
    paths = [ROOT / "lint.py", *ROOT.glob("src/*.py"), *ROOT.glob("tests/*.py")]
    return sorted(str(p) for p in paths)


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
