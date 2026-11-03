"""``/api/jobs``: monitor, cancel and read logs of the jobs the dashboard submitted
(the submit routes live in `api/jobs_submit.py`, the reports in `api/jobs_report.py`).

The local job registry (``store/jobs.py``) only records what the dashboard
itself submitted (cluster, Slurm job id, data_dir, a config snapshot); live
state always comes from a fresh ``crab status``/``crab logs --json`` call:
this module never assumes a job's state without asking the cluster.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import (
    CrabWebError,
    NotFoundError,
    RemoteCommandError,
    RemoteConnectionError,
)
from crab.web.remoteops.crab_cli import RemotePath, run_crab_json
from crab.web.store.cache import LocalCache
from crab.web.store.jobs import JobRecord, JobsStore
from crab.web.store.profiles import Profile, ProfileStore
from crab.web.trackers import ExpiringTracker

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# States that will never change again: jobs in one of these are never
# re-polled. Anything else (including our own "UNKNOWN") is treated as active.
# Slurm's terminal states plus the local scheduler's LOST (supervisor gone
# without a final state).
_TERMINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "PREEMPTED",
    "BOOT_FAIL",
    "DEADLINE",
    "REVOKED",
    "LOST",
}
# Worst-first: a purged job's data_dir can hold several experiments' metadata.csv
# rows (one crab run submission runs many); the job resolves to the worst one.
_HISTORY_STATUS_PRIORITY = ("FAILED", "TIMEOUT", "COMPLETED")


def _jobs_store(request: Request) -> JobsStore:
    return JobsStore(request.app.state.settings)


def _cache(request: Request) -> LocalCache:
    return LocalCache(request.app.state.settings)


def _submissions(request: Request) -> ExpiringTracker:
    return request.app.state.submissions


def _cached_within(request: Request, scope: str, key: str, ttl_seconds: float) -> dict | None:
    """The cached data for `(scope, key)` if fetched at most `ttl_seconds` ago, else None."""
    cached = _cache(request).read(scope, key)
    if cached is None:
        return None
    age = (
        datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])
    ).total_seconds()
    return cached["data"] if age <= ttl_seconds else None


async def _live_or_cached(
    request: Request,
    scope: str,
    key: str,
    fetch: Callable[[], Awaitable[dict]],
    ttl_seconds: float = 0,
) -> tuple[dict, bool, str | None]:
    """Try `fetch()` live; on disconnect/command failure, fall back to the local cache.

    `ttl_seconds` (default 0, meaning always live) lets a caller reuse a very
    recent live result instead of paying a second SSH round-trip: e.g. the
    Results picker and a job's own experiment-status query both read the same
    cluster's `crab history` moments apart. A hit within `ttl_seconds` is
    returned as fresh (`stale=False`), not as the disconnected-fallback case.

    Returns ``(data, stale, cached_at)``. A cache miss re-raises the live error
    unchanged (no regression for data that was never successfully fetched before).
    """
    cache = _cache(request)
    if ttl_seconds > 0:
        recent = _cached_within(request, scope, key, ttl_seconds)
        if recent is not None:
            return recent, False, None
    try:
        result = await fetch()
    except (RemoteConnectionError, RemoteCommandError):
        cached = cache.read(scope, key)
        if cached is None:
            raise
        return cached["data"], True, cached["fetched_at"]
    cache.write(scope, key, result)
    return result, False, None


def _profiles(request: Request) -> ProfileStore:
    return ProfileStore(request.app.state.settings)


def _manager(request: Request) -> ConnectionManager:
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise RemoteConnectionError("Connection manager is not initialised.")
    return manager


def _live_transport(profile_name: str, request: Request) -> Transport:
    """The profile's existing live transport, or a clean error if not connected.

    No implicit reconnect: the user must have connected the cluster first.
    """
    transport = _manager(request).get(profile_name)
    if transport is None:
        raise RemoteConnectionError(
            f"'{profile_name}' is not connected. Connect the cluster first, then retry."
        )
    return transport


class JobListItem(JobRecord):
    """A job record annotated with whether its cluster is currently connected.

    `cluster_error` is set when the cluster is connected but refreshing its jobs
    failed this time; the record then keeps its last known state.
    """

    connected: bool = False
    cluster_error: str | None = None


def worst_status(statuses: Iterable[str]) -> str | None:
    """Worst-first reduction of a set of `crab history` statuses.

    A job's data_dir can hold several experiments (one `crab run` submission
    runs every key in the config's `experiments` dict); this collapses their
    statuses to the single worst one, or None if there's nothing to reduce.
    """
    statuses = set(statuses)
    if not statuses:
        return None
    for candidate in _HISTORY_STATUS_PRIORITY:
        if candidate in statuses:
            return candidate
    return next(iter(statuses))


async def _resolve_via_history(
    transport: Transport, profile: Profile, record: JobRecord, timeout: float
) -> str | None:
    """Cross-check `crab history` against a job's actual per-experiment outcomes.

    Used two ways: (1) squeue/sacct report UNKNOWN (accounting purged), or
    (2) squeue/sacct report a fresh COMPLETED: engine.py's `_run_worker`
    catches each experiment's exception and keeps going, so the Slurm job can
    exit 0 while an experiment inside it genuinely failed. Both cases need the
    same worst-first read: a job's data_dir can hold several experiments; their
    metadata.csv rows all share `./<basename(data_dir)>/` as their
    relative_path prefix (`core/experiment/runner.py::_write_to_registry`).
    Returns the worst matching status, or None if nothing matches (still
    genuinely unknown/clean: never guessed).
    """
    history = await run_crab_json(
        transport, profile, ["history", "-s", record.system, "--json"], timeout=timeout
    )
    prefix = f"./{Path(record.data_dir).name}/"
    statuses = {
        e["status"] for e in history["experiments"] if e.get("relative_path", "").startswith(prefix)
    }
    return worst_status(statuses)


async def _refresh_cluster(
    transport: Transport, profile: Profile, recs: list[JobRecord]
) -> dict[str, str]:
    """Fresh state per record id for one cluster's active jobs (only the changed ones)."""
    status = await run_crab_json(
        transport, profile, ["status", *(r.job_id for r in recs), "--json"], timeout=30.0
    )
    by_job_id = {j["job_id"]: j["state"] for j in status["jobs"]}
    resolved: dict[str, str] = {}
    for rec in recs:
        state = by_job_id.get(rec.job_id, "UNKNOWN")
        # `recs` only ever holds jobs whose stored state isn't terminal yet
        # (see list_jobs' filter), so a fresh COMPLETED here is the one and
        # only moment to catch a failed experiment before this job is
        # never polled again.
        if state in ("UNKNOWN", "COMPLETED"):
            via_history = await _resolve_via_history(transport, profile, rec, timeout=30.0)
            if via_history is not None:
                state = via_history
        if state != rec.last_known_state:
            resolved[rec.id] = state
    return resolved


@router.get("")
async def list_jobs(request: Request) -> list[JobListItem]:
    """Registry ⨝ live `crab status`, batched one call per cluster with active jobs.

    Clusters are refreshed concurrently. A disconnected cluster's jobs are
    returned as-is (last known state, `connected: false`), and a connected
    cluster whose refresh fails keeps its last known states and carries the
    error in `cluster_error`; neither fails the whole list.
    """
    store = _jobs_store(request)
    manager = _manager(request)
    profiles = _profiles(request)
    records = store.list()

    active_by_cluster: dict[str, list[JobRecord]] = {}
    for rec in records:
        if rec.last_known_state not in _TERMINAL_STATES:
            active_by_cluster.setdefault(rec.cluster, []).append(rec)

    cluster_errors: dict[str, str] = {}

    async def refresh(cluster: str, recs: list[JobRecord]) -> dict[str, str]:
        transport = manager.get(cluster)
        if transport is None:
            return {}  # not connected: leave last_known_state as-is
        try:
            profile = profiles.get(cluster)
        except NotFoundError:
            return {}  # profile removed after the job was recorded
        try:
            return await _refresh_cluster(transport, profile, recs)
        except CrabWebError as e:
            # All-or-nothing per cluster: a status already read as COMPLETED must
            # not be stored when its history cross-check failed.
            cluster_errors[cluster] = e.message
            return {}

    refreshed = await asyncio.gather(
        *(refresh(cluster, recs) for cluster, recs in active_by_cluster.items())
    )
    for resolved_states in refreshed:
        for record_id, state in resolved_states.items():
            store.update(record_id, last_known_state=state)

    return [
        JobListItem(
            **rec.model_dump(),
            connected=manager.get(rec.cluster) is not None,
            cluster_error=cluster_errors.get(rec.cluster),
        )
        for rec in store.list()
    ]


class CancelResponse(BaseModel):
    job: JobRecord
    cancelled: bool
    detail: str | None = None


@router.post("/{record_id}/cancel")
async def cancel_job(record_id: str, request: Request) -> CancelResponse:
    store = _jobs_store(request)
    rec = store.get(record_id)
    profile = _profiles(request).get(rec.cluster)
    transport = _live_transport(rec.cluster, request)

    result = await run_crab_json(transport, profile, ["cancel", rec.job_id, "--json"], timeout=30.0)
    if result["cancelled"]:
        rec = store.update(record_id, last_known_state="CANCELLED")
    return CancelResponse(job=rec, cancelled=result["cancelled"], detail=result.get("detail"))


@router.get("/{record_id}/logs")
async def job_logs(record_id: str, request: Request, experiment: str | None = None) -> dict:
    """Job-level slurm logs, or one experiment's per-app error logs if `experiment` is given.

    Live-first with a local-cache fallback: a disconnected cluster or
    a failed remote command falls back to the last successfully fetched copy
    for this exact key, marked `stale`, instead of blanking the logs panel.
    A key miss (never fetched before) still raises exactly as before.
    """
    rec = _jobs_store(request).get(record_id)
    profile = _profiles(request).get(rec.cluster)
    cache_key = f"{record_id}__{experiment}" if experiment else record_id

    async def fetch() -> dict:
        transport = _live_transport(rec.cluster, request)
        args = ["logs", "--data-dir", RemotePath(rec.data_dir)]
        if experiment:
            args += ["--experiment", experiment]
        args.append("--json")
        return await run_crab_json(transport, profile, args, timeout=30.0)

    data, stale, cached_at = await _live_or_cached(request, "logs", cache_key, fetch)
    return {**data, "stale": stale, "cached_at": cached_at}
