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
import os
import shlex
import shutil
import sys
from pathlib import Path

import asyncssh
import pytest

import crab
from crab import paths

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.transport import (  # noqa: E402
    LocalTransport,
    SSHTransport,
    connect_ssh,
)
from crab.web.errors import (  # noqa: E402
    AuthError,
    RemoteCommandError,
    RemoteConnectionError,
)
from crab.web.store.profiles import Profile  # noqa: E402
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
        Path(local_path).mkdir()  # like asyncssh: the destination must not pre-exist


class _FakeSFTPConnection:
    def __init__(self, sftp: _FakeSFTPClient) -> None:
        self._sftp = sftp

    def start_sftp_client(self) -> _FakeSFTPClient:
        return self._sftp

    def is_closed(self) -> bool:
        return False


def test_ssh_transport_fetch_tree_calls_recursive_get(tmp_path: Path):
    sftp = _FakeSFTPClient()
    transport = SSHTransport(_FakeSFTPConnection(sftp))  # type: ignore[arg-type]
    local_dir = str(tmp_path / "local" / "results")

    asyncio.run(transport.fetch_tree("/remote/results", local_dir))

    [(remote, staged, recurse)] = sftp.calls
    assert (remote, recurse) == ("/remote/results", True)
    assert staged != local_dir  # fetched into staging, then swapped in
    assert Path(local_dir).is_dir()


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


async def test_ssh_transport_run_executes_a_real_command():
    async with local_ssh_server(run_real_commands=True) as port:
        conn = await connect_local(port)
        transport = SSHTransport(conn)
        try:
            result = await transport.run("echo hello")
            assert result.ok
            assert result.stdout.strip() == "hello"
        finally:
            await transport.close()


async def test_ssh_transport_run_reports_a_real_nonzero_exit():
    async with local_ssh_server(run_real_commands=True) as port:
        conn = await connect_local(port)
        transport = SSHTransport(conn)
        try:
            result = await transport.run("exit 3")
            assert result.rc == 3
            assert not result.ok
        finally:
            await transport.close()


async def test_ssh_transport_write_file_real_server_round_trip(tmp_path: Path):
    remote_path = tmp_path / "config.json"

    async with local_ssh_server() as port:
        conn = await connect_local(port)
        transport = SSHTransport(conn)
        try:
            await transport.write_file(str(remote_path), '{"a": 1}')
        finally:
            await transport.close()

    assert remote_path.read_text() == '{"a": 1}'


async def test_connect_ssh_real_server_accepts_the_authorized_key(tmp_path: Path):
    client_key = asyncssh.generate_private_key("ssh-rsa")
    key_path = tmp_path / "id_test"
    client_key.write_private_key(str(key_path))

    async with local_ssh_server(authorized_key=client_key) as port:
        profile = Profile(
            name="test",
            host="127.0.0.1",
            port=port,
            user="test",
            auth="key",
            key_path=str(key_path),
            hostkey_policy="insecure",
        )
        transport = await connect_ssh(profile)
        try:
            assert transport.alive
        finally:
            await transport.close()


async def test_connect_ssh_real_server_rejects_the_wrong_key(tmp_path: Path):
    authorized_key = asyncssh.generate_private_key("ssh-rsa")
    wrong_key = asyncssh.generate_private_key("ssh-rsa")
    key_path = tmp_path / "id_wrong"
    wrong_key.write_private_key(str(key_path))

    async with local_ssh_server(authorized_key=authorized_key) as port:
        profile = Profile(
            name="test",
            host="127.0.0.1",
            port=port,
            user="test",
            auth="key",
            key_path=str(key_path),
            hostkey_policy="insecure",
        )
        with pytest.raises(AuthError):
            await connect_ssh(profile)


async def test_connect_ssh_real_server_connection_refused_maps_cleanly(
    tmp_path: Path, unused_tcp_port: int
):
    key_path = tmp_path / "id_test"
    asyncssh.generate_private_key("ssh-rsa").write_private_key(str(key_path))
    profile = Profile(
        name="test",
        host="127.0.0.1",
        port=unused_tcp_port,
        user="test",
        auth="key",
        key_path=str(key_path),
        hostkey_policy="insecure",
    )
    with pytest.raises(RemoteConnectionError):
        await connect_ssh(profile)


async def test_connect_ssh_raises_a_clean_error_when_asyncssh_is_missing(monkeypatch):
    """`sys.modules[name] = None` is the standard way to simulate an uninstalled
    package: Python's import system raises ImportError rather than looking it up
    (docs.python.org/3/reference/import.html#the-module-cache)."""
    monkeypatch.setitem(sys.modules, "asyncssh", None)
    profile = Profile(
        name="test",
        host="cluster.example.org",
        user="test",
        auth="agent",
        hostkey_policy="insecure",
    )
    with pytest.raises(RemoteConnectionError, match="asyncssh"):
        await connect_ssh(profile)


async def test_connect_ssh_times_out_on_a_silent_server(tmp_path: Path, monkeypatch):
    """A host that accepts TCP but never speaks SSH (a hung login node) must fail within the
    connect timeout instead of hanging the dashboard (and every other connect behind its lock)."""
    import asyncio

    import crab.web.connections.transport as transport_mod

    monkeypatch.setattr(transport_mod, "CONNECT_TIMEOUT_S", 0.5)
    client_key = asyncssh.generate_private_key("ssh-rsa")
    key_path = tmp_path / "id_test"
    client_key.write_private_key(str(key_path))
    held: list[asyncio.StreamWriter] = []

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)  # keep the socket open, send nothing

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    profile = Profile(
        name="test",
        host="127.0.0.1",
        port=port,
        user="test",
        auth="key",
        key_path=str(key_path),
        hostkey_policy="insecure",
    )
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(RemoteConnectionError, match="Timed out"):
            await connect_ssh(profile)
        assert loop.time() - started < 5
    finally:
        for w in held:
            w.close()
        server.close()
        await server.wait_closed()


async def test_ssh_transport_fetch_tree_real_server_refetch_replaces_the_tree(tmp_path: Path):
    """asyncssh's recursive get() copies into ``local_dir/<basename>`` when local_dir already
    exists (sftp.py _begin_copy: dst_isdir -> compose_path(basename, parent=dstpath)), so a
    naive re-fetch nested the tree as ``<job>/<job>/...`` and kept files deleted remotely."""
    remote_root = tmp_path / "remote" / "job1"
    (remote_root / "sub").mkdir(parents=True)
    (remote_root / "sub" / "data.csv").write_text("x\n1\n")
    (remote_root / "gone.csv").write_text("old\n")
    local_dest = tmp_path / "cache" / "job1"

    async with local_ssh_server() as port:
        conn = await connect_local(port)
        transport = SSHTransport(conn)
        try:
            await transport.fetch_tree(str(remote_root), str(local_dest))
            (remote_root / "gone.csv").unlink()
            (remote_root / "sub" / "data.csv").write_text("x\n2\n")
            await transport.fetch_tree(str(remote_root), str(local_dest))
        finally:
            await transport.close()

    assert not (local_dest / "job1").exists()
    assert not (local_dest / "gone.csv").exists()
    assert (local_dest / "sub" / "data.csv").read_text() == "x\n2\n"
    assert sorted(p.name for p in local_dest.parent.iterdir()) == ["job1"]


async def test_ssh_transport_fetch_tree_real_server_failed_refetch_keeps_the_cache(
    tmp_path: Path,
):
    remote_root = tmp_path / "remote" / "job1"
    remote_root.mkdir(parents=True)
    (remote_root / "a.csv").write_text("a\n1\n")
    local_dest = tmp_path / "cache" / "job1"

    async with local_ssh_server() as port:
        conn = await connect_local(port)
        transport = SSHTransport(conn)
        try:
            await transport.fetch_tree(str(remote_root), str(local_dest))
            (remote_root / "a.csv").write_text("a\n2\n")
            unreadable = remote_root / "z.csv"
            unreadable.write_text("z\n")
            unreadable.chmod(0)  # the copy fails part-way through the tree
            try:
                with pytest.raises(RemoteCommandError):
                    await transport.fetch_tree(str(remote_root), str(local_dest))
            finally:
                unreadable.chmod(0o644)
        finally:
            await transport.close()

    assert sorted(p.name for p in local_dest.iterdir()) == ["a.csv"]
    assert (local_dest / "a.csv").read_text() == "a\n1\n"
    assert sorted(p.name for p in local_dest.parent.iterdir()) == ["job1"]


def test_local_transport_fetch_tree_refetch_drops_files_deleted_at_the_source(tmp_path: Path):
    source = _make_source_tree(tmp_path)
    dest = tmp_path / "cache" / "dest"
    transport = LocalTransport()

    asyncio.run(transport.fetch_tree(str(source), str(dest)))
    (source / "top.csv").unlink()
    asyncio.run(transport.fetch_tree(str(source), str(dest)))

    assert not (dest / "top.csv").exists()
    assert (dest / "lab-a" / "run.csv").read_text() == "x,y\n1,2\n"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["dest"]


def test_local_transport_fetch_tree_failed_refetch_keeps_the_cache(tmp_path: Path):
    source = _make_source_tree(tmp_path)
    dest = tmp_path / "cache" / "dest"
    transport = LocalTransport()

    asyncio.run(transport.fetch_tree(str(source), str(dest)))
    shutil.rmtree(source)
    with pytest.raises(RemoteCommandError):
        asyncio.run(transport.fetch_tree(str(source), str(dest)))

    assert (dest / "top.csv").read_text() == "a,b\n3,4\n"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["dest"]


# --------------------------------------------------------------------------- #
# LocalTransport.run starts its commands in a folder CRAB chose, not in the
# caller's current folder.
# --------------------------------------------------------------------------- #
async def test_local_transport_run_starts_in_the_neutral_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = await LocalTransport().run("pwd")

    assert result.rc == 0
    assert os.path.realpath(result.stdout.strip()) == os.path.realpath(paths.neutral_dir())


async def test_local_transport_run_ignores_a_crab_package_in_the_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    decoy = tmp_path / "decoy"
    (decoy / "crab").mkdir(parents=True)
    (decoy / "crab" / "__init__.py").write_text('raise RuntimeError("decoy crab imported")\n')
    monkeypatch.chdir(decoy)

    command = f"{shlex.quote(sys.executable)} -c 'import crab; print(crab.__file__)'"
    result = await LocalTransport().run(command)

    assert result.rc == 0, result.stderr
    assert result.stdout.strip() == crab.__file__


# --------------------------------------------------------------------------- #
# Connection hygiene: keepalive, liveness, local timeout kills its child,
# _swap_in restores the old cache.
# --------------------------------------------------------------------------- #
class _FakeLiveConnection:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


async def test_connect_ssh_sends_keepalives(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    async def fake_connect(**kwargs: object) -> _FakeLiveConnection:
        seen.update(kwargs)
        return _FakeLiveConnection()

    monkeypatch.setattr(asyncssh, "connect", fake_connect)
    profile = Profile(
        name="test",
        host="cluster.example.org",
        user="test",
        auth="password",
        hostkey_policy="insecure",
    )

    transport = await connect_ssh(profile, password="pw")

    interval = seen["keepalive_interval"]
    count_max = seen["keepalive_count_max"]
    assert isinstance(interval, (int, float)) and interval > 0
    assert isinstance(count_max, int) and count_max >= 1
    assert transport.alive


def test_ssh_transport_alive_follows_the_connection_and_close():
    conn = _FakeLiveConnection()
    transport = SSHTransport(conn)  # type: ignore[arg-type]
    assert transport.alive is True

    conn.closed = True  # asyncssh saw the connection drop
    assert transport.alive is False

    other = SSHTransport(_FakeLiveConnection())  # type: ignore[arg-type]
    assert other.alive is True
    asyncio.run(other.close())
    assert other.alive is False


async def test_local_transport_timeout_kills_the_child_it_started(
    monkeypatch: pytest.MonkeyPatch,
):
    started: list[asyncio.subprocess.Process] = []
    real_create = asyncio.create_subprocess_shell

    async def recording_create(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        proc = await real_create(*args, **kwargs)  # type: ignore[arg-type]
        started.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", recording_create)
    try:
        with pytest.raises(RemoteConnectionError):
            await LocalTransport().run("sleep 30", timeout=0.2)
        [proc] = started
        assert proc.returncode is not None  # the child is dead, not left running
    finally:
        for proc in started:  # only the process object this test's call created
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


async def test_swap_in_restores_the_old_cache_when_the_second_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from crab.web.connections.transport import _swap_in

    target = tmp_path / "cache" / "job1"
    target.mkdir(parents=True)
    (target / "marker.txt").write_text("old\n")
    staging = tmp_path / "cache" / ".job1.fetch-x"
    fresh = staging / "tree"
    fresh.mkdir(parents=True)
    (fresh / "new.txt").write_text("new\n")

    real_rename = Path.rename

    def flaky_rename(self: Path, new: object) -> Path:
        if self == fresh:
            raise OSError("simulated rename failure")
        return real_rename(self, new)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "rename", flaky_rename)

    with pytest.raises(RemoteCommandError):
        await _swap_in(staging, fresh, str(target))

    assert (target / "marker.txt").read_text() == "old\n"
    assert not (target / "new.txt").exists()


async def test_swap_in_keeps_the_old_cache_on_disk_when_the_restore_fails_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from crab.web.connections.transport import _swap_in

    target = tmp_path / "cache" / "job1"
    target.mkdir(parents=True)
    (target / "marker.txt").write_text("old\n")
    staging = tmp_path / "cache" / ".job1.fetch-x"
    fresh = staging / "tree"
    fresh.mkdir(parents=True)
    old = staging / "old"

    real_rename = Path.rename

    def failing_rename(self: Path, new: object) -> Path:
        if self == fresh:
            raise OSError("swap failure")
        if self == old:
            raise OSError("restore failure")
        return real_rename(self, new)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "rename", failing_rename)

    with pytest.raises(RemoteCommandError) as excinfo:
        await _swap_in(staging, fresh, str(target))

    assert (old / "marker.txt").read_text() == "old\n"  # the only copy was not deleted
    assert str(old) in excinfo.value.message
    assert "swap failure" in (excinfo.value.detail or "")
    assert "restore failure" in (excinfo.value.detail or "")
