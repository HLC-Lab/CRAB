"""Tests for /api/results/{cluster}/{system}/{job_basename}/... (plan 077 S5).

Job identity is off the local registry: a registry-known job resolves its
remote directory directly from the `JobsStore` record (fast path); a
CLI-only job (never submitted through this dashboard) falls back to a live
`crab history` call, matching by job_basename and reading the resolved
`absolute_path` (plan 077 S1).
"""

from __future__ import annotations

import asyncio
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
from crab.web.store.cache import LocalCache  # noqa: E402
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


# --------------------------------------------------------------------------- #
# GET /api/results — cross-cluster index (plan 077 S6)
# --------------------------------------------------------------------------- #
def _history_row(job_basename: str, status: str, system: str = SYSTEM) -> dict:
    return {
        "job_name": job_basename,
        "experiment_name": "e1",
        "status": status,
        "system": system,
        "relative_path": f"./{job_basename}/e1",
        "absolute_path": f"/remote/data/{system}/{job_basename}/e1",
    }


def _write_fetch_status(tmp_path: Path, cluster: str, system: str, job_basename: str, status: str):
    LocalCache(_settings(tmp_path)).write(
        "results_fetch_status", f"{cluster}:{system}:{job_basename}", {"status": status}
    )


def _entries_by_key(body: dict) -> dict[tuple[str, str, str], dict]:
    return {(e["cluster"], e["system"], e["job_basename"]): e for e in body["jobs"]}


def test_results_index_includes_registry_known_and_cli_only_jobs(tmp_path: Path):
    _seed_job(tmp_path)
    history = _history_json(
        [_history_row(JOB_BASENAME, "COMPLETED"), _history_row("cli_job_1", "FAILED")]
    )

    with _client(
        tmp_path, transport_factory=lambda: FakeFetchTransport(history_json=history)
    ) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/results")
        assert resp.status_code == 200
        by_key = _entries_by_key(resp.json())

        registry_entry = by_key[(CLUSTER, SYSTEM, JOB_BASENAME)]
        assert registry_entry["record_id"] is not None
        assert registry_entry["status"] == "COMPLETED"
        assert registry_entry["connected"] is True

        cli_entry = by_key[(CLUSTER, SYSTEM, "cli_job_1")]
        assert cli_entry["record_id"] is None
        assert cli_entry["status"] == "FAILED"


def test_results_index_marks_never_fetched_as_stale(tmp_path: Path):
    _seed_job(tmp_path)
    history = _history_json([_history_row(JOB_BASENAME, "COMPLETED")])

    with _client(
        tmp_path, transport_factory=lambda: FakeFetchTransport(history_json=history)
    ) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        entry = _entries_by_key(client.get("/api/results").json())[(CLUSTER, SYSTEM, JOB_BASENAME)]
        assert entry["cached"] is False
        assert entry["cached_bytes"] is None
        assert entry["possibly_stale"] is True


def test_results_index_fetched_then_unchanged_is_not_stale(tmp_path: Path):
    _seed_job(tmp_path)
    _cache_a_result_tree(tmp_path)
    _write_fetch_status(tmp_path, CLUSTER, SYSTEM, JOB_BASENAME, "COMPLETED")
    history = _history_json([_history_row(JOB_BASENAME, "COMPLETED")])

    with _client(
        tmp_path, transport_factory=lambda: FakeFetchTransport(history_json=history)
    ) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        entry = _entries_by_key(client.get("/api/results").json())[(CLUSTER, SYSTEM, JOB_BASENAME)]
        assert entry["cached"] is True
        assert entry["cached_bytes"] is not None and entry["cached_bytes"] > 0
        assert entry["possibly_stale"] is False


def test_results_index_fetched_then_status_changed_is_stale(tmp_path: Path):
    _seed_job(tmp_path)
    _cache_a_result_tree(tmp_path)
    _write_fetch_status(tmp_path, CLUSTER, SYSTEM, JOB_BASENAME, "COMPLETED")
    history = _history_json([_history_row(JOB_BASENAME, "FAILED")])

    with _client(
        tmp_path, transport_factory=lambda: FakeFetchTransport(history_json=history)
    ) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        entry = _entries_by_key(client.get("/api/results").json())[(CLUSTER, SYSTEM, JOB_BASENAME)]
        assert entry["status"] == "FAILED"
        assert entry["possibly_stale"] is True


def test_results_index_disconnected_cluster_with_prior_cache_still_lists_its_jobs(tmp_path: Path):
    # No profile ever added or connected -- purely a leftover on-disk cache.
    _cache_a_result_tree(tmp_path)

    with _client(tmp_path) as client:
        entry = _entries_by_key(client.get("/api/results").json())[(CLUSTER, SYSTEM, JOB_BASENAME)]
        assert entry["connected"] is False
        assert entry["possibly_stale"] is True
        assert entry["cached"] is True
        assert entry["status"] is None


def test_results_index_queries_clusters_in_parallel_not_sequentially(tmp_path: Path):
    """Plan 079: two connected clusters, each slow to answer `crab history`,
    must be queried concurrently -- wall time should track the SLOWEST one
    cluster, not the sum of both."""
    delay = 0.2

    class SlowHistoryTransport(FakeFetchTransport):
        async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
            if "crab history" in command:
                await asyncio.sleep(delay)
            return await super().run(command, timeout)

    async def connector(profile, password):
        return SlowHistoryTransport()

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    with auth_client(app) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")
        client.post("/api/remotes", json={**_leonardo_profile(), "name": "m100"})
        client.post("/api/remotes/m100/connect")

        start = time.monotonic()
        resp = client.get("/api/results")
        elapsed = time.monotonic() - start

    assert resp.status_code == 200
    # Sequential would take >= 2 * delay; parallel should stay well under
    # that even with test-machine scheduling slack.
    assert elapsed < delay * 1.7


def test_results_index_reraises_an_unexpected_error_instead_of_swallowing_it(tmp_path: Path):
    """A non-RemoteConnectionError failure from one cluster must still surface
    as a real error, not be silently treated like an unreachable cluster."""

    class BuggyTransport(FakeFetchTransport):
        async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
            if "crab history" in command:
                raise ValueError("boom, not a RemoteConnectionError")
            return await super().run(command, timeout)

    async def connector(profile, password):
        return BuggyTransport()

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    # raise_server_exceptions=False so the handler (not the test client) responds.
    with auth_client(app, raise_server_exceptions=False) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/results")

    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"


def test_clear_results_cache_removes_everything(tmp_path: Path):
    _cache_a_result_tree(tmp_path)

    with _client(tmp_path) as client:
        resp = client.delete("/api/results/cache")
        assert resp.status_code == 204

        assert client.get("/api/results/cache").json() == {"total_bytes": 0}
        again = client.get(f"/api/results/{CLUSTER}/{SYSTEM}/{JOB_BASENAME}")
        assert again.status_code == 404
