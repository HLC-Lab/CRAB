"""Tests for /api/results/{cluster}/{system}/{job_basename}/... (plan 077 S5).

Job identity is off the local registry: a registry-known job resolves its
remote directory directly from the `JobsStore` record (fast path); a
CLI-only job (never submitted through this dashboard) falls back to a live
`crab history` call, matching by job_basename and reading the resolved
`absolute_path` (plan 077 S1).
"""

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

CLUSTER = "leonardo"
SYSTEM = "leonardo"
JOB_BASENAME = "demo_job"

_INFO_JSON = json.dumps(
    {
        "schema": 1,
        "crab_version": "0.1.0",
        "crab_root": "/home/u/CRAB",
        "presets": [{"name": "leonardo", "description": "Leonardo @ CINECA"}],
    }
)


def _history_json(experiments: list[dict] | None = None) -> str:
    return json.dumps({"schema": 1, "experiments": experiments or []})


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


class FakeFetchTransport(Transport):
    """Only supports what connect + a results fetch need: `crab info`/`history` and fetch_tree."""

    def __init__(self, fail: Exception | None = None, history_json: str | None = None):
        self._alive = True
        self.calls: list[tuple[str, str]] = []
        self._fail = fail
        self._history_json = history_json if history_json is not None else _history_json()

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        if "crab info --json" in command:
            return CmdResult(0, _INFO_JSON, "")
        if "crab history" in command:
            return CmdResult(0, self._history_json, "")
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
        cluster=CLUSTER,
        job_id="1",
        data_dir=f"/remote/data/{JOB_BASENAME}",
        system=SYSTEM,
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


def _fetch_url(
    cluster: str = CLUSTER, system: str = SYSTEM, job_basename: str = JOB_BASENAME
) -> str:
    return f"/api/results/{cluster}/{system}/{job_basename}/fetch"


def _poll(
    client,
    fetch_id: str,
    cluster: str = CLUSTER,
    system: str = SYSTEM,
    job_basename: str = JOB_BASENAME,
) -> dict:
    url = f"/api/results/{cluster}/{system}/{job_basename}/fetch/{fetch_id}"
    status = None
    for _ in range(50):
        status = client.get(url).json()
        if status["status"] != "pending":
            break
        time.sleep(0.01)
    assert status is not None
    return status


def test_fetch_results_returns_202_and_eventually_done(tmp_path: Path):
    _seed_job(tmp_path)

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post(_fetch_url())
        assert resp.status_code == 202
        fetch_id = resp.json()["fetch_id"]

        status = _poll(client, fetch_id)
        assert status["status"] == "done"

        transport = client.app.state.manager.get("leonardo")
        expected_local_dir = str(
            ResultsCache(_settings(tmp_path)).path_for(CLUSTER, SYSTEM, JOB_BASENAME)
        )
        assert transport.calls == [(f"/remote/data/{JOB_BASENAME}", expected_local_dir)]

        # Cleaned up once a terminal status has been fetched.
        again = client.get(f"/api/results/{CLUSTER}/{SYSTEM}/{JOB_BASENAME}/fetch/{fetch_id}")
        assert again.status_code == 404


def test_fetch_results_resolves_cli_only_job_via_history_absolute_path(tmp_path: Path):
    # No JobsStore record at all -- this job was only ever run by hand on the cluster.
    cli_job_basename = "cli_job_1"
    history = _history_json(
        [
            {
                "job_name": "cli_job_1",
                "experiment_name": "e1",
                "status": "COMPLETED",
                "relative_path": f"./{cli_job_basename}/e1",
                "absolute_path": f"/remote/data/{SYSTEM}/{cli_job_basename}/e1",
            }
        ]
    )

    def factory():
        return FakeFetchTransport(history_json=history)

    with _client(tmp_path, transport_factory=factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post(_fetch_url(job_basename=cli_job_basename))
        assert resp.status_code == 202
        fetch_id = resp.json()["fetch_id"]

        status = _poll(client, fetch_id, job_basename=cli_job_basename)
        assert status["status"] == "done"

        transport = client.app.state.manager.get("leonardo")
        expected_local_dir = str(
            ResultsCache(_settings(tmp_path)).path_for(CLUSTER, SYSTEM, cli_job_basename)
        )
        # The remote dir is the resolved absolute_path's parent (the job dir,
        # sibling of each experiment dir), not the experiment dir itself.
        assert transport.calls == [
            (f"/remote/data/{SYSTEM}/{cli_job_basename}", expected_local_dir)
        ]


def test_fetch_results_error_preserves_message_and_detail(tmp_path: Path):
    _seed_job(tmp_path)

    def failing_factory():
        return FakeFetchTransport(
            fail=RemoteCommandError("Could not fetch results.", detail="boom")
        )

    with _client(tmp_path, transport_factory=failing_factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post(_fetch_url())
        fetch_id = resp.json()["fetch_id"]

        status = _poll(client, fetch_id)
        assert status["status"] == "error"
        assert status["message"] == "Could not fetch results."
        assert status["detail"] == "boom"


def test_fetch_results_unknown_job_404(tmp_path: Path):
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        # No JobsStore record, and the (empty) live history has no matching row either.
        resp = client.post(_fetch_url(job_basename="does-not-exist"))
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_get_fetch_status_unknown_fetch_id_404(tmp_path: Path):
    _seed_job(tmp_path)
    with _client(tmp_path) as client:
        resp = client.get(f"/api/results/{CLUSTER}/{SYSTEM}/{JOB_BASENAME}/fetch/nope")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_fetch_results_without_connection_fails(tmp_path: Path):
    _seed_job(tmp_path)
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        # No connect() call — never plugged into the manager.

        resp = client.post(_fetch_url())
        assert resp.status_code == 502
        assert resp.json()["code"] == "connection_error"


# --------------------------------------------------------------------------- #
# GET /api/results/{cluster}/{system}/{job_basename}, GET/DELETE /api/results/cache
# --------------------------------------------------------------------------- #
def _cache_a_result_tree(tmp_path: Path) -> Path:
    """Simulate a completed fetch by writing CSVs straight into the cache path
    (the fetch itself is covered above; this seams past it to test the get/cache routes)."""
    job_dir = ResultsCache(_settings(tmp_path)).path_for(CLUSTER, SYSTEM, JOB_BASENAME)
    job_dir.mkdir(parents=True)
    (job_dir / "data_app_0.csv").write_text("x\n1\n", encoding="utf-8")
    return job_dir


def test_get_results_404_when_never_fetched(tmp_path: Path):
    with _client(tmp_path) as client:
        resp = client.get(f"/api/results/{CLUSTER}/{SYSTEM}/{JOB_BASENAME}")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_get_results_returns_parsed_data_after_a_fetch(tmp_path: Path):
    _cache_a_result_tree(tmp_path)

    with _client(tmp_path) as client:
        resp = client.get(f"/api/results/{CLUSTER}/{SYSTEM}/{JOB_BASENAME}")
        assert resp.status_code == 200
        assert resp.json() == {"experiments": {"Root": {"App 0": [{"x": 1}]}}}


def test_results_cache_size_reflects_cached_bytes(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.get("/api/results/cache").json() == {"total_bytes": 0}

        _cache_a_result_tree(tmp_path)

        size = client.get("/api/results/cache").json()["total_bytes"]
        assert size > 0


def test_clear_results_cache_removes_everything(tmp_path: Path):
    _cache_a_result_tree(tmp_path)

    with _client(tmp_path) as client:
        resp = client.delete("/api/results/cache")
        assert resp.status_code == 204

        assert client.get("/api/results/cache").json() == {"total_bytes": 0}
        again = client.get(f"/api/results/{CLUSTER}/{SYSTEM}/{JOB_BASENAME}")
        assert again.status_code == 404
