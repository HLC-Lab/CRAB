"""Localhost API authentication: per-session token + host checks.

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

from conftest import requires_route, route_is_mounted  # noqa: E402
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
        resp = client.get("/api/remotes")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] and body["message"]  # stable envelope


def test_api_rejects_wrong_token(tmp_path: Path):
    _, client = _app_and_client(tmp_path)
    with client:
        resp = client.get("/api/remotes", headers={"X-Crab-Token": "nope"})
        assert resp.status_code == 401


def test_api_accepts_the_session_token(tmp_path: Path):
    app, client = _app_and_client(tmp_path)
    with client:
        token = app.state.api_token
        resp = client.get("/api/remotes", headers={"X-Crab-Token": token})
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
        assert resp.status_code == 403
        assert resp.json()["code"] == "forbidden_host"


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
        assert resp.status_code == 403
        assert resp.json()["code"] == "forbidden_origin"


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
# Path containment: profile names and results-route path
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


@requires_route("/api/results")
def test_results_get_rejects_encoded_dot_dot(tmp_path: Path):
    _outside_tree(tmp_path)
    _, client = _token_client(tmp_path)
    with client:
        resp = client.get("/api/results/x/%2E%2E/%2E%2E")
        assert resp.status_code == 404
        assert "experiments" not in resp.json()


@requires_route("/api/results")
def test_results_experiments_rejects_encoded_dot_dot(tmp_path: Path):
    _, client = _token_client(tmp_path)
    with client:
        client.post("/api/remotes", json=_PROFILE)
        resp = client.get("/api/results/leonardo/%2E%2E/%2E%2E/experiments")
        assert resp.status_code == 404


@requires_route("/api/results")
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
        assert resp.status_code == 404
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


def test_local_is_reserved_for_this_laptop(tmp_path: Path):
    _, client = _token_client(tmp_path)
    with client:
        resp = client.post("/api/remotes", json={**_PROFILE, "name": "local"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "input_error"
        assert client.get("/api/remotes").json() == []
        assert client.post("/api/remotes", json=_PROFILE).status_code == 201
        renamed = client.put("/api/remotes/leonardo", json={**_PROFILE, "name": "local"})
        assert renamed.status_code == 422


# --------------------------------------------------------------------------- #
# Host / Origin / token rules (ADR-013), pinned with exact statuses and codes.
# The guard checks Host first (403 forbidden_host), then Origin when present
# (403 forbidden_origin), then the token (401 auth_required), on every method.
# --------------------------------------------------------------------------- #
_PORT = 8765


def _port_client(tmp_path: Path) -> tuple:
    app = create_app(_settings(tmp_path))
    client = TestClient(app, base_url=f"http://127.0.0.1:{_PORT}")
    return app, client


@pytest.mark.parametrize(
    "host",
    [
        "[::1]",
        f"[::1]:{_PORT}",
        "localhost",
        f"localhost:{_PORT}",
        "127.0.0.1",
        f"127.0.0.1:{_PORT}",
    ],
)
def test_loopback_host_forms_are_accepted(tmp_path: Path, host: str):
    app, client = _port_client(tmp_path)
    with client:
        resp = client.get(
            "/api/health", headers={"Host": host, "X-Crab-Token": app.state.api_token}
        )
        assert resp.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "localhost.evil.com",
        f"localhost.evil.com:{_PORT}",
        "127.0.0.1.evil.com",
        "evil.com",
        "",
        "user@localhost",
        f"user@localhost:{_PORT}",
        "user:pass@127.0.0.1",
    ],
)
def test_lookalike_and_malformed_hosts_are_refused_even_with_the_token(tmp_path: Path, host: str):
    app, client = _port_client(tmp_path)
    with client:
        with_token = client.get(
            "/api/health", headers={"Host": host, "X-Crab-Token": app.state.api_token}
        )
        assert with_token.status_code == 403
        assert with_token.json()["code"] == "forbidden_host"
        # The host check runs before the token check: a bad host never gets a 401 hint.
        without_token = client.get("/api/health", headers={"Host": host})
        assert without_token.status_code == 403
        assert without_token.json()["code"] == "forbidden_host"


@pytest.mark.parametrize("method", ["post", "put", "delete"])
@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "http://evil.com",
        "https://evil.com:8765",
        "http://localhost.evil.com",
        "http://127.0.0.1.evil.com",
        "http://localhost@evil.com",
        "",
    ],
)
def test_foreign_or_null_origin_is_refused_on_mutating_requests(
    tmp_path: Path, method: str, origin: str
):
    app, client = _port_client(tmp_path)
    headers = {"X-Crab-Token": app.state.api_token, "Origin": origin}
    with client:
        if method == "post":
            resp = client.post("/api/remotes", headers=headers, json=_PROFILE)
        elif method == "put":
            resp = client.put("/api/remotes/leonardo", headers=headers, json=_PROFILE)
        else:
            resp = client.delete("/api/remotes/leonardo", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "forbidden_origin"
        # The refused POST created nothing.
        listed = client.get("/api/remotes", headers={"X-Crab-Token": app.state.api_token})
        assert listed.json() == []


@pytest.mark.parametrize(
    "origin", [f"http://127.0.0.1:{_PORT}", f"http://localhost:{_PORT}", f"http://[::1]:{_PORT}"]
)
def test_same_origin_forms_are_accepted_on_mutating_requests(tmp_path: Path, origin: str):
    app, client = _port_client(tmp_path)
    headers = {"X-Crab-Token": app.state.api_token, "Origin": origin}
    with client:
        assert client.post("/api/remotes", headers=headers, json=_PROFILE).status_code == 201
        assert (
            client.put("/api/remotes/leonardo", headers=headers, json=_PROFILE).status_code == 200
        )
        assert client.delete("/api/remotes/leonardo", headers=headers).status_code == 204


def test_a_request_without_origin_is_judged_on_host_and_token_alone(tmp_path: Path):
    # Non-browser callers (curl, the TestClient) send no Origin: the guard lets them
    # through when Host and token are right, and the token still decides.
    app, client = _port_client(tmp_path)
    with client:
        ok = client.post(
            "/api/remotes", headers={"X-Crab-Token": app.state.api_token}, json=_PROFILE
        )
        assert ok.status_code == 201
        bare = client.post("/api/remotes", json={**_PROFILE, "name": "other"})
        assert bare.status_code == 401
        assert bare.json()["code"] == "auth_required"


# One request per router `create_app` can include. The guard runs before routing, so
# nothing below reaches a handler: the answer is the guard's own. A router this line does
# not mount (experiments, jobs, jobs_submit, jobs_report, results on the sbatchman line)
# is left out, so the table holds on `develop` too.
_ALL_ROUTER_ROUTES = [
    ("remotes", "GET", "/api/remotes"),
    ("bootstrap", "POST", "/api/remotes/leonardo/bootstrap/plan"),
    ("wrappers", "GET", "/api/wrappers/local/list"),
    ("experiments", "GET", "/api/experiments"),
    ("local", "GET", "/api/local/benchmarks"),
    ("jobs_submit", "POST", "/api/jobs/submit"),
    ("jobs", "GET", "/api/jobs"),
    ("jobs_report", "GET", "/api/jobs/report/demo"),
    ("results", "GET", "/api/results"),
    ("sbatchman", "GET", "/api/sbatchman/campaigns"),
    ("sbatchman_write", "POST", "/api/sbatchman/write"),
]
_ROUTER_ROUTES = [
    route for route in _ALL_ROUTER_ROUTES if route_is_mounted("/".join(route[2].split("/")[:3]))
]


@pytest.mark.parametrize("token", [None, "", "nope", "x" * 200])
@pytest.mark.parametrize("router_name,method,path", _ROUTER_ROUTES)
def test_every_live_router_refuses_a_missing_or_wrong_token(
    tmp_path: Path, router_name: str, method: str, path: str, token: str | None
):
    _, client = _port_client(tmp_path)
    headers = {} if token is None else {"X-Crab-Token": token}
    with client:
        resp = client.request(method, path, headers=headers, json={} if method == "POST" else None)
        assert resp.status_code == 401, router_name
        body = resp.json()
        assert body["code"] == "auth_required"
        assert body["message"]


@pytest.mark.parametrize("token", [None, "", "nope"])
def test_sbatchman_routes_refuse_a_missing_or_wrong_token(tmp_path: Path, token: str | None):
    _, client = _port_client(tmp_path)
    headers = {} if token is None else {"X-Crab-Token": token}
    with client:
        listed = client.get("/api/sbatchman/campaigns", headers=headers)
        written = client.post("/api/sbatchman/write", headers=headers, json={})
        for resp in (listed, written):
            assert resp.status_code == 401
            assert resp.json()["code"] == "auth_required"


def test_sbatchman_routes_accept_the_session_token(tmp_path: Path):
    app, client = _port_client(tmp_path)
    headers = {"X-Crab-Token": app.state.api_token}
    with client:
        listed = client.get("/api/sbatchman/campaigns", headers=headers)
        assert listed.status_code == 200
        assert listed.json() == []
        # The guard let it through: the body `{}` is judged by the route (422), not by the guard.
        written = client.post("/api/sbatchman/write", headers=headers, json={})
        assert written.status_code == 422


def test_the_session_token_is_accepted_on_the_same_routes(tmp_path: Path):
    # Counterpart of the refusals above: same client, right token, no 401 on any route.
    app, client = _port_client(tmp_path)
    with client:
        for router_name, method, path in _ROUTER_ROUTES:
            resp = client.request(
                method,
                path,
                headers={"X-Crab-Token": app.state.api_token},
                json={} if method == "POST" else None,
            )
            assert resp.status_code != 401, router_name
            assert resp.status_code != 403, router_name


# --------------------------------------------------------------------------- #
# Traversal per path-taking route family: a refusal AND no file outside the data
# dir touched.
# --------------------------------------------------------------------------- #
def test_wrappers_show_refuses_a_relpath_that_climbs_out(tmp_path: Path, monkeypatch):
    # The guard (`_check_relpath`) must answer 422 before any `crab wrappers show`
    # runs: with no guard the relpath would be handed to the engine.
    from crab.web.api import wrappers

    calls: list[object] = []

    async def _spy(*args, **kwargs):
        calls.append(args)
        return {}

    monkeypatch.setattr(wrappers, "run_crab_json", _spy)
    _, client = _token_client(tmp_path)
    with client:
        for bad in ("../x", "a/../../x", "..", "/etc/passwd", "-rf", "a/b/../../../c"):
            resp = client.get("/api/wrappers/local/show", params={"relpath": bad})
            assert resp.status_code == 422, bad
            assert resp.json()["code"] == "input_error"
        # An encoded `..` reaches the handler decoded, and is refused the same way.
        resp = client.get("/api/wrappers/local/show?relpath=%2E%2E%2Fx")
        assert resp.status_code == 422
    assert calls == []


def test_wrappers_show_still_accepts_a_plain_relpath(tmp_path: Path, monkeypatch):
    from crab.web.api import wrappers

    seen: list[list] = []

    async def _spy(transport, profile, args, timeout):
        seen.append(list(args))
        return {"relpath": "hpl/hpl.py"}

    monkeypatch.setattr(wrappers, "run_crab_json", _spy)
    _, client = _token_client(tmp_path)
    with client:
        resp = client.get("/api/wrappers/local/show", params={"relpath": "hpl/hpl.py"})
        assert resp.status_code == 200
    assert seen == [["wrappers", "show", "hpl/hpl.py", "--json"]]


def test_library_store_refuses_an_id_that_climbs_out_and_leaves_the_file(tmp_path: Path):
    # `experiments/../secret.json` is a file in the data dir, outside the library.
    from crab.web.errors import InputError
    from crab.web.store.library import LibraryStore

    settings = _settings(tmp_path)
    settings.experiments_dir.mkdir(parents=True)
    secret = settings.experiments_dir.parent / "secret.json"
    secret.write_text('{"id": "secret", "name": "n", "updated_at": "t", "config": {}}')
    store = LibraryStore(settings)
    for bad in ("../secret", "../data/secret", "..", ".", "a/b", "/etc/passwd", "-x", "A"):
        with pytest.raises(InputError):
            store.get(bad)
        with pytest.raises(InputError):
            store.delete(bad)
        with pytest.raises(InputError):
            store.update(bad, "n", {})
        with pytest.raises(InputError):
            store.duplicate(bad)
    assert secret.read_text().startswith('{"id": "secret"')
    assert sorted(p.name for p in settings.data_dir.iterdir()) == ["experiments", "secret.json"]
    assert list(settings.experiments_dir.iterdir()) == []


@requires_route("/api/experiments")
def test_experiments_routes_refuse_a_dot_dot_id_and_leave_the_files(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.experiments_dir.mkdir(parents=True)
    secret = settings.experiments_dir.parent / "secret.json"
    secret.write_text('{"keep": true}')
    _, client = _token_client(tmp_path)
    with client:
        for method, path in (
            ("GET", "/api/experiments/%2E%2E"),
            ("PUT", "/api/experiments/%2E%2E"),
            ("DELETE", "/api/experiments/%2E%2E"),
            ("POST", "/api/experiments/%2E%2E/duplicate"),
        ):
            resp = client.request(
                method,
                path,
                json={"name": "n", "config": {}} if method == "PUT" else None,
            )
            assert resp.status_code == 422, (method, path)
            assert resp.json()["code"] == "input_error"
        # A slash in the id never matches the route; the decoded path falls past every
        # API route, so nothing reaches the store (DELETE has no catch-all: 405).
        slashed = client.delete("/api/experiments/%2E%2E%2Fsecret")
        assert slashed.status_code == 405
    assert secret.read_text() == '{"keep": true}'
    assert list(settings.experiments_dir.iterdir()) == []


def test_remote_profile_routes_refuse_a_dot_dot_name_and_write_nothing(tmp_path: Path):
    _, client = _token_client(tmp_path)
    with client:
        resp = client.post("/api/remotes", json={**_PROFILE, "name": "../../escape"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "input_error"
    # Nothing but the app's own folders under tmp_path.
    assert {p.name for p in tmp_path.iterdir()} <= {"config", "data"}
