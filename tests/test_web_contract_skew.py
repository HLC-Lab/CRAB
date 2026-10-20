"""Connecting reports a warning when the cluster's CRAB speaks a different `--json` contract
(CONTRACT_SCHEMA) than this dashboard, so a laptop/cluster version gap is visible, not silent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.cli.contract import CONTRACT_SCHEMA  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from test_web_remotes import FakeTransport, _leonardo  # noqa: E402


def _connect(tmp_path: Path, info: dict) -> dict:
    async def connector(profile, password):
        return FakeTransport(stdout=json.dumps(info))

    app = create_app(
        Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"),
        manager=ConnectionManager(connector=connector),
    )
    with auth_client(app) as client:
        assert client.post("/api/remotes", json=_leonardo().model_dump()).status_code == 201
        resp = client.post("/api/remotes/leonardo/connect")
        assert resp.status_code == 200
        return resp.json()


def _info(**extra: object) -> dict:
    return {"crab_version": "0.1.0", "crab_root": "/h/CRAB", "presets": [], **extra}


def test_matching_contract_has_no_warning(tmp_path: Path) -> None:
    assert _connect(tmp_path, _info(schema=CONTRACT_SCHEMA))["skew"] is None


@pytest.mark.parametrize("info", [_info(schema=CONTRACT_SCHEMA - 1), _info()])
def test_older_or_unknown_cluster_contract_asks_to_update_the_cluster(
    tmp_path: Path, info: dict
) -> None:
    body = _connect(tmp_path, info)
    assert body["crab_installed"] is True
    assert "Run crab update on the cluster" in body["skew"]


def test_newer_cluster_contract_asks_to_update_the_dashboard(tmp_path: Path) -> None:
    skew = _connect(tmp_path, _info(schema=CONTRACT_SCHEMA + 1))["skew"]
    assert "Run crab update on this laptop" in skew
