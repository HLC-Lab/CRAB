"""Saving an experiment reports the engine's own config checks as warnings (ADR-015).

A save is never rejected: an engine error comes back as a warning and the entry is stored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient  # noqa: E402

from conftest import auth_client  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402

_BAD_BOOL = "experiment e1: convergeall must be true or false, got 'yes'"


def _config(**global_options: object) -> dict:
    return {
        "global_options": {"numnodes": "1", "ppn": "1", **global_options},
        "experiments": {"e1": {"apps": {"0": {"path": "a.py"}}}},
    }


def _client(tmp_path: Path) -> TestClient:
    return auth_client(
        create_app(Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"))
    )


def test_a_clean_config_saves_without_warnings(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post("/api/experiments", json={"name": "ok", "config": _config()})

    assert created.status_code == 201
    assert created.json()["warnings"] == []


def test_an_engine_error_is_a_warning_and_the_entry_is_stored(tmp_path: Path) -> None:
    config = _config(convergeall="yes")
    with _client(tmp_path) as client:
        created = client.post("/api/experiments", json={"name": "bad", "config": config})
        stored = client.get("/api/experiments/bad")

    assert created.status_code == 201
    assert created.json()["warnings"] == [_BAD_BOOL]
    assert stored.json()["config"] == config


def test_an_update_reports_the_engine_error_too(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.post("/api/experiments", json={"name": "run", "config": _config()})
        updated = client.put(
            "/api/experiments/run", json={"name": "run", "config": _config(convergeall="yes")}
        )

    assert updated.status_code == 200
    assert updated.json()["warnings"] == [_BAD_BOOL]


def test_an_engine_warning_is_reported(tmp_path: Path) -> None:
    config = _config(allocation={"mode": "linear", "split": [50]})
    with _client(tmp_path) as client:
        created = client.post("/api/experiments", json={"name": "idle", "config": config})

    assert created.json()["warnings"] == [
        "experiment e1: allocation.split sums to 50; 50% of the nodes stay idle"
    ]


def test_a_newer_schema_version_is_a_warning(tmp_path: Path) -> None:
    config = {**_config(), "schema_version": 99}
    with _client(tmp_path) as client:
        created = client.post("/api/experiments", json={"name": "new", "config": config})

    assert created.status_code == 201
    assert len(created.json()["warnings"]) == 1
    assert "schema_version 99" in created.json()["warnings"][0]


def test_shape_and_engine_warnings_are_listed_together(tmp_path: Path) -> None:
    config = _config(convergeall="yes", numnodes=["x"])
    with _client(tmp_path) as client:
        created = client.post("/api/experiments", json={"name": "both", "config": config})

    warnings = created.json()["warnings"]
    assert created.status_code == 201
    assert any(w.startswith("global_options.numnodes") for w in warnings)


def test_a_config_the_engine_cannot_even_read_still_saves(tmp_path: Path) -> None:
    config = {"experiments": ["not", "a", "dict"]}
    with _client(tmp_path) as client:
        created = client.post("/api/experiments", json={"name": "odd", "config": config})

    assert created.status_code == 201
    assert created.json()["warnings"]
