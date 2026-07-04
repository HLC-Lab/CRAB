"""Localhost API authentication (plan 010): per-session token + host checks.

The dashboard executes SSH commands, so its localhost API must not be drivable
by a hostile web page (DNS rebinding / CSRF). Every ``/api/*`` request needs the
per-process token (``X-Crab-Token``), delivered to the SPA via a meta tag in the
served index.html; requests with a non-local ``Host`` (or a foreign ``Origin``)
are rejected regardless of token. Zero open API routes: ``/api/health`` included.

No SSH, no cluster, no network — pure backend (pattern: test_web_server.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient  # noqa: E402

from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402


def _settings(tmp_path: Path, static: Path | None = None) -> Settings:
    return Settings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        static_override=static,
    )


def _app_and_client(tmp_path: Path, static: Path | None = None):
    app = create_app(_settings(tmp_path, static))
    # Local host header: the middleware must accept the loopback names.
    client = TestClient(app, base_url="http://127.0.0.1")
    return app, client


def test_api_rejects_missing_token(tmp_path: Path):
    _, client = _app_and_client(tmp_path)
    with client:
        resp = client.get("/api/experiments")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] and body["message"]  # stable envelope


def test_api_rejects_wrong_token(tmp_path: Path):
    _, client = _app_and_client(tmp_path)
    with client:
        resp = client.get("/api/experiments", headers={"X-Crab-Token": "nope"})
        assert resp.status_code == 401


def test_api_accepts_the_session_token(tmp_path: Path):
    app, client = _app_and_client(tmp_path)
    with client:
        token = app.state.api_token
        resp = client.get("/api/experiments", headers={"X-Crab-Token": token})
        assert resp.status_code == 200


def test_health_needs_the_token_too(tmp_path: Path):
    app, client = _app_and_client(tmp_path)
    with client:
        assert client.get("/api/health").status_code == 401
        ok = client.get("/api/health", headers={"X-Crab-Token": app.state.api_token})
        assert ok.status_code == 200
        assert ok.json()["status"] == "ok"


def test_non_local_host_is_rejected_even_with_token(tmp_path: Path):
    # DNS rebinding: attacker.com resolves to 127.0.0.1, so the request arrives
    # with a non-local Host header. Must be rejected before anything else.
    app = create_app(_settings(tmp_path))
    client = TestClient(app, base_url="http://evil.example.com")
    with client:
        resp = client.get("/api/health", headers={"X-Crab-Token": app.state.api_token})
        assert resp.status_code in (400, 401, 403)


def test_foreign_origin_is_rejected(tmp_path: Path):
    app, client = _app_and_client(tmp_path)
    with client:
        resp = client.get(
            "/api/health",
            headers={
                "X-Crab-Token": app.state.api_token,
                "Origin": "https://evil.example.com",
            },
        )
        assert resp.status_code in (400, 401, 403)


def test_localhost_origin_is_accepted(tmp_path: Path):
    app, client = _app_and_client(tmp_path)
    with client:
        resp = client.get(
            "/api/health",
            headers={
                "X-Crab-Token": app.state.api_token,
                "Origin": "http://127.0.0.1",
            },
        )
        assert resp.status_code == 200


def test_served_index_carries_the_token_meta(tmp_path: Path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><html><head></head><body>x</body></html>")
    app, client = _app_and_client(tmp_path, static=static)
    with client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert f'name="crab-token" content="{app.state.api_token}"' in resp.text


def test_static_assets_do_not_need_the_token(tmp_path: Path):
    # Only /api/* is protected; the SPA shell must load without a token
    # (that's how the browser obtains it in the first place).
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><html><head></head><body>x</body></html>")
    _, client = _app_and_client(tmp_path, static=static)
    with client:
        assert client.get("/").status_code == 200
