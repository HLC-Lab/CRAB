"""Stage an authored config on the target machine before submitting it.

The config JSON is written to a per-profile staging directory so
``crab run <staged> -p <preset> --json`` (Phase 4 submit) can read it without
inventing a separate upload mechanism. For an SSH profile that is
``<crab_dir>/.web_staging`` inside the existing CRAB checkout; for the
``local`` transport (no checkout concept — see ``crab_cli.build_crab_command``)
it's a ``web_staging`` folder under the backend's own data dir. Staged files
are not cleaned up automatically (kept for post-hoc debugging on the cluster).
"""

from __future__ import annotations

import json
import re

from crab.web.connections.transport import Transport
from crab.web.errors import RemoteCommandError
from crab.web.remoteops.crab_cli import crab_dir, remote_path_expr
from crab.web.settings import Settings, get_settings
from crab.web.store.profiles import Profile

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    s = _NON_SLUG.sub("-", name.strip().lower()).strip("-")
    return s or "config"


def staging_dir(profile: Profile, settings: Settings | None = None) -> str:
    """Where staged configs live for this profile (raw path, `~` preserved)."""
    if profile.is_local():
        s = settings or get_settings()
        return str(s.data_dir / "web_staging")
    return f"{crab_dir(profile)}/.web_staging"


async def stage_config(
    transport: Transport,
    profile: Profile,
    config: dict,
    name: str,
    *,
    settings: Settings | None = None,
    timeout: float = 30.0,
) -> str:
    """Write ``config`` to a JSON file in the staging dir; return its path.

    The returned path is raw (may contain a literal ``~`` for SSH profiles):
    it's passed straight to ``Transport.write_file`` (SFTP, which expands
    ``~`` itself) and is what a later ``crab run`` command should quote via
    ``remote_path_expr`` — never ``shlex.quote``, which would break the
    expansion (see ``remote_path_expr``'s docstring).
    """
    remote_dir = staging_dir(profile, settings)
    mkdir_cmd = f"mkdir -p {remote_path_expr(remote_dir)}"
    result = await transport.run(mkdir_cmd, timeout=timeout)
    if not result.ok:
        raise RemoteCommandError(
            f"Could not create the staging directory {remote_dir}.",
            detail=(result.stderr or result.stdout or "").strip()[:2000],
        )

    remote_path = f"{remote_dir}/{_slug(name)}.json"
    await transport.write_file(remote_path, json.dumps(config, indent=2), timeout=timeout)
    return remote_path
