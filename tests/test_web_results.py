"""Tests for /api/jobs/{record_id}/results/fetch — async CSV tree fetch (plan 065 S4)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import RemoteCommandError  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.jobs import JobsStore  # noqa: E402
from crab.web.store.results_cache import ResultsCache  # noqa: E402

_INFO_JSON = json.dumps(
    {
        "schema": 1,
        "crab_version": "0.1.0",
        "crab_root": "/home/u/CRAB",
        "presets": [{"name": "leonardo", "description": "Leonardo @ CINECA"}],
    }
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


class FakeFetchTransport(Transport):
    """Only supports what connect + a results fetch need: `crab info --json` and fetch_tree."""

    def __init__(self, fail: Exception | None = None):
        self._alive = True
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        if "crab info --json" in command:
            return CmdResult(0, _INFO_JSON, "")
        raise AssertionError(f"unexpected command: {command}")

    async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
        raise AssertionError("results fetch should never call write_file()")

    async def fetch_tree(
        self, remote_dir: str, local_dir: str, timeout: float | None = 30.0
    ) -> None:
        self.calls.append((remote_dir, local_dir))
        if self._fail is not None:
            raise self._fail

    async def close(self) -> None:
        self._alive = False


def _client(tmp_path: Path, transport_factory=FakeFetchTransport):
    async def connector(profile, password):
        return transport_factory()

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    return auth_client(app)


def _seed_job(tmp_path: Path) -> str:
    store = JobsStore(_settings(tmp_path))
    rec = store.create(
        cluster="leonardo",
        job_id="1",
        data_dir="/remote/data/demo_job",
        system="leonardo",
        config_name="demo",
        config_snapshot={},
    )
    return rec.id


def _leonardo_profile() -> dict:
    return {
        "name": "leonardo",
        "host": "login.cluster.example.org",
        "user": "researcher",
        "auth": "agent",
        "hostkey_policy": "insecure",
        "remote_crab": "~/base",
    }


def _poll(client, record_id: str, fetch_id: str) -> dict:
    status = None
    for _ in range(50):
        status = client.get(f"/api/jobs/{record_id}/results/fetch/{fetch_id}").json()
        if status["status"] != "pending":
            break
        time.sleep(0.01)
    assert status is not None
    return status


def test_fetch_results_returns_202_and_eventually_done(tmp_path: Path):
    record_id = _seed_job(tmp_path)

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post(f"/api/jobs/{record_id}/results/fetch")
        assert resp.status_code == 202
        fetch_id = resp.json()["fetch_id"]

        status = _poll(client, record_id, fetch_id)
        assert status["status"] == "done"

        transport = client.app.state.manager.get("leonardo")
        expected_local_dir = str(ResultsCache(_settings(tmp_path)).path_for("leonardo", "demo_job"))
        assert transport.calls == [("/remote/data/demo_job", expected_local_dir)]

        # Cleaned up once a terminal status has been fetched.
        again = client.get(f"/api/jobs/{record_id}/results/fetch/{fetch_id}")
        assert again.status_code == 404


def test_fetch_results_error_preserves_message_and_detail(tmp_path: Path):
    record_id = _seed_job(tmp_path)

    def failing_factory():
        return FakeFetchTransport(
            fail=RemoteCommandError("Could not fetch results.", detail="boom")
        )

    with _client(tmp_path, transport_factory=failing_factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post(f"/api/jobs/{record_id}/results/fetch")
        fetch_id = resp.json()["fetch_id"]

        status = _poll(client, record_id, fetch_id)
        assert status["status"] == "error"
        assert status["message"] == "Could not fetch results."
        assert status["detail"] == "boom"


def test_fetch_results_unknown_record_id_404(tmp_path: Path):
    with _client(tmp_path) as client:
        resp = client.post("/api/jobs/does-not-exist/results/fetch")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_get_fetch_status_unknown_fetch_id_404(tmp_path: Path):
    record_id = _seed_job(tmp_path)
    with _client(tmp_path) as client:
        resp = client.get(f"/api/jobs/{record_id}/results/fetch/nope")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_fetch_results_without_connection_fails(tmp_path: Path):
    record_id = _seed_job(tmp_path)
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        # No connect() call — never plugged into the manager.

        resp = client.post(f"/api/jobs/{record_id}/results/fetch")
        assert resp.status_code == 502
        assert resp.json()["code"] == "connection_error"


# --------------------------------------------------------------------------- #
# GET /api/jobs/{record_id}/results, GET/DELETE /api/jobs/results/cache
# --------------------------------------------------------------------------- #
def _cache_a_result_tree(tmp_path: Path, record_id: str) -> Path:
    """Simulate a completed fetch by writing CSVs straight into the cache path
    (the fetch itself, S4, is covered above; this seams past it to test S5)."""
    rec = JobsStore(_settings(tmp_path)).get(record_id)
    job_dir = ResultsCache(_settings(tmp_path)).path_for(rec.cluster, Path(rec.data_dir).name)
    job_dir.mkdir(parents=True)
    (job_dir / "data_app_0.csv").write_text("x\n1\n", encoding="utf-8")
    return job_dir


def test_get_results_404_when_never_fetched(tmp_path: Path):
    record_id = _seed_job(tmp_path)
    with _client(tmp_path) as client:
        resp = client.get(f"/api/jobs/{record_id}/results")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_get_results_returns_parsed_data_after_a_fetch(tmp_path: Path):
    record_id = _seed_job(tmp_path)
    _cache_a_result_tree(tmp_path, record_id)

    with _client(tmp_path) as client:
        resp = client.get(f"/api/jobs/{record_id}/results")
        assert resp.status_code == 200
        assert resp.json() == {"experiments": {"Root": {"App 0": [{"x": 1}]}}}


def test_results_cache_size_reflects_cached_bytes(tmp_path: Path):
    record_id = _seed_job(tmp_path)

    with _client(tmp_path) as client:
        assert client.get("/api/jobs/results/cache").json() == {"total_bytes": 0}

        _cache_a_result_tree(tmp_path, record_id)

        size = client.get("/api/jobs/results/cache").json()["total_bytes"]
        assert size > 0


def test_clear_results_cache_removes_everything(tmp_path: Path):
    record_id = _seed_job(tmp_path)
    _cache_a_result_tree(tmp_path, record_id)

    with _client(tmp_path) as client:
        resp = client.delete("/api/jobs/results/cache")
        assert resp.status_code == 204

        assert client.get("/api/jobs/results/cache").json() == {"total_bytes": 0}
        again = client.get(f"/api/jobs/{record_id}/results")
        assert again.status_code == 404
