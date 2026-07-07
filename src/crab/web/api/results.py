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
from crab.web.api.jobs import worst_status
from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import CrabWebError, NotFoundError, RemoteConnectionError, logger
from crab.web.models.results import ResultsData
from crab.web.remoteops.crab_cli import run_crab_json
from crab.web.settings import Settings
from crab.web.store.cache import LocalCache
from crab.web.store.jobs import JobsStore
from crab.web.store.profiles import Profile, ProfileStore
from crab.web.store.results_cache import ResultsCache

router = APIRouter(prefix="/api/results", tags=["results"])


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
    return CacheSize(total_bytes=_results_cache(request).total_size())


@router.delete("/cache", status_code=204)
async def clear_results_cache(request: Request) -> None:
    _results_cache(request).clear()
