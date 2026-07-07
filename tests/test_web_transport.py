"""Tests for Transport.write_file() and Transport.fetch_tree().

LocalTransport is exercised against the real filesystem (tmp_path). SSHTransport's
asyncssh/SFTP path has two layers of coverage: a fake sftp client (below) proves the
recursive-get call is built correctly (args, error-branch behavior) without needing a
live connection; a real loopback SSH+SFTP server (tests/ssh_server.py, bottom of this
file) exercises the genuine asyncssh wire path end to end. The latter exists because a
real bug (asyncssh's recursive get() not creating local_dir's missing parent
directories) shipped past the fake-only tests and only surfaced against a real cluster —
see fetch_tree()'s fix in transport.py and test_ssh_transport_fetch_tree_real_server_*
below. SSHTransport.run()/connect_ssh() still have no real-server coverage yet (not
needed by this fix); tests/ssh_server.py's `local_ssh_server()` is written to be reused
there too.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncssh
import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.transport import LocalTransport, SSHTransport  # noqa: E402
from crab.web.errors import RemoteCommandError, RemoteConnectionError  # noqa: E402
from ssh_server import connect_local, local_ssh_server  # noqa: E402


def test_local_transport_write_file_writes_content(tmp_path: Path):
    transport = LocalTransport()
    target = tmp_path / "config.json"

    asyncio.run(transport.write_file(str(target), '{"a": 1}'))

    assert target.read_text() == '{"a": 1}'


def test_local_transport_write_file_missing_parent_raises(tmp_path: Path):
    transport = LocalTransport()
    target = tmp_path / "no-such-dir" / "config.json"

    with pytest.raises(RemoteCommandError):
        asyncio.run(transport.write_file(str(target), "{}"))


def _make_source_tree(root: Path) -> Path:
    source = root / "source"
    (source / "lab-a").mkdir(parents=True)
    (source / "lab-a" / "run.csv").write_text("x,y\n1,2\n")
    (source / "top.csv").write_text("a,b\n3,4\n")
    return source


def test_local_transport_fetch_tree_copies_nested_files(tmp_path: Path):
    source = _make_source_tree(tmp_path)
    dest = tmp_path / "dest"
    transport = LocalTransport()

    asyncio.run(transport.fetch_tree(str(source), str(dest)))

    assert (dest / "top.csv").read_text() == "a,b\n3,4\n"
    assert (dest / "lab-a" / "run.csv").read_text() == "x,y\n1,2\n"


def test_local_transport_fetch_tree_missing_source_raises(tmp_path: Path):
    transport = LocalTransport()

    with pytest.raises(RemoteCommandError):
        asyncio.run(transport.fetch_tree(str(tmp_path / "no-such-source"), str(tmp_path / "dest")))


class _FakeSFTPClient:
    def __init__(self, get_error: Exception | None = None) -> None:
        self.get_error = get_error
        self.calls: list[tuple[str, str, bool]] = []

    async def __aenter__(self) -> _FakeSFTPClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get(self, remote_path: str, local_path: str, *, recurse: bool = False) -> None:
        if self.get_error is not None:
            raise self.get_error
        self.calls.append((remote_path, local_path, recurse))


class _FakeSFTPConnection:
    def __init__(self, sftp: _FakeSFTPClient) -> None:
        self._sftp = sftp

    def start_sftp_client(self) -> _FakeSFTPClient:
        return self._sftp


def test_ssh_transport_fetch_tree_calls_recursive_get(tmp_path: Path):
    sftp = _FakeSFTPClient()
    transport = SSHTransport(_FakeSFTPConnection(sftp))  # type: ignore[arg-type]
    local_dir = str(tmp_path / "local" / "results")

    asyncio.run(transport.fetch_tree("/remote/results", local_dir))

    assert sftp.calls == [("/remote/results", local_dir, True)]


def test_ssh_transport_fetch_tree_sftp_error_does_not_close_connection(tmp_path: Path):
    sftp = _FakeSFTPClient(get_error=asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "no such file"))
    transport = SSHTransport(_FakeSFTPConnection(sftp))  # type: ignore[arg-type]

    with pytest.raises(RemoteCommandError):
        asyncio.run(transport.fetch_tree("/remote/missing", str(tmp_path / "local" / "results")))

    assert transport.alive


def test_ssh_transport_fetch_tree_other_error_closes_connection(tmp_path: Path):
    sftp = _FakeSFTPClient(get_error=ConnectionResetError("dropped"))
    transport = SSHTransport(_FakeSFTPConnection(sftp))  # type: ignore[arg-type]

    with pytest.raises(RemoteConnectionError):
        asyncio.run(transport.fetch_tree("/remote/results", str(tmp_path / "local" / "results")))

    assert not transport.alive


# --------------------------------------------------------------------------- #
# Real loopback SSH+SFTP server (tests/ssh_server.py) — genuine asyncssh wire
# path, not a fake. Added after a real bug (asyncssh's recursive get() doesn't
# create local_dir's missing parent directories) shipped past the fake-only
# tests above and only surfaced against a real cluster.
# --------------------------------------------------------------------------- #
async def test_ssh_transport_fetch_tree_real_server_creates_missing_parents(tmp_path: Path):
    remote_root = tmp_path / "remote"
    (remote_root / "sub").mkdir(parents=True)
    (remote_root / "sub" / "data.csv").write_text("x\n1\n")
    # Deliberately nested under directories that don't exist yet, matching the
    # real bug report: a fresh install has no results_cache/<cluster>/ yet.
    local_dest = tmp_path / "does" / "not" / "exist" / "job1"

    async with local_ssh_server() as port:
        conn = await connect_local(port)
        transport = SSHTransport(conn)
        try:
            await transport.fetch_tree(str(remote_root), str(local_dest))
        finally:
            await transport.close()

    assert (local_dest / "sub" / "data.csv").read_text() == "x\n1\n"


async def test_ssh_transport_fetch_tree_real_server_missing_remote_raises(tmp_path: Path):
    async with local_ssh_server() as port:
        conn = await connect_local(port)
        transport = SSHTransport(conn)
        try:
            with pytest.raises(RemoteCommandError):
                await transport.fetch_tree(
                    str(tmp_path / "no-such-remote-dir"), str(tmp_path / "d")
                )
            assert transport.alive
        finally:
            await transport.close()
