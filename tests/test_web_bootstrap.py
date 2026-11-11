"""Tests for guided CRAB install (detect / plan / install / verify).

Fake transport only — the real install over SSH is user-verified on Leonardo.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import ContractError, InputError, RemoteCommandError  # noqa: E402
from crab.web.remoteops.bootstrap import (  # noqa: E402
    CRAB_REPO_BRANCH,
    CRAB_REPO_URL,
    _clone_command,
    build_install_command,
    default_plan,
    detect,
    install,
)
from crab.web.remoteops.crab_cli import run_crab_json  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.profiles import Profile  # noqa: E402

_INFO_JSON = '{"schema":1,"crab_version":"0.1.0","crab_root":"/h/CRAB","presets":[{"name":"leonardo","description":"x"}]}'


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


class FakeTransport(Transport):
    def __init__(self, stdout: str = _INFO_JSON, rc: int = 0, stderr: str = ""):
        self._stdout, self._rc, self._stderr = stdout, rc, stderr
        self._alive = True
        self.calls: list[str] = []

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.calls.append(command)
        return CmdResult(self._rc, self._stdout, self._stderr)

    async def close(self) -> None:
        self._alive = False


def _profile() -> Profile:
    return Profile(
        name="leo",
        host="login.cluster.example.org",
        user="someresearcher",
        auth="agent",
        hostkey_policy="insecure",
        remote_crab="~",
    )


# --------------------------------------------------------------------------- #
# Pure logic
# --------------------------------------------------------------------------- #
def test_default_plan_is_clean_and_hardcoded():
    steps = default_plan(_profile())
    assert [s.id for s in steps] == ["clone", "build"]
    blob = " ".join(s.command for s in steps)
    # Repo + branch + structure are fixed; the remote path is honoured.
    assert CRAB_REPO_URL in blob and CRAB_REPO_BRANCH in blob
    assert "--branch develop " in blob  # the product line, not the old feature branch
    assert "git clone" in blob and "make venv" in blob and "pip install -e ." in blob
    assert '"$HOME"/CRAB' in blob  # ~ expanded
    # After installing, move to the newest release of the branch's line (stays put if none).
    assert blob.endswith("&& .venv/bin/crab update --to-release")
    # Preview commands are readable: no bash -lc wrapper, no personal data.
    assert "bash -lc" not in blob
    assert _profile().user not in blob  # no personal data in previews


def test_install_command_runs_pre_once_in_one_shell():
    cmd = build_install_command(_profile(), ["module load python", "module load git"])
    # Single login shell, pre-commands once, then clone then build.
    assert cmd.startswith("bash -lc ")
    assert cmd.count("module load python") == 1
    i_pre = cmd.index("module load python")
    i_clone = cmd.index("git clone")
    i_build = cmd.index("make venv")
    assert i_pre < i_clone < i_build


def test_install_command_drops_blank_pre_lines_and_strips_the_rest():
    cmd = build_install_command(_profile(), ["", "  module load python  ", "   "])
    assert "module load python &&" in cmd
    assert "&&  &&" not in cmd and "&& &&" not in cmd
    assert cmd.index("module load python") < cmd.index("git clone")


@pytest.mark.parametrize("bad", ["module load a\nmodule load b", "module load x # comment", "#"])
def test_install_command_refuses_a_pre_command_with_a_newline_or_hash(bad: str):
    with pytest.raises(InputError, match="Pre-command 2 must be one line without '#'"):
        build_install_command(_profile(), ["module load python", bad])


def test_install_command_without_pre():
    cmd = build_install_command(_profile(), [])
    assert "git clone" in cmd and "make venv" in cmd
    assert "&&  &&" not in cmd  # no empty pre segment


def test_install_command_clones_only_when_there_is_no_checkout():
    cmd = build_install_command(_profile(), [])
    # The clone is guarded by the `.git` folder, and the build still follows it.
    assert 'test -d "$HOME"/CRAB/.git || git clone' in cmd
    assert cmd.index("test -d") < cmd.index("git clone") < cmd.index("make venv")
    assert "unless it is already there" in default_plan(_profile())[0].label


def _fake_git(tmp_path: Path) -> tuple[Path, Path]:
    """A `git` that records its call and fails, so a clone shows up as a marker file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "git-called"
    git = bin_dir / "git"
    git.write_text(f'#!/bin/sh\necho "$@" > {marker}\nexit 1\n')
    git.chmod(0o755)
    return bin_dir, marker


def _run_clone_chain(profile: Profile, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    chain = f"{_clone_command(profile)} && echo BUILD-STEPS-REACHED"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", chain], env=env, capture_output=True, text=True, timeout=30, check=False
    )


def test_clone_step_is_skipped_when_the_folder_is_already_a_clone(tmp_path: Path):
    base = tmp_path / "base"
    (base / "CRAB" / ".git").mkdir(parents=True)  # a half-built folder: clone, no .venv
    bin_dir, marker = _fake_git(tmp_path)
    profile = _profile()
    profile.remote_crab = str(base)

    run = _run_clone_chain(profile, bin_dir)

    assert run.returncode == 0, run.stderr
    assert "BUILD-STEPS-REACHED" in run.stdout  # the chain continues to the build steps
    assert not marker.exists()  # git was never called


def test_clone_step_runs_when_there_is_no_checkout(tmp_path: Path):
    base = tmp_path / "base"
    base.mkdir()
    bin_dir, marker = _fake_git(tmp_path)
    profile = _profile()
    profile.remote_crab = str(base)

    run = _run_clone_chain(profile, bin_dir)

    assert run.returncode != 0  # the fake git fails, so the chain stops
    assert "BUILD-STEPS-REACHED" not in run.stdout
    assert "clone --branch develop" in marker.read_text()


async def test_detect_installed_vs_missing():
    p = _profile()
    ok = await detect(FakeTransport(), p)
    assert ok.installed and ok.info["presets"][0]["name"] == "leonardo"

    missing = await detect(FakeTransport(stdout="bash: crab: not found", rc=127), p)
    assert not missing.installed and missing.reason

    unparsable = await detect(FakeTransport(stdout="garbage", rc=0), p)
    assert not unparsable.installed


async def test_detect_no_checkout_is_not_installed():
    p = _profile()
    cd_failed = await detect(
        FakeTransport(
            stdout="",
            rc=1,
            stderr="bash: line 1: cd: /home/u/CRAB: No such file or directory\n",
        ),
        p,
    )
    assert cd_failed.installed is False
    assert cd_failed.reason and "No such file or directory" in cd_failed.reason

    not_found = await detect(
        FakeTransport(stdout="", rc=127, stderr="bash: line 1: crab: command not found\n"), p
    )
    assert not_found.installed is False
    assert not_found.reason and "command not found" in not_found.reason

    # Exit 127 alone (the shell could not find the command) is enough.
    bare_127 = await detect(FakeTransport(stdout="bash: crab: not found", rc=127), p)
    assert bare_127.installed is False and bare_127.reason


async def test_detect_missing_venv_is_not_installed():
    result = await detect(
        FakeTransport(
            stdout="",
            rc=1,
            stderr="bash: line 1: /home/u/CRAB/.venv/bin/activate: No such file or directory\n",
        ),
        _profile(),
    )
    assert result.installed is False
    assert result.reason and "activate" in result.reason


async def test_detect_crab_that_ran_and_failed_raises_with_the_traceback():
    stderr = (
        "Traceback (most recent call last):\n"
        '  File "/home/u/CRAB/.venv/bin/crab", line 5, in <module>\n'
        "    from crab.cli import main\n"
        "ImportError: cannot import name 'main' from 'crab.cli'\n"
    )
    with pytest.raises(RemoteCommandError) as excinfo:
        await detect(FakeTransport(stdout="", rc=1, stderr=stderr), _profile())
    assert "ImportError: cannot import name 'main'" in (excinfo.value.detail or "")

    # A traceback that merely mentions a missing file is still CRAB failing, not CRAB missing.
    stderr = (
        "Traceback (most recent call last):\n"
        "FileNotFoundError: [Errno 2] No such file or directory: 'settings.toml'\n"
    )
    with pytest.raises(RemoteCommandError):
        await detect(FakeTransport(stdout="", rc=1, stderr=stderr), _profile())


async def test_detect_survives_login_noise_before_the_json():
    stdout = f"Welcome to the cluster\nModule loaded\n{_INFO_JSON}\n"
    result = await detect(FakeTransport(stdout=stdout), _profile())
    assert result.installed is True
    assert result.info is not None and result.info["presets"][0]["name"] == "leonardo"


async def test_run_crab_json_takes_the_last_json_object_line():
    p = _profile()
    args = ["info", "--json"]
    noisy_after = await run_crab_json(
        FakeTransport(stdout='{"old": 1}\nnoise\n{"new": 2}\nbye\n'), p, args
    )
    assert noisy_after == {"new": 2}

    # A pretty-printed object (several lines) still parses as a whole.
    pretty = await run_crab_json(FakeTransport(stdout='{\n  "a": 1,\n  "b": {}\n}\n'), p, args)
    assert pretty == {"a": 1, "b": {}}

    # An indented list of empty objects is a list, not its last `{}` line.
    as_list = await run_crab_json(FakeTransport(stdout="[\n  {},\n  {}\n]\n"), p, args)
    assert as_list == [{}, {}]

    with pytest.raises(ContractError):
        await run_crab_json(FakeTransport(stdout="Welcome\nno json here\n"), p, args)


async def test_install_captures_output():
    res = await install(FakeTransport(stdout="cloned\nbuilt", rc=0), _profile(), [])
    assert res.ok and res.rc == 0 and "built" in res.stdout

    fail = await install(FakeTransport(stdout="", rc=2, stderr="boom"), _profile(), [])
    assert not fail.ok and fail.stderr == "boom"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def _client(tmp_path: Path, transport: FakeTransport):
    async def connector(profile, password):
        return transport

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    return auth_client(app)


def test_bootstrap_api_flow(tmp_path: Path):
    # CRAB missing → connect handshake fails, but transport stays connected.
    transport = FakeTransport(stdout="not json", rc=0)
    with _client(tmp_path, transport) as client:
        client.post("/api/remotes", json=_profile().model_dump())
        client.post("/api/remotes/leo/connect")  # surfaces a handshake error

        plan = client.post("/api/remotes/leo/bootstrap/plan").json()
        assert plan["installed"] is False
        assert [s["id"] for s in plan["steps"]] == ["clone", "build"]

        run = client.post("/api/remotes/leo/bootstrap/install", json={"pre_commands": []}).json()
        assert "rc" in run and "stdout" in run

        # Flip the fake to a healthy CRAB → verify reports installed.
        transport._stdout, transport._rc = _INFO_JSON, 0
        verify = client.post("/api/remotes/leo/bootstrap/verify").json()
        assert verify["installed"] is True


def test_bootstrap_requires_connection(tmp_path: Path):
    with _client(tmp_path, FakeTransport()) as client:
        client.post("/api/remotes", json=_profile().model_dump())
        resp = client.post("/api/remotes/leo/bootstrap/plan")
        assert resp.status_code == 502
        assert resp.json()["code"] == "connection_error"


# --------------------------------------------------------------------------- #
# A profile with its own crab command: the user manages the install
# --------------------------------------------------------------------------- #
def _managed_profile() -> Profile:
    p = _profile()
    p.crab_command = "~/.local/bin/crab"
    return p


def test_a_crab_command_profile_has_no_install_plan():
    with pytest.raises(InputError, match="pipx install crab-hpc"):
        default_plan(_managed_profile())
    with pytest.raises(InputError, match="pipx install crab-hpc"):
        build_install_command(_managed_profile(), [])


def test_bootstrap_plan_and_install_refuse_a_crab_command_profile(tmp_path: Path):
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        client.post("/api/remotes", json=_managed_profile().model_dump())
        client.post("/api/remotes/leo/connect")
        transport.calls.clear()

        for route, body in (("plan", None), ("install", {"pre_commands": []})):
            resp = client.post(f"/api/remotes/leo/bootstrap/{route}", json=body)
            assert resp.status_code == 422, route
            assert resp.json()["code"] == "input_error"
            assert "you manage this install: pipx install crab-hpc" in resp.json()["message"]
        assert transport.calls == []  # nothing ran on the cluster

        # verify is only a `crab info` re-check, so it stays available.
        assert client.post("/api/remotes/leo/bootstrap/verify").json()["installed"] is True
