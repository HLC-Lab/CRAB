"""Tests for Transport.write_file() and Transport.fetch_tree().

LocalTransport is exercised against the real filesystem (tmp_path). SSHTransport's
asyncssh/SFTP path has no live-connection coverage here, by the same convention as the
rest of this file: SSHTransport.run()/connect_ssh() are untested at the asyncssh
boundary too (Transport is the fake-able seam — see tests/test_web_remotes.py's
FakeTransport); the real SFTP path is verified live against a real cluster
(.crab-web-dev/local-notes.md). fetch_tree's SSHTransport tests use a fake sftp client
to prove the recursive-get call is built correctly, without needing a live connection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncssh
import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.transport import LocalTransport, SSHTransport  # noqa: E402
from crab.web.errors import RemoteCommandError, RemoteConnectionError  # noqa: E402


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


def test_ssh_transport_fetch_tree_calls_recursive_get():
    sftp = _FakeSFTPClient()
    transport = SSHTransport(_FakeSFTPConnection(sftp))  # type: ignore[arg-type]

    asyncio.run(transport.fetch_tree("/remote/results", "/local/results"))

    assert sftp.calls == [("/remote/results", "/local/results", True)]


def test_ssh_transport_fetch_tree_sftp_error_does_not_close_connection():
    sftp = _FakeSFTPClient(get_error=asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "no such file"))
    transport = SSHTransport(_FakeSFTPConnection(sftp))  # type: ignore[arg-type]

    with pytest.raises(RemoteCommandError):
        asyncio.run(transport.fetch_tree("/remote/missing", "/local/results"))

    assert transport.alive


def test_ssh_transport_fetch_tree_other_error_closes_connection():
    sftp = _FakeSFTPClient(get_error=ConnectionResetError("dropped"))
    transport = SSHTransport(_FakeSFTPConnection(sftp))  # type: ignore[arg-type]

    with pytest.raises(RemoteConnectionError):
        asyncio.run(transport.fetch_tree("/remote/results", "/local/results"))

    assert not transport.alive
