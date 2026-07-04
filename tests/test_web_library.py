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


# --------------------------------------------------------------------------- #
# Custom library location (plan 040 / ADR-014)
# --------------------------------------------------------------------------- #
def test_custom_library_dir_is_used(tmp_path: Path):
    lib = tmp_path / "my-configs"
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data", library_dir=lib)
    settings.ensure_dirs()
    store = LibraryStore(settings)
    entry = store.create("In Custom Dir", _CFG)
    assert (lib / f"{entry.id}.json").is_file()
    assert not (tmp_path / "data" / "experiments" / f"{entry.id}.json").exists()


def test_default_library_migrates_into_a_fresh_custom_dir(tmp_path: Path):
    # Entries saved before library_dir was configured...
    plain = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    plain.ensure_dirs()
    LibraryStore(plain).create("Old Entry", _CFG)

    # ...appear after pointing library_dir at an empty folder (one-time copy).
    lib = tmp_path / "my-configs"
    custom = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data", library_dir=lib)
    app = create_app(custom)
    with auth_client(app) as client:
        names = [e["name"] for e in client.get("/api/experiments").json()]
        assert "Old Entry" in names
    assert (lib / "old-entry.json").is_file()
    # The original stays put (copy, not move) so nothing is lost on rollback.
    assert (tmp_path / "data" / "experiments" / "old-entry.json").is_file()


def test_env_var_sets_library_dir(tmp_path: Path, monkeypatch):
    from crab.web.settings import get_settings

    monkeypatch.setenv("CRAB_WEB_LIBRARY_DIR", str(tmp_path / "envlib"))
    get_settings.cache_clear()
    try:
        assert get_settings().experiments_dir == tmp_path / "envlib"
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Save warnings (plan 020 / ADR-015)
# --------------------------------------------------------------------------- #
def test_save_reports_shape_warnings_but_still_saves(tmp_path: Path):
    app = create_app(_settings(tmp_path))
    with auth_client(app) as client:
        bad = {"global_options": {"numnodes": ["not", "a", "number"]}, "experiments": {}}
        resp = client.post("/api/experiments", json={"name": "Odd", "config": bad})
        assert resp.status_code == 201  # warnings never block a save
        body = resp.json()
        assert any("numnodes" in w for w in body["warnings"])
        # The entry really was stored, verbatim.
        stored = client.get(f"/api/experiments/{body['id']}").json()
        assert stored["config"]["global_options"]["numnodes"] == ["not", "a", "number"]


def test_save_of_a_clean_config_has_no_warnings(tmp_path: Path):
    app = create_app(_settings(tmp_path))
    with auth_client(app) as client:
        resp = client.post("/api/experiments", json={"name": "Clean", "config": _CFG})
        assert resp.status_code == 201
        assert resp.json()["warnings"] == []
