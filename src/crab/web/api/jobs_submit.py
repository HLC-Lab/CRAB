"""``/api/jobs`` submit: stage an authored config on a connected cluster and run it
(``POST /submit``), then poll the result (``GET /submissions/{submission_id}``).

Split out of `api/jobs.py`, whose shared helpers (`_profiles`, `_submissions`)
this module imports; `jobs.py` never imports it back. The router is included
right before `jobs.router` in `server.py` so the registration order of the
`/api/jobs` routes stays the same as before the split.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crab.web.api.jobs import _profiles, _submissions
from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import (
    CrabWebError,
    InputError,
    NotFoundError,
    RemoteConnectionError,
    logger,
)
from crab.web.remoteops.crab_cli import RemotePath, run_crab_json
from crab.web.remoteops.transfer import stage_config
from crab.web.settings import Settings
from crab.web.store.jobs import JobRecord, JobsStore
from crab.web.store.library import LibraryStore
from crab.web.store.profiles import Profile
from crab.web.trackers import ExpiringTracker

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class SubmitRequest(BaseModel):
    profile_name: str
    config_id: str | None = None
    config: dict | None = None
    name: str | None = None
    preset: str | None = None
    only: list[str] | None = None
    rerun_of: str | None = None


def _manager(request: Request) -> ConnectionManager:
    # A copy of jobs.py's helper, deliberate: two small independent copies
    # (same as remotes.py and results.py), not a shared abstraction.
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
    """Polled result of an async submit/rerun. `status` is one of
    "pending", "done", "error"."""

    status: str
    record: JobRecord | None = None
    message: str | None = None
    detail: str | None = None


async def _run_submission(
    tracker: ExpiringTracker,
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
    """The actual staging/run work, off the request cycle (async submit).

    Takes plain values, not the request, since `request` is request-scoped and
    this keeps running after the response that created it has been sent.
    Never lets an exception escape uncaught: an unhandled error in a
    fire-and-forget task is only logged by asyncio's default handler, which
    would leave the tracker stuck at "pending" forever with no way to notice.
    """
    try:
        staged_path = await stage_config(transport, profile, config, name, settings=settings)
        run_args = ["run", RemotePath(staged_path), "-p", preset]
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
    """Validate synchronously, then stage/run in the background.

    Everything that can fail instantly (profile exists, connected, config
    resolves, a preset is chosen) still happens before responding, same as
    before: only the SSH round-trip (staging + `crab run`) moves off the
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
    returned; an entry nobody polls expires on its own (`web/trackers.py`).
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
