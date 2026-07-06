"""Phase 4: local job registry (store/jobs.py) and the /api/jobs routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.api.jobs import _run_submission  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import NotFoundError  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.jobs import JobsStore  # noqa: E402
from crab.web.store.profiles import ProfileStore  # noqa: E402

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
    assert rec.rerun_of is None
    assert rec.rerun_experiments is None

    assert store.get("leonardo:12345").job_id == "12345"
    assert [r.id for r in store.list()] == ["leonardo:12345"]

    # Persistence across store instances.
    assert JobsStore(_settings(tmp_path)).get("leonardo:12345").cluster == "leonardo"


def test_store_create_records_rerun_lineage(tmp_path: Path):
    store = JobsStore(_settings(tmp_path))
    rec = store.create(
        cluster="leonardo",
        job_id="2",
        data_dir="/d",
        system="leonardo",
        config_name="demo",
        config_snapshot=_SNAPSHOT,
        rerun_of="leonardo:1",
        rerun_experiments=["01_baseline"],
    )
    assert rec.rerun_of == "leonardo:1"
    assert rec.rerun_experiments == ["01_baseline"]
    assert JobsStore(_settings(tmp_path)).get("leonardo:2").rerun_of == "leonardo:1"


def test_store_loads_an_old_record_missing_rerun_fields(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    settings.jobs_file.write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "leonardo:1",
                        "cluster": "leonardo",
                        "job_id": "1",
                        "data_dir": "/d",
                        "system": "leonardo",
                        "config_name": "demo",
                        "config_snapshot": {},
                        "submitted_at": "2026-01-01T00:00:00+00:00",
                        "last_known_state": "COMPLETED",
                    }
                ],
            }
        )
    )
    rec = JobsStore(settings).get("leonardo:1")
    assert rec.rerun_of is None
    assert rec.rerun_experiments is None


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
        experiment_logs_json: str = '{"schema":1,"data_dir":"/d/e1","files":[]}',
        experiment_logs_rc: int = 0,
    ):
        self._alive = True
        self.calls: list[str] = []
        self.written_files: dict[str, str] = {}
        self._status_json = status_json
        self._history_json = history_json
        self._cancel_json = cancel_json
        self._logs_json = logs_json
        self._experiment_logs_json = experiment_logs_json
        self._experiment_logs_rc = experiment_logs_rc

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
        if "crab logs" in command and "--experiment" in command:
            return CmdResult(self._experiment_logs_rc, self._experiment_logs_json, "boom")
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


def _alps_profile() -> dict:
    return {
        "name": "alps",
        "host": "login.alps.example.org",
        "user": "researcher",
        "auth": "agent",
        "hostkey_policy": "insecure",
        "remote_crab": "~/base",
        "preset": "alps",
    }


def _client(tmp_path: Path, transport_factory=ScriptedTransport):
    async def connector(profile, password):
        return transport_factory()

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    return auth_client(app)


def _profile(tmp_path: Path):
    """A persisted profile, read back as a real `Profile` object for `_run_submission`."""
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
    return ProfileStore(_settings(tmp_path)).get("leonardo")


# _run_submission is the actual staging/run work, run off the request cycle
# by /api/jobs/submit's background task — tested directly here (fake
# transport, no HTTP) since observing it through TestClient would mean racing
# a real asyncio.Task against the test's own assertions (see the route-level
# tests below for what IS safe to assert through HTTP: the 202 shape and the
# validation failures that happen before the task is even created).
async def test_run_submission_success_creates_a_job_record(tmp_path: Path):
    profile = _profile(tmp_path)
    transport = ScriptedTransport()
    tracker: dict = {}

    await _run_submission(
        tracker,
        "sub-1",
        transport,
        profile,
        _SNAPSHOT,
        "My Run",
        "leonardo",
        None,
        _settings(tmp_path),
    )

    assert tracker["sub-1"]["status"] == "done"
    rec = tracker["sub-1"]["record"]
    assert rec.id == "leonardo:42"
    assert rec.cluster == "leonardo"
    assert rec.job_id == "42"
    assert rec.data_dir == "/data/leonardo/demo_job"
    assert rec.system == "leonardo"
    assert rec.config_name == "My Run"
    assert rec.config_snapshot == _SNAPSHOT
    assert any("crab run" in c for c in transport.calls)
    assert any(f.endswith("/my-run.json") for f in transport.written_files)


async def test_run_submission_with_only_passes_the_flag_to_crab_run(tmp_path: Path):
    """`only` (plan 060 rerun) must reach the remote `crab run --only ...` invocation."""
    profile = _profile(tmp_path)
    transport = ScriptedTransport()
    tracker: dict = {}

    await _run_submission(
        tracker,
        "sub-1",
        transport,
        profile,
        _SNAPSHOT,
        "My Run",
        "leonardo",
        ["ex1", "ex3"],
        _settings(tmp_path),
    )

    run_calls = [c for c in transport.calls if "crab run" in c]
    assert len(run_calls) == 1
    assert "--only ex1,ex3" in run_calls[0]


async def test_run_submission_without_only_has_no_only_flag(tmp_path: Path):
    profile = _profile(tmp_path)
    transport = ScriptedTransport()
    tracker: dict = {}

    await _run_submission(
        tracker,
        "sub-1",
        transport,
        profile,
        _SNAPSHOT,
        "My Run",
        "leonardo",
        None,
        _settings(tmp_path),
    )

    run_calls = [c for c in transport.calls if "crab run" in c]
    assert "--only" not in run_calls[0]


async def test_run_submission_failure_preserves_message_and_detail(tmp_path: Path):
    """A failed `crab run` must keep its stderr/traceback `detail` (commit 0d17605's fix),
    not just the top-line message, once it travels through the tracker instead of an HTTP
    error response."""
    profile = _profile(tmp_path)

    class FailingTransport(ScriptedTransport):
        async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
            if "crab run" in command:
                return CmdResult(1, "", "TypeError: unsupported operand type(s)")
            return await super().run(command, timeout=timeout)

    transport = FailingTransport()
    tracker: dict = {}

    await _run_submission(
        tracker,
        "sub-1",
        transport,
        profile,
        _SNAPSHOT,
        "My Run",
        "leonardo",
        None,
        _settings(tmp_path),
    )

    entry = tracker["sub-1"]
    assert entry["status"] == "error"
    assert "failed on the cluster" in entry["message"]
    assert entry["detail"] == "TypeError: unsupported operand type(s)"


def test_submit_returns_202_with_a_pending_entry_before_any_work_runs(tmp_path: Path, monkeypatch):
    """The response must not wait on the SSH round-trip — proven by replacing the
    background task with a no-op rather than racing a real one (see the module docstring
    above `_run_submission`'s tests for why timing-based assertions aren't used here)."""
    import crab.web.api.jobs as jobs_api

    class _DummyTask:
        def add_done_callback(self, cb):
            pass

    def fake_create_task(coro):
        coro.close()  # never actually run the staging/run work
        return _DummyTask()

    monkeypatch.setattr(jobs_api.asyncio, "create_task", fake_create_task)

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")
        entry = client.post("/api/experiments", json={"name": "My Run", "config": _SNAPSHOT}).json()

        resp = client.post(
            "/api/jobs/submit", json={"profile_name": "leonardo", "config_id": entry["id"]}
        )

        assert resp.status_code == 202
        submission_id = resp.json()["submission_id"]
        assert client.app.state.submissions[submission_id] == {"status": "pending"}

        transport = client.app.state.manager.get("leonardo")
        assert not any("crab run" in c for c in transport.calls)


def test_submit_end_to_end_resolves_via_the_submissions_endpoint(tmp_path: Path):
    """Wiring proof: a real submit, polled through the public endpoint to its real
    completion (bounded retries for the background task to finish — not a race, since
    we only assert on the eventual terminal state, never on "still pending")."""
    import time

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")
        entry = client.post("/api/experiments", json={"name": "My Run", "config": _SNAPSHOT}).json()

        resp = client.post(
            "/api/jobs/submit", json={"profile_name": "leonardo", "config_id": entry["id"]}
        )
        assert resp.status_code == 202
        submission_id = resp.json()["submission_id"]

        status = None
        for _ in range(50):
            status = client.get(f"/api/jobs/submissions/{submission_id}").json()
            if status["status"] != "pending":
                break
            time.sleep(0.01)

        assert status is not None and status["status"] == "done"
        assert status["record"]["config_name"] == "My Run"

        # Cleaned up once a terminal status has been fetched.
        again = client.get(f"/api/jobs/submissions/{submission_id}")
        assert again.status_code == 404


def test_get_submission_unknown_id_returns_404(tmp_path: Path):
    with _client(tmp_path) as client:
        resp = client.get("/api/jobs/submissions/nope")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


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


def test_list_jobs_completed_with_failed_experiment_downgrades_to_failed(tmp_path: Path):
    """A Slurm job can exit 0 (COMPLETED) while one experiment inside it fails
    (engine.py's `_run_worker` logs and continues rather than aborting the
    allocation) — the worst-first history cross-check must catch this the
    first time squeue reports COMPLETED, same as it already does for a
    purged/UNKNOWN job."""
    _seed_job(tmp_path, job_id="1", data_dir="/data/leonardo/demo_2026-01-01")
    status_json = json.dumps({"schema": 1, "jobs": [{"job_id": "1", "state": "COMPLETED"}]})
    history_json = json.dumps(
        {
            "schema": 1,
            "experiments": [
                {"relative_path": "./demo_2026-01-01/exp_a", "status": "COMPLETED"},
                {"relative_path": "./demo_2026-01-01/exp_b", "status": "FAILED"},
            ],
        }
    )

    def factory():
        return ScriptedTransport(status_json=status_json, history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs")
        assert resp.json()[0]["last_known_state"] == "FAILED"

        transport = client.app.state.manager.get("leonardo")
        assert any("crab history" in c for c in transport.calls)


def test_list_jobs_completed_with_no_history_match_stays_completed(tmp_path: Path):
    """No matching history rows (e.g. metadata not written yet) must not
    downgrade a clean COMPLETED to anything else — never guess."""
    _seed_job(tmp_path, job_id="1", data_dir="/data/leonardo/demo_2026-01-01")
    status_json = json.dumps({"schema": 1, "jobs": [{"job_id": "1", "state": "COMPLETED"}]})
    history_json = json.dumps({"schema": 1, "experiments": []})

    def factory():
        return ScriptedTransport(status_json=status_json, history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs")
        assert resp.json()[0]["last_known_state"] == "COMPLETED"


def test_list_jobs_already_completed_job_is_never_repolled(tmp_path: Path):
    """Once stored as a terminal state, a job drops out of active polling
    entirely — the history cross-check for COMPLETED only ever runs once,
    at the moment of the transition, not on every subsequent poll."""
    _seed_job(
        tmp_path,
        job_id="1",
        data_dir="/data/leonardo/demo_2026-01-01",
        last_known_state="COMPLETED",
    )

    def factory():
        return ScriptedTransport()

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs")
        assert resp.json()[0]["last_known_state"] == "COMPLETED"

        transport = client.app.state.manager.get("leonardo")
        assert not any("crab status" in c or "crab history" in c for c in transport.calls)


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


def test_job_logs_with_experiment_returns_per_app_files(tmp_path: Path):
    _seed_job(tmp_path, job_id="1", data_dir="/data/leonardo/demo_2026")

    def transport_factory():
        return ScriptedTransport(
            experiment_logs_json=(
                '{"schema":1,"data_dir":"/data/leonardo/demo_2026/01_exp","files":['
                '{"app_id":"0","path":"/x/error_app_0.log","exists":true,'
                '"content":"boom","truncated":false}]}'
            )
        )

    with _client(tmp_path, transport_factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs/leonardo:1/logs", params={"experiment": "01_exp"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["files"] == [
            {
                "app_id": "0",
                "path": "/x/error_app_0.log",
                "exists": True,
                "content": "boom",
                "truncated": False,
            }
        ]

        transport = client.app.state.manager.get("leonardo")
        assert any(
            "crab logs --data-dir /data/leonardo/demo_2026 --experiment 01_exp --json" in c
            for c in transport.calls
        )


def test_job_logs_with_unknown_experiment_surfaces_remote_error(tmp_path: Path):
    _seed_job(tmp_path, job_id="1", data_dir="/data/leonardo/demo_2026")

    def transport_factory():
        return ScriptedTransport(experiment_logs_rc=1)

    with _client(tmp_path, transport_factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs/leonardo:1/logs", params={"experiment": "ghost"})
        assert resp.status_code >= 400
        assert resp.json()["code"] == "remote_command_error"


def test_job_logs_live_success_caches_and_marks_fresh(tmp_path: Path):
    _seed_job(tmp_path, job_id="1", data_dir="/data/leonardo/demo_2026")

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs/leonardo:1/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is False
        assert body["cached_at"] is None


def test_job_logs_disconnected_with_prior_cache_returns_stale_copy(tmp_path: Path):
    _seed_job(tmp_path, job_id="1", data_dir="/data/leonardo/demo_2026")

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")
        first = client.get("/api/jobs/leonardo:1/logs").json()

        client.post("/api/remotes/leonardo/disconnect")
        resp = client.get("/api/jobs/leonardo:1/logs")

        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        assert body["cached_at"] is not None
        assert body["stdout"]["content"] == first["stdout"]["content"]


def test_job_logs_disconnected_with_no_prior_cache_still_errors(tmp_path: Path):
    _seed_job(tmp_path, job_id="1", data_dir="/data/leonardo/demo_2026")

    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        # Never connected, never fetched: nothing to fall back to.

        resp = client.get("/api/jobs/leonardo:1/logs")
        assert resp.status_code >= 400
        assert resp.json()["code"] == "connection_error"


# --------------------------------------------------------------------------- #
# /api/jobs/{record_id}/experiments (per-job detail view, plan 075)
# --------------------------------------------------------------------------- #
def test_job_experiments_returns_only_rows_for_this_exact_submission(tmp_path: Path):
    _seed_job(
        tmp_path,
        job_id="48552582",
        data_dir="/leonardo/data/msgsize_scaling_study_2026-07-04_20-03-37-168111",
        config_name="msgsize_scaling_study",
        system="leonardo",
    )
    history_json = json.dumps(
        {
            "schema": 1,
            "experiments": [
                _history_row(),  # belongs to this job's data_dir
                _history_row(
                    experiment_name="02_unrelated",
                    relative_path="./some_other_job_dir/02_unrelated",
                ),  # same config_name, different submission: excluded
            ],
        }
    )

    def factory():
        return ScriptedTransport(history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs/leonardo:48552582/experiments")
        assert resp.status_code == 200
        body = resp.json()

        assert body["record_id"] == "leonardo:48552582"
        assert body["config_name"] == "msgsize_scaling_study"
        assert body["job_id"] == "48552582"
        assert body["stale"] is False
        assert [e["experiment_name"] for e in body["experiments"]] == ["01_baseline"]

        transport = client.app.state.manager.get("leonardo")
        assert any("crab history -s leonardo --json" in c for c in transport.calls)


def test_job_experiments_unknown_record_returns_404(tmp_path: Path):
    with _client(tmp_path) as client:
        resp = client.get("/api/jobs/nope/experiments")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_job_experiments_disconnected_with_prior_cache_returns_stale_copy(tmp_path: Path):
    _seed_job(
        tmp_path,
        job_id="1",
        data_dir="/leonardo/data/msgsize_scaling_study_2026-07-04_20-03-37-168111",
        system="leonardo",
    )
    history_json = json.dumps({"schema": 1, "experiments": [_history_row()]})

    def factory():
        return ScriptedTransport(history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")
        first = client.get("/api/jobs/leonardo:1/experiments").json()

        client.post("/api/remotes/leonardo/disconnect")
        resp = client.get("/api/jobs/leonardo:1/experiments")

        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        assert body["cached_at"] is not None
        assert body["experiments"] == first["experiments"]


# --------------------------------------------------------------------------- #
# /api/jobs/report/{config_name} (per-use-case experiment report, plan 060)
# --------------------------------------------------------------------------- #
def _history_row(**overrides) -> dict:
    row = {
        "job_name": "msgsize_scaling_study",
        "experiment_name": "01_baseline",
        "timestamp": "2026-07-04_20-03-37",
        "numnodes": "4",
        "ppn": "2",
        "apps_list": "netgauge",
        "status": "COMPLETED",
        "tags": "",
        "relative_path": "./msgsize_scaling_study_2026-07-04_20-03-37-168111/01_baseline",
        "system": "leonardo",
    }
    row.update(overrides)
    return row


def test_use_case_report_filters_by_config_name_and_joins_registry(tmp_path: Path):
    # This job's data_dir basename matches the seeded history row's relative_path
    # prefix, so the report should attach record_id/job_id/submitted_at to it.
    _seed_job(
        tmp_path,
        job_id="48552582",
        data_dir="/leonardo/data/msgsize_scaling_study_2026-07-04_20-03-37-168111",
        config_name="msgsize_scaling_study",
    )
    history_json = json.dumps(
        {
            "schema": 1,
            "experiments": [
                _history_row(),  # matches config_name + a known job record
                _history_row(
                    experiment_name="02_unmatched",
                    status="FAILED",
                    relative_path="./some_other_job_dir/02_unmatched",
                ),  # matches config_name, no registry entry
                _history_row(
                    job_name="a_different_config",
                    relative_path="./a_different_config_2026-07-01_00-00-00-000000/01_baseline",
                ),  # unrelated job dir, no registry entry, job_name mismatch: filtered out
            ],
        }
    )

    def factory():
        return ScriptedTransport(history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get("/api/jobs/report/msgsize_scaling_study")
        assert resp.status_code == 200
        body = resp.json()

        assert body["config_name"] == "msgsize_scaling_study"
        assert body["clusters_skipped"] == []
        assert len(body["experiments"]) == 2  # the "a_different_config" row is excluded
        by_name = {e["experiment_name"]: e for e in body["experiments"]}

        matched = by_name["01_baseline"]
        assert matched["cluster"] == "leonardo"
        assert matched["record_id"] == "leonardo:48552582"
        assert matched["job_id"] == "48552582"

        unmatched = by_name["02_unmatched"]
        assert unmatched["status"] == "FAILED"
        assert unmatched["record_id"] is None
        assert unmatched["job_id"] is None


def test_use_case_report_matches_registry_config_name_over_internal_job_name(tmp_path: Path):
    # Real-world case: a config authored with an internal `global_options.name`
    # of "msgsize_scaling_study" but saved in the library under a human display
    # name. The registry record carries the display name; `crab history`'s
    # `job_name` column carries the internal name. The report must key off the
    # display name (what the report route/URL and the Jobs view use), joining
    # through the registry, not off the internal `job_name` string.
    _seed_job(
        tmp_path,
        job_id="1",
        data_dir="/leonardo/data/msgsize_scaling_study_2026-07-04_20-03-37-168111",
        config_name="Message-size scaling study: 1 KiB to 4 MiB across 16 points",
    )
    history_json = json.dumps({"schema": 1, "experiments": [_history_row()]})

    def factory():
        return ScriptedTransport(history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")

        resp = client.get(
            "/api/jobs/report/Message-size%20scaling%20study%3A%201%20KiB%20to%204%20MiB%20across%2016%20points"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["experiments"]) == 1
        assert body["experiments"][0]["record_id"] == "leonardo:1"

        # And a lookup under the *internal* name (job_name) must NOT match this
        # row, since it belongs to a different display-named use case.
        resp2 = client.get("/api/jobs/report/msgsize_scaling_study")
        assert resp2.json()["experiments"] == []


def test_use_case_report_disconnected_cluster_is_listed_as_skipped(tmp_path: Path):
    with _client(tmp_path) as client:
        client.post("/api/remotes", json=_leonardo_profile())  # registered, never connected

        resp = client.get("/api/jobs/report/anything")
        assert resp.status_code == 200
        body = resp.json()
        assert body["experiments"] == []
        assert body["clusters_skipped"] == ["leonardo"]
        assert body["clusters_stale"] == []


def test_use_case_report_disconnected_cluster_with_prior_cache_is_stale_not_skipped(
    tmp_path: Path,
):
    history_json = json.dumps({"schema": 1, "experiments": [_history_row()]})

    def factory():
        return ScriptedTransport(history_json=history_json)

    with _client(tmp_path, factory) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")
        first = client.get("/api/jobs/report/msgsize_scaling_study").json()

        client.post("/api/remotes/leonardo/disconnect")
        resp = client.get("/api/jobs/report/msgsize_scaling_study")

        assert resp.status_code == 200
        body = resp.json()
        assert body["experiments"] == first["experiments"]
        assert body["clusters_skipped"] == []
        assert [s["cluster"] for s in body["clusters_stale"]] == ["leonardo"]
        assert body["clusters_stale"][0]["cached_at"] is not None


def test_use_case_report_spans_multiple_connected_clusters(tmp_path: Path):
    leonardo_history = json.dumps({"schema": 1, "experiments": [_history_row(system="leonardo")]})
    alps_history = json.dumps(
        {
            "schema": 1,
            "experiments": [_history_row(system="alps", status="TIMEOUT")],
        }
    )

    async def connector(profile, password):
        return ScriptedTransport(
            history_json=leonardo_history if profile.name == "leonardo" else alps_history
        )

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    with auth_client(app) as client:
        client.post("/api/remotes", json=_leonardo_profile())
        client.post("/api/remotes/leonardo/connect")
        client.post("/api/remotes", json=_alps_profile())
        client.post("/api/remotes/alps/connect")

        resp = client.get("/api/jobs/report/msgsize_scaling_study")
        assert resp.status_code == 200
        body = resp.json()
        assert body["clusters_skipped"] == []
        clusters = sorted(e["cluster"] for e in body["experiments"])
        assert clusters == ["alps", "leonardo"]
