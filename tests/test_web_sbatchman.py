"""Plan 084 S7: local + remote persistence of a composed SbatchMan campaign
YAML (``store/sbatchman.py``, ``remoteops/transfer.py::stage_text``), and the
``/api/sbatchman`` write route. No real SSH — a fake transport stands in,
same pattern as test_web_jobs.py/test_web_remotes.py. Launch was removed in
plan 085 — SbatchMan owns launch/monitor/results, CRAB no longer triggers it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import RemoteCommandError  # noqa: E402
from crab.web.remoteops.transfer import stage_text  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.profiles import Profile  # noqa: E402
from crab.web.store.sbatchman import save_campaign_yaml  # noqa: E402

_YAML = 'configs: "configs.yaml"\njobs:\n  - tag: "demo"\n'
_INFO_JSON = '{"schema":1,"crab_version":"0.1.0","crab_root":"/home/u/CRAB","presets":[{"name":"leonardo","description":"Leonardo @ CINECA"}]}'


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def _leonardo() -> Profile:
    return Profile(
        name="leonardo",
        host="login.cluster.example.org",
        user="researcher",
        auth="agent",
        hostkey_policy="insecure",
        remote_crab="~/base",
        preset="leonardo",
    )


class FakeTransport(Transport):
    def __init__(self):
        self._alive = True
        self.calls: list[str] = []
        self.written_files: dict[str, str] = {}

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.calls.append(command)
        if "crab info --json" in command:
            return CmdResult(0, _INFO_JSON, "")
        if "mkdir -p" in command:
            return CmdResult(0, "/home/researcher/base/CRAB/.web_staging\n", "")
        raise AssertionError(f"unexpected command: {command}")

    async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
        self.written_files[path] = content

    async def close(self) -> None:
        self._alive = False


def _client(tmp_path: Path, transport: Transport):
    async def connector(profile, password):
        return transport

    mgr = ConnectionManager(connector=connector)
    app = create_app(_settings(tmp_path), manager=mgr)
    return auth_client(app)


def _leonardo_profile_body() -> dict:
    return {
        "name": "leonardo",
        "host": "login.cluster.example.org",
        "user": "researcher",
        "auth": "agent",
        "hostkey_policy": "insecure",
        "remote_crab": "~/base",
        "preset": "leonardo",
    }


# --------------------------------------------------------------------------- #
# store/sbatchman.py (pure, no transport)
# --------------------------------------------------------------------------- #
def test_save_campaign_yaml_writes_a_timestamped_file(tmp_path: Path):
    settings = _settings(tmp_path)
    path = save_campaign_yaml(_YAML, "My Campaign", settings)

    assert path.parent == settings.sbatchman_dir
    assert path.suffix == ".yaml"
    assert "my-campaign" in path.name
    assert path.read_text() == _YAML


# --------------------------------------------------------------------------- #
# remoteops/transfer.py::stage_text
# --------------------------------------------------------------------------- #
async def test_stage_text_resolves_dir_and_writes_via_transport():
    transport = FakeTransport()
    profile = _leonardo()

    remote_path = await stage_text(transport, profile, _YAML, "campaign.yaml")

    assert remote_path == "/home/researcher/base/CRAB/.web_staging/campaign.yaml"
    assert transport.written_files[remote_path] == _YAML


async def test_stage_text_raises_on_mkdir_failure():
    class FailingTransport(FakeTransport):
        async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
            return CmdResult(1, "", "permission denied")

    with pytest.raises(RemoteCommandError):
        await stage_text(FailingTransport(), _leonardo(), _YAML, "campaign.yaml")


# --------------------------------------------------------------------------- #
# /api/sbatchman routes
# --------------------------------------------------------------------------- #
def test_write_persists_locally_and_remotely(tmp_path: Path):
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        client.post("/api/remotes", json=_leonardo_profile_body())
        client.post("/api/remotes/leonardo/connect")

        resp = client.post(
            "/api/sbatchman/write",
            json={"profile_name": "leonardo", "yaml": _YAML, "name": "My Campaign"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert Path(body["local_path"]).read_text() == _YAML
        assert body["remote_path"].endswith(".web_staging/my-campaign.yaml")
        assert transport.written_files[body["remote_path"]] == _YAML


def test_write_without_connection_fails(tmp_path: Path):
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        client.post("/api/remotes", json=_leonardo_profile_body())

        resp = client.post(
            "/api/sbatchman/write",
            json={"profile_name": "leonardo", "yaml": _YAML, "name": "demo"},
        )

        assert resp.status_code == 502
        assert resp.json()["code"] == "connection_error"


# --------------------------------------------------------------------------- #
# /api/sbatchman/campaigns — the saved-campaign-draft library (plan 086)
# --------------------------------------------------------------------------- #
_SPEC = {
    "configsPath": "",
    "crabRoot": "",
    "system": "",
    "env": [],
    "variables": [{"name": "nodes", "values": ["4", "8"]}],
    "groups": [{"tag": "baseline-{nodes}", "preset": "", "variables": [], "draft": {}}],
}


def test_campaigns_api_flow(tmp_path: Path):
    with _client(tmp_path, FakeTransport()) as client:
        assert client.get("/api/sbatchman/campaigns").json() == []

        created = client.post(
            "/api/sbatchman/campaigns", json={"name": "Campaign A", "spec": _SPEC}
        )
        assert created.status_code == 201
        cid = created.json()["id"]
        assert cid == "campaign-a"

        assert client.get(f"/api/sbatchman/campaigns/{cid}").json()["name"] == "Campaign A"

        updated_spec = {**_SPEC, "system": "leonardo"}
        client.put(
            f"/api/sbatchman/campaigns/{cid}", json={"name": "Campaign A2", "spec": updated_spec}
        )
        got = client.get(f"/api/sbatchman/campaigns/{cid}").json()
        assert got["name"] == "Campaign A2"
        assert got["spec"]["system"] == "leonardo"

        dup = client.post(f"/api/sbatchman/campaigns/{cid}/duplicate")
        assert dup.status_code == 201 and dup.json()["id"] != cid

        assert len(client.get("/api/sbatchman/campaigns").json()) == 2

        assert client.delete(f"/api/sbatchman/campaigns/{cid}").status_code == 204
        assert client.get(f"/api/sbatchman/campaigns/{cid}").status_code == 404


def test_campaign_with_var_placeholders_saves_without_validation(tmp_path: Path):
    """Unlike /api/experiments, no shape-validation pass — a group's `{var}`
    placeholders would fail typed single-config validation."""
    with _client(tmp_path, FakeTransport()) as client:
        resp = client.post("/api/sbatchman/campaigns", json={"name": "Swept", "spec": _SPEC})
        assert resp.status_code == 201
        assert "warnings" not in resp.json()
