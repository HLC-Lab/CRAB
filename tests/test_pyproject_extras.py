"""The packaging extras must name every import the web app makes at runtime."""

from __future__ import annotations

import re
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _extra(name: str) -> list[str]:
    """Entries of one `[project.optional-dependencies]` list, read as text (no
    `tomllib`: Python 3.10 is supported)."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\s*=\s*\[(.*?)^\]", text, re.MULTILINE | re.DOTALL)
    assert match, f"extra {name!r} not found in pyproject.toml"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_web_extra_installs_pyyaml_itself():
    # The SbatchMan campaign code imports yaml; it must not depend on uvicorn[standard]
    # happening to pull PyYAML in.
    assert any(entry.lower().startswith("pyyaml") for entry in _extra("web"))


def test_dev_extra_keeps_pyyaml_for_the_tests():
    assert any(entry.lower().startswith("pyyaml") for entry in _extra("dev"))
