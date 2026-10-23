"""Check a built wheel's member list: required files present, stray trees absent.

Usage: python scripts/check_wheel.py <wheel>

Prints one line per problem and exits 1 when there is any (2 on bad usage).
"""

from __future__ import annotations

import sys
import zipfile

# Members every wheel must contain. Later steps add to this list.
REQUIRED_MEMBERS = [
    "crab/web/static/index.html",
    "crab/crab_dashboard.html",
    "crab/wrappers/base.py",
    "crab/setup/recipes/__init__.py",
]

# A member is forbidden when any directory component of its path is one of these.
FORBIDDEN_DIRS = ["node_modules", "tests", "examples"]


def check(members: list[str]) -> list[str]:
    """Return one message per problem found in the wheel's member names."""
    problems = [f"missing required member: {name}" for name in REQUIRED_MEMBERS if name not in members]
    for name in members:
        dirs = name.split("/")[:-1]
        for forbidden in FORBIDDEN_DIRS:
            if forbidden in dirs:
                problems.append(f"forbidden member ({forbidden}): {name}")
                break
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_wheel.py <wheel>")
        return 2
    try:
        with zipfile.ZipFile(argv[0]) as zf:
            members = zf.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"cannot read wheel {argv[0]}: {exc}")
        return 1
    problems = check(members)
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s) in {argv[0]}")
        return 1
    print(f"OK: {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
