"""Phase 3 increment 1: local experiment-config library (store + API)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient  # noqa: E402

from conftest import auth_client  # noqa: E402
from crab.web.errors import InputError, NotFoundError  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.library import LibraryStore, _slug  # noqa: E402

_CFG = {"global_options": {"numnodes": "8", "ppn": "1"}, "experiments": {}}


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def test_slug():
    assert _slug("Congestion Study!") == "congestion-study"
    assert _slug("  ") == "config"


def test_store_crud_and_uniqueness(tmp_path: Path):
    store = LibraryStore(_settings(tmp_path))
    assert store.list() == []

    a = store.create("My Run", _CFG)
    assert a.id == "my-run" and a.config["global_options"]["numnodes"] == "8"

    # Same name → unique id, not a clobber.
    b = store.create("My Run", _CFG)
    assert b.id == "my-run-2"
    assert {e.id for e in store.list()} == {"my-run", "my-run-2"}

    # Persistence across store instances.
    assert LibraryStore(_settings(tmp_path)).get("my-run").name == "My Run"

    updated = {**_CFG, "global_options": {"numnodes": "16"}}
    store.update("my-run", "Renamed", updated)
    got = store.get("my-run")
    assert got.name == "Renamed" and got.config["global_options"]["numnodes"] == "16"

    dup = store.duplicate("my-run")
    assert dup.name == "Renamed copy" and dup.id != "my-run"

    store.delete("my-run")
    with pytest.raises(NotFoundError):
        store.get("my-run")


def test_invalid_id_rejected(tmp_path: Path):
    store = LibraryStore(_settings(tmp_path))
    with pytest.raises(InputError):
        store.get("../etc/passwd")


def _client(tmp_path: Path) -> TestClient:
    return auth_client(create_app(_settings(tmp_path)))


def test_experiments_api_flow(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.get("/api/experiments").json() == []

        created = client.post("/api/experiments", json={"name": "Run A", "config": _CFG})
        assert created.status_code == 201
        eid = created.json()["id"]
        assert eid == "run-a"

        assert client.get(f"/api/experiments/{eid}").json()["name"] == "Run A"

        client.put(f"/api/experiments/{eid}", json={"name": "Run A2", "config": _CFG})
        assert client.get(f"/api/experiments/{eid}").json()["name"] == "Run A2"

        dup = client.post(f"/api/experiments/{eid}/duplicate")
        assert dup.status_code == 201 and dup.json()["id"] != eid

        assert len(client.get("/api/experiments").json()) == 2

        assert client.delete(f"/api/experiments/{eid}").status_code == 204
        assert client.get(f"/api/experiments/{eid}").status_code == 404
