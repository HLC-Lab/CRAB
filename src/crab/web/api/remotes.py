"""``/api/remotes`` — manage cluster profiles and live connections.

Connecting opens (or reuses) the SSH/local transport and immediately runs
``crab info --json`` as a handshake, so the UI gets the remote's version and
presets in one round-trip. All failures surface as the stable error envelope.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crab.web.errors import RemoteConnectionError
from crab.web.remoteops.crab_cli import run_crab_json
from crab.web.store.profiles import Profile, ProfileStore

router = APIRouter(prefix="/api/remotes", tags=["remotes"])


class ConnectRequest(BaseModel):
    password: str | None = None


def _store(request: Request) -> ProfileStore:
    return ProfileStore(request.app.state.settings)


def _manager(request: Request):
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise RemoteConnectionError("Connection manager is not initialised.")
    return manager


@router.get("")
async def list_remotes(request: Request) -> list[dict]:
    manager = getattr(request.app.state, "manager", None)
    out: list[dict] = []
    for profile in _store(request).list():
        data = profile.model_dump()
        data["connected"] = bool(manager and manager.is_connected(profile.name))
        out.append(data)
    return out


@router.post("", status_code=201)
async def add_remote(profile: Profile, request: Request) -> dict:
    return _store(request).add(profile).model_dump()


@router.put("/{name}")
async def update_remote(name: str, profile: Profile, request: Request) -> dict:
    return _store(request).update(name, profile).model_dump()


@router.delete("/{name}", status_code=204)
async def remove_remote(name: str, request: Request) -> None:
    manager = getattr(request.app.state, "manager", None)
    if manager:
        await manager.disconnect(name)
    _store(request).remove(name)


@router.post("/{name}/connect")
async def connect_remote(
    name: str, request: Request, body: ConnectRequest | None = None
) -> dict:
    profile = _store(request).get(name)
    transport = await _manager(request).connect(
        profile, password=(body.password if body else None)
    )
    info = await run_crab_json(transport, profile, ["info", "--json"])
    return {"connected": True, "info": info}


@router.post("/{name}/disconnect", status_code=204)
async def disconnect_remote(name: str, request: Request) -> None:
    manager = getattr(request.app.state, "manager", None)
    if manager:
        await manager.disconnect(name)
