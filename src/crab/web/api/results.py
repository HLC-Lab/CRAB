"""``/api/jobs/{record_id}/results`` — fetch and cache a job's CSV result tree.

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
from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import CrabWebError, NotFoundError, RemoteConnectionError, logger
from crab.web.models.results import ResultsData
from crab.web.store.jobs import JobsStore
from crab.web.store.results_cache import ResultsCache

router = APIRouter(prefix="/api/jobs", tags=["results"])


def _jobs_store(request: Request) -> JobsStore:
    return JobsStore(request.app.state.settings)


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


class FetchAccepted(BaseModel):
    fetch_id: str


class FetchStatus(BaseModel):
    """Polled result of an async results fetch. `status` is one of
    "pending", "done", "error"."""

    status: str
    message: str | None = None
    detail: str | None = None


async def _run_fetch(
    tracker: dict[str, dict[str, Any]],
    fetch_id: str,
    transport: Transport,
    remote_dir: str,
    local_dir: str,
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
    except Exception as e:
        logger.exception("Unhandled error in background results fetch %s", fetch_id)
        tracker[fetch_id] = {"status": "error", "message": str(e), "detail": None}


@router.post("/{record_id}/results/fetch", status_code=202)
async def fetch_results(record_id: str, request: Request) -> FetchAccepted:
    """Validate synchronously (job exists, cluster connected), then fetch in the background."""
    rec = _jobs_store(request).get(record_id)
    transport = _live_transport(rec.cluster, request)
    local_dir = _results_cache(request).path_for(rec.cluster, Path(rec.data_dir).name)

    fetch_id = str(uuid.uuid4())
    tracker = _fetches(request)
    tracker[fetch_id] = {"status": "pending"}

    task = asyncio.create_task(
        _run_fetch(tracker, fetch_id, transport, rec.data_dir, str(local_dir))
    )
    pending_tasks = request.app.state.pending_result_fetch_tasks
    pending_tasks.add(task)
    task.add_done_callback(pending_tasks.discard)

    return FetchAccepted(fetch_id=fetch_id)


@router.get("/{record_id}/results/fetch/{fetch_id}")
async def get_fetch_status(record_id: str, fetch_id: str, request: Request) -> FetchStatus:
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


@router.get("/{record_id}/results")
async def get_results(record_id: str, request: Request) -> ResultsData:
    """The cached CSV tree for this job, parsed. 404 if it was never fetched."""
    rec = _jobs_store(request).get(record_id)
    local_dir = _results_cache(request).path_for(rec.cluster, Path(rec.data_dir).name)
    if not local_dir.is_dir():
        raise NotFoundError(f"No results cached yet for job '{record_id}'. Fetch them first.")
    experiments = await asyncio.to_thread(collect_result_data, local_dir)
    return ResultsData(experiments=experiments)


class CacheSize(BaseModel):
    total_bytes: int


@router.get("/results/cache")
async def get_results_cache_size(request: Request) -> CacheSize:
    return CacheSize(total_bytes=_results_cache(request).total_size())


@router.delete("/results/cache", status_code=204)
async def clear_results_cache(request: Request) -> None:
    _results_cache(request).clear()
