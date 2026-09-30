"""``/api/remotes/{name}/wrappers`` and ``/receipts`` — the Wrappers page.

Lists a connected cluster's wrappers with where each binary comes from (config, receipt, PATH
or missing), and imports a missing binary by writing a receipt there. Both run the cluster's
own CLI (`crab wrappers list --json`, `crab receipts set ... --json`); the engine decides.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from crab.web.api.remotes import _live_transport, _store
from crab.web.remoteops.crab_cli import run_crab_json

router = APIRouter(prefix="/api/remotes", tags=["wrappers"])


class ReceiptImport(BaseModel):
    # Same rule as `crab receipts set` (the id becomes a file name on the cluster).
    id: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", max_length=64)
    binary: str = Field(min_length=1, max_length=4096)
    pre_run: list[str] = Field(default_factory=list, max_length=20)
    launcher: Literal["", "srun", "mpirun"] = ""


@router.get("/{name}/wrappers")
async def remote_wrappers(name: str, request: Request) -> dict:
    """`crab wrappers list --json` on the connected cluster.

    Listing loads every wrapper module on the login node, so allow a generous timeout.
    """
    profile = _store(request).get(name)
    transport = _live_transport(name, request)
    return await run_crab_json(transport, profile, ["wrappers", "list", "--json"], timeout=90.0)


@router.post("/{name}/receipts")
async def import_binary(name: str, body: ReceiptImport, request: Request) -> dict:
    """Record where a benchmark's binary lives on the cluster (`crab receipts set`)."""
    profile = _store(request).get(name)
    transport = _live_transport(name, request)
    args = ["receipts", "set", body.id, "--binary", body.binary]
    for command in body.pre_run:
        if command.strip():
            args += ["--pre-run", command.strip()]
    if body.launcher:
        args += ["--launcher", body.launcher]
    return await run_crab_json(transport, profile, [*args, "--json"], timeout=30.0)
