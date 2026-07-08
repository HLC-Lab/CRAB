"""``/api/results`` — fetch and cache a job's CSV result tree.

Job identity is off the local registry (plan 077): a route key is
``(cluster, system, job_basename)``, resolved registry-first against
`JobsStore` for a fast path, falling back to a live `crab history` call for a
job never submitted through this dashboard (CLI-only, per ADR-002 -- the
engine/CLI stays authoritative, the dashboard never guesses a path).

Fetching a large result tree over SFTP can take a while, so it runs as a
background task with its own accept/poll/pop tracker — the same shape as
`api/jobs.py`'s async-submit tracker (`_submissions`/`_run_submission`/
`submit_job`/`get_submission`), copied rather than shared (plan 065's Design:
two small independent trackers, not a premature shared abstraction).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crab.cli.export import collect_result_data
from crab.web.api.jobs import _live_or_cached, worst_status
from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import CrabWebError, NotFoundError, RemoteConnectionError, logger
from crab.web.models.results import ResultsData
from crab.web.remoteops.crab_cli import run_crab_json
from crab.web.settings import Settings
from crab.web.store.cache import LocalCache
from crab.web.store.jobs import JobRecord, JobsStore
from crab.web.store.profiles import Profile, ProfileStore
from crab.web.store.results_cache import ResultsCache

router = APIRouter(prefix="/api/results", tags=["results"])

# The Results picker (get_results_index) and a job's own experiment-status
# query (get_results_experiments) both read the same cluster's `crab history`
# moments apart in normal use (open the picker, then a job). Reusing a result
# this recent instead of a second live SSH round-trip is imperceptible for a
# monitoring dashboard's freshness needs.
_HISTORY_TTL_SECONDS = 10.0


def _jobs_store(request: Request) -> JobsStore:
    return JobsStore(request.app.state.settings)


def _profiles(request: Request) -> ProfileStore:
    return ProfileStore(request.app.state.settings)


def _results_cache(request: Request) -> ResultsCache:
    return ResultsCache(request.app.state.settings)


def _fetches(request: Request) -> dict[str, dict[str, Any]]:
    return request.app.state.result_fetches


def _manager(request: Request) -> ConnectionManager:
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise RemoteConnectionError("Connection manager is not initialised.")
    return manager


def _live_transport(cluster: str, request: Request) -> Transport:
    """The cluster's existing live transport, or a clean error if not connected.

    No implicit reconnect — the user must have connected the cluster first.
    """
    transport = _manager(request).get(cluster)
    if transport is None:
        raise RemoteConnectionError(
            f"'{cluster}' is not connected. Connect the cluster first, then retry."
        )
    return transport


def _job_basename(relative_path: str) -> str:
    """First path component of a history row's ``./<job_basename>/<exp_basename>``."""
    parts = [p for p in relative_path.split("/") if p and p != "."]
    return parts[0] if parts else ""


async def _resolve_remote_dir(
    cluster: str, system: str, job_basename: str, request: Request
) -> str:
    """The job's remote directory: registry-first, else a live CLI-only fallback.

    A `JobsStore` record whose (cluster, system, basename-of-data_dir) match
    gives `data_dir` directly. Otherwise this job was never submitted through
    this dashboard -- fall back to a live `crab history` call and match by
    job_basename, reading the resolved absolute path a matching row already
    reports (plan 077 S1) rather than reconstructing one client-side.
    """
    for rec in _jobs_store(request).list():
        if (
            rec.cluster == cluster
            and rec.system == system
            and Path(rec.data_dir).name == job_basename
        ):
            return rec.data_dir

    profile = _profiles(request).get(cluster)
    transport = _live_transport(cluster, request)
    history = await run_crab_json(
        transport, profile, ["history", "-s", system, "--json"], timeout=30.0
    )
    for row in history["experiments"]:
        if _job_basename(row.get("relative_path", "")) == job_basename:
            return str(Path(row["absolute_path"]).parent)

    raise NotFoundError(f"No job '{job_basename}' found on {cluster}/{system}.")


def _cached_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class ResultsJobEntry(BaseModel):
    """One (cluster, system, job_basename) the Results picker can show."""

    cluster: str
    system: str
    job_basename: str
    connected: bool
    status: str | None = None
    record_id: str | None = None
    job_id: str | None = None
    submitted_at: str | None = None
    cached: bool
    cached_bytes: int | None = None
    possibly_stale: bool


class ResultsIndex(BaseModel):
    jobs: list[ResultsJobEntry]


@router.get("")
async def get_results_index(request: Request) -> ResultsIndex:
    """Every job any connected-or-previously-cached cluster's `crab history` reports.

    Joined against the local registry (optional -- a CLI-only job has none) and
    the on-disk results cache. A cluster with no live connection and no prior
    cached history at all still contributes anything it has cached to disk
    (`ResultsCache.list_cached()`), marked `connected: false, possibly_stale:
    true`, so fetched work never disappears from the picker just because the
    cluster isn't reachable right now.
    """
    manager = _manager(request)
    cache = _results_cache(request)
    local_cache = LocalCache(request.app.state.settings)

    registry_by_triple: dict[tuple[str, str, str], JobRecord] = {
        (rec.cluster, rec.system, Path(rec.data_dir).name): rec
        for rec in _jobs_store(request).list()
    }
    cached_triples = set(cache.list_cached())
    entries: dict[tuple[str, str, str], ResultsJobEntry] = {}

    async def entries_for_profile(profile: Profile) -> list[ResultsJobEntry]:
        async def fetch() -> dict:
            transport = _live_transport(profile.name, request)
            return await run_crab_json(transport, profile, ["history", "--json"], timeout=30.0)

        try:
            history, _stale, _cached_at = await _live_or_cached(
                request, "history", f"cluster:{profile.name}", fetch, _HISTORY_TTL_SECONDS
            )
        except RemoteConnectionError:
            return []  # nothing live or cached for this cluster's history at all

        connected = manager.get(profile.name) is not None
        by_group: dict[tuple[str, str], set[str]] = {}
        for row in history["experiments"]:
            basename = _job_basename(row.get("relative_path", ""))
            if not basename:
                continue
            by_group.setdefault((row["system"], basename), set()).add(row["status"])

        profile_entries: list[ResultsJobEntry] = []
        for (system, basename), statuses in by_group.items():
            triple = (profile.name, system, basename)
            status = worst_status(statuses)
            known = registry_by_triple.get(triple)
            is_cached = triple in cached_triples
            snapshot = local_cache.read(
                "results_fetch_status", f"{profile.name}:{system}:{basename}"
            )
            fetch_status = snapshot["data"]["status"] if snapshot else None
            cached_bytes = (
                await asyncio.to_thread(_cached_bytes, cache.path_for(*triple))
                if is_cached
                else None
            )
            profile_entries.append(
                ResultsJobEntry(
                    cluster=profile.name,
                    system=system,
                    job_basename=basename,
                    connected=connected,
                    status=status,
                    record_id=known.id if known else None,
                    job_id=known.job_id if known else None,
                    submitted_at=known.submitted_at if known else None,
                    cached=is_cached,
                    cached_bytes=cached_bytes,
                    possibly_stale=not is_cached or fetch_status != status,
                )
            )
        return profile_entries

    # Every connected cluster's `crab history` in flight at once -- with N
    # clusters this was N sequential SSH round-trips before (plan 079).
    # `RemoteConnectionError` is already handled per-profile above (returns
    # []); any OTHER exception must still surface, not be swallowed by
    # `gather`, so it's re-raised here after every task has settled.
    fanned_out = await asyncio.gather(
        *(entries_for_profile(profile) for profile in _profiles(request).list()),
        return_exceptions=True,
    )
    for result in fanned_out:
        if isinstance(result, BaseException):
            raise result
        for entry in result:
            entries[(entry.cluster, entry.system, entry.job_basename)] = entry

    for triple in cached_triples - entries.keys():
        cluster, system, basename = triple
        known = registry_by_triple.get(triple)
        cached_bytes = await asyncio.to_thread(_cached_bytes, cache.path_for(*triple))
        entries[triple] = ResultsJobEntry(
            cluster=cluster,
            system=system,
            job_basename=basename,
            connected=manager.get(cluster) is not None,
            status=None,
            record_id=known.id if known else None,
            job_id=known.job_id if known else None,
            submitted_at=known.submitted_at if known else None,
            cached=True,
            cached_bytes=cached_bytes,
            possibly_stale=True,
        )

    return ResultsIndex(jobs=list(entries.values()))


class ExperimentRunStatus(BaseModel):
    """One experiment's status and run-failure counts (plan 081) -- lets a
    caller show "3/10 runs failed" instead of just "FAILED" for an
    experiment where most runs actually succeeded and have real data."""

    experiment_name: str
    status: str
    total_runs: str
    failed_runs: str


class ExperimentRunStatusList(BaseModel):
    experiments: list[ExperimentRunStatus]


@router.get("/{cluster}/{system}/{job_basename}/experiments")
async def get_results_experiments(
    cluster: str, system: str, job_basename: str, request: Request
) -> ExperimentRunStatusList:
    """Per-experiment status/run-failure counts for one job.

    Not registry-dependent, unlike `job_experiments` (`api/jobs.py`) --
    Results must work identically for CLI-only jobs (plan 077 decision 7),
    and a live/cached `crab history` call already has everything needed
    without a registry join. Shares `_live_or_cached`'s cache scope and TTL
    with `get_results_index` (same `f"cluster:{cluster}"` key), so opening a
    job shortly after the picker loaded often reuses that result instead of a
    second live round-trip -- the reused history may be UNSCOPED (the
    picker's own query has no `-s system`), so `row["system"]` is checked
    explicitly below rather than trusting the query's own scope.
    """
    profile = _profiles(request).get(cluster)

    async def fetch() -> dict:
        transport = _live_transport(cluster, request)
        return await run_crab_json(
            transport, profile, ["history", "-s", system, "--json"], timeout=30.0
        )

    try:
        history, _stale, _cached_at = await _live_or_cached(
            request, "history", f"cluster:{cluster}", fetch, _HISTORY_TTL_SECONDS
        )
    except RemoteConnectionError:
        return ExperimentRunStatusList(experiments=[])

    return ExperimentRunStatusList(
        experiments=[
            ExperimentRunStatus(
                experiment_name=row.get("experiment_name", ""),
                status=row.get("status", ""),
                total_runs=row.get("total_runs", ""),
                failed_runs=row.get("failed_runs", ""),
            )
            for row in history["experiments"]
            if row["system"] == system
            and _job_basename(row.get("relative_path", "")) == job_basename
        ]
    )


class FetchAccepted(BaseModel):
    fetch_id: str


class FetchStatus(BaseModel):
    """Polled result of an async results fetch. `status` is one of
    "pending", "done", "error"."""

    status: str
    message: str | None = None
    detail: str | None = None


async def _snapshot_fetch_status(
    transport: Transport, profile: Profile, settings: Settings, system: str, job_basename: str
) -> None:
    """Best-effort: record this job's worst `crab history` status at fetch time.

    Lets S6's staleness check notice a status change since the last fetch.
    Never fatal -- a failure here must not affect the fetch's own outcome.
    """
    history = await run_crab_json(
        transport, profile, ["history", "-s", system, "--json"], timeout=30.0
    )
    statuses = {
        row["status"]
        for row in history["experiments"]
        if _job_basename(row.get("relative_path", "")) == job_basename
    }
    status = worst_status(statuses)
    if status is not None:
        LocalCache(settings).write(
            "results_fetch_status", f"{profile.name}:{system}:{job_basename}", {"status": status}
        )


async def _run_fetch(
    tracker: dict[str, dict[str, Any]],
    fetch_id: str,
    transport: Transport,
    profile: Profile,
    remote_dir: str,
    local_dir: str,
    settings: Settings,
    system: str,
    job_basename: str,
) -> None:
    """The actual SFTP tree fetch, off the request cycle.

    Never lets an exception escape uncaught: an unhandled error in a
    fire-and-forget task is only logged by asyncio's default handler, which
    would leave the tracker stuck at "pending" forever with no way to notice.
    """
    try:
        await transport.fetch_tree(remote_dir, local_dir)
        tracker[fetch_id] = {"status": "done"}
    except CrabWebError as e:
        tracker[fetch_id] = {"status": "error", "message": e.message, "detail": e.detail}
        return
    except Exception as e:
        logger.exception("Unhandled error in background results fetch %s", fetch_id)
        tracker[fetch_id] = {"status": "error", "message": str(e), "detail": None}
        return

    try:
        await _snapshot_fetch_status(transport, profile, settings, system, job_basename)
    except Exception:
        logger.exception("Best-effort results-fetch status snapshot failed for %s", fetch_id)


@router.post("/{cluster}/{system}/{job_basename}/fetch", status_code=202)
async def fetch_results(
    cluster: str, system: str, job_basename: str, request: Request
) -> FetchAccepted:
    """Validate synchronously (job resolves, cluster connected), then fetch in the background."""
    remote_dir = await _resolve_remote_dir(cluster, system, job_basename, request)
    profile = _profiles(request).get(cluster)
    transport = _live_transport(cluster, request)
    local_dir = _results_cache(request).path_for(cluster, system, job_basename)

    fetch_id = str(uuid.uuid4())
    tracker = _fetches(request)
    tracker[fetch_id] = {"status": "pending"}

    task = asyncio.create_task(
        _run_fetch(
            tracker,
            fetch_id,
            transport,
            profile,
            remote_dir,
            str(local_dir),
            request.app.state.settings,
            system,
            job_basename,
        )
    )
    pending_tasks = request.app.state.pending_result_fetch_tasks
    pending_tasks.add(task)
    task.add_done_callback(pending_tasks.discard)

    return FetchAccepted(fetch_id=fetch_id)


@router.get("/{cluster}/{system}/{job_basename}/fetch/{fetch_id}")
async def get_fetch_status(
    cluster: str, system: str, job_basename: str, fetch_id: str, request: Request
) -> FetchStatus:
    """Poll a fetch's status; 404 once a terminal result has been fetched.

    Entries are dropped from the tracker as soon as a terminal status is
    returned so it doesn't grow forever (there's no other cleanup — the
    tracker is in-memory and process-lifetime only, same as jobs.py's).
    """
    tracker = _fetches(request)
    entry = tracker.get(fetch_id)
    if entry is None:
        raise NotFoundError(f"No results fetch with id '{fetch_id}'.")
    response = FetchStatus(
        status=entry["status"], message=entry.get("message"), detail=entry.get("detail")
    )
    if entry["status"] != "pending":
        tracker.pop(fetch_id, None)
    return response


@router.get("/{cluster}/{system}/{job_basename}")
async def get_results(
    cluster: str, system: str, job_basename: str, request: Request
) -> ResultsData:
    """The cached CSV tree for this job, parsed. 404 if it was never fetched."""
    local_dir = _results_cache(request).path_for(cluster, system, job_basename)
    if not local_dir.is_dir():
        raise NotFoundError(
            f"No results cached yet for '{cluster}/{system}/{job_basename}'. Fetch them first."
        )
    experiments = await asyncio.to_thread(collect_result_data, local_dir)
    return ResultsData(experiments=experiments)


class CacheSize(BaseModel):
    total_bytes: int


@router.get("/cache")
async def get_results_cache_size(request: Request) -> CacheSize:
    total_bytes = await asyncio.to_thread(_results_cache(request).total_size)
    return CacheSize(total_bytes=total_bytes)


@router.delete("/cache", status_code=204)
async def clear_results_cache(request: Request) -> None:
    _results_cache(request).clear()
