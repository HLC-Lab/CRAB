"""Per-remote connection manager: one reused transport per cluster.

Connections are opened lazily and kept for the session (asyncssh multiplexes
channels, so one auth covers everything). A dropped connection is detected via
``Transport.alive`` and evicted, so the next call reconnects cleanly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from crab.web.connections.transport import (
    LocalTransport,
    Transport,
    connect_ssh,
)
from crab.web.store.profiles import Profile

# A connector opens a transport for an SSH profile. Injectable for tests.
Connector = Callable[[Profile, "str | None"], Awaitable[Transport]]


class ConnectionManager:
    def __init__(self, connector: Connector | None = None) -> None:
        self._connector: Connector = connector or connect_ssh
        self._conns: dict[str, Transport] = {}
        self._lock = asyncio.Lock()

    async def connect(self, profile: Profile, password: str | None = None) -> Transport:
        """Return a live transport for ``profile``, opening one if needed."""
        async with self._lock:
            existing = self._conns.get(profile.name)
            if existing is not None and existing.alive:
                return existing
            if existing is not None:  # stale → drop before reconnecting
                self._conns.pop(profile.name, None)

            transport = (
                LocalTransport() if profile.is_local() else await self._connector(profile, password)
            )
            self._conns[profile.name] = transport
            return transport

    def get(self, name: str) -> Transport | None:
        """Return the live transport for ``name``, evicting it if it has dropped."""
        transport = self._conns.get(name)
        if transport is not None and not transport.alive:
            self._conns.pop(name, None)
            return None
        return transport

    def is_connected(self, name: str) -> bool:
        return self.get(name) is not None

    async def disconnect(self, name: str) -> None:
        transport = self._conns.pop(name, None)
        if transport is not None:
            await transport.close()

    async def close_all(self) -> None:
        for transport in list(self._conns.values()):
            await transport.close()
        self._conns.clear()
