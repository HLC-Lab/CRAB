"""Phase 3 cluster-catalog routes: /api/remotes/{name}/benchmarks and /nodes.

No real SSH — a command-aware fake transport returns the right `crab … --json`
payload per command.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import CmdResult, Transport
from crab.web.server import create_app
from crab.web.settings import Settings
from crab.web.store.profiles import Profile

_INFO = '{"schema":1,"crab_version":"0.1.0","crab_root":"/home/u/CRAB","presets":[{"name":"leonardo","description":"L"}]}'
_BENCH = (
    '{"schema":1,"benchmarks":[{"id":"blink"}],"wrappers":['
    '{"file":"a2a_comm_only.py","relpath":"blink/a2a_comm_only.py","group":"blink",'
    '"loadable":true,"benchmark_id":"a2a","bench_name":"A2A","metadata":[{"name":"lat","unit":"us","conv":true}]},'
    '{"file":"broken.py","relpath":"x/broken.py","group":"x","loadable":false,'
    '"benchmark_id":null,"bench_name":null,"metadata":[],"error":"ImportError"}]}'
)
_NODES = '{"schema":1,"available":true,"partitions":[{"name":"boost_usr_prod","avail":"up","nodes":3456}],"nodes":["lrdn0001","lrdn0002"]}'


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


class CmdAwareTransport(Transport):
    """Returns info/benchmarks/nodes JSON based on the command run."""

    def __init__(self) -> None:
        self._alive = True
        self.calls: list[str] = []

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.calls.append(command)
        if "list-benchmarks" in command:
            return CmdResult(0, _BENCH, "")
        if "nodes" in command:
            return CmdResult(0, _NODES, "")
        return CmdResult(0, _INFO, "")

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


def _client(tmp_path: Path) -> TestClient:
    async def connector(profile, password):
        return CmdAwareTransport()

    app = create_app(_settings(tmp_path), manager=ConnectionManager(connector=connector))
    return auth_client(app)


def test_benchmarks_and_nodes_require_connection_then_return_payloads(tmp_path: Path):
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo().model_dump())

        # Not connected yet → clean connection error (502), no implicit reconnect.
        pre = client.get("/api/remotes/leonardo/benchmarks")
        assert pre.status_code == 502
        assert pre.json()["code"] == "connection_error"

        assert client.post("/api/remotes/leonardo/connect").status_code == 200

        bench = client.get("/api/remotes/leonardo/benchmarks")
        assert bench.status_code == 200
        wrappers = bench.json()["wrappers"]
        # Unloadable wrappers are listed too (the Author UI marks, not filters, them).
        assert {w["relpath"] for w in wrappers} == {"blink/a2a_comm_only.py", "x/broken.py"}
        assert any(w["loadable"] is False for w in wrappers)

        nodes = client.get("/api/remotes/leonardo/nodes")
        assert nodes.status_code == 200
        assert nodes.json()["partitions"][0]["name"] == "boost_usr_prod"


def test_catalog_unknown_profile_404(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.get("/api/remotes/ghost/benchmarks").status_code == 404
