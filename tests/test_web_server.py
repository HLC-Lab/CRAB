"""Phase 0 tests for the web dashboard backend skeleton.

Covered:
* settings paths derive correctly and ``ensure_dirs`` is idempotent;
* ``/api/health`` handshake;
* dev placeholder when the frontend is unbuilt;
* SPA serving + fallback + path-traversal guard when the frontend is built;
* the error taxonomy maps to the stable envelope.

No SSH, no cluster, no network — pure backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from conftest import auth_client  # noqa: E402

from crab.web.errors import (  # noqa: E402
    AuthError,
    CrabWebError,
    register_exception_handlers,
)
from crab.web.server import API_SCHEMA_VERSION, create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402


def _settings(tmp_path: Path, static: Path | None = None) -> Settings:
    return Settings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        static_override=static,
    )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_settings_paths_and_ensure_dirs(tmp_path: Path):
    s = _settings(tmp_path)
    assert s.clusters_file == s.config_dir / "clusters.json"
    assert s.experiments_dir == s.data_dir / "experiments"
    assert s.jobs_file == s.data_dir / "jobs.json"
    assert s.cache_dir == s.data_dir / "cache"

    s.ensure_dirs()
    s.ensure_dirs()  # idempotent
    assert s.config_dir.is_dir()
    assert s.experiments_dir.is_dir()
    assert s.cache_dir.is_dir()
    assert s.log_file.parent.is_dir()


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health_handshake(tmp_path: Path):
    client = auth_client(create_app(_settings(tmp_path)))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["api_schema"] == API_SCHEMA_VERSION
    assert "crab_version" in body


# --------------------------------------------------------------------------- #
# Frontend: dev placeholder vs built SPA
# --------------------------------------------------------------------------- #
def test_placeholder_when_frontend_unbuilt(tmp_path: Path):
    # static_override points at an empty dir => no index.html => placeholder.
    empty = tmp_path / "empty_static"
    empty.mkdir()
    client = TestClient(create_app(_settings(tmp_path, static=empty)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "backend is running" in resp.text


def test_spa_serving_and_fallback(tmp_path: Path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    (static / "assets" / "app.js").write_text("console.log('hi')")

    client = auth_client(create_app(_settings(tmp_path, static=static)))

    # Root serves index.
    assert client.get("/").status_code == 200
    assert "title>app" in client.get("/").text

    # A real built asset is served as-is.
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "console.log" in asset.text

    # Unknown client-side route falls back to index.html (SPA routing).
    deep = client.get("/jobs/123")
    assert deep.status_code == 200
    assert "title>app" in deep.text

    # API still takes precedence over the SPA catch-all.
    assert client.get("/api/health").status_code == 200


def test_spa_path_traversal_blocked(tmp_path: Path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("INDEX")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")

    client = TestClient(create_app(_settings(tmp_path, static=static)))
    # Attempt to escape static_dir: must fall back to index, never leak the file.
    resp = client.get("/../secret.txt")
    assert resp.status_code == 200
    assert "TOP SECRET" not in resp.text


# --------------------------------------------------------------------------- #
# Error taxonomy + envelope
# --------------------------------------------------------------------------- #
def test_error_envelope_shape():
    err = AuthError("Bad OTP", detail="code rejected")
    env = err.to_envelope()
    assert env == {"code": "auth_error", "message": "Bad OTP", "detail": "code rejected"}
    assert err.http_status == 401
    assert isinstance(err, CrabWebError)


def test_registered_handler_returns_envelope():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise AuthError("nope")

    # raise_server_exceptions=False so the handler (not the test client) responds.
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 401
    assert resp.json() == {"code": "auth_error", "message": "nope"}
