"""Phase 2 tests: profile store, connection manager, remote crab command, API.

No real SSH — a fake transport/connector stands in. The real asyncssh + agent
path against Leonardo is user-verified (see .crab-web-dev/02-phases.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, LocalTransport, Transport  # noqa: E402
from crab.web.errors import (  # noqa: E402
    ConflictError,
    ContractError,
    NotFoundError,
    RemoteCommandError,
)
from crab.web.remoteops.crab_cli import build_crab_command, run_crab_json  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.profiles import Profile, ProfileStore  # noqa: E402

_INFO_JSON = '{"schema":1,"crab_version":"0.1.0","crab_root":"/home/u/CRAB","presets":[{"name":"leonardo","description":"Leonardo @ CINECA"}]}'


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


class FakeTransport(Transport):
    def __init__(self, stdout: str = _INFO_JSON, rc: int = 0, stderr: str = ""):
        self._stdout, self._rc, self._stderr = stdout, rc, stderr
        self._alive = True
        self.calls: list[str] = []

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.calls.append(command)
        return CmdResult(self._rc, self._stdout, self._stderr)

    async def close(self) -> None:
        self._alive = False


def _leonardo() -> Profile:
    return Profile(
        name="leonardo",
        host="login.cluster.example.org",
        user="researcher",
        auth="agent",
        hostkey_policy="insecure",
        remote_crab="~/CRAB",
        preset="leonardo",
    )


# --------------------------------------------------------------------------- #
# Profile store
# --------------------------------------------------------------------------- #
def test_profile_store_crud_and_persistence(tmp_path: Path):
    store = ProfileStore(_settings(tmp_path))
    assert store.list() == []

    store.add(_leonardo())
    assert [p.name for p in store.list()] == ["leonardo"]
    assert store.get("leonardo").host == "login.cluster.example.org"

    with pytest.raises(ConflictError):
        store.add(_leonardo())
    with pytest.raises(NotFoundError):
        store.get("ghost")

    # New store over the same dir sees the persisted profile.
    assert ProfileStore(_settings(tmp_path)).get("leonardo").preset == "leonardo"

    updated = _leonardo()
    updated.user = "other"
    store.update("leonardo", updated)
    assert store.get("leonardo").user == "other"

    store.remove("leonardo")
    assert store.list() == []
    with pytest.raises(NotFoundError):
        store.remove("leonardo")


# --------------------------------------------------------------------------- #
# Connection manager (fake connector)
# --------------------------------------------------------------------------- #
async def test_manager_reuses_and_evicts():
    made: list[FakeTransport] = []

    async def connector(profile, password):
        t = FakeTransport()
        made.append(t)
        return t

    mgr = ConnectionManager(connector=connector)
    p = _leonardo()

    t1 = await mgr.connect(p)
    t2 = await mgr.connect(p)
    assert t1 is t2 and len(made) == 1  # reused
    assert mgr.is_connected("leonardo")

    # Simulate a drop → get() evicts, next connect() builds fresh.
    t1._alive = False
    assert mgr.get("leonardo") is None
    assert not mgr.is_connected("leonardo")
    t3 = await mgr.connect(p)
    assert t3 is not t1 and len(made) == 2

    await mgr.disconnect("leonardo")
    assert mgr.get("leonardo") is None


async def test_manager_local_profile_needs_no_connector():
    async def connector(profile, password):  # must NOT be called for local
        raise AssertionError("connector used for local transport")

    mgr = ConnectionManager(connector=connector)
    local = Profile(name="local", transport="local", preset="local")
    t = await mgr.connect(local)
    assert isinstance(t, LocalTransport)


# --------------------------------------------------------------------------- #
# Remote crab command builder
# --------------------------------------------------------------------------- #
def test_build_crab_command_remote_activates_venv():
    cmd = build_crab_command(_leonardo(), ["info", "--json"])
    assert cmd.startswith("bash -lc ")
    # ~ is rewritten to $HOME (tilde quoting would break expansion).
    assert "$HOME/CRAB" in cmd
    assert ".venv/bin/activate" in cmd
    assert "crab info --json" in cmd


def test_build_crab_command_local_uses_interpreter():
    local = Profile(name="local", transport="local")
    cmd = build_crab_command(local, ["nodes", "--json"])
    assert "-m crab nodes --json" in cmd
    assert "bash -lc" not in cmd


async def test_run_crab_json_parses_and_maps_errors():
    p = _leonardo()
    ok = await run_crab_json(FakeTransport(), p, ["info", "--json"])
    assert ok["presets"][0]["name"] == "leonardo"

    with pytest.raises(RemoteCommandError):
        await run_crab_json(FakeTransport(rc=1, stderr="boom"), p, ["info", "--json"])

    with pytest.raises(ContractError):
        await run_crab_json(FakeTransport(stdout="not json"), p, ["info", "--json"])


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def _client(tmp_path: Path):
    async def connector(profile, password):
        return FakeTransport()

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    return auth_client(app)


def test_remotes_api_full_flow(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.get("/api/remotes").json() == []

        payload = _leonardo().model_dump()
        assert client.post("/api/remotes", json=payload).status_code == 201

        listed = client.get("/api/remotes").json()
        assert listed[0]["name"] == "leonardo"
        assert listed[0]["connected"] is False

        # Duplicate → conflict envelope.
        dup = client.post("/api/remotes", json=payload)
        assert dup.status_code == 409
        assert dup.json()["code"] == "conflict"

        # Connect runs `crab info --json` (fake) and returns it.
        conn = client.post("/api/remotes/leonardo/connect")
        assert conn.status_code == 200
        body = conn.json()
        assert body["connected"] is True
        assert body["info"]["presets"][0]["name"] == "leonardo"

        assert client.get("/api/remotes").json()[0]["connected"] is True

        assert client.post("/api/remotes/leonardo/disconnect").status_code == 204
        assert client.get("/api/remotes").json()[0]["connected"] is False

        assert client.delete("/api/remotes/leonardo").status_code == 204
        assert client.get("/api/remotes").json() == []


def test_connect_unknown_profile_returns_404(tmp_path: Path):
    with _client(tmp_path) as client:
        resp = client.post("/api/remotes/ghost/connect")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_rename_to_an_existing_name_conflicts(tmp_path: Path):
    with _client(tmp_path) as client:
        a = _leonardo().model_dump()
        b = {**a, "name": "other"}
        assert client.post("/api/remotes", json=a).status_code == 201
        assert client.post("/api/remotes", json=b).status_code == 201

        clash = client.put("/api/remotes/leonardo", json={**a, "name": "other"})
        assert clash.status_code == 409
        assert clash.json()["code"] == "conflict"


def test_rename_closes_the_stale_connection(tmp_path: Path):
    with _client(tmp_path) as client:
        app = client.app
        payload = _leonardo().model_dump()
        assert client.post("/api/remotes", json=payload).status_code == 201
        assert client.post("/api/remotes/leonardo/connect").status_code == 200
        assert app.state.manager.get("leonardo") is not None

        renamed = client.put("/api/remotes/leonardo", json={**payload, "name": "leo2"})
        assert renamed.status_code == 200
        # The live connection was keyed by the old name; it must not linger.
        assert app.state.manager.get("leonardo") is None
        assert client.get("/api/remotes").json()[0]["name"] == "leo2"
