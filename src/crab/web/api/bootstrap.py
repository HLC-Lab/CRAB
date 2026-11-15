"""``/api/remotes/{name}/bootstrap/*``: guided CRAB install on a remote (D5).

The flow is stateless: ``plan`` reports whether CRAB is present and previews the
fixed install commands; ``install`` runs the whole install in one shell (the
commands are built server-side, so only the optional pre-commands are user
supplied); ``verify`` re-checks. All three need an already-open connection.
Connecting to a CRAB-less cluster still succeeds at the SSH level even though
its ``crab info`` handshake fails, so the transport is available here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from crab.cli.progress import stage
from crab.web.connections.transport import Transport
from crab.web.errors import RemoteConnectionError
from crab.web.progress import watching
from crab.web.remoteops.bootstrap import (
    DETECT_TIMEOUT_S,
    INSTALL_TIMEOUT,
    BootstrapStep,
    DetectResult,
    StepResult,
    default_plan,
    detect,
    ensure_installable,
    install,
)
from crab.web.store.profiles import ProfileStore

router = APIRouter(prefix="/api/remotes", tags=["bootstrap"])


class PlanResponse(BaseModel):
    installed: bool
    info: dict | None = None
    reason: str | None = None
    pre_commands: list[str]
    steps: list[BootstrapStep]


class InstallRequest(BaseModel):
    pre_commands: list[str] = Field(default_factory=list)


def _store(request: Request) -> ProfileStore:
    return ProfileStore(request.app.state.settings)


def _connected_transport(request: Request, name: str) -> Transport:
    manager = getattr(request.app.state, "manager", None)
    transport = manager.get(name) if manager else None
    if transport is None:
        raise RemoteConnectionError(
            f"Not connected to {name!r}. Connect to the cluster first, then set up CRAB."
        )
    return transport


@router.post("/{name}/bootstrap/plan")
async def bootstrap_plan(name: str, request: Request) -> PlanResponse:
    profile = _store(request).get(name)
    ensure_installable(profile)
    transport = _connected_transport(request, name)
    result = await detect(transport, profile)
    return PlanResponse(
        installed=result.installed,
        info=result.info,
        reason=result.reason,
        pre_commands=profile.remote_setup,
        steps=default_plan(profile),
    )


@router.post("/{name}/bootstrap/install")
async def bootstrap_install(name: str, body: InstallRequest, request: Request) -> StepResult:
    profile = _store(request).get(name)
    ensure_installable(profile)
    transport = _connected_transport(request, name)
    with (
        watching(request, name),
        stage(
            "install",
            "Installing CRAB on the cluster",
            "the install commands (git clone, then pip install) to finish on the login node",
            INSTALL_TIMEOUT,
        ),
    ):
        return await install(transport, profile, body.pre_commands)


@router.post("/{name}/bootstrap/verify")
async def bootstrap_verify(name: str, request: Request) -> DetectResult:
    profile = _store(request).get(name)
    transport = _connected_transport(request, name)
    with (
        watching(request, name),
        stage(
            "verify",
            "Checking the install",
            "`crab info --json` to report CRAB's version and presets",
            DETECT_TIMEOUT_S,
        ),
    ):
        return await detect(transport, profile)
