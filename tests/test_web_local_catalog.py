"""GET /api/local/benchmarks: introspect the wrappers checked out on the
machine running crab web, with no SSH/profile involved.

Runs the real local `crab list-benchmarks --json` (same as how the existing
`local` transport is exercised in test_web_remotes.py) rather than mocking,
since there is no injectable seam for a plain local subprocess call.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from crab.web.server import create_app
from crab.web.settings import Settings


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    return TestClient(create_app(settings))


def test_local_benchmarks_runs_the_real_cli(tmp_path: Path):
    with _client(tmp_path) as client:
        resp = client.get("/api/local/benchmarks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema"] == 1
        assert isinstance(body["wrappers"], list)
        assert len(body["wrappers"]) > 0
        assert {"relpath", "loadable"}.issubset(body["wrappers"][0].keys())
