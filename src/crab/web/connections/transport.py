"""Transport abstraction: run a command on a remote (SSH) or local host.

A ``Transport`` exposes one uniform ``run(command) -> CmdResult`` so the rest of
the backend is identical whether the cluster is reached over SSH or is the
local machine. ``SSHTransport`` wraps a single long-lived ``asyncssh``
connection (channels are multiplexed over it, so one auth per session).

asyncssh is imported lazily inside the factory so this module — and the unit
tests that inject a fake transport — stay importable without the web extra.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from crab.web.errors import AuthError, RemoteCommandError, RemoteConnectionError

if TYPE_CHECKING:  # pragma: no cover
    import asyncssh

    from crab.web.store.profiles import Profile

DEFAULT_TIMEOUT = 30.0


@dataclass
class CmdResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class Transport:
    """Interface implemented by SSHTransport, LocalTransport, and test fakes."""

    is_local: bool = False

    @property
    def alive(self) -> bool:  # pragma: no cover - trivial
        raise NotImplementedError

    async def run(self, command: str, timeout: float | None = DEFAULT_TIMEOUT) -> CmdResult:
        raise NotImplementedError

    async def write_file(
        self, path: str, content: str, timeout: float | None = DEFAULT_TIMEOUT
    ) -> None:
        raise NotImplementedError

    async def fetch_tree(
        self, remote_dir: str, local_dir: str, timeout: float | None = DEFAULT_TIMEOUT
    ) -> None:
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - trivial
        raise NotImplementedError


class SSHTransport(Transport):
    """A live asyncssh connection to one cluster."""

    is_local = False

    def __init__(self, conn: asyncssh.SSHClientConnection) -> None:
        self._conn = conn
        self._closed = False

    @property
    def alive(self) -> bool:
        # We track liveness ourselves rather than poking asyncssh internals:
        # close() and detected drops flip this flag.
        return not self._closed

    async def run(self, command: str, timeout: float | None = DEFAULT_TIMEOUT) -> CmdResult:
        try:
            result = await asyncio.wait_for(self._conn.run(command, check=False), timeout=timeout)
        except asyncio.TimeoutError:
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"Remote command timed out after {timeout:g}s.",
                detail=command,
            )
        except Exception as exc:  # asyncssh ChannelOpenError, ConnectionLost, ...
            self._closed = True  # treat any channel error as a drop
            raise RemoteConnectionError(
                "The SSH connection dropped while running a command.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        out, err = result.stdout or "", result.stderr or ""
        return CmdResult(
            rc=result.exit_status if result.exit_status is not None else -1,
            stdout=out.decode("utf-8", "replace") if isinstance(out, bytes) else out,
            stderr=err.decode("utf-8", "replace") if isinstance(err, bytes) else err,
        )

    async def write_file(
        self, path: str, content: str, timeout: float | None = DEFAULT_TIMEOUT
    ) -> None:
        import asyncssh

        async def _write() -> None:
            async with self._conn.start_sftp_client() as sftp:
                async with sftp.open(path, "w") as f:
                    await f.write(content)

        try:
            await asyncio.wait_for(_write(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"Writing {path} timed out after {timeout:g}s.",
                detail=path,
            )
        except asyncssh.SFTPError as exc:
            # A file/permission error, not a dropped connection (e.g. the
            # parent directory doesn't exist yet) — the connection stays alive.
            raise RemoteCommandError(f"Could not write {path} over SFTP.", detail=str(exc)) from exc
        except Exception as exc:  # asyncssh ChannelOpenError, ConnectionLost, ...
            self._closed = True  # treat any other SFTP/channel error as a drop
            raise RemoteConnectionError(
                "The SSH connection dropped while writing a file.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

    async def fetch_tree(
        self, remote_dir: str, local_dir: str, timeout: float | None = DEFAULT_TIMEOUT
    ) -> None:
        import asyncssh

        async def _fetch() -> None:
            async with self._conn.start_sftp_client() as sftp:
                await sftp.get(remote_dir, local_dir, recurse=True)

        try:
            await asyncio.wait_for(_fetch(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"Fetching {remote_dir} timed out after {timeout:g}s.",
                detail=remote_dir,
            )
        except asyncssh.SFTPError as exc:
            # A file/permission error, not a dropped connection (e.g. the
            # remote directory doesn't exist) — the connection stays alive.
            raise RemoteCommandError(
                f"Could not fetch {remote_dir} over SFTP.", detail=str(exc)
            ) from exc
        except Exception as exc:  # asyncssh ChannelOpenError, ConnectionLost, ...
            self._closed = True  # treat any other SFTP/channel error as a drop
            raise RemoteConnectionError(
                "The SSH connection dropped while fetching results.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

    async def close(self) -> None:
        self._closed = True
        try:
            self._conn.close()
            await self._conn.wait_closed()
        except Exception:  # pragma: no cover - best-effort teardown
            pass


async def connect_ssh(profile: Profile, password: str | None = None) -> SSHTransport:
    """Open an asyncssh connection for ``profile``, mapping failures to our errors.

    Auth:
      * 'agent'    — uses the inherited SSH_AUTH_SOCK (Leonardo's step-cli cert,
                     or ordinary keys-in-agent). No secret needed.
      * 'key'      — explicit private key file.
      * 'password' — transient password (never persisted).
    Host key:
      * 'strict'   — verify against the system known_hosts.
      * 'insecure' — no verification (required for round-robin login nodes).
    """
    import asyncssh

    if not profile.host:
        raise RemoteConnectionError("Profile has no host to connect to.")

    opts: dict = {
        "host": profile.host,
        "port": profile.port,
        "username": profile.user,
        # 'insecure' disables host-key checking; otherwise asyncssh loads the
        # user's ~/.ssh/known_hosts by default.
        "known_hosts": None if profile.hostkey_policy == "insecure" else (),
    }

    if profile.auth == "agent":
        sock = os.environ.get("SSH_AUTH_SOCK")
        if not sock:
            raise AuthError(
                "No SSH agent found (SSH_AUTH_SOCK is unset). Start ssh-agent and "
                "load your identity (e.g. run `step-cli ssh login …` for Leonardo), "
                "then launch `crab web` from that same shell."
            )
        opts["agent_path"] = sock
        opts["client_keys"] = []  # force agent-only, don't probe ~/.ssh keys
    elif profile.auth == "key":
        if not profile.key_path:
            raise AuthError("Auth method 'key' selected but no key_path is set.")
        opts["client_keys"] = [os.path.expanduser(profile.key_path)]
        opts["agent_path"] = None
    elif profile.auth == "password":
        if not password:
            raise AuthError("This cluster needs a password; none was provided.")
        opts["password"] = password
        opts["agent_path"] = None
        opts["client_keys"] = []

    try:
        conn = await asyncssh.connect(**opts)
    except asyncssh.PermissionDenied as exc:
        raise AuthError(
            "Authentication was rejected by the cluster. For Leonardo, your "
            "certificate may have expired — re-run `step-cli ssh login …` into "
            "the same ssh-agent, then reconnect.",
            detail=str(exc),
        ) from exc
    except (asyncssh.HostKeyNotVerifiable, asyncssh.KeyExchangeFailed) as exc:
        raise RemoteConnectionError(
            "Host key verification failed. If this cluster rotates login-node "
            "host keys (e.g. Leonardo), set the profile's host-key policy to "
            "'insecure'.",
            detail=str(exc),
        ) from exc
    except (OSError, asyncssh.Error) as exc:
        raise RemoteConnectionError(
            f"Could not connect to {profile.host}:{profile.port}.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    return SSHTransport(conn)


class LocalTransport(Transport):
    """Run commands on the local machine (the 'local' preset / no SSH)."""

    is_local = True

    @property
    def alive(self) -> bool:
        return True

    async def run(self, command: str, timeout: float | None = DEFAULT_TIMEOUT) -> CmdResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"Local command timed out after {timeout:g}s."
            )
        return CmdResult(
            rc=proc.returncode if proc.returncode is not None else -1,
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
        )

    async def write_file(
        self, path: str, content: str, timeout: float | None = DEFAULT_TIMEOUT
    ) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(Path(path).write_text, content), timeout)
        except asyncio.TimeoutError:
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"Writing {path} timed out after {timeout:g}s."
            )
        except OSError as exc:
            raise RemoteCommandError(f"Could not write {path}.", detail=str(exc)) from exc

    async def fetch_tree(
        self, remote_dir: str, local_dir: str, timeout: float | None = DEFAULT_TIMEOUT
    ) -> None:
        import shutil

        def _copy() -> None:
            shutil.copytree(remote_dir, local_dir, dirs_exist_ok=True)

        try:
            await asyncio.wait_for(asyncio.to_thread(_copy), timeout)
        except asyncio.TimeoutError:
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"Fetching {remote_dir} timed out after {timeout:g}s."
            )
        except OSError as exc:
            raise RemoteCommandError(f"Could not fetch {remote_dir}.", detail=str(exc)) from exc

    async def close(self) -> None:
        pass
