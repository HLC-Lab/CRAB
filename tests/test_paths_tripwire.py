"""Tripwire: no module outside `crab.paths` computes the CRAB root from its own `__file__`."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "crab"

# By design: `paths.py` is the one place that knows the layout; the web static dir ships inside
# the package; the local scheduler finds the package's own source dir for PYTHONPATH.
ALLOWED = {
    "paths.py",
    "web/settings.py",
    "core/execution/scheduler/local.py",
}

# `parents[3]` anywhere; `parents[2]` on a line that reads `__file__`; a triple ".." join.
_WALK_UPS = (
    re.compile(r"parents\[3\]"),
    re.compile(r"__file__.*parents\[2\]"),
    re.compile(r"""(["']\.\.["']\s*,\s*){2}["']\.\.["']"""),
)


def _walk_ups() -> list[str]:
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if any(pattern.search(line) for pattern in _WALK_UPS):
                hits.append(f"src/crab/{rel}:{number}: {line.strip()}")
    return hits


def test_no_root_walk_up_outside_paths_module():
    hits = _walk_ups()
    assert not hits, "compute the root through crab.paths instead:\n" + "\n".join(hits)
