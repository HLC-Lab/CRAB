"""Transport abstraction: run a command on a remote (SSH) or local host.

A ``Transport`` exposes one uniform ``run(command) -> CmdResult`` so the rest of
the backend is identical whether the cluster is reached over SSH or is the
local machine. ``SSHTransport`` wraps a single long-lived ``asyncssh``
connection (channels are multiplexed over it, so one auth per session).

asyncssh is imported lazily inside the factory so this module, and the unit
tests that inject a fake transport, stay importable without the web extra.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from crab import paths
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
        # Our own flag covers close() and errors we saw on a channel; the connection's
        # public is_closed() covers a drop asyncssh noticed on its own (e.g. a keepalive
        # that went unanswered while the transport sat idle).
        return not self._closed and not self._conn.is_closed()

    async def run(self, command: str, timeout: float | None = DEFAULT_TIMEOUT) -> CmdResult:
        try:
            result = await asyncio.wait_for(self._conn.run(command, check=False), timeout=timeout)
        except asyncio.TimeoutError:
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"The cluster did not answer within {timeout:g} seconds. Try again.",
                detail=f"Remote command timed out after {timeout:g}s: {command}",
            )
        except Exception as exc:  # asyncssh ChannelOpenError, ConnectionLost, ...
            self._closed = True  # treat any channel error as a drop
            raise RemoteConnectionError(
                "The connection to the cluster dropped while running a command. "
                "Reconnect and try again.",
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
                f"Writing {path} on the cluster took too long. Try again.",
                detail=f"Writing {path} timed out after {timeout:g}s.",
            )
        except asyncssh.SFTPError as exc:
            # A file/permission error, not a dropped connection (e.g. the
            # parent directory doesn't exist yet): the connection stays alive.
            raise RemoteCommandError(
                f"Could not write {path} on the cluster. Check that the folder exists and "
                "that you may write there.",
                detail=f"SFTP error: {exc}",
            ) from exc
        except Exception as exc:  # asyncssh ChannelOpenError, ConnectionLost, ...
            self._closed = True  # treat any other SFTP/channel error as a drop
            raise RemoteConnectionError(
                "The connection to the cluster dropped while writing a file. "
                "Reconnect and try again.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

    async def fetch_tree(
        self, remote_dir: str, local_dir: str, timeout: float | None = DEFAULT_TIMEOUT
    ) -> None:
        import asyncssh

        # asyncssh's recursive get() copies INTO local_dir/<basename> when local_dir already
        # exists, so a re-fetch would nest the tree. It always fetches into a fresh staging
        # path instead, which _swap_in() then moves into place (see _staging_dir()).
        staging, fresh = await _staging_dir(local_dir)

        async def _fetch() -> None:
            async with self._conn.start_sftp_client() as sftp:
                await sftp.get(remote_dir, str(fresh), recurse=True)

        try:
            await asyncio.wait_for(_fetch(), timeout=timeout)
        except asyncio.TimeoutError:
            await _discard(staging)
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"Fetching {remote_dir} from the cluster took too long. Try again.",
                detail=f"Fetching {remote_dir} timed out after {timeout:g}s.",
            )
        except asyncssh.SFTPError as exc:
            await _discard(staging)
            # A file/permission error, not a dropped connection (e.g. the
            # remote directory doesn't exist): the connection stays alive.
            raise RemoteCommandError(
                f"Could not fetch {remote_dir} from the cluster. Check that it still exists.",
                detail=f"SFTP error: {exc}",
            ) from exc
        except Exception as exc:  # asyncssh ChannelOpenError, ConnectionLost, ...
            await _discard(staging)
            self._closed = True  # treat any other SFTP/channel error as a drop
            raise RemoteConnectionError(
                "The connection to the cluster dropped while fetching results. "
                "Reconnect and try again.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        await _swap_in(staging, fresh, local_dir)

    async def close(self) -> None:
        self._closed = True
        try:
            self._conn.close()
            await self._conn.wait_closed()
        except Exception:  # pragma: no cover - best-effort teardown
            pass


# Upper bound for opening an SSH connection (TCP + handshake + auth). A login node that
# accepts TCP but never answers would otherwise hang the connect, and every connect queued
# behind the connection manager's lock.
CONNECT_TIMEOUT_S = 30.0

# SSH keepalives: a probe every KEEPALIVE_INTERVAL_S; after KEEPALIVE_COUNT_MAX unanswered
# probes asyncssh closes the connection, so an idle drop shows up in SSHTransport.alive and
# the next call reconnects instead of failing on a dead socket.
KEEPALIVE_INTERVAL_S = 30
KEEPALIVE_COUNT_MAX = 3


async def connect_ssh(profile: Profile, password: str | None = None) -> SSHTransport:
    """Open an asyncssh connection for ``profile``, mapping failures to our errors.

    Auth:
      * 'agent':    uses the inherited SSH_AUTH_SOCK (a certificate tool such as step-cli
                     loads its cert there, or ordinary keys-in-agent). No secret needed.
      * 'key':      explicit private key file.
      * 'password': transient password (never persisted).
    Host key:
      * 'strict':   verify against the system known_hosts.
      * 'insecure': no verification (required for round-robin login nodes).
    """
    try:
        import asyncssh
    except ImportError as e:
        raise RemoteConnectionError(
            "The SSH part of the dashboard is not installed. "
            'Install it with `pip install -e ".[web]"` from your CRAB checkout, then restart `crab web`.',
            detail=f"The asyncssh package could not be imported: {e}",
        ) from e

    if not profile.host:
        raise RemoteConnectionError(
            "This remote has no host address. Edit the remote, enter its host, and save it.",
            detail="The profile's host is empty.",
        )

    opts: dict = {
        "host": profile.host,
        "port": profile.port,
        "username": profile.user,
        "keepalive_interval": KEEPALIVE_INTERVAL_S,
        "keepalive_count_max": KEEPALIVE_COUNT_MAX,
        # 'insecure' disables host-key checking; otherwise asyncssh loads the
        # user's ~/.ssh/known_hosts by default.
        "known_hosts": None if profile.hostkey_policy == "insecure" else (),
    }

    if profile.auth == "agent":
        sock = os.environ.get("SSH_AUTH_SOCK")
        if not sock:
            raise AuthError(
                "No SSH key is loaded in your SSH agent. Start your agent and log in to the "
                "cluster the way you normally do (for example with your certificate tool), "
                "then start `crab web` again from that same terminal.",
                detail="SSH_AUTH_SOCK is not set in the environment of crab web.",
            )
        opts["agent_path"] = sock
        opts["client_keys"] = []  # force agent-only, don't probe ~/.ssh keys
    elif profile.auth == "key":
        if not profile.key_path:
            raise AuthError(
                "This remote uses a key file, but no key file is set. "
                "Edit the remote and enter the path to your key file.",
                detail="Auth method 'key' selected but key_path is empty.",
            )
        opts["client_keys"] = [os.path.expanduser(profile.key_path)]
        opts["agent_path"] = None
    elif profile.auth == "password":
        if not password:
            raise AuthError(
                "This cluster needs a password. Type it next to the Connect button and try again.",
                detail="Auth method 'password' selected but no password was provided.",
            )
        opts["password"] = password
        opts["agent_path"] = None
        opts["client_keys"] = []

    try:
        conn = await asyncio.wait_for(asyncssh.connect(**opts), timeout=CONNECT_TIMEOUT_S)
    except asyncio.TimeoutError as exc:  # not the builtin TimeoutError on Python 3.10
        raise RemoteConnectionError(
            f"The cluster did not answer within {CONNECT_TIMEOUT_S:.0f} seconds. "
            "The login node may be down or busy, or your network or VPN may be off. "
            "Check that and try again.",
            detail=f"Timed out after {CONNECT_TIMEOUT_S:g}s connecting to "
            f"{profile.host}:{profile.port}.",
        ) from exc
    except asyncssh.PermissionDenied as exc:
        raise AuthError(
            "The cluster rejected your login. Check your user name and credentials. "
            "If you use a certificate, it may have expired: log in to the cluster again "
            "the way you normally do, then reconnect.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    except (asyncssh.HostKeyNotVerifiable, asyncssh.KeyExchangeFailed) as exc:
        raise RemoteConnectionError(
            "Could not verify the cluster's host key (its identity). If you trust this "
            "cluster, connect to it once with ssh in a terminal to save its key. If its "
            "login nodes change keys, set this remote's Host key to insecure.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    except (OSError, asyncssh.Error) as exc:
        raise RemoteConnectionError(
            f"Could not reach {profile.host}:{profile.port}. Check the host name, "
            "your network or VPN, and that the cluster is up, then try again.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    return SSHTransport(conn)


async def _staging_dir(local_dir: str) -> tuple[Path, Path]:
    """Create a hidden staging directory next to ``local_dir``; return it and the
    not-yet-existing path a fetch must copy into.

    A fetch never writes into ``local_dir`` directly: the old cache must survive a failed
    fetch, and files deleted at the source must not linger after a successful one. The
    staging dir is a sibling so the final rename stays on one filesystem. Its name starts
    with a dot so ResultsCache.list_cached() never mistakes it for a cached job.
    """
    target = Path(local_dir)

    def _make() -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=f".{target.name}.fetch-", dir=target.parent))

    try:
        staging = await asyncio.to_thread(_make)
    except OSError as exc:
        raise RemoteCommandError(f"Could not create {local_dir} locally.", detail=str(exc)) from exc
    return staging, staging / "tree"


async def _discard(staging: Path) -> None:
    await asyncio.to_thread(shutil.rmtree, staging, True)


class _RestoreFailed(Exception):
    """The swap failed and moving the previous cache back failed too."""

    def __init__(self, old: Path, swap_error: OSError, restore_error: OSError) -> None:
        super().__init__(str(restore_error))
        self.old = old
        self.swap_error = swap_error
        self.restore_error = restore_error


async def _swap_in(staging: Path, fresh: Path, local_dir: str) -> None:
    """Replace ``local_dir`` with the freshly fetched tree, then drop the staging dir."""
    target = Path(local_dir)
    keep_staging = False

    def _swap() -> None:
        old = staging / "old"
        moved_aside = target.exists()
        if moved_aside:
            target.rename(old)
        try:
            fresh.rename(target)
        except OSError as exc:
            if moved_aside:  # keep the previous cache rather than losing both trees
                try:
                    old.rename(target)
                except OSError as restore_exc:
                    raise _RestoreFailed(old, exc, restore_exc) from exc
            raise

    try:
        await asyncio.to_thread(_swap)
    except _RestoreFailed as failed:
        keep_staging = True  # staging holds the only copy of the previous cache
        raise RemoteCommandError(
            f"Could not update {local_dir} locally, and the previous copy could not be put "
            f"back. It is kept at {failed.old}.",
            detail=f"{failed.swap_error}; restoring the previous copy failed: {failed.restore_error}",
        ) from failed.swap_error
    except OSError as exc:
        raise RemoteCommandError(f"Could not update {local_dir} locally.", detail=str(exc)) from exc
    finally:
        if not keep_staging:
            await _discard(staging)


class LocalTransport(Transport):
    """Run commands on the local machine (the 'local' preset / no SSH)."""

    is_local = True

    @property
    def alive(self) -> bool:
        return True

    async def run(self, command: str, timeout: float | None = DEFAULT_TIMEOUT) -> CmdResult:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=paths.neutral_dir(),  # a `crab/` in the caller's folder must not shadow ours
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            # Only the process object we started: stop it so it does not run on, unwatched.
            try:
                proc.kill()
            except ProcessLookupError:  # it exited on its own just now
                pass
            # Bounded: a grandchild of the shell may outlive it and hold the pipes open, and
            # on Python 3.10/3.11 wait() can then block until it exits.
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                pass
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
        staging, fresh = await _staging_dir(local_dir)

        def _copy() -> None:
            shutil.copytree(remote_dir, fresh)

        try:
            await asyncio.wait_for(asyncio.to_thread(_copy), timeout)
        except asyncio.TimeoutError:
            await _discard(staging)
            raise RemoteConnectionError(  # noqa: B904 -- timeout carries no useful chain
                f"Fetching {remote_dir} timed out after {timeout:g}s."
            )
        except OSError as exc:
            await _discard(staging)
            raise RemoteCommandError(f"Could not fetch {remote_dir}.", detail=str(exc)) from exc
        await _swap_in(staging, fresh, local_dir)

    async def close(self) -> None:
        pass
