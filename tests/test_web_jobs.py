"""Phase 4: local job registry (store/jobs.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.store.jobs import JobsStore  # noqa: E402

from crab.web.errors import NotFoundError  # noqa: E402
from crab.web.settings import Settings  # noqa: E402

_SNAPSHOT = {"global_options": {"name": "demo"}, "experiments": []}


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def test_store_crud(tmp_path: Path):
    store = JobsStore(_settings(tmp_path))
    assert store.list() == []

    rec = store.create(
        cluster="leonardo",
        job_id="12345",
        data_dir="/data/leonardo/demo_2026",
        system="leonardo",
        config_name="demo",
        config_snapshot=_SNAPSHOT,
    )
    assert rec.id == "leonardo:12345"
    assert rec.last_known_state == "UNKNOWN"
    assert rec.config_snapshot == _SNAPSHOT

    assert store.get("leonardo:12345").job_id == "12345"
    assert [r.id for r in store.list()] == ["leonardo:12345"]

    # Persistence across store instances.
    assert JobsStore(_settings(tmp_path)).get("leonardo:12345").cluster == "leonardo"


def test_update_last_known_state(tmp_path: Path):
    store = JobsStore(_settings(tmp_path))
    store.create(
        cluster="leonardo",
        job_id="1",
        data_dir="/d",
        system="leonardo",
        config_name="c",
        config_snapshot={},
    )

    updated = store.update("leonardo:1", last_known_state="RUNNING")
    assert updated.last_known_state == "RUNNING"
    assert store.get("leonardo:1").last_known_state == "RUNNING"


def test_get_unknown_id_raises(tmp_path: Path):
    store = JobsStore(_settings(tmp_path))
    with pytest.raises(NotFoundError):
        store.get("nope")


def test_update_unknown_id_raises(tmp_path: Path):
    store = JobsStore(_settings(tmp_path))
    with pytest.raises(NotFoundError):
        store.update("nope", last_known_state="RUNNING")


def test_list_sorted_newest_first(tmp_path: Path):
    store = JobsStore(_settings(tmp_path))
    store.create(
        cluster="a", job_id="1", data_dir="/d", system="a", config_name="c", config_snapshot={}
    )
    store.create(
        cluster="a", job_id="2", data_dir="/d", system="a", config_name="c", config_snapshot={}
    )

    ids = [r.id for r in store.list()]
    assert ids == ["a:2", "a:1"]


def test_atomic_write_survives_partial_write(tmp_path: Path):
    settings = _settings(tmp_path)
    store = JobsStore(settings)
    store.create(
        cluster="a", job_id="1", data_dir="/d", system="a", config_name="c", config_snapshot={}
    )

    # Simulate a crash mid-write: only a stray .tmp file, real file untouched.
    tmp_leftover = settings.jobs_file.with_suffix(".json.tmp")
    tmp_leftover.write_text("{not valid json")

    assert JobsStore(settings).get("a:1").job_id == "1"
