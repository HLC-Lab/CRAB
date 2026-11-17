"""Tests for remoteops/transfer.py (stage_config): no real SSH, a fake transport
stands in (see tests/test_web_remotes.py's FakeTransport for the same pattern).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.transport import CmdResult, LocalTransport, Transport  # noqa: E402
from crab.web.errors import RemoteCommandError  # noqa: E402
from crab.web.remoteops.transfer import stage_config, staging_dir  # noqa: E402
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
        assert command.endswith("pwd)")
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
    # has no shell, so it cannot expand `~` the way `mkdir -p "$HOME"/...`
    # does — resolving via `cd ... && pwd` once removes the ambiguity instead
    # of betting on the remote sftp-server also expanding a literal `~`.
    transport = FakeTransport(resolved_dir="/home/researcher/base/CRAB/.web_staging")
    config = {"global_options": {"name": "My Run!"}, "experiments": []}

    path = await stage_config(transport, _ssh_profile(), config, "My Run!")

    assert transport.run_calls == [
        'mkdir -p "$HOME"/base/CRAB/.web_staging && cd "$HOME"/base/CRAB/.web_staging '
        "&& (chmod 700 . ; pwd)"
    ]
    assert path == "/home/researcher/base/CRAB/.web_staging/my-run.json"
    assert json.loads(transport.written_files[path]) == config


async def test_stage_config_crab_command_profile_stages_under_the_base_dir():
    # A pipx install has no <base>/CRAB checkout: staging sits directly under the base dir.
    transport = FakeTransport(resolved_dir="/home/researcher/base/.crab_web_staging")
    profile = _ssh_profile()
    profile.crab_command = "~/.local/bin/crab"

    path = await stage_config(transport, profile, {}, "demo")

    assert transport.run_calls == [
        'mkdir -p "$HOME"/base/.crab_web_staging && cd "$HOME"/base/.crab_web_staging '
        "&& (chmod 700 . ; pwd)"
    ]
    assert path == "/home/researcher/base/.crab_web_staging/demo.json"


def test_staging_dir_for_a_crab_command_profile_defaults_to_the_home_dir():
    profile = Profile(name="p", host="h.example.org", crab_command="/opt/bin/crab")
    assert staging_dir(profile) == "~/.crab_web_staging"
    profile.remote_crab = "/scratch/me/"
    assert staging_dir(profile) == "/scratch/me/.crab_web_staging"


async def test_stage_config_local_uses_data_dir(tmp_path: Path):
    resolved = str(tmp_path / "data" / "web_staging")
    transport = FakeTransport(resolved_dir=resolved)
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    config = {"global_options": {}, "experiments": []}

    path = await stage_config(transport, _local_profile(), config, "demo", settings=settings)

    assert transport.run_calls == [f"mkdir -p {resolved} && cd {resolved} && (chmod 700 . ; pwd)"]
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


async def test_stage_config_still_stages_when_chmod_fails(tmp_path: Path, monkeypatch):
    # A shared checkout made by another account: chmod on its staging folder fails,
    # but the run must still be staged. A fake chmod that always fails stands in.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "chmod").write_text("#!/bin/sh\necho 'chmod: not permitted' >&2\nexit 1\n")
    (fake_bin / "chmod").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")

    path = await stage_config(
        LocalTransport(), _local_profile(), {"experiments": []}, "demo", settings=settings
    )

    assert json.loads(Path(path).read_text()) == {"experiments": []}


def test_ensure_dirs_makes_private_folders(tmp_path: Path):
    """The data and config folders hold job registries and cached results: 0700."""
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")

    settings.ensure_dirs()

    for folder in (settings.config_dir, settings.data_dir, settings.experiments_dir):
        assert folder.stat().st_mode & 0o777 == 0o700
