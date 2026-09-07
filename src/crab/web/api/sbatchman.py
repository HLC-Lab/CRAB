"""``/api/sbatchman`` — persist a composed campaign jobs YAML locally and push
it to the connected cluster (plan 084 S7). Composing the YAML itself is the
frontend's job (``lib/sbatchman.ts``, S4) — this module treats it as opaque
text. Launching, monitoring, and results are SbatchMan's own job; CRAB never
runs `sbatchman launch` (plan 085).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import RemoteConnectionError
from crab.web.remoteops.transfer import stage_text
from crab.web.store.campaign_library import CampaignEntry, CampaignLibraryStore
from crab.web.store.profiles import Profile, ProfileStore
from crab.web.store.sbatchman import save_campaign_yaml

router = APIRouter(prefix="/api/sbatchman", tags=["sbatchman"])

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    s = _NON_SLUG.sub("-", name.strip().lower()).strip("-")
    return s or "campaign"


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


class WriteRequest(BaseModel):
    profile_name: str
    yaml: str
    name: str = "campaign"


class WriteResponse(BaseModel):
    local_path: str
    remote_path: str


@router.post("/write", response_model=WriteResponse)
async def write_campaign(body: WriteRequest, request: Request) -> WriteResponse:
    transport = _live_transport(body.profile_name, request)
    profile = _profile(body.profile_name, request)
    settings = request.app.state.settings

    local_path = save_campaign_yaml(body.yaml, body.name, settings)
    remote_path = await stage_text(
        transport, profile, body.yaml, f"{_slug(body.name)}.yaml", settings=settings
    )
    return WriteResponse(local_path=str(local_path), remote_path=remote_path)


# --------------------------------------------------------------------------- #
# /api/sbatchman/campaigns — the local library of saved campaign drafts
# (plan 086). Pure local CRUD, no cluster involved; unlike /api/experiments,
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
