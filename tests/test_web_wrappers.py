"""Wrappers page routes: list a cluster's wrappers with their binary status
(`crab wrappers list --json`) and import a missing binary (`crab receipts set ... --json`).
No real SSH; a command-aware fake transport answers and records the commands."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from test_web_catalog import _INFO, _leonardo  # noqa: E402

_LIST = {
    "schema": 1,
    "search_path": ["/h/CRAB/local/wrappers", "/h/CRAB/wrappers"],
    "wrappers": [
        {
            "relpath": "hpl/hpl.py",
            "loadable": True,
            "benchmark_id": "hpl",
            "binary": {"status": "missing", "path": None},
        },
    ],
}
_RECEIPT = {"schema": 1, "receipt": {"id": "hpl", "binary_path": "/opt/hpl/xhpl"}}


class Fake(Transport):
    def __init__(self) -> None:
        self._alive = True
        self.calls: list[str] = []

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.calls.append(command)
        if "wrappers list" in command:
            return CmdResult(0, json.dumps(_LIST), "")
        if "receipts set" in command:
            return CmdResult(0, json.dumps(_RECEIPT), "")
        return CmdResult(0, _INFO, "")

    async def close(self) -> None:
        self._alive = False


@pytest.fixture
def client_and_fake(tmp_path: Path):
    fake = Fake()

    async def connector(profile, password):
        return fake

    app = create_app(
        Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"),
        manager=ConnectionManager(connector=connector),
    )
    with auth_client(app) as client:
        client.post("/api/remotes", json=_leonardo().model_dump())
        client.post("/api/remotes/leonardo/connect")
        yield client, fake


def _inner(command: str) -> list[str]:
    """The words of the `bash -lc '<inner>'` command, as the remote shell would split them."""
    return shlex.split(shlex.split(command)[2])


def test_list_returns_the_cluster_catalog(client_and_fake) -> None:
    client, fake = client_and_fake
    resp = client.get("/api/remotes/leonardo/wrappers")
    assert resp.status_code == 200
    assert resp.json()["wrappers"][0]["binary"]["status"] == "missing"
    assert "crab wrappers list --json" in fake.calls[-1]


def test_import_binary_sends_every_field_quoted(client_and_fake) -> None:
    client, fake = client_and_fake
    body = {
        "id": "hpl",
        "binary": "/opt/hpl dir/xhpl",
        "pre_run": ["module load mpi/4"],
        "launcher": "srun",
    }
    resp = client.post("/api/remotes/leonardo/receipts", json=body)
    assert resp.status_code == 200
    assert resp.json()["receipt"]["id"] == "hpl"
    words = _inner(fake.calls[-1])
    tail = words[words.index("crab") :]
    assert tail == [
        "crab",
        "receipts",
        "set",
        "hpl",
        "--binary",
        "/opt/hpl dir/xhpl",
        "--pre-run",
        "module load mpi/4",
        "--launcher",
        "srun",
        "--json",
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"id": "../evil", "binary": "/x"},
        {"id": "hpl", "binary": ""},
        {"id": "hpl", "binary": "/x", "launcher": "srun; rm -rf ~"},
    ],
)
def test_bad_import_input_is_refused_before_reaching_the_cluster(client_and_fake, body) -> None:
    client, fake = client_and_fake
    before = len(fake.calls)
    resp = client.post("/api/remotes/leonardo/receipts", json=body)
    assert resp.status_code == 422
    assert len(fake.calls) == before
