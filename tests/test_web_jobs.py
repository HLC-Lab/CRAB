"""Phase 4: local job registry (store/jobs.py) and the /api/jobs routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import NotFoundError  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.jobs import JobsStore  # noqa: E402

_SNAPSHOT = {"global_options": {"name": "demo"}, "experiments": []}
_INFO_JSON = json.dumps(
    {
        "schema": 1,
        "crab_version": "0.1.0",
        "crab_root": "/home/u/CRAB",
        "presets": [{"name": "leonardo", "description": "Leonardo @ CINECA"}],
    }
)
_RUN_JSON = json.dumps(
    {"job_id": "42", "data_dir": "/data/leonardo/demo_job", "system": "leonardo"}
)


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


# --------------------------------------------------------------------------- #
# /api/jobs/submit (fake transport, no real SSH)
# --------------------------------------------------------------------------- #
class ScriptedTransport(Transport):
    """Returns canned `crab ... --json` output keyed by which command ran.

    `status_json`/`history_json` default to "nothing to report" shapes so
    tests that don't care about polling (e.g. submit-only tests) still work;
    override either to script the /api/jobs GET behavior.
    """

    def __init__(
        self,
        status_json: str = '{"schema":1,"jobs":[]}',
        history_json: str = '{"schema":1,"experiments":[]}',
        cancel_json: str = '{"schema":1,"job_id":"1","cancelled":true,"detail":null}',
        logs_json: str = (
            '{"schema":1,"data_dir":"/d","'
            'stdout":{"path":"/d/slurm_output.log","exists":true,"content":"out","truncated":false},'
            '"stderr":{"path":"/d/slurm_error.log","exists":false,"content":"","truncated":false}}'
        ),
    ):
        self._alive = True
        self.calls: list[str] = []
        self.written_files: dict[str, str] = {}
        self._status_json = status_json
        self._history_json = history_json
        self._cancel_json = cancel_json
        self._logs_json = logs_json

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.calls.append(command)
        if "crab info --json" in command:
            return CmdResult(0, _INFO_JSON, "")
        if "mkdir -p" in command:
            # stage_config resolves the (possibly `~`-relative) staging dir to
            # an absolute path via `cd ... && pwd` in the same command.
            return CmdResult(0, "/data/staging/.web_staging\n", "")
        if "crab run" in command:
            return CmdResult(0, _RUN_JSON, "")
        if "crab status" in command:
            return CmdResult(0, self._status_json, "")
        if "crab history" in command:
            return CmdResult(0, self._history_json, "")
        if "crab cancel" in command:
            return CmdResult(0, self._cancel_json, "")
        if "crab logs" in command:
            return CmdResult(0, self._logs_json, "")
        raise AssertionError(f"unexpected command: {command}")

    async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
        self.written_files[path] = content

    async def close(self) -> None:
        self._alive = False


def _leonardo_profile() -> dict:
    return {
        "name": "leonardo",
        "host": "login.cluster.example.org",
        "user": "researcher",
        "auth": "agent",
        "hostkey_policy": "insecure",
        "remote_crab": "~/base",
        "preset": "leonardo",
    }


def _client(tmp_path: Path, transport_factory=ScriptedTransport):
    async def connector(profile, password):
        return transport_factory()

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    return auth_client(app)


def test_submit_from_library_entry_creates_job_record(tmp_path: Path):
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")
        entry = client.post("/api/experiments", json={"name": "My Run", "config": _SNAPSHOT}).json()

        resp = client.post(
            "/api/jobs/submit",
            json={"profile_name": "leonardo", "config_id": entry["id"]},
        )

        assert resp.status_code == 201
        rec = resp.json()
        assert rec["id"] == "leonardo:42"
        assert rec["cluster"] == "leonardo"
        assert rec["job_id"] == "42"
        assert rec["data_dir"] == "/data/leonardo/demo_job"
        assert rec["system"] == "leonardo"
        assert rec["config_name"] == "My Run"
        assert rec["config_snapshot"] == _SNAPSHOT

        transport = client.app.state.manager.get("leonardo")
        assert any("crab run" in c for c in transport.calls)
        assert any(f.endswith("/my-run.json") for f in transport.written_files)


def test_submit_inline_config_requires_name(tmp_path: Path):
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post(
            "/api/jobs/submit",
            json={"profile_name": "leonardo", "config": _SNAPSHOT},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "input_error"


def test_submit_requires_config_id_or_config(tmp_path: Path):
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post("/api/jobs/submit", json={"profile_name": "leonardo"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "input_error"


def test_submit_without_connection_fails(tmp_path: Path):
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        # No connect() call — never plugged into the manager.

        resp = client.post(
            "/api/jobs/submit",
            json={"profile_name": "leonardo", "config": _SNAPSHOT, "name": "demo"},
        )
        assert resp.status_code == 502
        assert resp.json()["code"] == "connection_error"


def test_submit_no_preset_available_fails(tmp_path: Path):
    with _client(tmp_path) as client:
        profile = _leonardo_profile()
        profile["preset"] = None
        client.post("/api/remotes", json=profile)
        client.post("/api/remotes/leonardo/connect")

        resp = client.post(
            "/api/jobs/submit",
            json={"profile_name": "leonardo", "config": _SNAPSHOT, "name": "demo"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "input_error"


# --------------------------------------------------------------------------- #
# GET /api/jobs (batched status polling + sacct-purge history fallback)
# --------------------------------------------------------------------------- #
def _seed_job(tmp_path: Path, last_known_state: str | None = None, **overrides) -> None:
    store = JobsStore(_settings(tmp_path))
    fields = {
        "cluster": "leonardo",
        "job_id": "1",
        "data_dir": "/data/leonardo/demo_2026-01-01",
        "system": "leonardo",
        "config_name": "demo",
        "config_snapshot": {},
    }
    fields.update(overrides)
    rec = store.create(**fields)
    if last_known_state is not None:
        store.update(rec.id, last_known_state=last_known_state)


def test_list_jobs_batches_status_per_cluster(tmp_path: Path):
    _seed_job(tmp_path, job_id="1")
    _seed_job(tmp_path, job_id="2")
    status_json = json.dumps(
        {
            "schema": 1,
            "jobs": [
                {"job_id": "1", "state": "RUNNING", "source": "squeue"},
                {"job_id": "2", "state": "COMPLETED", "source": "sacct"},
            ],
        }
    )

    def factory():
        return ScriptedTransport(status_json=status_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        by_id = {r["job_id"]: r for r in resp.json()}
        assert by_id["1"]["last_known_state"] == "RUNNING"
        assert by_id["2"]["last_known_state"] == "COMPLETED"
        assert all(r["connected"] for r in resp.json())

        transport = client.app.state.manager.get("leonardo")
        status_calls = [c for c in transport.calls if "crab status" in c]
        assert len(status_calls) == 1
        assert "1" in status_calls[0] and "2" in status_calls[0]


def test_list_jobs_sacct_purge_resolves_via_history(tmp_path: Path):
    _seed_job(tmp_path, job_id="99", data_dir="/data/leonardo/demo_2026-01-01")
    status_json = json.dumps({"schema": 1, "jobs": [{"job_id": "99", "state": "UNKNOWN"}]})
    history_json = json.dumps(
        {
            "schema": 1,
            "experiments": [
                {"relative_path": "./demo_2026-01-01/exp_a", "status": "COMPLETED"},
                {"relative_path": "./demo_2026-01-01/exp_b", "status": "FAILED"},
                {"relative_path": "./other_job/exp_c", "status": "COMPLETED"},
            ],
        }
    )

    def factory():
        return ScriptedTransport(status_json=status_json, history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs")
        # Two rows share this job's data_dir; FAILED wins over COMPLETED (fail-safe).
        assert resp.json()[0]["last_known_state"] == "FAILED"


def test_list_jobs_sacct_purge_no_match_stays_unknown(tmp_path: Path):
    _seed_job(tmp_path, job_id="99", data_dir="/data/leonardo/demo_2026-01-01")
    status_json = json.dumps({"schema": 1, "jobs": [{"job_id": "99", "state": "UNKNOWN"}]})
    history_json = json.dumps({"schema": 1, "experiments": []})

    def factory():
        return ScriptedTransport(status_json=status_json, history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs")
        assert resp.json()[0]["last_known_state"] == "UNKNOWN"


def test_list_jobs_disconnected_cluster_returns_stale_no_error(tmp_path: Path):
    _seed_job(tmp_path, last_known_state="RUNNING")

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        # Never connected.

        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        rec = resp.json()[0]
        assert rec["connected"] is False
        assert rec["last_known_state"] == "RUNNING"  # unchanged, not guessed


def test_list_jobs_skips_already_terminal_jobs(tmp_path: Path):
    _seed_job(tmp_path, last_known_state="COMPLETED")

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs")
        assert resp.json()[0]["last_known_state"] == "COMPLETED"

        transport = client.app.state.manager.get("leonardo")
        assert not any("crab status" in c for c in transport.calls)


# --------------------------------------------------------------------------- #
# POST /api/jobs/{id}/cancel, GET /api/jobs/{id}/logs
# --------------------------------------------------------------------------- #
def test_cancel_job_updates_state(tmp_path: Path):
    _seed_job(tmp_path, job_id="1")
    cancel_json = '{"schema":1,"job_id":"1","cancelled":true,"detail":null}'

    def factory():
        return ScriptedTransport(cancel_json=cancel_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post("/api/jobs/leonardo:1/cancel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] is True
        assert body["job"]["last_known_state"] == "CANCELLED"

        transport = client.app.state.manager.get("leonardo")
        assert any("crab cancel 1 --json" in c for c in transport.calls)


def test_cancel_already_gone_job_leaves_state_unchanged(tmp_path: Path):
    _seed_job(tmp_path, job_id="1", last_known_state="RUNNING")
    cancel_json = '{"schema":1,"job_id":"1","cancelled":false,"detail":"already gone"}'

    def factory():
        return ScriptedTransport(cancel_json=cancel_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post("/api/jobs/leonardo:1/cancel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] is False
        assert body["detail"] == "already gone"
        assert body["job"]["last_known_state"] == "RUNNING"


def test_cancel_unknown_job_returns_404(tmp_path: Path):
    with _client(tmp_path) as client:
        resp = client.post("/api/jobs/nope/cancel")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_job_logs_returns_contract_shape(tmp_path: Path):
    _seed_job(tmp_path, job_id="1", data_dir="/data/leonardo/demo_2026")

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs/leonardo:1/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stdout"]["content"] == "out"
        assert body["stderr"]["exists"] is False

        transport = client.app.state.manager.get("leonardo")
        assert any(
            "crab logs --data-dir /data/leonardo/demo_2026 --json" in c for c in transport.calls
        )


def test_job_logs_unknown_job_returns_404(tmp_path: Path):
    with _client(tmp_path) as client:
        resp = client.get("/api/jobs/nope/logs")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
