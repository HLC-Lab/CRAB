"""Tests for Transport.write_file().

LocalTransport is exercised against the real filesystem (tmp_path). SSHTransport's
asyncssh/SFTP path has no unit coverage here, by the same convention as the rest of
this file: SSHTransport.run()/connect_ssh() are untested at the asyncssh boundary too
(Transport is the fake-able seam — see tests/test_web_remotes.py's FakeTransport); the
real SFTP path is verified live against a real cluster (.crab-web-dev/local-notes.md).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.transport import LocalTransport  # noqa: E402
from crab.web.errors import RemoteCommandError  # noqa: E402


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
