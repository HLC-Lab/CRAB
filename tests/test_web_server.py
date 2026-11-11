"""Tests for the web dashboard backend skeleton.

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


@pytest.fixture
def fresh_settings(monkeypatch: pytest.MonkeyPatch):
    """`get_settings` with an empty cache before and after, and no CRAB_WEB_PORT set."""
    from crab.web.settings import get_settings

    monkeypatch.delenv("CRAB_WEB_PORT", raising=False)
    get_settings.cache_clear()
    yield get_settings
    get_settings.cache_clear()


def test_default_port_is_8899(fresh_settings):
    assert fresh_settings().port == 8899


def test_valid_port_env_is_used_silently(
    fresh_settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("CRAB_WEB_PORT", "9001")
    assert fresh_settings().port == 9001
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("bad", ["abc", "0", "70000", "-5"])
def test_bad_port_env_warns_once_and_falls_back(
    fresh_settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bad: str,
):
    monkeypatch.setenv("CRAB_WEB_PORT", bad)
    assert fresh_settings().port == 8899
    assert fresh_settings().port == 8899  # cached: no second warning
    err = capsys.readouterr().err
    assert err == f"CRAB_WEB_PORT={bad} is not a port number from 1 to 65535; using 8899.\n"


def test_web_help_states_the_new_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    import sys

    from crab.cli import main

    monkeypatch.setattr(sys, "argv", ["crab", "web", "--help"])
    with pytest.raises(SystemExit):
        main.cli_router()
    out = capsys.readouterr().out
    assert "8899" in out
    assert "8765" not in out


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_wrong_ascii_token_is_401(tmp_path: Path):
    client = TestClient(create_app(_settings(tmp_path)), base_url="http://127.0.0.1")
    resp = client.get("/api/health", headers={"X-Crab-Token": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "auth_required"


def test_non_ascii_token_is_401_not_500(tmp_path: Path):
    client = TestClient(create_app(_settings(tmp_path)), base_url="http://127.0.0.1")
    # Raw bytes: httpx refuses a non-ASCII str header; Starlette decodes it as latin-1.
    resp = client.get("/api/health", headers={"X-Crab-Token": "tökén".encode("latin-1")})
    assert resp.status_code == 401
    assert resp.json()["code"] == "auth_required"


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


def test_hashed_assets_get_a_long_lived_cache_header(tmp_path: Path):
    """Vite content-hashes every filename under assets/, so a
    changed file is always a new URL -- safe to cache for a long time."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    (static / "assets" / "app.js").write_text("console.log('hi')")

    client = auth_client(create_app(_settings(tmp_path, static=static)))

    asset = client.get("/assets/app.js")
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

    # The SPA shell must never be cached (it names the current asset bundle).
    shell = client.get("/")
    assert shell.headers["cache-control"] == "no-cache"


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


def test_unhandled_exception_gets_a_plain_message_without_a_traceback():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internal state")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    body = resp.json()
    assert resp.status_code == 500
    assert body["code"] == "internal_error"
    message = body["message"]
    assert "unexpected error" not in message.lower()
    assert "terminal where crab web is running" in message
    assert "below" not in message.lower()
    assert "Traceback" not in message
    assert "secret internal state" not in message
    assert "\u2014" not in message and "\u2013" not in message
    # The exception type goes to `detail` for debugging; its text and the traceback stay in the log.
    assert body["detail"] == "RuntimeError"
