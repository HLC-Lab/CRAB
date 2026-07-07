"""A real local SSH+SFTP server for exercising genuine asyncssh code paths in
tests, without a real remote cluster.

Motivation: SSHTransport's asyncssh boundary had no real-connection coverage
(only fake-sftp-client tests, per test_web_transport.py's long-standing
convention) — `fetch_tree()` shipped with a bug (asyncssh's recursive `get()`
doesn't create the local destination's missing parent directories, unlike
`shutil.copytree`) that no test caught until a real cluster fetch hit it.
This module closes that gap generically: any test can spin up a real loopback
SSH server and drive a real `SSHTransport` against it, so `run()`,
`write_file()`, and `fetch_tree()` can all be exercised over a genuine SSH
connection, not just a hand-written fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncssh


class _OpenAuthServer(asyncssh.SSHServer):
    """Accepts any client with no authentication. Test-only, loopback-bound."""

    def begin_auth(self, username: str) -> bool:
        return False


@asynccontextmanager
async def local_ssh_server() -> AsyncIterator[int]:
    """Start a real SSH server on 127.0.0.1 with SFTP enabled; yields its port.

    Backed by asyncssh's own server implementation (the same library used on
    the client side), listening on an OS-assigned loopback port with a
    throwaway host key. Its SFTP server proxies to the real local filesystem,
    so recursive get/put exercise genuine wire-protocol behavior instead of a
    fake's approximation of it.
    """
    host_key = asyncssh.generate_private_key("ssh-rsa")
    server = await asyncssh.create_server(
        _OpenAuthServer,
        host="127.0.0.1",
        port=0,
        server_host_keys=[host_key],
        sftp_factory=True,
    )
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def connect_local(port: int) -> asyncssh.SSHClientConnection:
    """A client connection to a local_ssh_server(); host-key checks/auth are
    both disabled server-side, matching how the real client would need to be
    configured for a cluster with `hostkey_policy: insecure`."""
    return await asyncssh.connect(
        "127.0.0.1", port=port, known_hosts=None, username="test", client_keys=[]
    )
