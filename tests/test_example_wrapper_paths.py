"""Every wrapper a shipped example names exists. Wrappers moved into family folders
(wrappers/blink/...) long ago; examples still naming the old flat paths failed at launch with
"Wrapper not found" while passing every pre-submit check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _example_apps():
    for f in sorted((REPO / "examples").rglob("*.json")):
        config = json.loads(f.read_text())
        for exp, body in (config.get("experiments") or {}).items():
            for key, app in (body.get("apps") or {}).items():
                path = app.get("path")
                if path and not Path(path).is_absolute():
                    rel = f.relative_to(REPO)
                    yield pytest.param(path, id=f"{rel}:{exp}:{key}")


@pytest.mark.parametrize("path", list(_example_apps()))
def test_example_wrapper_exists(path: str) -> None:
    assert (REPO / "wrappers" / path).is_file(), f"no wrapper at wrappers/{path}"
