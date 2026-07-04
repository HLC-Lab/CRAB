"""Tests for remoteops/transfer.py (stage_config): no real SSH, a fake transport
stands in (see tests/test_web_remotes.py's FakeTransport for the same pattern).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.remoteops.transfer import stage_config  # noqa: E402

from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import RemoteCommandError  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.profiles import Profile  # noqa: E402


class FakeTransport(Transport):
    def __init__(self, mkdir_rc: int = 0):
        self._mkdir_rc = mkdir_rc
        self.run_calls: list[str] = []
        self.written_files: dict[str, str] = {}

    @property
    def alive(self) -> bool:
        return True

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.run_calls.append(command)
        return CmdResult(self._mkdir_rc, "", "" if self._mkdir_rc == 0 else "boom")

    async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
        self.written_files[path] = content

    async def close(self) -> None:
        pass


def _ssh_profile() -> Profile:
    return Profile(
        name="leonardo",
        host="login.cluster.example.org",
        user="researcher",
        remote_crab="~/base",
    )


def _local_profile() -> Profile:
    return Profile(name="local", transport="local")


async def test_stage_config_ssh_mkdirs_via_shell_then_writes_raw_tilde_path():
    # mkdir runs through a shell, so `~` must be rewritten to `$HOME` there.
    # write_file goes over SFTP (no shell), so the stored/returned path keeps
    # the literal `~` — SFTP servers (OpenSSH's sftp-server) expand it themselves.
    transport = FakeTransport()
    config = {"global_options": {"name": "My Run!"}, "experiments": []}

    path = await stage_config(transport, _ssh_profile(), config, "My Run!")

    assert transport.run_calls == ['mkdir -p "$HOME/base/CRAB/.web_staging"']
    assert path == "~/base/CRAB/.web_staging/my-run.json"
    assert json.loads(transport.written_files[path]) == config


async def test_stage_config_local_uses_data_dir(tmp_path: Path):
    transport = FakeTransport()
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    config = {"global_options": {}, "experiments": []}

    path = await stage_config(transport, _local_profile(), config, "demo", settings=settings)

    expected_dir = str(tmp_path / "data" / "web_staging")
    assert transport.run_calls == [f"mkdir -p {expected_dir}"]
    assert path == f"{expected_dir}/demo.json"
    assert json.loads(transport.written_files[path]) == config


async def test_stage_config_slugifies_name():
    transport = FakeTransport()

    path = await stage_config(transport, _ssh_profile(), {}, "  Weird / Name!! ")

    assert path.endswith("/weird-name.json")


async def test_stage_config_mkdir_failure_raises():
    transport = FakeTransport(mkdir_rc=1)

    with pytest.raises(RemoteCommandError):
        await stage_config(transport, _ssh_profile(), {}, "demo")

    assert transport.written_files == {}
