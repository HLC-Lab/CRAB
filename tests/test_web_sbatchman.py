"""Local + remote persistence of a composed SbatchMan campaign
YAML (``store/sbatchman.py``, ``remoteops/transfer.py::stage_text``), and the
``/api/sbatchman`` write route. No real SSH — a fake transport stands in,
same pattern as test_web_jobs.py/test_web_remotes.py. Launch was removed in
ADR-026: SbatchMan owns launch/monitor/results, CRAB no longer triggers it.
"""

from __future__ import annotations

import shlex
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

_YAML = 'jobs:\n  - tag: "demo"\n'
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
    """A cluster that keeps files: write_file puts them in `written_files`, and the
    shell commands stage_text issues (`test -e`, `chmod`, `mv -f`, `rm -f`) act on it."""

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
        argv = shlex.split(command)
        if argv[:2] == ["test", "-e"]:
            return CmdResult(0 if argv[2] in self.written_files else 1, "", "")
        if argv[:2] == ["chmod", "600"]:
            return CmdResult(0 if argv[2] in self.written_files else 1, "", "")
        if argv[:2] == ["mv", "-f"]:
            self.written_files[argv[3]] = self.written_files.pop(argv[2])
            return CmdResult(0, "", "")
        if argv[:2] == ["rm", "-f"]:
            self.written_files.pop(argv[2], None)
            return CmdResult(0, "", "")
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


def _write_body(yaml_text: str = _YAML, name: str = "My Campaign", **extra) -> dict:
    return {"profile_name": "leonardo", "yaml": yaml_text, "name": name, **extra}


def _connect(client) -> None:
    client.post("/api/remotes", json=_leonardo_profile_body())
    client.post("/api/remotes/leonardo/connect")


def _local_campaign_files(tmp_path: Path) -> list[Path]:
    folder = _settings(tmp_path).sbatchman_dir
    return sorted(folder.glob("*.yaml")) if folder.exists() else []


def test_write_caps_a_very_long_name(tmp_path: Path):
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(name="x" * 300))

        assert resp.status_code == 200
        body = resp.json()
        remote_name = body["remote_path"].rsplit("/", 1)[1]
        assert remote_name == "x" * 60 + ".yaml"
        # The local copy is `<timestamp>-<slug>.yaml`: the slug part has the same cap.
        assert Path(body["local_path"]).name.endswith("-" + "x" * 60 + ".yaml")
        shown = client.get(
            "/api/sbatchman/staging-path", params={"profile_name": "leonardo", "name": "x" * 300}
        ).json()["path"]
        assert shown.rsplit("/", 1)[1] == remote_name


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("jobs: [unclosed\n", "not valid YAML"),
        ("foo: 1\n", "no `jobs` section"),
        ("- a\n- b\n", "no `jobs` section"),
        ("", "no `jobs` section"),
        ("just text\n", "no `jobs` section"),
        # The loader raises ValueError for an impossible date and RecursionError for deep nesting.
        ("jobs: []\nd: 2001-13-45\n", "not valid YAML"),
        ("[" * 5000, "not valid YAML"),
    ],
)
def test_write_refuses_a_body_that_is_not_a_campaign_file(
    tmp_path: Path, yaml_text: str, message: str
):
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(yaml_text))

        assert resp.status_code == 422
        assert resp.json()["code"] == "input_error"
        assert message in resp.json()["message"]
        assert _local_campaign_files(tmp_path) == []
        assert transport.written_files == {}
        assert not any(c.startswith(("chmod", "mv", "test")) for c in transport.calls)


def test_write_of_an_existing_cluster_file_asks_before_replacing_it(tmp_path: Path):
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)
        first = client.post("/api/sbatchman/write", json=_write_body())
        assert first.status_code == 200
        remote_path = first.json()["remote_path"]
        before = list(_local_campaign_files(tmp_path))
        other_yaml = 'jobs:\n  - tag: "changed"\n'

        second = client.post("/api/sbatchman/write", json=_write_body(other_yaml))

        assert second.status_code == 409
        assert second.json()["code"] == "conflict"
        assert second.json()["message"] == (
            "A campaign file named my-campaign.yaml already exists on the cluster. "
            "Write again to replace it."
        )
        assert transport.written_files == {remote_path: _YAML}
        assert _local_campaign_files(tmp_path) == before
        assert [p.read_text() for p in before] == [_YAML]


def test_write_with_overwrite_replaces_the_cluster_file(tmp_path: Path):
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)
        remote_path = client.post("/api/sbatchman/write", json=_write_body()).json()["remote_path"]
        other_yaml = 'jobs:\n  - tag: "changed"\n'

        resp = client.post("/api/sbatchman/write", json=_write_body(other_yaml, overwrite=True))

        assert resp.status_code == 200
        assert resp.json()["remote_path"] == remote_path
        assert transport.written_files == {remote_path: other_yaml}
        assert Path(resp.json()["local_path"]).read_text() == other_yaml


def test_write_says_so_when_only_the_local_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def broken_save(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("crab.web.api.sbatchman.save_campaign_yaml", broken_save)
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body())

        assert resp.status_code == 502
        body = resp.json()
        assert body["code"] == "remote_command_error"
        assert "written to the cluster" in body["message"]
        assert "local copy" in body["message"]
        assert "No space left on device" in body["detail"]
        assert list(transport.written_files.values()) == [_YAML]


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


def test_staging_path_for_an_ssh_profile_keeps_the_tilde(tmp_path: Path):
    body = {**_leonardo_profile_body(), "remote_crab": "~"}
    with _client(tmp_path, FakeTransport()) as client:
        client.post("/api/remotes", json=body)

        resp = client.get(
            "/api/sbatchman/staging-path", params={"profile_name": "leonardo", "name": "Pilot A2A"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"path": "~/CRAB/.web_staging/pilot-a2a.yaml"}


def test_staging_path_for_a_local_profile_is_under_the_data_dir(tmp_path: Path):
    with _client(tmp_path, FakeTransport()) as client:
        client.post("/api/remotes", json={"name": "here", "transport": "local"})

        resp = client.get(
            "/api/sbatchman/staging-path", params={"profile_name": "here", "name": "Pilot A2A"}
        )

        assert resp.json() == {"path": str(tmp_path / "data" / "web_staging" / "pilot-a2a.yaml")}


def test_staging_path_blank_name_falls_back_to_campaign(tmp_path: Path):
    with _client(tmp_path, FakeTransport()) as client:
        client.post("/api/remotes", json=_leonardo_profile_body())

        resp = client.get(
            "/api/sbatchman/staging-path", params={"profile_name": "leonardo", "name": "  "}
        )

        assert resp.json() == {"path": "~/base/CRAB/.web_staging/campaign.yaml"}


def test_staging_path_unknown_profile_is_not_found(tmp_path: Path):
    with _client(tmp_path, FakeTransport()) as client:
        resp = client.get(
            "/api/sbatchman/staging-path", params={"profile_name": "nope", "name": "x"}
        )

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


def test_staging_path_matches_where_write_puts_the_file(tmp_path: Path):
    transport = FakeTransport()
    with _client(tmp_path, transport) as client:
        client.post("/api/remotes", json=_leonardo_profile_body())
        client.post("/api/remotes/leonardo/connect")

        shown = client.get(
            "/api/sbatchman/staging-path",
            params={"profile_name": "leonardo", "name": "My Campaign"},
        ).json()["path"]
        written = client.post(
            "/api/sbatchman/write",
            json={"profile_name": "leonardo", "yaml": _YAML, "name": "My Campaign"},
        ).json()["remote_path"]

        assert shown.rsplit("/", 1)[1] == written.rsplit("/", 1)[1] == "my-campaign.yaml"


# --------------------------------------------------------------------------- #
# /api/sbatchman/campaigns — the saved-campaign-draft library
# --------------------------------------------------------------------------- #
_SPEC = {
    # A campaign saved before the dashboard dropped the configs field: stored as opaque data.
    "configsPath": "old/configs.yaml",
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
