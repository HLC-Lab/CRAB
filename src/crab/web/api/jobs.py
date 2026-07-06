"""``/api/jobs`` — submit an authored config to a connected cluster, then
monitor/cancel/read logs for it.

The local job registry (``store/jobs.py``) only records what the dashboard
itself submitted (cluster, Slurm job id, data_dir, a config snapshot); live
state always comes from a fresh ``crab status``/``crab logs --json`` call —
this module never assumes a job's state without asking the cluster.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import (
    CrabWebError,
    InputError,
    NotFoundError,
    RemoteCommandError,
    RemoteConnectionError,
    logger,
)
from crab.web.remoteops.crab_cli import run_crab_json
from crab.web.remoteops.transfer import stage_config
from crab.web.settings import Settings
from crab.web.store.cache import LocalCache
from crab.web.store.jobs import JobRecord, JobsStore
from crab.web.store.library import LibraryStore
from crab.web.store.profiles import Profile, ProfileStore

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Slurm states that will never change again — jobs in one of these are never
# re-polled. Anything else (including our own "UNKNOWN") is treated as active.
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
}
# Worst-first: a purged job's data_dir can hold several experiments' metadata.csv
# rows (one crab run submission runs many); the job resolves to the worst one.
_HISTORY_STATUS_PRIORITY = ("FAILED", "TIMEOUT", "COMPLETED")


class SubmitRequest(BaseModel):
    profile_name: str
    config_id: str | None = None
    config: dict | None = None
    name: str | None = None
    preset: str | None = None
    only: list[str] | None = None
    rerun_of: str | None = None


def _jobs_store(request: Request) -> JobsStore:
    return JobsStore(request.app.state.settings)


def _cache(request: Request) -> LocalCache:
    return LocalCache(request.app.state.settings)


def _submissions(request: Request) -> dict[str, dict[str, Any]]:
    return request.app.state.submissions


async def _live_or_cached(
    request: Request, scope: str, key: str, fetch: Callable[[], Awaitable[dict]]
) -> tuple[dict, bool, str | None]:
    """Try `fetch()` live; on disconnect/command failure, fall back to the local cache.

    Returns ``(data, stale, cached_at)``. A cache miss re-raises the live error
    unchanged (no regression for data that was never successfully fetched before).
    """
    cache = _cache(request)
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

    No implicit reconnect — the user must have connected the cluster first.
    """
    transport = _manager(request).get(profile_name)
    if transport is None:
        raise RemoteConnectionError(
            f"'{profile_name}' is not connected. Connect the cluster first, then retry."
        )
    return transport


def _resolve_config(body: SubmitRequest, request: Request) -> tuple[dict, str]:
    """Return ``(config, name)`` from either a saved library entry or an inline config."""
    if body.config_id:
        entry = LibraryStore(request.app.state.settings).get(body.config_id)
        return entry.config, entry.name
    if body.config is not None:
        if not body.name:
            raise InputError("`name` is required when submitting an inline config.")
        return body.config, body.name
    raise InputError("Either `config_id` or `config` is required.")


class SubmissionAccepted(BaseModel):
    submission_id: str


class SubmissionStatus(BaseModel):
    """Polled result of an async submit/rerun (plan 075). `status` is one of
    "pending", "done", "error"."""

    status: str
    record: JobRecord | None = None
    message: str | None = None
    detail: str | None = None


async def _run_submission(
    tracker: dict[str, dict[str, Any]],
    submission_id: str,
    transport: Transport,
    profile: Profile,
    config: dict,
    name: str,
    preset: str,
    only: list[str] | None,
    settings: Settings,
    rerun_of: str | None = None,
) -> None:
    """The actual staging/run work, off the request cycle (plan 075's async submit).

    Takes plain values, not the request, since `request` is request-scoped and
    this keeps running after the response that created it has been sent.
    Never lets an exception escape uncaught: an unhandled error in a
    fire-and-forget task is only logged by asyncio's default handler, which
    would leave the tracker stuck at "pending" forever with no way to notice.
    """
    try:
        staged_path = await stage_config(transport, profile, config, name, settings=settings)
        run_args = ["run", staged_path, "-p", preset]
        if only:
            run_args += ["--only", ",".join(only)]
        run_args.append("--json")
        result = await run_crab_json(transport, profile, run_args, timeout=60.0)
        record = JobsStore(settings).create(
            cluster=profile.name,
            job_id=str(result["job_id"]),
            data_dir=result["data_dir"],
            system=result["system"],
            config_name=name,
            config_snapshot=config,
            rerun_of=rerun_of,
            rerun_experiments=only,
        )
        tracker[submission_id] = {"status": "done", "record": record}
    except CrabWebError as e:
        tracker[submission_id] = {"status": "error", "message": e.message, "detail": e.detail}
    except Exception as e:
        logger.exception("Unhandled error in background submission %s", submission_id)
        tracker[submission_id] = {"status": "error", "message": str(e), "detail": None}


@router.post("/submit", status_code=202)
async def submit_job(body: SubmitRequest, request: Request) -> SubmissionAccepted:
    """Validate synchronously, then stage/run in the background (plan 075).

    Everything that can fail instantly (profile exists, connected, config
    resolves, a preset is chosen) still happens before responding, same as
    before — only the SSH round-trip (staging + `crab run`) moves off the
    request cycle, since that's what used to make a submit or rerun feel like
    it hung with no feedback.
    """
    profile = _profiles(request).get(body.profile_name)
    transport = _live_transport(body.profile_name, request)
    config, name = _resolve_config(body, request)

    preset = body.preset or profile.preset
    if not preset:
        raise InputError("No preset selected and this remote has no default preset.")

    submission_id = str(uuid.uuid4())
    tracker = _submissions(request)
    tracker[submission_id] = {"status": "pending"}

    task = asyncio.create_task(
        _run_submission(
            tracker,
            submission_id,
            transport,
            profile,
            config,
            name,
            preset,
            body.only,
            request.app.state.settings,
            body.rerun_of,
        )
    )
    pending_tasks = request.app.state.pending_submission_tasks
    pending_tasks.add(task)
    task.add_done_callback(pending_tasks.discard)

    return SubmissionAccepted(submission_id=submission_id)


@router.get("/submissions/{submission_id}")
async def get_submission(submission_id: str, request: Request) -> SubmissionStatus:
    """Poll a submission's status; 404 once a terminal result has been fetched.

    Entries are dropped from the tracker as soon as a terminal status is
    returned so it doesn't grow forever (there's no other cleanup — the
    tracker is in-memory and process-lifetime only).
    """
    tracker = _submissions(request)
    entry = tracker.get(submission_id)
    if entry is None:
        raise NotFoundError(f"No submission with id '{submission_id}'.")
    response = SubmissionStatus(
        status=entry["status"],
        record=entry.get("record"),
        message=entry.get("message"),
        detail=entry.get("detail"),
    )
    if entry["status"] != "pending":
        tracker.pop(submission_id, None)
    return response


class JobListItem(JobRecord):
    """A job record annotated with whether its cluster is currently connected."""

    connected: bool = False


async def _resolve_via_history(
    transport: Transport, profile: Profile, record: JobRecord, timeout: float
) -> str | None:
    """Cross-check `crab history` against a job's actual per-experiment outcomes.

    Used two ways: (1) squeue/sacct report UNKNOWN (accounting purged), or
    (2) squeue/sacct report a fresh COMPLETED — engine.py's `_run_worker`
    catches each experiment's exception and keeps going, so the Slurm job can
    exit 0 while an experiment inside it genuinely failed. Both cases need the
    same worst-first read: a job's data_dir can hold several experiments (one
    `crab run` submission runs every key in the config's `experiments` dict);
    their metadata.csv rows all share `./<basename(data_dir)>/` as their
    relative_path prefix (`core/experiment/runner.py::_write_to_registry`).
    Returns the worst matching status, or None if nothing matches (still
    genuinely unknown/clean — never guessed).
    """
    history = await run_crab_json(
        transport, profile, ["history", "-s", record.system, "--json"], timeout=timeout
    )
    prefix = f"./{Path(record.data_dir).name}/"
    statuses = {
        e["status"] for e in history["experiments"] if e.get("relative_path", "").startswith(prefix)
    }
    if not statuses:
        return None
    for candidate in _HISTORY_STATUS_PRIORITY:
        if candidate in statuses:
            return candidate
    return next(iter(statuses))


@router.get("")
async def list_jobs(request: Request) -> list[JobListItem]:
    """Registry ⨝ live `crab status`, batched one call per cluster with active jobs.

    A disconnected cluster's jobs are returned as-is (last known state,
    `connected: false`) rather than failing the whole list.
    """
    store = _jobs_store(request)
    manager = _manager(request)
    profiles = _profiles(request)
    records = store.list()

    active_by_cluster: dict[str, list[JobRecord]] = {}
    for rec in records:
        if rec.last_known_state not in _TERMINAL_STATES:
            active_by_cluster.setdefault(rec.cluster, []).append(rec)

    resolved_states: dict[str, str] = {}
    for cluster, recs in active_by_cluster.items():
        transport = manager.get(cluster)
        if transport is None:
            continue  # not connected: leave last_known_state as-is
        try:
            profile = profiles.get(cluster)
        except NotFoundError:
            continue  # profile removed after the job was recorded

        status = await run_crab_json(
            transport, profile, ["status", *(r.job_id for r in recs), "--json"], timeout=30.0
        )
        by_job_id = {j["job_id"]: j["state"] for j in status["jobs"]}
        for rec in recs:
            state = by_job_id.get(rec.job_id, "UNKNOWN")
            # `recs` only ever holds jobs whose stored state isn't terminal yet
            # (see the filter above), so a fresh COMPLETED here is the one and
            # only moment to catch a failed experiment before this job is
            # never polled again.
            if state in ("UNKNOWN", "COMPLETED"):
                via_history = await _resolve_via_history(transport, profile, rec, timeout=30.0)
                if via_history is not None:
                    state = via_history
            if state != rec.last_known_state:
                resolved_states[rec.id] = state

    for record_id, state in resolved_states.items():
        store.update(record_id, last_known_state=state)

    return [
        JobListItem(**rec.model_dump(), connected=manager.get(rec.cluster) is not None)
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

    Live-first with a local-cache fallback (plan 075): a disconnected cluster or
    a failed remote command falls back to the last successfully fetched copy
    for this exact key, marked `stale`, instead of blanking the logs panel.
    A key miss (never fetched before) still raises exactly as before.
    """
    rec = _jobs_store(request).get(record_id)
    profile = _profiles(request).get(rec.cluster)
    cache_key = f"{record_id}__{experiment}" if experiment else record_id

    async def fetch() -> dict:
        transport = _live_transport(rec.cluster, request)
        args = ["logs", "--data-dir", rec.data_dir]
        if experiment:
            args += ["--experiment", experiment]
        args.append("--json")
        return await run_crab_json(transport, profile, args, timeout=30.0)

    data, stale, cached_at = await _live_or_cached(request, "logs", cache_key, fetch)
    return {**data, "stale": stale, "cached_at": cached_at}


class ReportExperiment(BaseModel):
    """One `crab history` row for a use case, joined with local submit metadata if known."""

    cluster: str
    system: str
    job_name: str
    experiment_name: str
    timestamp: str
    numnodes: str
    ppn: str
    apps_list: str
    status: str
    tags: str
    relative_path: str
    record_id: str | None = None
    job_id: str | None = None
    submitted_at: str | None = None


class StaleCluster(BaseModel):
    """One cluster whose history in this report came from the local cache, not live."""

    cluster: str
    cached_at: str


class UseCaseReport(BaseModel):
    config_name: str
    experiments: list[ReportExperiment]
    clusters_skipped: list[str]
    clusters_stale: list[StaleCluster]


def _job_basename(relative_path: str) -> str:
    """First path component of a history row's ``./<job_basename>/<exp_basename>``."""
    parts = [p for p in relative_path.split("/") if p and p != "."]
    return parts[0] if parts else ""


class JobDetail(BaseModel):
    """Every `crab history` row for one exact submission (plan 075's detail view)."""

    record_id: str
    config_name: str
    cluster: str
    system: str
    job_id: str
    submitted_at: str
    experiments: list[ReportExperiment]
    stale: bool
    cached_at: str | None = None
    rerun_of: JobRecord | None = None
    rerun_experiments: list[str] | None = None
    reruns: list[JobRecord]


@router.get("/{record_id}/experiments")
async def job_experiments(record_id: str, request: Request) -> JobDetail:
    """Every `crab history` row for this exact submission, not the whole use case.

    Same worst-first-independent matching as `_resolve_via_history`/`use_case_report`
    (a data_dir's basename against a history row's `relative_path` prefix), but
    returns every matching row instead of collapsing to one worst status.
    """
    jobs_store = _jobs_store(request)
    rec = jobs_store.get(record_id)
    profile = _profiles(request).get(rec.cluster)
    basename = Path(rec.data_dir).name

    parent = None
    if rec.rerun_of:
        try:
            parent = jobs_store.get(rec.rerun_of)
        except NotFoundError:
            parent = None
    reruns = [r for r in jobs_store.list() if r.rerun_of == rec.id]

    async def fetch() -> dict:
        transport = _live_transport(rec.cluster, request)
        return await run_crab_json(
            transport, profile, ["history", "-s", rec.system, "--json"], timeout=30.0
        )

    history, stale, cached_at = await _live_or_cached(
        request, "history", f"system:{rec.system}", fetch
    )

    experiments = [
        ReportExperiment(
            cluster=rec.cluster,
            record_id=rec.id,
            job_id=rec.job_id,
            submitted_at=rec.submitted_at,
            **row,
        )
        for row in history["experiments"]
        if _job_basename(row.get("relative_path", "")) == basename
    ]

    return JobDetail(
        record_id=rec.id,
        config_name=rec.config_name,
        cluster=rec.cluster,
        system=rec.system,
        job_id=rec.job_id,
        submitted_at=rec.submitted_at,
        experiments=experiments,
        stale=stale,
        cached_at=cached_at,
        rerun_of=parent,
        rerun_experiments=rec.rerun_experiments,
        reruns=reruns,
    )


@router.get("/report/{config_name}")
async def use_case_report(config_name: str, request: Request) -> UseCaseReport:
    """Every experiment ever run under `config_name`, across every connected cluster.

    Sourced from `crab history --json` (authoritative — matches manual runs too,
    not only ones submitted through this dashboard), then joined against the
    local job registry just for submit metadata (job id, submitted_at). A
    disconnected cluster is skipped and named in `clusters_skipped` rather than
    silently omitted, same convention as `list_jobs`'s `connected` flag. A
    disconnected cluster with a prior cached fetch falls back to it instead of
    being skipped, named in `clusters_stale` (plan 075) rather than
    `clusters_skipped` — the caller can still tell the two cases apart.
    """
    records_by_cluster: dict[str, dict[str, JobRecord]] = {}
    for rec in _jobs_store(request).list():
        records_by_cluster.setdefault(rec.cluster, {})[Path(rec.data_dir).name] = rec

    experiments: list[ReportExperiment] = []
    clusters_skipped: list[str] = []
    clusters_stale: list[StaleCluster] = []
    for profile in _profiles(request).list():

        async def fetch(profile: Profile = profile) -> dict:
            transport = _live_transport(profile.name, request)
            return await run_crab_json(transport, profile, ["history", "--json"], timeout=30.0)

        try:
            history, stale, cached_at = await _live_or_cached(
                request, "history", f"cluster:{profile.name}", fetch
            )
        except RemoteConnectionError:
            clusters_skipped.append(profile.name)
            continue

        if stale:
            assert cached_at is not None
            clusters_stale.append(StaleCluster(cluster=profile.name, cached_at=cached_at))

        known_jobs = records_by_cluster.get(profile.name, {})
        for row in history["experiments"]:
            known = known_jobs.get(_job_basename(row["relative_path"]))
            # A row's `job_name` is the config's internal `global_options.name`,
            # which can differ from the library display name used as `config_name`
            # here (e.g. a submitted config named "msgsize_scaling_study" saved in
            # the library as "Message-size scaling study..."). The local registry
            # record carries the display name, so prefer that join; only fall back
            # to matching `job_name` directly for runs with no local record (manual
            # `crab run` on the cluster, never submitted through this dashboard).
            if known is not None:
                if known.config_name != config_name:
                    continue
            elif row["job_name"] != config_name:
                continue
            experiments.append(
                ReportExperiment(
                    cluster=profile.name,
                    record_id=known.id if known else None,
                    job_id=known.job_id if known else None,
                    submitted_at=known.submitted_at if known else None,
                    **row,
                )
            )

    return UseCaseReport(
        config_name=config_name,
        experiments=experiments,
        clusters_skipped=clusters_skipped,
        clusters_stale=clusters_stale,
    )
