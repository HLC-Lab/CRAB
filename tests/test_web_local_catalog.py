"""GET /api/local/benchmarks: introspect the wrappers on the search path of the
machine running crab web, with no SSH/profile involved.

Runs the real local `crab list-benchmarks --json` (same as how the existing
`local` transport is exercised in test_web_remotes.py) rather than mocking,
since there is no injectable seam for a plain local subprocess call.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import FIXTURE_WRAPPERS, auth_client  # noqa: E402
from crab.web.server import create_app
from crab.web.settings import Settings


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    return auth_client(create_app(settings))


def test_local_benchmarks_runs_the_real_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The CLI subprocess inherits this env, so it lists the fixture wrappers, not a checkout's.
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(FIXTURE_WRAPPERS))
    with _client(tmp_path) as client:
        resp = client.get("/api/local/benchmarks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema"] == 1
        assert isinstance(body["wrappers"], list)
        assert {"relpath", "loadable"}.issubset(body["wrappers"][0].keys())
        listed = {w["relpath"]: w for w in body["wrappers"]}
        assert listed["fixture/path_app.py"]["loadable"] is True
