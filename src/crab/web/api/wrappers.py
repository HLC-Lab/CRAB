"""The Wrappers section's routes.

``/api/wrappers/{target}/list|show|status|pull|receipts``, each running the target's own CLI with
``--json`` (the engine decides). ``target`` is ``local`` (this laptop, always present, no
connection needed) or the name of a connected cluster profile. The name ``local`` is reserved for
the laptop (``check_profile_name`` refuses it for cluster profiles). Runs
``crab wrappers list|show|status|pull`` and ``crab receipts set``.
"""

from __future__ import annotations

import shlex
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from crab.web.api.local import _LOCAL_PROFILE
from crab.web.api.remotes import _live_transport, _store
from crab.web.connections.transport import LocalTransport, Transport
from crab.web.errors import (
    ConflictError,
    CrabOutdatedError,
    InputError,
    NotFoundError,
    RemoteCommandError,
)
from crab.web.remoteops.crab_cli import RemotePath, run_crab_json
from crab.web.store.profiles import Profile

# The paths are spelled in full: server.py includes the router once, without a prefix.
router = APIRouter(tags=["wrappers"])

_LOCAL_TARGET = "local"
_MAX_RELPATH = 512


class ReceiptImport(BaseModel):
    # Same rule as `crab receipts set` (the id becomes a file name on the cluster).
    id: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", max_length=64)
    binary: str = Field(min_length=1, max_length=4096)
    pre_run: list[str] = Field(default_factory=list, max_length=20)
    launcher: Literal["", "srun", "mpirun"] = ""


def _check_binary(binary: str) -> None:
    """Refuse a relative path: it would be resolved in the remote's folder, not the person's.

    One word with a `/` that starts with neither `/` nor `~` is relative. Command names, absolute
    and `~` paths and launcher lines of several words pass.

    Raises:
        InputError: the binary is a relative path.
    """
    try:
        tokens = shlex.split(binary)
    except ValueError:
        tokens = binary.split()
    if len(tokens) == 1 and "/" in tokens[0] and not tokens[0].startswith(("/", "~")):
        raise InputError(
            f"{tokens[0]!r} is a relative path: give the full path to the binary on that machine."
        )


def _receipt_args(body: ReceiptImport) -> list[str | RemotePath]:
    """The `crab receipts set` arguments for an import. Raises InputError for a relative binary."""
    _check_binary(body.binary)
    args: list[str | RemotePath] = ["receipts", "set", body.id, "--binary", RemotePath(body.binary)]
    for command in body.pre_run:
        if command.strip():
            args += ["--pre-run", command.strip()]
    if body.launcher:
        args += ["--launcher", body.launcher]
    return [*args, "--json"]


# --- /api/wrappers/{target}/...: this laptop or a connected cluster ---


def _local_transport() -> Transport:
    """The transport for the `local` target (a seam: tests swap in a fake)."""
    return LocalTransport()


def _target(target: str, request: Request) -> tuple[Transport, Profile]:
    """`local` is this laptop (no profile store, no connection); anything else is a cluster.

    Raises NotFoundError for an unknown profile and RemoteConnectionError when the cluster is
    not connected.
    """
    if target == _LOCAL_TARGET:
        return _local_transport(), _LOCAL_PROFILE
    profile = _store(request).get(target)
    return _live_transport(target, request), profile


def _crab_line(detail: str, prefix: str) -> str | None:
    """The first line of `detail` starting with `prefix`, without the `crab: ` lead.

    A login shell can print module/MOTD lines to stderr before crab's own line.
    """
    for line in detail.splitlines():
        if line.startswith(prefix):
            return line.removeprefix("crab: ").strip()
    return None


async def _run(
    target: str,
    request: Request,
    args: list[str | RemotePath],
    *,
    timeout: float,
    subcommand: str | None = None,
) -> dict:
    """Run `crab <args>` on the target and map the engine's known refusals.

    `subcommand` is the `crab wrappers <name>` being called: an older crab answers it with
    argparse's "invalid choice", which becomes `crab_outdated`.
    """
    transport, profile = _target(target, request)
    try:
        return await run_crab_json(transport, profile, args, timeout=timeout)
    except RemoteCommandError as exc:
        detail = exc.detail or ""
        outdated = subcommand and (
            f"invalid choice: '{subcommand}'" in detail or "invalid choice: 'wrappers'" in detail
        )
        if outdated:
            where = "This laptop's" if target == _LOCAL_TARGET else "This cluster's"
            raise CrabOutdatedError(
                f"{where} crab has no 'crab wrappers {subcommand}'. "
                "Update it there (crab update), then retry.",
                detail=detail,
            ) from exc
        if subcommand == "show":
            line = _crab_line(detail, "crab: wrapper not found")
            if line:
                raise NotFoundError(line, detail=detail) from exc
        if subcommand == "pull":
            line = _crab_line(detail, "crab: wrappers pull stopped:")
            if line and "has local edits" in line:
                raise ConflictError(line, detail=detail) from exc
        raise


def _check_relpath(relpath: str) -> str:
    """A wrapper path relative to the wrappers folders: no absolute path, no `..` part."""
    if not relpath or len(relpath) > _MAX_RELPATH:
        raise InputError(f"relpath must be 1 to {_MAX_RELPATH} characters.")
    if relpath.startswith(("/", "-")) or ".." in relpath.split("/"):
        raise InputError(
            "relpath must be relative to the wrappers folders (no leading '/' or '-', no '..')."
        )
    if any(ord(c) < 32 or c == "\x7f" for c in relpath):
        raise InputError("relpath must not contain control characters.")
    return relpath


@router.get("/api/wrappers/{target}/list")
async def wrappers_list(target: str, request: Request) -> dict:
    """`crab wrappers list --json` on the target.

    Listing loads every wrapper module, so allow a generous timeout.
    """
    return await _run(target, request, ["wrappers", "list", "--json"], timeout=90.0)


@router.get("/api/wrappers/{target}/show")
async def wrappers_show(target: str, request: Request, relpath: str = Query()) -> dict:
    """`crab wrappers show <relpath> --json` on the target."""
    checked = _check_relpath(relpath)
    return await _run(
        target,
        request,
        ["wrappers", "show", checked, "--json"],
        timeout=60.0,
        subcommand="show",
    )


@router.get("/api/wrappers/{target}/status")
async def wrappers_status(target: str, request: Request) -> dict:
    """`crab wrappers status --json` on the target (runs `git ls-remote`, 20 s cap there)."""
    return await _run(
        target, request, ["wrappers", "status", "--json"], timeout=45.0, subcommand="status"
    )


@router.post("/api/wrappers/{target}/pull")
async def wrappers_pull(target: str, request: Request) -> dict:
    """`crab wrappers pull --json` on the target; local edits to tracked files refuse (409)."""
    return await _run(
        target, request, ["wrappers", "pull", "--json"], timeout=180.0, subcommand="pull"
    )


@router.post("/api/wrappers/{target}/receipts")
async def wrappers_import_binary(target: str, body: ReceiptImport, request: Request) -> dict:
    """Record where a benchmark's binary lives on the target (`crab receipts set`)."""
    return await _run(target, request, _receipt_args(body), timeout=30.0)
