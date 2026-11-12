"""``/api/sbatchman`` — persist a composed campaign jobs YAML locally and push
it to the connected cluster. Composing the YAML itself is the
frontend's job (``lib/sbatchman.ts``) — this module treats it as opaque
text. Launching, monitoring, and results are SbatchMan's own job; CRAB never
runs `sbatchman launch` (ADR-026).
"""

from __future__ import annotations

import asyncio
from typing import Any

# PyYAML arrives through uvicorn[standard]; declaring it in the web extra is an owner decision.
import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import InputError, RemoteCommandError, RemoteConnectionError
from crab.web.remoteops.transfer import stage_text, staging_dir
from crab.web.store.campaign_library import CampaignEntry, CampaignLibraryStore
from crab.web.store.profiles import Profile, ProfileStore
from crab.web.store.sbatchman import campaign_slug, save_campaign_yaml

router = APIRouter(prefix="/api/sbatchman", tags=["sbatchman"])


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


def _profile(profile_name: str, request: Request) -> Profile:
    return ProfileStore(request.app.state.settings).get(profile_name)


def _campaign_filename(name: str) -> str:
    """The staged file's name for a campaign: the single source of truth for the
    write route and the staging-path preview."""
    return f"{campaign_slug(name)}.yaml"


def _check_campaign_yaml(text: str) -> None:
    """Refuse text that is not a SbatchMan campaign file before anything is written.
    Only the shape is checked (a mapping with a `jobs` key); SbatchMan validates the rest."""
    try:
        doc: Any = yaml.safe_load(text)
    except RecursionError as e:
        raise InputError("The campaign file is not valid YAML: it is nested too deeply.") from e
    except (yaml.YAMLError, ValueError) as e:
        # YAMLError is the parser's own; ValueError comes from a value the loader cannot
        # build, such as the date 2001-13-45.
        text_lines = str(e).strip().splitlines()
        problem = getattr(e, "problem", None) or (text_lines[0] if text_lines else "")
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        reason = f": {problem}{where}" if problem else ""
        raise InputError(f"The campaign file is not valid YAML{reason}.") from e
    if not isinstance(doc, dict) or "jobs" not in doc:
        raise InputError("The campaign file has no `jobs` section.")


class StagingPathResponse(BaseModel):
    path: str


@router.get("/staging-path", response_model=StagingPathResponse)
async def staging_path(profile_name: str, name: str, request: Request) -> StagingPathResponse:
    """Where Write files will put this campaign on the cluster (`~` preserved)."""
    profile = _profile(profile_name, request)
    directory = staging_dir(profile, request.app.state.settings)
    return StagingPathResponse(path=f"{directory}/{_campaign_filename(name)}")


class WriteRequest(BaseModel):
    profile_name: str
    yaml: str
    name: str = "campaign"
    # Replace a file of the same name that is already on the cluster.
    overwrite: bool = False


class WriteResponse(BaseModel):
    local_path: str
    remote_path: str


@router.post("/write", response_model=WriteResponse)
async def write_campaign(body: WriteRequest, request: Request) -> WriteResponse:
    _check_campaign_yaml(body.yaml)
    transport = _live_transport(body.profile_name, request)
    profile = _profile(body.profile_name, request)
    settings = request.app.state.settings

    # The cluster first: an existing file (409) or a failed write leaves no local copy.
    remote_path = await stage_text(
        transport,
        profile,
        body.yaml,
        _campaign_filename(body.name),
        overwrite=body.overwrite,
        settings=settings,
    )
    try:
        local_path = await asyncio.to_thread(save_campaign_yaml, body.yaml, body.name, settings)
    except OSError as e:
        raise RemoteCommandError(
            f"The campaign file was written to the cluster ({remote_path}), "
            "but saving the local copy failed.",
            detail=str(e),
        ) from e
    return WriteResponse(local_path=str(local_path), remote_path=remote_path)


# --------------------------------------------------------------------------- #
# /api/sbatchman/campaigns — the local library of saved campaign drafts
# Pure local CRUD, no cluster involved; unlike /api/experiments,
# no shape-validation pass (a group's `{var}` placeholders would fail one).
# --------------------------------------------------------------------------- #
class CampaignBody(BaseModel):
    name: str = ""
    spec: dict = Field(default_factory=dict)


def _campaign_store(request: Request) -> CampaignLibraryStore:
    return CampaignLibraryStore(request.app.state.settings)


@router.get("/campaigns")
async def list_campaigns(request: Request) -> list[CampaignEntry]:
    return _campaign_store(request).list()


@router.post("/campaigns", status_code=201)
async def create_campaign(body: CampaignBody, request: Request) -> CampaignEntry:
    return _campaign_store(request).create(body.name, body.spec)


@router.get("/campaigns/{entry_id}")
async def get_campaign(entry_id: str, request: Request) -> CampaignEntry:
    return _campaign_store(request).get(entry_id)


@router.put("/campaigns/{entry_id}")
async def update_campaign(entry_id: str, body: CampaignBody, request: Request) -> CampaignEntry:
    return _campaign_store(request).update(entry_id, body.name, body.spec)


@router.post("/campaigns/{entry_id}/duplicate", status_code=201)
async def duplicate_campaign(entry_id: str, request: Request) -> CampaignEntry:
    return _campaign_store(request).duplicate(entry_id)


@router.delete("/campaigns/{entry_id}", status_code=204)
async def delete_campaign(entry_id: str, request: Request) -> None:
    _campaign_store(request).delete(entry_id)
