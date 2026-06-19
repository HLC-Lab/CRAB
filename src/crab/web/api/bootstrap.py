"""``/api/remotes/{name}/bootstrap/*`` — guided CRAB install on a remote (D5).

The flow is stateless: ``plan`` reports whether CRAB is present and the (fixed)
steps to install it; ``run`` executes one step (rebuilding its command
server-side from the step id + the user's pre-commands, so structural commands
can't be tampered with); ``verify`` re-checks. All three need an already-open
connection — connecting to a CRAB-less cluster succeeds at the SSH level even
though its ``crab info`` handshake reports a contract error, so the transport is
available here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from crab.web.errors import RemoteConnectionError
from crab.web.remoteops.bootstrap import (
    BootstrapStep,
    DetectResult,
    StepResult,
    default_plan,
    detect,
    run_step,
)
from crab.web.store.profiles import ProfileStore

router = APIRouter(prefix="/api/remotes", tags=["bootstrap"])


class PlanResponse(BaseModel):
    installed: bool
    info: dict | None = None
    reason: str | None = None
    pre_commands: list[str]
    steps: list[BootstrapStep]


class RunRequest(BaseModel):
    step_id: str
    pre_commands: list[str] = Field(default_factory=list)


def _store(request: Request) -> ProfileStore:
    return ProfileStore(request.app.state.settings)


def _connected_transport(request: Request, name: str):
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
    transport = _connected_transport(request, name)
    result = await detect(transport, profile)
    return PlanResponse(
        installed=result.installed,
        info=result.info,
        reason=result.reason,
        pre_commands=profile.remote_setup,
        steps=default_plan(profile),
    )


@router.post("/{name}/bootstrap/run")
async def bootstrap_run(name: str, body: RunRequest, request: Request) -> StepResult:
    profile = _store(request).get(name)
    transport = _connected_transport(request, name)
    return await run_step(transport, profile, body.step_id, body.pre_commands)


@router.post("/{name}/bootstrap/verify")
async def bootstrap_verify(name: str, request: Request) -> DetectResult:
    profile = _store(request).get(name)
    transport = _connected_transport(request, name)
    return await detect(transport, profile)
