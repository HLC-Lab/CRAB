"""Local + remote persistence of a composed SbatchMan campaign
YAML (``store/sbatchman.py``, ``remoteops/transfer.py::stage_text``), and the
``/api/sbatchman`` write route. No real SSH — a fake transport stands in,
same pattern as test_web_jobs.py/test_web_remotes.py. Launch was removed in
ADR-026: SbatchMan owns launch/monitor/results, CRAB no longer triggers it.
"""

from __future__ import annotations

import re
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
    shell commands stage_text issues (`chmod`, `ln`, `test -e`, `mv -f`, `rm -f`) act on it."""

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
        if argv[:2] == ["ln", "--"]:
            # A hard link: refused when the final name is taken, otherwise both names hold it.
            if argv[3] in self.written_files:
                return CmdResult(1, "", "ln: failed to create hard link: File exists")
            self.written_files[argv[3]] = self.written_files[argv[2]]
            return CmdResult(0, "", "")
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


# --------------------------------------------------------------------------- #
# /api/sbatchman/target: umbrella or plain install
# --------------------------------------------------------------------------- #
_CONFIGS = """\
variables:
  nodes: [2, 4]

leonardo:
  scheduler: slurm
  default_conf:
    account: "x"
  configs:
    - name: "crab_run_{nodes}"
      nodes: "{nodes}"
    - name: "crab_qe_{nodes}"
    - name: "crab_run_{nodes}"

other:
  scheduler: slurm
  configs:
    - name: "gpu_box"
    - nodes: 3
    - just a string
"""

_ROOT = "/home/researcher/base"
_MARKER = re.compile(r"@@([0-9a-f]{16}):root")


def _probe_output(
    command: str,
    *,
    root: str = _ROOT,
    configs: str = ".local/configs.leonardo.yaml",
    benchmarks: tuple[str, ...] = ("blink", "qe"),
    content: str = _CONFIGS,
    banner: str = "",
) -> str:
    """What the probe script prints, using the random marker the command carries. The
    content section is there only when the command asks for it."""
    match = _MARKER.search(command)
    assert match, "the probe command carries no marker"
    mark = f"@@{match.group(1)}"
    out = f"{banner}{mark}:root\n{root}\n{mark}:configs\n{configs}\n{mark}:benchmarks\n"
    out += "".join(f"{name}\n" for name in benchmarks)
    if f"{mark}:content" in command:
        out += f"{mark}:content\n{content}"
    return out


def _ok(**fields):
    """A probe answer: rc 0 and the script's output for ``fields``."""
    return lambda command: CmdResult(0, _probe_output(command, **fields), "")


_MESSY = {
    "banner": "Welcome to the cluster\n",
    "benchmarks": ("blink", "qe", "bad name", "..", ".hidden"),
}


class ProbeTransport(Transport):
    """Answers the umbrella probe with a fixed result, or raises."""

    def __init__(self, result):
        # A CmdResult, an exception to raise, or a function of the command.
        self._result = result
        self.calls: list[str] = []

    @property
    def alive(self) -> bool:
        return True

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        if "crab info --json" in command:  # the connect step, not the probe
            return CmdResult(0, _INFO_JSON, "")
        self.calls.append(command)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result(command) if callable(self._result) else self._result

    async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
        raise AssertionError("the probe never writes")

    async def close(self) -> None:
        pass


def _target(tmp_path: Path, transport: Transport, *, connect: bool = True) -> dict:
    with _client(tmp_path, transport) as client:
        client.post("/api/remotes", json=_leonardo_profile_body())
        if connect:
            client.post("/api/remotes/leonardo/connect")
        resp = client.get("/api/sbatchman/target", params={"profile_name": "leonardo"})
        assert resp.status_code == 200
        return resp.json()


_PLAIN = {
    "kind": "plain",
    "root": "",
    "configs_file": "",
    "configs_names": [],
    "benchmarks": [],
}


def test_target_of_an_umbrella_lists_configs_and_benchmark_folders(tmp_path: Path):
    transport = ProbeTransport(_ok(**_MESSY))

    body = _target(tmp_path, transport)

    assert body == {
        "kind": "umbrella",
        "root": "/home/researcher/base",
        "configs_file": "/home/researcher/base/.local/configs.leonardo.yaml",
        # Both cluster blocks, in file order, once each; entries without a name are skipped.
        "configs_names": ["crab_run_{nodes}", "crab_qe_{nodes}", "gpu_box"],
        # Only names that are safe as one folder; sorted.
        "benchmarks": ["blink", "qe"],
    }
    assert len(transport.calls) == 1


def test_target_of_a_plain_install_is_plain(tmp_path: Path):
    for rc in (2, 3, 4):
        assert _target(tmp_path, ProbeTransport(CmdResult(rc, "", ""))) == _PLAIN


def test_target_with_a_failing_command_is_plain(tmp_path: Path):
    from crab.web.errors import RemoteConnectionError

    assert _target(tmp_path, ProbeTransport(RemoteConnectionError("dropped"))) == _PLAIN


def test_target_with_output_that_is_not_the_probes_is_plain(tmp_path: Path):
    assert _target(tmp_path, ProbeTransport(CmdResult(0, "hello\n", ""))) == _PLAIN
    assert _target(tmp_path, ProbeTransport(_ok(root="relative"))) == _PLAIN


def test_target_with_an_unreadable_configs_file_is_an_umbrella_without_names(tmp_path: Path):
    body = _target(tmp_path, ProbeTransport(_ok(benchmarks=("blink",), content="")))

    assert body["kind"] == "umbrella"
    assert body["configs_names"] == []
    assert body["benchmarks"] == ["blink"]


def test_target_with_a_configs_file_that_is_not_yaml_has_no_names(tmp_path: Path):
    body = _target(tmp_path, ProbeTransport(_ok(benchmarks=(), content="leonardo: [unclosed\n")))

    assert body["kind"] == "umbrella"
    assert body["configs_names"] == []


def test_target_of_a_cluster_that_is_not_connected_is_plain_and_runs_nothing(tmp_path: Path):
    transport = ProbeTransport(_ok(**_MESSY))

    assert _target(tmp_path, transport, connect=False) == _PLAIN
    assert transport.calls == []


def test_target_of_a_local_profile_is_plain(tmp_path: Path):
    transport = ProbeTransport(_ok(**_MESSY))
    with _client(tmp_path, transport) as client:
        client.post("/api/remotes", json={"name": "here", "transport": "local"})

        resp = client.get("/api/sbatchman/target", params={"profile_name": "here"})

        assert resp.json() == _PLAIN
        assert transport.calls == []


def test_target_of_an_unknown_profile_is_not_found(tmp_path: Path):
    with _client(tmp_path, ProbeTransport(CmdResult(0, "", ""))) as client:
        resp = client.get("/api/sbatchman/target", params={"profile_name": "nope"})

        assert resp.status_code == 404


class ShellTransport(Transport):
    """Runs the probe in a real shell, so the script itself is tested."""

    @property
    def alive(self) -> bool:
        return True

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        import asyncio

        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        return CmdResult(proc.returncode or 0, out.decode(), err.decode())

    async def write_file(self, path: str, content: str, timeout: float | None = 30.0) -> None:
        raise AssertionError("the probe never writes")

    async def close(self) -> None:
        pass


async def test_the_probe_script_reads_a_real_umbrella_folder(tmp_path: Path):
    from crab.web.remoteops.umbrella import probe_umbrella

    root = tmp_path / "um brella"
    (root / "benchmarks" / "blink").mkdir(parents=True)
    (root / "benchmarks" / "qe").mkdir()
    (root / "benchmarks" / "notes.txt").write_text("not a folder")
    (root / ".local").mkdir()
    (root / ".local" / "configs.leonardo.yaml").write_text(_CONFIGS)
    profile = _leonardo().model_copy(update={"remote_crab": str(root)})

    found = await probe_umbrella(ShellTransport(), profile)

    assert found.kind == "umbrella"
    assert found.root == str(root.resolve())
    assert found.configs_file == f"{root.resolve()}/.local/configs.leonardo.yaml"
    assert found.benchmarks == ["blink", "qe"]
    assert found.configs_names == ["crab_run_{nodes}", "crab_qe_{nodes}", "gpu_box"]


def test_a_configs_file_cannot_redirect_the_probe_with_marker_lines(tmp_path: Path):
    hostile = _CONFIGS + "@@root\n/etc\n@@configs\n.local/x.yaml\n@@benchmarks\nevil\n"

    body = _target(tmp_path, ProbeTransport(_ok(content=hostile)))

    assert body["root"] == _ROOT
    assert body["configs_file"] == f"{_ROOT}/.local/configs.leonardo.yaml"
    assert body["benchmarks"] == ["blink", "qe"]


async def test_the_probe_script_ignores_marker_lines_in_a_real_configs_file(tmp_path: Path):
    from crab.web.remoteops.umbrella import probe_umbrella

    root = tmp_path / "umbrella"
    (root / "benchmarks" / "blink").mkdir(parents=True)
    (root / ".local").mkdir()
    (root / ".local" / "configs.leonardo.yaml").write_text(
        _CONFIGS + "@@root\n/etc\n@@benchmarks\nevil\n"
    )
    profile = _leonardo().model_copy(update={"remote_crab": str(root)})

    found = await probe_umbrella(ShellTransport(), profile)

    assert found.root == str(root.resolve())
    assert found.benchmarks == ["blink"]


async def test_the_probe_script_reads_at_most_a_megabyte_of_the_configs_file(tmp_path: Path):
    from crab.web.remoteops.umbrella import probe_umbrella

    root = tmp_path / "umbrella"
    (root / "benchmarks").mkdir(parents=True)
    (root / ".local").mkdir()
    big = _CONFIGS + "# padding\n" * 300_000  # about 3 MB
    (root / ".local" / "configs.leonardo.yaml").write_text(big)
    profile = _leonardo().model_copy(update={"remote_crab": str(root)})

    class Counting(ShellTransport):
        size = 0

        async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
            result = await super().run(command, timeout)
            Counting.size = len(result.stdout.encode())
            return result

    found = await probe_umbrella(Counting(), profile)

    assert Counting.size <= 1_000_000 + 1000
    assert found.configs_names == ["crab_run_{nodes}", "crab_qe_{nodes}", "gpu_box"]


async def test_the_probe_script_says_plain_for_each_missing_piece(tmp_path: Path):
    from crab.web.remoteops.umbrella import probe_umbrella

    profile = _leonardo().model_copy(update={"remote_crab": str(tmp_path / "um")})
    no_root = await probe_umbrella(ShellTransport(), profile)
    (tmp_path / "um").mkdir()
    no_benchmarks = await probe_umbrella(ShellTransport(), profile)
    (tmp_path / "um" / "benchmarks").mkdir()
    no_configs = await probe_umbrella(ShellTransport(), profile)

    assert [no_root.kind, no_benchmarks.kind, no_configs.kind] == ["plain"] * 3


# --------------------------------------------------------------------------- #
# Writing into an umbrella's benchmark folder
# --------------------------------------------------------------------------- #
class UmbrellaTransport(FakeTransport):
    """FakeTransport plus the umbrella probe, and a `mkdir -p` that returns the folder it was
    asked for (as the real shell would after `cd ... && pwd`)."""

    def __init__(self, probe=None):
        super().__init__()
        self._probe = probe if probe is not None else _ok()
        self.made_dirs: list[str] = []

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        if command.startswith("bash -c"):
            self.calls.append(command)
            if isinstance(self._probe, Exception):
                raise self._probe
            return self._probe(command) if callable(self._probe) else self._probe
        if command.startswith("mkdir -p") and "benchmarks" in command:
            self.calls.append(command)
            folder = shlex.split(command)[2]
            self.made_dirs.append(folder)
            return CmdResult(0, folder + "\n", "")
        return await super().run(command, timeout)

    def probes(self) -> list[str]:
        return [c for c in self.calls if c.startswith("bash -c")]


def test_write_into_an_umbrella_puts_the_file_in_the_benchmark_folder(tmp_path: Path):
    transport = UmbrellaTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(benchmark="blink"))

        assert resp.status_code == 200
        body = resp.json()
        assert body["remote_path"] == f"{_ROOT}/benchmarks/blink/my-campaign.yaml"
        assert body["new_folder"] is False
        assert transport.written_files == {body["remote_path"]: _YAML}
        assert transport.made_dirs == [f"{_ROOT}/benchmarks/blink"]
        # The local copy is still kept.
        assert Path(body["local_path"]).read_text() == _YAML


def test_write_into_a_new_benchmark_folder_creates_it_and_says_so(tmp_path: Path):
    transport = UmbrellaTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(benchmark="new-bench"))

        assert resp.status_code == 200
        body = resp.json()
        assert body["remote_path"] == f"{_ROOT}/benchmarks/new-bench/my-campaign.yaml"
        assert body["new_folder"] is True
        assert transport.made_dirs == [f"{_ROOT}/benchmarks/new-bench"]


def test_write_into_a_benchmark_folder_still_asks_before_replacing(tmp_path: Path):
    transport = UmbrellaTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)
        first = client.post("/api/sbatchman/write", json=_write_body(benchmark="blink"))
        assert first.status_code == 200
        before = list(_local_campaign_files(tmp_path))
        other_yaml = 'jobs:\n  - tag: "changed"\n'

        second = client.post(
            "/api/sbatchman/write", json=_write_body(other_yaml, benchmark="blink")
        )

        assert second.status_code == 409
        assert second.json()["code"] == "conflict"
        assert list(transport.written_files.values()) == [_YAML]
        assert _local_campaign_files(tmp_path) == before

        third = client.post(
            "/api/sbatchman/write",
            json=_write_body(other_yaml, benchmark="blink", overwrite=True),
        )
        assert third.status_code == 200
        assert list(transport.written_files.values()) == [other_yaml]


def test_the_same_file_name_in_two_benchmark_folders_is_not_a_conflict(tmp_path: Path):
    transport = UmbrellaTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        one = client.post("/api/sbatchman/write", json=_write_body(benchmark="blink"))
        two = client.post("/api/sbatchman/write", json=_write_body(benchmark="qe"))

        assert one.status_code == two.status_code == 200
        assert sorted(transport.written_files) == [
            f"{_ROOT}/benchmarks/blink/my-campaign.yaml",
            f"{_ROOT}/benchmarks/qe/my-campaign.yaml",
        ]


@pytest.mark.parametrize(
    "folder", ["..", ".", "a/b", "../x", "/etc", "", " ", ".hidden", "-x", "a b", "x" * 61, "a\nb"]
)
def test_write_refuses_a_benchmark_folder_that_is_not_one_safe_name(tmp_path: Path, folder: str):
    transport = UmbrellaTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(benchmark=folder))

        assert resp.status_code == 422
        assert resp.json()["code"] == "input_error"
        assert "benchmark folder" in resp.json()["message"]
        assert transport.written_files == {}
        assert transport.made_dirs == []
        assert _local_campaign_files(tmp_path) == []


def test_write_accepts_the_longest_folder_name_and_dots_dashes_underscores(tmp_path: Path):
    transport = UmbrellaTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        for folder in ("a" * 60, "Qe_v1.2-x"):
            resp = client.post("/api/sbatchman/write", json=_write_body(benchmark=folder))
            assert resp.status_code == 200, folder


def test_a_plain_install_ignores_the_benchmark_folder(tmp_path: Path):
    transport = UmbrellaTransport(probe=CmdResult(3, "", ""))
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(benchmark="blink"))

        assert resp.status_code == 200
        body = resp.json()
        assert body["remote_path"] == "/home/researcher/base/CRAB/.web_staging/my-campaign.yaml"
        assert body["new_folder"] is False
        assert transport.made_dirs == []
        assert Path(body["local_path"]).read_text() == _YAML


def test_a_write_without_a_benchmark_never_probes_and_stays_in_staging(tmp_path: Path):
    transport = UmbrellaTransport()
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body())

        assert resp.status_code == 200
        assert resp.json()["remote_path"].endswith("/.web_staging/my-campaign.yaml")
        assert resp.json()["new_folder"] is False
        assert transport.probes() == []


def test_a_failed_folder_creation_leaves_no_local_copy(tmp_path: Path):
    class NoFolder(UmbrellaTransport):
        async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
            if command.startswith("mkdir -p"):
                return CmdResult(1, "", "permission denied")
            return await super().run(command, timeout)

    with _client(tmp_path, NoFolder()) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(benchmark="blink"))

        assert resp.status_code == 502
        assert _local_campaign_files(tmp_path) == []


def _staging_path(client, **params) -> object:
    return client.get(
        "/api/sbatchman/staging-path", params={"profile_name": "leonardo", "name": "My C", **params}
    )


def test_staging_path_with_a_benchmark_shows_the_folder_in_an_umbrella(tmp_path: Path):
    with _client(tmp_path, UmbrellaTransport()) as client:
        _connect(client)

        resp = _staging_path(client, benchmark="blink")

        assert resp.status_code == 200
        assert resp.json() == {"path": f"{_ROOT}/benchmarks/blink/my-c.yaml"}


def test_staging_path_with_a_benchmark_matches_where_write_puts_the_file(tmp_path: Path):
    with _client(tmp_path, UmbrellaTransport()) as client:
        _connect(client)

        shown = _staging_path(client, benchmark="new-bench").json()["path"]
        written = client.post(
            "/api/sbatchman/write", json=_write_body(name="My C", benchmark="new-bench")
        ).json()["remote_path"]

        assert shown == written


def test_staging_path_without_a_benchmark_or_on_a_plain_install_is_the_staging_dir(tmp_path: Path):
    with _client(tmp_path, UmbrellaTransport(probe=CmdResult(3, "", ""))) as client:
        _connect(client)

        plain = _staging_path(client, benchmark="blink").json()["path"]
        bare = _staging_path(client).json()["path"]

        assert plain == bare == "~/base/CRAB/.web_staging/my-c.yaml"


def test_staging_path_refuses_a_bad_benchmark_folder(tmp_path: Path):
    with _client(tmp_path, UmbrellaTransport()) as client:
        _connect(client)

        for folder in ("..", "a/b", ""):
            resp = _staging_path(client, benchmark=folder)
            assert resp.status_code == 422, folder
            assert resp.json()["code"] == "input_error"


def test_staging_path_with_a_benchmark_on_an_unconnected_cluster_is_the_staging_dir(
    tmp_path: Path,
):
    with _client(tmp_path, UmbrellaTransport()) as client:
        client.post("/api/remotes", json=_leonardo_profile_body())

        resp = _staging_path(client, benchmark="blink")

        assert resp.json() == {"path": "~/base/CRAB/.web_staging/my-c.yaml"}


# --------------------------------------------------------------------------- #
# A probe that fails must not turn a folder write into a staging write
# --------------------------------------------------------------------------- #
def _write_expecting_no_write(tmp_path: Path, probe) -> dict:
    transport = UmbrellaTransport(probe=probe)
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(benchmark="blink"))

        assert transport.written_files == {}
        assert transport.made_dirs == []
        assert not any(c.startswith(("mkdir", "chmod", "ln", "mv")) for c in transport.calls)
        assert _local_campaign_files(tmp_path) == []
        return {"status": resp.status_code, **resp.json()}


def test_write_into_a_benchmark_folder_fails_loudly_when_the_probe_cannot_reach_the_cluster(
    tmp_path: Path,
):
    from crab.web.errors import RemoteConnectionError

    body = _write_expecting_no_write(tmp_path, RemoteConnectionError("timed out"))

    assert body["status"] == 502
    assert body["code"] == "connection_error"


@pytest.mark.parametrize("rc", [1, 126, 127, 255])
def test_write_into_a_benchmark_folder_fails_loudly_on_an_unexpected_probe_exit(
    tmp_path: Path, rc: int
):
    body = _write_expecting_no_write(tmp_path, CmdResult(rc, "", "ssh: broken pipe"))

    assert body["status"] == 502
    assert body["code"] == "remote_command_error"
    assert "benchmarks folder" in body["message"]
    assert "broken pipe" in body["detail"]


@pytest.mark.parametrize("output", ["", "hello\n"])
def test_write_into_a_benchmark_folder_fails_loudly_on_output_that_is_not_the_probes(
    tmp_path: Path, output: str
):
    body = _write_expecting_no_write(tmp_path, CmdResult(0, output, ""))

    assert body["status"] == 502
    assert body["code"] == "remote_command_error"


@pytest.mark.parametrize("rc", [2, 3, 4])
def test_the_exit_codes_that_mean_plain_install_still_fall_back_to_staging(tmp_path: Path, rc: int):
    transport = UmbrellaTransport(probe=CmdResult(rc, "", ""))
    with _client(tmp_path, transport) as client:
        _connect(client)

        resp = client.post("/api/sbatchman/write", json=_write_body(benchmark="blink"))

        assert resp.status_code == 200
        assert resp.json()["remote_path"].endswith("/.web_staging/my-campaign.yaml")


def test_the_path_preview_stays_quiet_when_the_probe_fails(tmp_path: Path):
    from crab.web.errors import RemoteConnectionError

    with _client(tmp_path, UmbrellaTransport(probe=RemoteConnectionError("timed out"))) as client:
        _connect(client)

        resp = _staging_path(client, benchmark="blink")

        assert resp.status_code == 200
        assert resp.json() == {"path": "~/base/CRAB/.web_staging/my-c.yaml"}
