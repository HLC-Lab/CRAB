"""``/api/jobs`` — submit an authored config to a connected cluster, then
monitor/cancel/read logs for it.

The local job registry (``store/jobs.py``) only records what the dashboard
itself submitted (cluster, Slurm job id, data_dir, a config snapshot); live
state always comes from a fresh ``crab status``/``crab logs --json`` call —
this module never assumes a job's state without asking the cluster.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import InputError, RemoteConnectionError
from crab.web.remoteops.crab_cli import run_crab_json
from crab.web.remoteops.transfer import stage_config
from crab.web.store.jobs import JobRecord, JobsStore
from crab.web.store.library import LibraryStore
from crab.web.store.profiles import ProfileStore

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class SubmitRequest(BaseModel):
    profile_name: str
    config_id: str | None = None
    config: dict | None = None
    name: str | None = None
    preset: str | None = None


def _jobs_store(request: Request) -> JobsStore:
    return JobsStore(request.app.state.settings)


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


@router.post("/submit", status_code=201)
async def submit_job(body: SubmitRequest, request: Request) -> JobRecord:
    """Stage the config on the cluster and run it (ADR-010: preset chosen here, not in the config)."""
    profile = _profiles(request).get(body.profile_name)
    transport = _live_transport(body.profile_name, request)
    config, name = _resolve_config(body, request)

    preset = body.preset or profile.preset
    if not preset:
        raise InputError("No preset selected and this remote has no default preset.")

    staged_path = await stage_config(
        transport, profile, config, name, settings=request.app.state.settings
    )
    result = await run_crab_json(
        transport, profile, ["run", staged_path, "-p", preset, "--json"], timeout=60.0
    )

    return _jobs_store(request).create(
        cluster=profile.name,
        job_id=str(result["job_id"]),
        data_dir=result["data_dir"],
        system=result["system"],
        config_name=name,
        config_snapshot=config,
    )
