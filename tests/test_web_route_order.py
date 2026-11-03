"""The ordered route table of /api/jobs."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402


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
