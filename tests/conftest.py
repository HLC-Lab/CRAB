"""Shared web-test helpers.

The localhost API requires a per-session token and a local Host header
(see src/crab/web/server.py api_guard). ``auth_client`` builds a TestClient
that authenticates like the real SPA does, so route tests exercise the routes
rather than the guard (test_web_security.py covers the guard itself).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
FIXTURE_WRAPPERS = Path(__file__).resolve().parent / "fixtures" / "wrappers"

# Tests of the shipped wrappers themselves need the crab-wrappers checkout in wrappers/.
# Tests of CRAB's own behaviour use FIXTURE_WRAPPERS instead and never carry this marker.
requires_wrappers_checkout = pytest.mark.skipif(
    not (REPO / "wrappers").is_dir(),
    reason="needs the crab-wrappers checkout in wrappers/ (crab update clones it)",
)


def auth_client(app: FastAPI, **kwargs) -> TestClient:
    from fastapi.testclient import TestClient

    return TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-Crab-Token": app.state.api_token},
        **kwargs,
    )
