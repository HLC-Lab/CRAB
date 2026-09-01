"""``/api/sbatchman`` — persist a composed campaign jobs YAML (locally + on the
cluster) and run ``sbatchman launch`` over the connected profile (plan 084 S7).
Composing the YAML itself is the frontend's job (``lib/sbatchman.ts``, S4) —
this module treats it as opaque text.
"""

from __future__ import annotations

import re
import shlex

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import RemoteConnectionError
from crab.web.remoteops.crab_cli import remote_path_expr
from crab.web.remoteops.transfer import stage_text
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


def _build_sbatchman_command(profile: Profile, remote_path: str) -> str:
    """``sbatchman launch -f <remote_path>``, run through the profile's login
    shell so ``remote_setup`` (e.g. module loads) and PATH (e.g. a pipx
    install) apply — mirrors ``crab_cli.build_crab_command``'s wrapping,
    without CRAB's own venv-activate step (SbatchMan is a separate tool, not
    necessarily installed into CRAB's venv).
    """
    launch = f"sbatchman launch -f {remote_path_expr(remote_path)}"
    if profile.is_local():
        return launch
    parts = [*profile.remote_setup, launch]
    inner = " && ".join(parts)
    return f"bash -lc {shlex.quote(inner)}"


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


class LaunchRequest(BaseModel):
    profile_name: str
    remote_path: str


class LaunchResponse(BaseModel):
    ok: bool
    stdout: str
    stderr: str


@router.post("/launch", response_model=LaunchResponse)
async def launch_campaign(body: LaunchRequest, request: Request) -> LaunchResponse:
    transport = _live_transport(body.profile_name, request)
    profile = _profile(body.profile_name, request)

    command = _build_sbatchman_command(profile, body.remote_path)
    result = await transport.run(command, timeout=60.0)
    return LaunchResponse(ok=result.ok, stdout=result.stdout, stderr=result.stderr)
