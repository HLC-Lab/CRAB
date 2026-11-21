"""A hostile SFTP server cannot make `SSHTransport.fetch_tree` write outside the results cache.

asyncssh's recursive `get()` recreated the server's symlinks locally and wrote through them: a
link `x` -> `.`, a link `L` -> `x/x/x/x/x/x/../../../../../..` (asyncssh's own link check
collapses `x/../` and passes it), then `L` again as a directory holding a file. A real server
runs here, on loopback, listing exactly that tree; the client is the real transport.
"""

from __future__ import annotations

import io
import stat
from pathlib import Path

import asyncssh
import pytest
from asyncssh import SFTPAttrs, SFTPName, SFTPServer

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.sftp_tree import MAX_DEPTH  # noqa: E402
from crab.web.connections.transport import SSHTransport  # noqa: E402
from crab.web.errors import RemoteCommandError  # noqa: E402

DIR = stat.S_IFDIR | 0o755
REG = stat.S_IFREG | 0o644
LNK = stat.S_IFLNK | 0o777
BODY = b"written by the remote\n"


def _attrs(kind: str, size: int = 0) -> SFTPAttrs:
    mode = {"d": DIR, "f": REG, "l": LNK}[kind]
    return SFTPAttrs(permissions=mode, size=size)


class _Body(io.BytesIO):
    """A file that is all data: asyncssh probes SEEK_DATA / SEEK_HOLE for sparse downloads."""

    def seek(self, offset, whence=0):
        if whence == 3:
            if offset >= len(self.getvalue()):
                raise OSError(6, "no data")
            return super().seek(offset, 0)
        if whence == 4:
            return super().seek(len(self.getvalue()), 0)
        return super().seek(offset, whence)


class _FakeTreeServer(SFTPServer):
    """Serves `TREE`: a directory path -> the (name, kind) entries it lists, repeats allowed."""

    TREE: dict[str, list[tuple[str, str]]] = {}

    async def scandir(self, path):
        yield SFTPName(b".", attrs=_attrs("d"))
        yield SFTPName(b"..", attrs=_attrs("d"))
        for name, kind in self.TREE.get(path.decode(), []):
            yield SFTPName(name.encode(), attrs=_attrs(kind, len(BODY)))

    def stat(self, path):
        return _attrs("d") if path.decode() in self.TREE else _attrs("f", len(BODY))

    def lstat(self, path):
        return self.stat(path)

    def realpath(self, path):
        return path

    def open(self, path, pflags, attrs):
        return _Body(BODY)

    def read(self, handle, offset, size):
        handle.seek(offset)
        return handle.read(size)

    def close(self, handle):
        pass


class _NoAuth(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return False


async def _fetch(tree: dict[str, list[tuple[str, str]]], local_dir: Path) -> None:
    server_type = type("Tree", (_FakeTreeServer,), {"TREE": tree})
    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        server_factory=_NoAuth,
        sftp_factory=server_type,
    )
    port = server.sockets[0].getsockname()[1]
    conn = await asyncssh.connect(
        "127.0.0.1", port, username="u", known_hosts=None, client_keys=None, agent_path=None
    )
    transport = SSHTransport(conn)
    try:
        await transport.fetch_tree("/evil/job", str(local_dir))
    finally:
        await transport.close()
        server.close()
        await server.wait_closed()


def _cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "data" / "results_cache" / "cluster" / "system" / "job"


def _nothing_outside_the_cache(tmp_path: Path) -> bool:
    written = [p for p in tmp_path.rglob("*") if p.is_file() and "results_cache" not in p.parts]
    return not written


async def test_a_symlink_chain_with_a_repeated_name_is_refused(tmp_path: Path):
    tree = {
        "/evil/job": [("x", "l"), ("L", "l"), ("L", "d")],
        "/evil/job/L": [("PWNED", "f")],
    }

    with pytest.raises(RemoteCommandError, match="Refused to fetch"):
        await _fetch(tree, _cache_dir(tmp_path))

    assert _nothing_outside_the_cache(tmp_path)
    assert not _cache_dir(tmp_path).exists()  # nothing was swapped in


async def test_symlinks_are_skipped_and_regular_files_are_kept(tmp_path: Path):
    tree = {
        "/evil/job": [("link", "l"), ("sub", "d"), ("top.csv", "f")],
        "/evil/job/sub": [("a", "f")],
    }

    await _fetch(tree, _cache_dir(tmp_path))

    cache = _cache_dir(tmp_path)
    assert (cache / "top.csv").read_bytes() == BODY
    assert (cache / "sub" / "a").read_bytes() == BODY
    assert not (cache / "link").exists()
    assert not any(p.is_symlink() for p in cache.rglob("*"))


async def test_a_name_with_a_slash_is_refused(tmp_path: Path):
    with pytest.raises(RemoteCommandError, match="Refused to fetch"):
        await _fetch({"/evil/job": [("a/b", "f")]}, _cache_dir(tmp_path))
    assert _nothing_outside_the_cache(tmp_path)


async def test_a_tree_deeper_than_the_cap_is_refused(tmp_path: Path):
    tree = {"/evil/job": [("d", "d")]}
    path = "/evil/job"
    for _ in range(MAX_DEPTH + 2):
        path += "/d"
        tree[path] = [("d", "d")]

    with pytest.raises(RemoteCommandError, match="Refused to fetch"):
        await _fetch(tree, _cache_dir(tmp_path))
