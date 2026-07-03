"""``/api/local``, introspect the CRAB checkout on the machine running
``crab web`` itself.

Unlike ``/api/remotes/{name}/benchmarks`` (over SSH to a cluster), this runs
``crab list-benchmarks --json`` as a plain local subprocess on the host (no
SSH, no stored profile, no live connection required). Reuses the exact same
contract (``cli/contract.py``) and command-building path (``LocalTransport`` +
``run_crab_json``) already proven by the ``local`` transport profile. Backs the
wrapper picker's local half (see ``.crab-web-dev/14-authoring-polish-design.md`` §9).
"""

from __future__ import annotations

from fastapi import APIRouter

from crab.web.connections.transport import LocalTransport
from crab.web.remoteops.crab_cli import run_crab_json
from crab.web.store.profiles import Profile

router = APIRouter(prefix="/api/local", tags=["local"])

# Stateless: a fresh LocalTransport + a minimal local Profile are enough to
# build and run `python -m crab list-benchmarks --json` (see crab_cli.py's
# `build_crab_command`, which special-cases `profile.is_local()`).
_LOCAL_PROFILE = Profile(name="local", transport="local")


@router.get("/benchmarks")
async def local_benchmarks() -> dict:
    """`crab list-benchmarks --json` on the machine running crab web.

    Introspection runs every wrapper module, so allow the same generous
    timeout as the remote path.
    """
    return await run_crab_json(
        LocalTransport(), _LOCAL_PROFILE, ["list-benchmarks", "--json"], timeout=90.0
    )
