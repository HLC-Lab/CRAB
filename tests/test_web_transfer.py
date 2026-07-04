"""Tests for remoteops/transfer.py (stage_config): no real SSH, a fake transport
stands in (see tests/test_web_remotes.py's FakeTransport for the same pattern).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import RemoteCommandError  # noqa: E402
from crab.web.remoteops.transfer import stage_config  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.profiles import Profile  # noqa: E402


class FakeTransport(Transport):
    """Stands in for a real shell resolving a (possibly `~`-relative) staging
    dir to an absolute path via `cd ... && pwd` — `resolved_dir` is that
    absolute path, independent of whatever the command's `cd` target was.
    """

    def __init__(self, mkdir_rc: int = 0, resolved_dir: str = "/home/researcher/base/CRAB"):
        self._mkdir_rc = mkdir_rc
        self._resolved_dir = resolved_dir
        self.run_calls: list[str] = []
        self.written_files: dict[str, str] = {}

    @property
    def alive(self) -> bool:
        return True

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.run_calls.append(command)
        if self._mkdir_rc != 0:
            return CmdResult(self._mkdir_rc, "", "boom")
        assert command.endswith("&& pwd")
        return CmdResult(0, self._resolved_dir + "\n", "")

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


async def test_stage_config_ssh_resolves_tilde_to_absolute_before_writing():
    # mkdir and the SFTP write must target the SAME resolved directory: SFTP
    # has no shell, so it cannot expand `~` the way `mkdir -p "$HOME/..."`
    # does — resolving via `cd ... && pwd` once removes the ambiguity instead
    # of betting on the remote sftp-server also expanding a literal `~`.
    transport = FakeTransport(resolved_dir="/home/researcher/base/CRAB/.web_staging")
    config = {"global_options": {"name": "My Run!"}, "experiments": []}

    path = await stage_config(transport, _ssh_profile(), config, "My Run!")

    assert transport.run_calls == [
        'mkdir -p "$HOME/base/CRAB/.web_staging" && cd "$HOME/base/CRAB/.web_staging" && pwd'
    ]
    assert path == "/home/researcher/base/CRAB/.web_staging/my-run.json"
    assert json.loads(transport.written_files[path]) == config


async def test_stage_config_local_uses_data_dir(tmp_path: Path):
    resolved = str(tmp_path / "data" / "web_staging")
    transport = FakeTransport(resolved_dir=resolved)
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    config = {"global_options": {}, "experiments": []}

    path = await stage_config(transport, _local_profile(), config, "demo", settings=settings)

    assert transport.run_calls == [f"mkdir -p {resolved} && cd {resolved} && pwd"]
    assert path == f"{resolved}/demo.json"
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
