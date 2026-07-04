"""Build and run the cluster's ``crab … --json`` over a Transport.

Non-interactive SSH shells don't activate venvs or source rc files, so for a
remote profile we explicitly ``cd`` into the CRAB repo, source its venv, and
run ``crab`` — all under ``bash -lc`` so module systems and login config load.
For a local profile we invoke the same interpreter that runs the backend.
"""

from __future__ import annotations

import json
import shlex
import sys
from typing import Any

from crab.web.connections.transport import Transport
from crab.web.errors import ContractError, RemoteCommandError
from crab.web.store.profiles import Profile


def remote_path_expr(path: str) -> str:
    """Quote a remote path while preserving ``~``/``$HOME`` expansion.

    ``shlex.quote('~/CRAB')`` would stop tilde expansion, so leading ``~`` is
    rewritten to ``$HOME`` inside double quotes (expanded by the remote bash).
    """
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME/' + path[2:].replace('"', '\\"') + '"'
    return shlex.quote(path)


def crab_dir(profile: Profile) -> str:
    """The CRAB repo directory: a ``CRAB`` subfolder of the profile's base dir.

    ``remote_crab`` is the *base* directory (default ``~``); CRAB always lives at
    ``<base>/CRAB``, so the dashboard can create that subfolder on install and
    find it again on every command.
    """
    base = profile.remote_crab.rstrip("/") or "~"
    return f"{base}/CRAB"


def build_crab_command(profile: Profile, args: list[str]) -> str:
    """Return the shell command that runs ``crab <args>`` for this profile.

    Args are quoted with ``remote_path_expr`` (not plain ``shlex.quote``): a
    staged config path (``remoteops/transfer.py``) may itself start with
    ``~``, and quoting a leading ``~`` would break its expansion the same way
    it would for ``crab_dir``/``venv`` below. ``remote_path_expr`` behaves
    exactly like ``shlex.quote`` for any arg that isn't a tilde path.
    """
    crab_args = " ".join(remote_path_expr(a) for a in args)

    if profile.is_local():
        # The backend already runs inside CRAB's environment.
        return f"{shlex.quote(sys.executable)} -m crab {crab_args}"

    parts: list[str] = list(profile.remote_setup)
    cdir = crab_dir(profile)
    venv = profile.venv_activate or f"{cdir}/.venv/bin/activate"
    parts.append(f"cd {remote_path_expr(cdir)}")
    parts.append(f". {remote_path_expr(venv)}")
    parts.append(f"crab {crab_args}")
    inner = " && ".join(parts)
    # Wrap so the remote login shell hands `inner` to a login bash verbatim.
    return f"bash -lc {shlex.quote(inner)}"


async def run_crab_json(
    transport: Transport, profile: Profile, args: list[str], timeout: float = 30.0
) -> Any:
    """Run ``crab <args>`` (which must include ``--json``) and parse the result."""
    command = build_crab_command(profile, args)
    result = await transport.run(command, timeout=timeout)

    if not result.ok:
        raise RemoteCommandError(
            f"`crab {' '.join(args)}` failed on the cluster (exit {result.rc}).",
            detail=(result.stderr or result.stdout or "").strip()[:2000],
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError(
            "Could not parse JSON from the cluster's `crab` output — the remote "
            "CRAB may be missing, too old, or not reachable via the configured "
            "path/venv.",
            detail=f"{exc}; stdout[:500]={result.stdout[:500]!r}; "
            f"stderr[:500]={result.stderr[:500]!r}",
        ) from exc
