"""Tests for remoteops/transfer.py (stage_config): no real SSH, a fake transport
stands in (see tests/test_web_remotes.py's FakeTransport for the same pattern).
"""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import (  # noqa: E402
    ConflictError,
    RemoteCommandError,
    RemoteConnectionError,
)
from crab.web.remoteops.transfer import stage_config, stage_text, staging_dir  # noqa: E402
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
    # has no shell, so it cannot expand `~` the way `mkdir -p "$HOME"/...`
    # does — resolving via `cd ... && pwd` once removes the ambiguity instead
    # of betting on the remote sftp-server also expanding a literal `~`.
    transport = FakeTransport(resolved_dir="/home/researcher/base/CRAB/.web_staging")
    config = {"global_options": {"name": "My Run!"}, "experiments": []}

    path = await stage_config(transport, _ssh_profile(), config, "My Run!")

    assert transport.run_calls == [
        'mkdir -p "$HOME"/base/CRAB/.web_staging && cd "$HOME"/base/CRAB/.web_staging && pwd'
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
        'mkdir -p "$HOME"/base/.crab_web_staging && cd "$HOME"/base/.crab_web_staging && pwd'
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


# --------------------------------------------------------------------------- #
# stage_text: resolving the directory, and the temp-write-then-move sequence
# --------------------------------------------------------------------------- #
_DIR = "/home/u/CRAB/.web_staging"


class RecordingTransport(Transport):
    """Records every operation in order. `resolver_output` is what the directory
    resolver prints; `fail_on` makes every command starting with that text fail."""

    def __init__(self, resolver_output: str = _DIR + "\n", fail_on: str | None = None):
        self._resolver_output = resolver_output
        self._fail_on = fail_on
        self.ops: list[tuple[str, str]] = []

    @property
    def alive(self) -> bool:
        return True

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.ops.append(("run", command))
        if self._fail_on and command.startswith(self._fail_on):
            return CmdResult(1, "", "no space left")
        if command.endswith("&& pwd"):
            return CmdResult(0, self._resolver_output, "")
        return CmdResult(0, "", "")

    async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
        self.ops.append(("write", path))

    async def close(self) -> None:
        pass


async def test_stage_text_takes_the_last_line_of_the_resolver_output():
    # A login shell can print a banner or module messages before `pwd` runs.
    transport = RecordingTransport(
        resolver_output="Welcome\nModule x loaded\n/home/u/CRAB/.web_staging\n"
    )

    path = await stage_text(transport, _ssh_profile(), "jobs: []\n", "c.yaml")

    assert path == f"{_DIR}/c.yaml"
    assert transport.ops[1][1].startswith(f"{_DIR}/c.yaml.tmp-")


@pytest.mark.parametrize("output", ["Welcome\nnot a path\n", "relative/dir\n", "\n \n"])
async def test_stage_text_rejects_resolver_output_that_is_not_an_absolute_path(output: str):
    transport = RecordingTransport(resolver_output=output)

    with pytest.raises(RemoteCommandError):
        await stage_text(transport, _ssh_profile(), "x", "c.yaml")

    assert [kind for kind, _ in transport.ops] == ["run"]


async def test_stage_text_writes_a_temp_file_sets_mode_600_then_moves_it_into_place():
    transport = RecordingTransport()

    path = await stage_text(transport, _ssh_profile(), "jobs: []\n", "my campaign.yaml")

    assert path == f"{_DIR}/my campaign.yaml"
    assert [kind for kind, _ in transport.ops] == ["run", "write", "run", "run"]
    tmp = transport.ops[1][1]
    assert tmp.startswith(f"{_DIR}/my campaign.yaml.tmp-")
    assert len(tmp) > len(f"{_DIR}/my campaign.yaml.tmp-")
    assert transport.ops[2][1] == f"chmod 600 {shlex.quote(tmp)}"
    assert transport.ops[3][1] == f"mv -f {shlex.quote(tmp)} {shlex.quote(path)}"


async def test_stage_text_uses_a_different_temp_name_each_time():
    first, second = RecordingTransport(), RecordingTransport()

    await stage_text(first, _ssh_profile(), "x", "c.yaml")
    await stage_text(second, _ssh_profile(), "x", "c.yaml")

    assert first.ops[1][1] != second.ops[1][1]


async def test_stage_text_removes_the_temp_file_when_the_move_fails():
    transport = RecordingTransport(fail_on="mv -f")

    with pytest.raises(RemoteCommandError):
        await stage_text(transport, _ssh_profile(), "x", "c.yaml")

    tmp = transport.ops[1][1]
    assert transport.ops[-1] == ("run", f"rm -f {shlex.quote(tmp)}")


async def test_stage_text_refuses_to_replace_an_existing_file_unless_told_to():
    class ExistingFile(RecordingTransport):
        async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
            if command.startswith("test -e"):
                self.ops.append(("run", command))
                return CmdResult(0, "", "")
            return await super().run(command, timeout)

    refused = ExistingFile()
    with pytest.raises(ConflictError, match="already exists on the cluster"):
        await stage_text(refused, _ssh_profile(), "x", "c.yaml", overwrite=False)
    assert ("run", f"test -e {_DIR}/c.yaml") in refused.ops
    assert not any(kind == "write" for kind, _ in refused.ops)

    allowed = ExistingFile()
    path = await stage_text(allowed, _ssh_profile(), "x", "c.yaml")
    assert path == f"{_DIR}/c.yaml"
    assert not any(command.startswith("test -e") for _, command in allowed.ops)


class ScriptedTransport(RecordingTransport):
    """RecordingTransport whose commands can answer a chosen exit code or raise."""

    def __init__(
        self,
        rc_for: dict[str, int] | None = None,
        raise_on: dict | None = None,
        resolver_output: str = _DIR + "\n",
    ):
        super().__init__(resolver_output=resolver_output)
        self._rc_for = rc_for or {}
        self._raise_on = raise_on or {}

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        for prefix, exc in self._raise_on.items():
            if command.startswith(prefix):
                self.ops.append(("run", command))
                raise exc
        for prefix, rc in self._rc_for.items():
            if command.startswith(prefix):
                self.ops.append(("run", command))
                return CmdResult(rc, "", "ssh: connection reset")
        return await super().run(command, timeout)

    async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
        for prefix, exc in self._raise_on.items():
            if prefix == "write":
                self.ops.append(("write", path))
                raise exc
        await super().write_file(path, content, timeout)


async def test_stage_text_treats_an_unknown_test_result_as_a_failure_and_writes_nothing():
    # `test -e` answers 0 (exists) or 1 (absent); anything else (ssh itself failed: 255)
    # says nothing about the file, so nothing may be written on the strength of it.
    transport = ScriptedTransport(rc_for={"test -e": 255})

    with pytest.raises(RemoteCommandError) as excinfo:
        await stage_text(transport, _ssh_profile(), "x", "c.yaml", overwrite=False)

    assert excinfo.value.detail == "ssh: connection reset"
    assert not any(kind == "write" for kind, _ in transport.ops)


async def test_stage_text_continues_when_test_says_the_file_is_absent():
    transport = ScriptedTransport(rc_for={"test -e": 1})

    path = await stage_text(transport, _ssh_profile(), "x", "c.yaml", overwrite=False)

    assert path == f"{_DIR}/c.yaml"
    assert [kind for kind, _ in transport.ops] == ["run", "run", "write", "run", "run"]


async def test_stage_text_cleanup_failure_does_not_hide_the_original_error():
    transport = ScriptedTransport(
        rc_for={"mv -f": 1}, raise_on={"rm -f": RemoteConnectionError("connection lost")}
    )

    with pytest.raises(RemoteCommandError, match="Could not move"):
        await stage_text(transport, _ssh_profile(), "x", "c.yaml")


async def test_stage_text_cleans_up_when_cancelled_and_stays_cancelled():
    transport = ScriptedTransport(raise_on={"write": asyncio.CancelledError()})

    with pytest.raises(asyncio.CancelledError):
        await stage_text(transport, _ssh_profile(), "x", "c.yaml")

    tmp = transport.ops[1][1]
    assert transport.ops[-1] == ("run", f"rm -f {shlex.quote(tmp)}")


async def test_stage_text_quotes_a_staging_directory_with_spaces_quotes_and_dollars():
    weird = "/home/u/my dir/it's $HOME/.web_staging"
    transport = ScriptedTransport(rc_for={"test -e": 1, "mv -f": 1}, resolver_output=weird + "\n")

    with pytest.raises(RemoteCommandError):
        await stage_text(transport, _ssh_profile(), "x", "c.yaml", overwrite=False)

    commands = [command for kind, command in transport.ops if kind == "run"][1:]
    assert [shlex.split(command)[0] for command in commands] == ["test", "chmod", "mv", "rm"]
    tmp = transport.ops[2][1]
    assert tmp.startswith(f"{weird}/c.yaml.tmp-")
    assert [shlex.split(command)[1:] for command in commands] == [
        ["-e", f"{weird}/c.yaml"],
        ["600", tmp],
        ["-f", tmp, f"{weird}/c.yaml"],
        ["-f", tmp],
    ]
