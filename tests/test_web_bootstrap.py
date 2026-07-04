"""Phase 2b tests: guided CRAB install (detect / plan / install / verify).

Fake transport only — the real install over SSH is user-verified on Leonardo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.remoteops.bootstrap import (  # noqa: E402
    CRAB_REPO_BRANCH,
    CRAB_REPO_URL,
    build_install_command,
    default_plan,
    detect,
    install,
)
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
    assert "git clone" in blob and "make venv" in blob and "pip install -e ." in blob
    assert "$HOME/CRAB" in blob  # ~ expanded
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


def test_install_command_without_pre():
    cmd = build_install_command(_profile(), [])
    assert "git clone" in cmd and "make venv" in cmd
    assert "&&  &&" not in cmd  # no empty pre segment


async def test_detect_installed_vs_missing():
    p = _profile()
    ok = await detect(FakeTransport(), p)
    assert ok.installed and ok.info["presets"][0]["name"] == "leonardo"

    missing = await detect(FakeTransport(stdout="bash: crab: not found", rc=127), p)
    assert not missing.installed and missing.reason

    unparsable = await detect(FakeTransport(stdout="garbage", rc=0), p)
    assert not unparsable.installed


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
