"""Stage an authored config on the target machine before submitting it.

The config JSON is written to a per-profile staging directory so
``crab run <staged>`` can read it without
inventing a separate upload mechanism. For an SSH profile that is
``<crab_dir>/.web_staging`` inside the existing CRAB checkout (``<remote_crab>/.crab_web_staging``
when the profile names its own crab command, which has no checkout); for the
``local`` transport (no checkout concept: see ``crab_cli.build_crab_command``)
it's a ``web_staging`` folder under the backend's own data dir. Staged files
are not cleaned up automatically (kept for post-hoc debugging on the cluster).
"""

from __future__ import annotations

import json
import re
import secrets
import shlex

from crab.web.connections.transport import Transport
from crab.web.errors import ConflictError, CrabWebError, RemoteCommandError
from crab.web.remoteops.crab_cli import crab_dir, output_tail, remote_path_expr
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
    if profile.crab_command is not None:
        # No <base>/CRAB checkout to put it in, and none is created just for staging.
        return f"{profile.remote_crab.rstrip('/') or '~'}/.crab_web_staging"
    return f"{crab_dir(profile)}/.web_staging"


async def _resolve_staging_dir(
    transport: Transport,
    profile: Profile,
    settings: Settings | None,
    timeout: float,
) -> str:
    """Resolve the staging dir to an absolute path, creating it if needed.

    ``staging_dir`` may contain a literal ``~`` for SSH profiles. Only a shell
    can expand that (``mkdir -p "$HOME/..."`` via ``remote_path_expr``), but
    a write goes over SFTP, which has no shell and does no expansion of its
    own: so mkdir and the write could target different trees if each handled
    ``~`` separately. Instead, one shell round-trip resolves the directory to
    an absolute path (``cd ... && pwd``) that mkdir and every write share.
    """
    remote_dir = staging_dir(profile, settings)
    quoted_dir = remote_path_expr(remote_dir)
    resolve_cmd = f"mkdir -p {quoted_dir} && cd {quoted_dir} && pwd"
    result = await transport.run(resolve_cmd, timeout=timeout)
    if not result.ok:
        raise RemoteCommandError(
            f"Could not create the staging directory {remote_dir}.",
            detail=output_tail(result),
        )
    # A login shell can print a banner or module messages before `pwd` runs: the
    # directory is the last non-empty line.
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    absolute_dir = lines[-1] if lines else ""
    if not absolute_dir.startswith("/"):
        raise RemoteCommandError(
            f"Could not resolve the staging directory {remote_dir}.",
            detail=output_tail(result),
        )
    return absolute_dir


async def stage_config(
    transport: Transport,
    profile: Profile,
    config: dict,
    name: str,
    *,
    settings: Settings | None = None,
    timeout: float = 30.0,
) -> str:
    """Write ``config`` to a JSON file in the staging dir; return its absolute path."""
    absolute_dir = await _resolve_staging_dir(transport, profile, settings, timeout)
    remote_path = f"{absolute_dir}/{_slug(name)}.json"
    await transport.write_file(remote_path, json.dumps(config, indent=2), timeout=timeout)
    return remote_path


async def stage_text(
    transport: Transport,
    profile: Profile,
    text: str,
    filename: str,
    *,
    overwrite: bool = True,
    settings: Settings | None = None,
    timeout: float = 30.0,
) -> str:
    """Write raw ``text`` to ``filename`` in the staging dir; return its absolute
    path. Same directory/resolution as ``stage_config``, for content that isn't
    a CRAB config JSON blob (e.g. a composed SbatchMan campaign YAML).

    With ``overwrite=False`` an existing file of that name raises ``ConflictError``
    and nothing is written. The text goes to a unique temporary name first, gets
    mode 600 whatever the umask, and is then moved onto the final name, so a failed
    write never leaves a half file under the real name.
    """
    absolute_dir = await _resolve_staging_dir(transport, profile, settings, timeout)
    remote_path = f"{absolute_dir}/{filename}"
    if not overwrite:
        exists = await transport.run(f"test -e {shlex.quote(remote_path)}", timeout=timeout)
        # `test` answers 0 (exists) or 1 (absent). Any other code (ssh itself failing, say)
        # says nothing about the file, so nothing is written on the strength of it.
        if exists.rc == 0:
            raise ConflictError(
                f"A campaign file named {filename} already exists on the cluster. "
                "Write again to replace it."
            )
        if exists.rc != 1:
            raise RemoteCommandError(
                f"Could not check whether {filename} already exists on the cluster.",
                detail=output_tail(exists),
            )
    temp_path = f"{remote_path}.tmp-{secrets.token_hex(4)}"
    try:
        # The SFTP create uses the server's umask, so the content is readable by whoever that
        # allows until the chmod below runs.
        await transport.write_file(temp_path, text, timeout=timeout)
        for command, what in (
            (f"chmod 600 {shlex.quote(temp_path)}", "set the permissions of"),
            (f"mv -f {shlex.quote(temp_path)} {shlex.quote(remote_path)}", "move"),
        ):
            result = await transport.run(command, timeout=timeout)
            if not result.ok:
                raise RemoteCommandError(
                    f"Could not {what} the staged file {filename}.", detail=output_tail(result)
                )
    except BaseException:
        # Best effort, and also when cancelled: the connection may be the very thing that
        # failed. The original error (or the cancellation) keeps propagating.
        try:
            await transport.run(f"rm -f {shlex.quote(temp_path)}", timeout=timeout)
        except CrabWebError:
            pass
        raise
    return remote_path
