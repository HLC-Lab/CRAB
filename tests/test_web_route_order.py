"""The ordered route table of /api/jobs, and which routers this line mounts."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client, mounted_api_paths, requires_route  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402


# `develop` mounts /api/jobs; the sbatchman line does not (it only authors campaigns),
# so the test skips here and runs again after a merge from `develop`.
@requires_route("/api/jobs")
def test_jobs_route_order_is_pinned(tmp_path: Path):
    # Pinned because FastAPI matches routes in registration order and the jobs
    # routers get split across modules. `app.routes` holds lazy included-router
    # objects, so the table is read from the OpenAPI paths, which follow that order.
    app = create_app(Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"))
    actual = [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/jobs")
        for method in operations
    ]
    expected = [
        ("POST", "/api/jobs/submit"),
        ("GET", "/api/jobs/submissions/{submission_id}"),
        ("GET", "/api/jobs"),
        ("POST", "/api/jobs/{record_id}/cancel"),
        ("GET", "/api/jobs/{record_id}/logs"),
        ("GET", "/api/jobs/{record_id}/experiments"),
        ("GET", "/api/jobs/report/{config_name}"),
    ]
    assert actual == expected


def test_dead_routers_are_not_mounted_and_live_ones_are(tmp_path: Path, monkeypatch):
    # The sbatchman line only authors campaigns (ADR-026, ADR-045): the Jobs and Results
    # routers stay in the tree for merges from `develop` but are not registered here.
    paths = mounted_api_paths()
    for dead in ("/api/jobs", "/api/experiments", "/api/results"):
        assert not [p for p in paths if p.startswith(dead)], dead
    for live in ("/api/remotes", "/api/wrappers", "/api/sbatchman", "/api/local", "/api/health"):
        assert [p for p in paths if p.startswith(live)], live

    from crab.web.api import jobs_submit

    calls: list[object] = []

    async def _boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("crab run must not be reachable on this line")

    monkeypatch.setattr(jobs_submit, "run_crab_json", _boom)
    app = create_app(Settings(config_dir=tmp_path / "cfg2", data_dir=tmp_path / "data2"))
    with auth_client(app) as client:
        posted = client.post("/api/jobs/submit", json={})
        assert posted.status_code in (404, 405), posted.status_code
        # A GET to an unmatched /api/... path falls through to the SPA catch-all (200 with
        # the HTML shell) when the frontend is built, else 404: never the JSON jobs list.
        listed = client.get("/api/jobs")
        assert listed.status_code in (200, 404), listed.status_code
        assert "application/json" not in listed.headers.get("content-type", "")
        assert client.get("/api/health").status_code == 200
    assert calls == []
