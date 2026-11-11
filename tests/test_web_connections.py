"""ConnectionManager: stale-transport eviction and per-name connect locking.

Everything is faked: connectors are plain async callables and transports are tiny stand-ins,
so no network or SSH is involved.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import Transport  # noqa: E402
from crab.web.store.profiles import Profile  # noqa: E402


class _FakeTransport(Transport):
    def __init__(self) -> None:
        self.up = True
        self.closed = False

    @property
    def alive(self) -> bool:
        return self.up

    async def close(self) -> None:
        self.closed = True
        self.up = False


def _profile(name: str) -> Profile:
    return Profile(name=name, host=f"{name}.example.org", user="u", auth="agent")


async def test_stale_transport_is_evicted_by_get():
    opened: list[_FakeTransport] = []

    async def connector(profile: Profile, password: str | None) -> Transport:
        t = _FakeTransport()
        opened.append(t)
        return t

    manager = ConnectionManager(connector)
    first = await manager.connect(_profile("a"))
    assert await manager.connect(_profile("a")) is first

    opened[0].up = False  # the connection dropped while idle
    assert manager.get("a") is None
    assert not manager.is_connected("a")

    second = await manager.connect(_profile("a"))
    assert second is not first
    assert len(opened) == 2


async def test_stale_transport_is_replaced_by_connect():
    opened: list[_FakeTransport] = []

    async def connector(profile: Profile, password: str | None) -> Transport:
        t = _FakeTransport()
        opened.append(t)
        return t

    manager = ConnectionManager(connector)
    first = await manager.connect(_profile("a"))
    opened[0].up = False

    second = await manager.connect(_profile("a"))

    assert second is not first
    assert manager.get("a") is second


async def test_a_stale_transport_is_closed_before_the_replacement_is_opened():
    opened: list[_FakeTransport] = []
    close_calls: list[int] = []

    class _Counting(_FakeTransport):
        async def close(self) -> None:
            close_calls.append(len(opened))  # how many transports existed at close time
            await super().close()

    async def connector(profile: Profile, password: str | None) -> Transport:
        t = _Counting()
        opened.append(t)
        return t

    manager = ConnectionManager(connector)
    first = await manager.connect(_profile("a"))
    opened[0].up = False

    await manager.connect(_profile("a"))

    assert close_calls == [1]  # closed once, before the second transport was opened
    assert isinstance(first, _FakeTransport) and first.closed


async def test_a_failing_close_of_a_stale_transport_does_not_block_reconnect():
    opened: list[_FakeTransport] = []

    class _BadClose(_FakeTransport):
        async def close(self) -> None:
            raise RuntimeError("close failed")

    async def connector(profile: Profile, password: str | None) -> Transport:
        t = _BadClose()
        opened.append(t)
        return t

    manager = ConnectionManager(connector)
    first = await manager.connect(_profile("a"))
    opened[0].up = False

    second = await manager.connect(_profile("a"))

    assert second is not first
    assert manager.get("a") is second


async def test_a_slow_connect_does_not_block_another_cluster():
    release = asyncio.Event()

    async def connector(profile: Profile, password: str | None) -> Transport:
        if profile.name == "slow":
            await release.wait()
        return _FakeTransport()

    manager = ConnectionManager(connector)
    slow = asyncio.create_task(manager.connect(_profile("slow")))
    await asyncio.sleep(0)  # let "slow" take its lock and block in the connector
    try:
        fast = await asyncio.wait_for(manager.connect(_profile("fast")), 1)
        assert fast.alive
        assert not slow.done()
    finally:
        release.set()
        await slow
    assert manager.is_connected("slow")


async def test_concurrent_connects_to_one_name_open_a_single_transport():
    release = asyncio.Event()
    calls: list[str] = []

    async def connector(profile: Profile, password: str | None) -> Transport:
        calls.append(profile.name)
        await release.wait()
        return _FakeTransport()

    manager = ConnectionManager(connector)
    one = asyncio.create_task(manager.connect(_profile("a")))
    two = asyncio.create_task(manager.connect(_profile("a")))
    await asyncio.sleep(0)
    release.set()

    first, second = await asyncio.gather(one, two)

    assert calls == ["a"]
    assert first is second
