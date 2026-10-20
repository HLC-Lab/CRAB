"""A real local SSH+SFTP server for exercising genuine asyncssh code paths in
tests, without a real remote cluster.

Motivation: SSHTransport's asyncssh boundary had no real-connection coverage
(only fake-sftp-client tests, per test_web_transport.py's long-standing
convention) — `fetch_tree()` shipped with a bug (asyncssh's recursive `get()`
doesn't create the local destination's missing parent directories, unlike
`shutil.copytree`) that no test caught until a real cluster fetch hit it.
This module closes that gap generically: any test can spin up a real loopback
SSH server and drive a real `SSHTransport`/`connect_ssh()` against it, so
`run()`, `write_file()`, `fetch_tree()`, and connection/auth handling can all
be exercised over a genuine SSH connection, not just a hand-written fake.

Scope note: this is for testing the Transport implementations themselves.
Route-level tests (test_web_jobs.py, test_web_remotes.py) should keep using
FakeTransport — that pattern exists specifically to prove *what command a
route builds* cheaply and deterministically (see the `tdd` skill); swapping
those to a real socket would slow them down for no correctness benefit, since
route logic doesn't depend on which Transport implementation runs underneath.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncssh


class _OpenAuthServer(asyncssh.SSHServer):
    """Accepts any client with no authentication. Test-only, loopback-bound."""

    def begin_auth(self, username: str) -> bool:
        return False


class _KeyAuthServer(asyncssh.SSHServer):
    """Requires public-key auth; the `authorized_client_keys` server option
    (set by local_ssh_server()) decides which key(s) are accepted."""

    def begin_auth(self, username: str) -> bool:
        return True


async def _run_command_process(process: asyncssh.SSHServerProcess) -> None:
    """Executes the requested command via a real local subprocess, so
    SSHTransport.run() gets genuine stdout/stderr/exit-code behavior instead
    of a canned response."""
    proc = await asyncio.create_subprocess_shell(
        process.command or "",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    process.stdout.write(out.decode("utf-8", "replace"))
    process.stderr.write(err.decode("utf-8", "replace"))
    process.exit(proc.returncode if proc.returncode is not None else -1)


@asynccontextmanager
async def local_ssh_server(
    *,
    authorized_key: asyncssh.SSHKey | None = None,
    run_real_commands: bool = False,
) -> AsyncIterator[int]:
    """Start a real SSH server on 127.0.0.1; yields its port.

    Args:
        authorized_key: if given, public-key auth is required and only this
            exact key is accepted (for testing connect_ssh()'s real auth
            accept/reject behavior). If ``None`` (default), any client
            connects with no authentication — for tests that only care about
            what happens *after* a connection is established.
        run_real_commands: if true, a requested command runs via a real local
            subprocess (see _run_command_process) instead of the server only
            answering SFTP requests — needed to test SSHTransport.run().
    """
    host_key = asyncssh.generate_private_key("ssh-rsa")
    kwargs: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 0,
        "server_host_keys": [host_key],
        "sftp_factory": True,
    }
    if run_real_commands:
        kwargs["process_factory"] = _run_command_process
    if authorized_key is not None:
        kwargs["authorized_client_keys"] = asyncssh.import_authorized_keys(
            authorized_key.export_public_key().decode()
        )
        server_factory = _KeyAuthServer
    else:
        server_factory = _OpenAuthServer

    server = await asyncssh.create_server(server_factory, **kwargs)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def connect_local(
    port: int, *, client_key: asyncssh.SSHKey | None = None
) -> asyncssh.SSHClientConnection:
    """A client connection to a local_ssh_server(); host-key checks are
    disabled, matching how a real client is configured for a cluster with
    `hostkey_policy: insecure`."""
    return await asyncssh.connect(
        "127.0.0.1",
        port=port,
        known_hosts=None,
        username="test",
        client_keys=[client_key] if client_key is not None else [],
    )
