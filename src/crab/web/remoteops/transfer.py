"""Stage an authored config on the target machine before submitting it.

The config JSON is written to a per-profile staging directory so
``crab run <staged>`` can read it without
inventing a separate upload mechanism. (``stage_text`` can write into any other directory
instead, such as an umbrella install's ``benchmarks/<name>/``.) For an SSH profile that is
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
    directory: str | None = None,
) -> str:
    """Resolve the staging dir (or ``directory``, when given) to an absolute path,
    creating it if needed.

    The directory may contain a literal ``~`` for SSH profiles. Only a shell
    can expand that (``mkdir -p "$HOME/..."`` via ``remote_path_expr``), but
    a write goes over SFTP, which has no shell and does no expansion of its
    own: so mkdir and the write could target different trees if each handled
    ``~`` separately. Instead, one shell round-trip resolves the directory to
    an absolute path (``cd ... && pwd``) that mkdir and every write share. The staging
    dir (not a ``directory`` the caller names) is also made private, see below.
    """
    remote_dir = directory if directory is not None else staging_dir(profile, settings)
    quoted_dir = remote_path_expr(remote_dir)
    what = "the staging directory" if directory is None else "the folder"
    if directory is None:
        # 700: the staged files hold the user's campaign, so no other account may read them.
        # Best effort: in a shared checkout another account may own the folder, and chmod
        # then fails; staging must still work, so only pwd decides the exit code. A folder
        # the caller names (an umbrella `benchmarks/<name>/`) is not ours to restrict.
        resolve_cmd = f"mkdir -p {quoted_dir} && cd {quoted_dir} && (chmod 700 . ; pwd)"
    else:
        resolve_cmd = f"mkdir -p {quoted_dir} && cd {quoted_dir} && pwd"
    result = await transport.run(resolve_cmd, timeout=timeout)
    if not result.ok:
        raise RemoteCommandError(
            f"Could not create {what} {remote_dir}.",
            detail=output_tail(result),
        )
    # A login shell can print a banner or module messages before `pwd` runs: the
    # directory is the last non-empty line.
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    absolute_dir = lines[-1] if lines else ""
    if not absolute_dir.startswith("/"):
        raise RemoteCommandError(
            f"Could not resolve {what} {remote_dir}.",
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
    directory: str | None = None,
    settings: Settings | None = None,
    timeout: float = 30.0,
) -> str:
    """Write raw ``text`` to ``filename`` in the staging dir; return its absolute
    path. Same directory/resolution as ``stage_config``, for content that isn't
    a CRAB config JSON blob (e.g. a composed SbatchMan campaign YAML). ``directory``
    (a raw path, ``~`` allowed) replaces the staging dir; it is created if missing.

    With ``overwrite=False`` an existing file of that name raises ``ConflictError``
    and nothing is replaced. The text goes to a unique temporary name first and gets
    mode 600 whatever the umask. It then reaches the final name by ``mv -f`` (overwrite)
    or by a hard link (no overwrite). ``ln`` fails atomically when the name is taken, so
    a file that appears between the request and the write is never clobbered, which a
    separate ``test -e`` followed by ``mv`` could not promise. Either way a failed write
    never leaves a half file under the real name.
    """
    absolute_dir = await _resolve_staging_dir(
        transport, profile, settings, timeout, directory=directory
    )
    remote_path = f"{absolute_dir}/{filename}"
    temp_path = f"{remote_path}.tmp-{secrets.token_hex(4)}"
    quoted_temp = shlex.quote(temp_path)
    quoted_final = shlex.quote(remote_path)
    try:
        # The SFTP create uses the server's umask, so the content is readable by whoever that
        # allows until the chmod below runs.
        await transport.write_file(temp_path, text, timeout=timeout)
        result = await transport.run(f"chmod 600 {quoted_temp}", timeout=timeout)
        if not result.ok:
            raise RemoteCommandError(
                f"Could not set the permissions of the staged file {filename}.",
                detail=output_tail(result),
            )
        if overwrite:
            result = await transport.run(f"mv -f {quoted_temp} {quoted_final}", timeout=timeout)
            if not result.ok:
                raise RemoteCommandError(
                    f"Could not move the staged file {filename}.", detail=output_tail(result)
                )
        else:
            result = await transport.run(f"ln -- {quoted_temp} {quoted_final}", timeout=timeout)
            if not result.ok:
                # `test` answers 0 (exists) or 1 (absent). Any other code (ssh itself failing,
                # say) says nothing about the file, so it must not read as a conflict.
                exists = await transport.run(f"test -e {quoted_final}", timeout=timeout)
                if exists.rc == 0:
                    raise ConflictError(
                        f"A campaign file named {filename} already exists on the cluster. "
                        "Write again to replace it."
                    )
                raise RemoteCommandError(
                    f"Could not move the staged file {filename} into place.",
                    detail=output_tail(exists if exists.rc != 1 else result),
                )
    except BaseException:
        # Best effort, and also when cancelled: the connection may be the very thing that
        # failed. The original error (or the cancellation) keeps propagating.
        try:
            await transport.run(f"rm -f {quoted_temp}", timeout=timeout)
        except CrabWebError:
            pass
        raise
    if not overwrite:
        # The file is in place under the final name; the second link to it is only litter.
        try:
            await transport.run(f"rm -f {quoted_temp}", timeout=timeout)
        except CrabWebError:
            pass
    return remote_path
