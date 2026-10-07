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


# --------------------------------------------------------------------------- #
# Path containment (plan 092 S19): profile names and results-route path
# params end up in local paths, so neither may escape its folder.
# --------------------------------------------------------------------------- #
_PROFILE = {"name": "leonardo", "host": "login.example.org", "user": "u", "auth": "agent"}


def _token_client(tmp_path: Path, manager=None) -> tuple:
    app = create_app(_settings(tmp_path), manager=manager)
    client = TestClient(
        app, base_url="http://127.0.0.1", headers={"X-Crab-Token": app.state.api_token}
    )
    return app, client


@pytest.mark.parametrize("bad", ["a/b", "..", ".", "../x", "x/..", "has space"])
def test_new_profile_with_a_path_like_name_is_rejected(tmp_path: Path, bad: str):
    _, client = _token_client(tmp_path)
    with client:
        resp = client.post("/api/remotes", json={**_PROFILE, "name": bad})
        assert resp.status_code == 422
        assert client.get("/api/remotes").json() == []


def test_renaming_a_profile_to_a_path_like_name_is_rejected(tmp_path: Path):
    _, client = _token_client(tmp_path)
    with client:
        assert client.post("/api/remotes", json=_PROFILE).status_code == 201
        resp = client.put("/api/remotes/leonardo", json={**_PROFILE, "name": "../evil"})
        assert resp.status_code == 422
        assert [p["name"] for p in client.get("/api/remotes").json()] == ["leonardo"]


def test_valid_profile_names_are_accepted(tmp_path: Path):
    _, client = _token_client(tmp_path)
    with client:
        for name in ("leonardo", "lumi-g.v2", "my_cluster", "a..b"):
            assert client.post("/api/remotes", json={**_PROFILE, "name": name}).status_code == 201


def test_a_saved_profile_with_an_old_style_name_still_loads_and_edits(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.config_dir.mkdir(parents=True)
    settings.clusters_file.write_text(
        '{"version": 1, "clusters": [{"name": "my cluster", "host": "h"}]}'
    )
    _, client = _token_client(tmp_path)
    with client:
        listed = client.get("/api/remotes")
        assert listed.status_code == 200
        assert [p["name"] for p in listed.json()] == ["my cluster"]
        # Editing other fields keeps the old name; only a NEW name is checked.
        resp = client.put("/api/remotes/my cluster", json={"name": "my cluster", "host": "h2"})
        assert resp.status_code == 200


def _outside_tree(tmp_path: Path) -> None:
    """A CSV tree just outside the results cache, at the data dir itself
    (`results_cache/x/../..`), that a traversal would read. `results_cache/x`
    must exist for the OS to walk back up through it."""
    settings = _settings(tmp_path)
    (settings.results_cache_dir / "x").mkdir(parents=True)
    exp = settings.data_dir / "e1"
    exp.mkdir(parents=True)
    (exp / "data_app_0.csv").write_text("x\n1\n", encoding="utf-8")


def test_results_get_rejects_encoded_dot_dot(tmp_path: Path):
    _outside_tree(tmp_path)
    _, client = _token_client(tmp_path)
    with client:
        resp = client.get("/api/results/x/%2E%2E/%2E%2E")
        assert resp.status_code in (400, 404)
        assert "experiments" not in resp.json()


def test_results_experiments_rejects_encoded_dot_dot(tmp_path: Path):
    _, client = _token_client(tmp_path)
    with client:
        client.post("/api/remotes", json=_PROFILE)
        resp = client.get("/api/results/leonardo/%2E%2E/%2E%2E/experiments")
        assert resp.status_code in (400, 404)


def test_results_fetch_never_writes_outside_the_cache(tmp_path: Path):
    from crab.web.connections.manager import ConnectionManager
    from crab.web.connections.transport import CmdResult, Transport
    from crab.web.store.jobs import JobsStore

    class _Fake(Transport):
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        @property
        def alive(self) -> bool:
            return True

        async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
            if "crab history" in command:
                return CmdResult(0, '{"schema": 1, "experiments": []}', "")
            return CmdResult(0, '{"schema": 1, "crab_version": "0.1.0", "presets": []}', "")

        async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
            raise AssertionError("unexpected write_file")

        async def fetch_tree(
            self, remote_dir: str, local_dir: str, timeout: float | None = 30.0
        ) -> None:
            self.calls.append((remote_dir, local_dir))

        async def close(self) -> None:
            pass

    fake = _Fake()

    async def connector(profile, password):
        return fake

    # A registry record whose system/basename are `..` would resolve the
    # local fetch target to the data dir itself.
    JobsStore(_settings(tmp_path)).create(
        cluster="leonardo",
        job_id="1",
        data_dir="/remote/data/..",
        system="..",
        config_name="demo",
        config_snapshot={},
    )
    _, client = _token_client(tmp_path, manager=ConnectionManager(connector=connector))
    with client:
        client.post("/api/remotes", json=_PROFILE)
        client.post("/api/remotes/leonardo/connect")
        resp = client.post("/api/results/leonardo/%2E%2E/%2E%2E/fetch")
        assert resp.status_code in (400, 404)
        assert fake.calls == []


def test_results_cache_rejects_a_dot_dot_that_stays_inside_the_root(tmp_path: Path):
    # `leonardo/../other` resolves inside the cache but names another job.
    from crab.web.errors import NotFoundError
    from crab.web.store.results_cache import ResultsCache

    cache = ResultsCache(_settings(tmp_path))
    (_settings(tmp_path).results_cache_dir / "leonardo").mkdir(parents=True)
    with pytest.raises(NotFoundError):
        cache.path_for("leonardo", "..", "other")


def test_results_cache_rejects_a_symlink_that_leaves_the_root(tmp_path: Path):
    from crab.web.errors import NotFoundError
    from crab.web.store.results_cache import ResultsCache

    settings = _settings(tmp_path)
    settings.results_cache_dir.mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (settings.results_cache_dir / "leonardo").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(NotFoundError):
        ResultsCache(settings).path_for("leonardo", "sys", "job")
