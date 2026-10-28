"""Wrappers page routes: list a cluster's wrappers with their binary status
(`crab wrappers list --json`) and import a missing binary (`crab receipts set ... --json`).
No real SSH; a command-aware fake transport answers and records the commands."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.connections.manager import ConnectionManager  # noqa: E402
from crab.web.connections.transport import CmdResult, Transport  # noqa: E402
from crab.web.errors import InputError  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from test_web_catalog import _INFO, _leonardo  # noqa: E402

_LIST = {
    "schema": 1,
    "search_path": ["/h/CRAB/local/wrappers", "/h/CRAB/wrappers"],
    "wrappers": [
        {
            "relpath": "hpl/hpl.py",
            "loadable": True,
            "benchmark_id": "hpl",
            "binary": {"status": "missing", "path": None},
        },
    ],
}
_RECEIPT = {"schema": 1, "receipt": {"id": "hpl", "binary_path": "/opt/hpl/xhpl"}}
_SHOW = {"schema": 1, "relpath": "blink/alltoall/a2a.py", "source": "x = 1\n", "loadable": True}
_STATUS = {"schema": 1, "state": "clean", "clone": True, "modified": [], "upstream": "up to date"}
_PULL = {"schema": 1, "dir": "/h/CRAB/wrappers", "action": "up to date", "old": "abc", "new": "abc"}


class Fake(Transport):
    def __init__(self) -> None:
        self._alive = True
        self.calls: list[str] = []
        self.timeouts: list[float | None] = []
        # Canned failure for any command containing the key (checked before the JSON answers).
        self.failures: dict[str, CmdResult] = {}

    @property
    def alive(self) -> bool:
        return self._alive

    async def run(self, command: str, timeout: float | None = 30.0) -> CmdResult:
        self.calls.append(command)
        self.timeouts.append(timeout)
        for needle, failure in self.failures.items():
            if needle in command:
                return failure
        if "wrappers list" in command:
            return CmdResult(0, json.dumps(_LIST), "")
        if "wrappers show" in command:
            return CmdResult(0, json.dumps(_SHOW), "")
        if "wrappers status" in command:
            return CmdResult(0, json.dumps(_STATUS), "")
        if "wrappers pull" in command:
            return CmdResult(0, json.dumps(_PULL), "")
        if "receipts set" in command:
            return CmdResult(0, json.dumps(_RECEIPT), "")
        return CmdResult(0, _INFO, "")

    async def close(self) -> None:
        self._alive = False


@pytest.fixture
def client_and_fake(tmp_path: Path):
    fake = Fake()

    async def connector(profile, password):
        return fake

    app = create_app(
        Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"),
        manager=ConnectionManager(connector=connector),
    )
    with auth_client(app) as client:
        client.post("/api/remotes", json=_leonardo().model_dump())
        client.post("/api/remotes/leonardo/connect")
        yield client, fake


def _inner(command: str) -> list[str]:
    """The words of the `bash -lc '<inner>'` command, as the remote shell would split them."""
    return shlex.split(shlex.split(command)[2])


# --- /api/wrappers/{target}/...: `local` is this laptop, anything else a connected cluster ---

_BODY = {"id": "hpl", "binary": "/opt/hpl dir/xhpl", "pre_run": ["module load mpi/4"]}


@pytest.fixture
def laptop(monkeypatch: pytest.MonkeyPatch) -> Fake:
    """The fake the `local` target runs on (never a real subprocess)."""
    fake = Fake()
    monkeypatch.setattr("crab.web.api.wrappers._local_transport", lambda: fake)
    return fake


def _crab_words(command: str, *, local: bool) -> list[str]:
    """The `crab ...` words of a command; for local, the words after `python -m crab`."""
    if local:
        words = shlex.split(command)
        return ["crab", *words[words.index("-m") + 2 :]]
    words = _inner(command)
    return words[words.index("crab") :]


def _send(client, target: str, route: str):
    base = f"/api/wrappers/{target}"
    if route == "show":
        return client.get(f"{base}/show", params={"relpath": "blink/alltoall/a2a.py"})
    if route == "pull":
        return client.post(f"{base}/pull")
    if route == "receipts":
        return client.post(f"{base}/receipts", json=_BODY)
    return client.get(f"{base}/{route}")


_EXPECTED = {
    "list": (["crab", "wrappers", "list", "--json"], _LIST),
    "show": (["crab", "wrappers", "show", "blink/alltoall/a2a.py", "--json"], _SHOW),
    "status": (["crab", "wrappers", "status", "--json"], _STATUS),
    "pull": (["crab", "wrappers", "pull", "--json"], _PULL),
    "receipts": (
        [
            "crab",
            "receipts",
            "set",
            "hpl",
            "--binary",
            "/opt/hpl dir/xhpl",
            "--pre-run",
            "module load mpi/4",
            "--json",
        ],
        _RECEIPT,
    ),
}


@pytest.mark.parametrize("route", list(_EXPECTED))
def test_remote_target_runs_the_clusters_own_command(client_and_fake, route) -> None:
    client, fake = client_and_fake
    resp = _send(client, "leonardo", route)
    assert resp.status_code == 200
    words, canned = _EXPECTED[route]
    assert resp.json() == canned
    assert _crab_words(fake.calls[-1], local=False) == words


@pytest.mark.parametrize("route", list(_EXPECTED))
def test_local_target_runs_on_the_laptop_without_any_cluster(
    tmp_path: Path, laptop: Fake, route
) -> None:
    app = create_app(Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"))
    with auth_client(app) as client:
        resp = _send(client, "local", route)
    assert resp.status_code == 200
    words, canned = _EXPECTED[route]
    assert resp.json() == canned
    command = laptop.calls[-1]
    assert shlex.split(command)[:3] == [sys.executable, "-m", "crab"]
    assert _crab_words(command, local=True) == words


def test_show_relpath_with_a_space_is_one_argument(client_and_fake) -> None:
    client, fake = client_and_fake
    resp = client.get("/api/wrappers/leonardo/show", params={"relpath": "my app/run one.py"})
    assert resp.status_code == 200
    assert _crab_words(fake.calls[-1], local=False)[3] == "my app/run one.py"


def test_local_target_never_touches_the_connection_manager(
    tmp_path: Path, laptop: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_k):
        raise AssertionError("local must not resolve a profile or a connection")

    monkeypatch.setattr("crab.web.api.wrappers._store", boom)
    monkeypatch.setattr("crab.web.api.wrappers._live_transport", boom)
    app = create_app(Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"))
    with auth_client(app) as client:
        assert client.get("/api/wrappers/local/list").status_code == 200


def test_remote_target_not_connected_is_a_connection_error(tmp_path: Path) -> None:
    app = create_app(Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"))
    with auth_client(app) as client:
        client.post("/api/remotes", json=_leonardo().model_dump())
        resp = client.get("/api/wrappers/leonardo/list")
    assert resp.status_code == 502
    assert resp.json()["code"] == "connection_error"


def test_unknown_profile_is_not_found(client_and_fake) -> None:
    client, fake = client_and_fake
    before = len(fake.calls)
    resp = client.get("/api/wrappers/nowhere/list")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    assert len(fake.calls) == before


def test_show_unknown_wrapper_is_not_found(client_and_fake) -> None:
    client, fake = client_and_fake
    fake.failures["wrappers show"] = CmdResult(2, "", "crab: wrapper not found: x/y.py\n")
    resp = client.get("/api/wrappers/leonardo/show", params={"relpath": "x/y.py"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    assert resp.json()["message"] == "wrapper not found: x/y.py"


@pytest.mark.parametrize("relpath", ["/etc/passwd", "../x.py", "a/../b.py", "a/..", "", "a" * 513])
def test_show_bad_relpath_is_refused_before_any_command(client_and_fake, relpath) -> None:
    client, fake = client_and_fake
    before = len(fake.calls)
    resp = client.get("/api/wrappers/leonardo/show", params={"relpath": relpath})
    assert resp.status_code == 422
    assert resp.json()["code"] == "input_error"
    assert len(fake.calls) == before


def test_show_without_relpath_is_refused(client_and_fake) -> None:
    client, fake = client_and_fake
    before = len(fake.calls)
    assert client.get("/api/wrappers/leonardo/show").status_code == 422
    assert len(fake.calls) == before


_OLD = (
    "usage: crab wrappers [-h] {{list,test,new}} ...\n"
    "crab wrappers: error: argument wrappers_cmd: invalid choice: '{cmd}' "
    "(choose from 'list', 'test', 'new')\n"
)


@pytest.mark.parametrize("cmd", ["show", "status", "pull"])
def test_outdated_cluster_crab_is_a_crab_outdated_error(client_and_fake, cmd) -> None:
    client, fake = client_and_fake
    fake.failures[f"wrappers {cmd}"] = CmdResult(2, "", _OLD.format(cmd=cmd))
    resp = _send(client, "leonardo", cmd)
    assert resp.status_code == 409
    assert resp.json()["code"] == "crab_outdated"
    assert resp.json()["message"] == (
        f"This cluster's crab has no 'crab wrappers {cmd}'. Update it there (crab update), "
        "then retry."
    )


def test_outdated_laptop_crab_says_this_laptop(laptop: Fake, tmp_path: Path) -> None:
    laptop.failures["wrappers status"] = CmdResult(2, "", _OLD.format(cmd="status"))
    app = create_app(Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data"))
    with auth_client(app) as client:
        resp = client.get("/api/wrappers/local/status")
    assert resp.status_code == 409
    assert resp.json()["code"] == "crab_outdated"
    assert resp.json()["message"].startswith("This laptop's crab has no 'crab wrappers status'.")


def test_pull_refused_for_local_edits_is_a_conflict(client_and_fake) -> None:
    client, fake = client_and_fake
    stderr = (
        "crab: wrappers pull stopped: /h/CRAB/wrappers has local edits to tracked files: a.py; "
        "commit, stash or move them to local/wrappers, then pull again\n"
    )
    fake.failures["wrappers pull"] = CmdResult(1, "", stderr)
    resp = client.post("/api/wrappers/leonardo/pull")
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"
    assert resp.json()["message"] == stderr.strip().removeprefix("crab: ")


_NOISE = "Loading module gcc/12\nWelcome to the cluster\n(venv) activated\n"


def test_show_not_found_survives_login_shell_noise(client_and_fake) -> None:
    client, fake = client_and_fake
    fake.failures["wrappers show"] = CmdResult(2, "", _NOISE + "crab: wrapper not found: x/y.py\n")
    resp = client.get("/api/wrappers/leonardo/show", params={"relpath": "x/y.py"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
    assert resp.json()["message"] == "wrapper not found: x/y.py"
    assert "Welcome" in resp.json()["detail"]


def test_pull_conflict_survives_login_shell_noise(client_and_fake) -> None:
    client, fake = client_and_fake
    line = (
        "crab: wrappers pull stopped: /h/CRAB/wrappers has local edits to tracked files: a.py; "
        "commit, stash or move them to local/wrappers, then pull again"
    )
    fake.failures["wrappers pull"] = CmdResult(1, "", _NOISE + line + "\n")
    resp = client.post("/api/wrappers/leonardo/pull")
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"
    assert resp.json()["message"] == line.removeprefix("crab: ")


@pytest.mark.parametrize("relpath", ["-h", "--help", "a\x00b", "a\nb", "a\x7fb"])
def test_show_flag_like_or_control_char_relpath_is_refused(client_and_fake, relpath) -> None:
    client, fake = client_and_fake
    before = len(fake.calls)
    resp = client.get("/api/wrappers/leonardo/show", params={"relpath": relpath})
    assert resp.status_code == 422
    assert resp.json()["code"] == "input_error"
    assert len(fake.calls) == before


def test_crab_without_wrappers_command_is_outdated(client_and_fake) -> None:
    client, fake = client_and_fake
    fake.failures["wrappers status"] = CmdResult(
        2, "", "crab: error: argument command: invalid choice: 'wrappers' (choose from 'run')\n"
    )
    resp = client.get("/api/wrappers/leonardo/status")
    assert resp.status_code == 409
    assert resp.json()["code"] == "crab_outdated"
    assert resp.json()["message"] == (
        "This cluster's crab has no 'crab wrappers status'. Update it there (crab update), "
        "then retry."
    )


def test_other_pull_failures_stay_remote_command_errors(client_and_fake) -> None:
    client, fake = client_and_fake
    stderr = "crab: wrappers pull stopped: fatal: unable to access the remote\n"
    fake.failures["wrappers pull"] = CmdResult(1, "", stderr)
    resp = client.post("/api/wrappers/leonardo/pull")
    assert resp.status_code == 502
    assert resp.json()["code"] == "remote_command_error"
    assert "unable to access the remote" in resp.json()["detail"]


def test_each_route_has_its_own_timeout(client_and_fake) -> None:
    client, fake = client_and_fake
    seen = {}
    for route in ("list", "show", "status", "pull", "receipts"):
        assert _send(client, "leonardo", route).status_code == 200
        seen[route] = fake.timeouts[-1]
    assert seen == {"list": 90.0, "show": 60.0, "status": 45.0, "pull": 180.0, "receipts": 30.0}


def test_the_old_cluster_only_urls_are_gone(client_and_fake) -> None:
    """No API route is left there: GET falls through to the SPA shell (HTML, not the catalog), POST
    has no handler (405, the catch-all only takes GET). Neither reaches the cluster."""
    client, fake = client_and_fake
    before = len(fake.calls)
    got = client.get("/api/remotes/leonardo/wrappers")
    assert got.headers["content-type"].startswith("text/html")
    posted = client.post("/api/remotes/leonardo/receipts", json=_BODY)
    assert posted.status_code == 405
    assert len(fake.calls) == before


def test_receipt_import_refuses_a_relative_binary_before_any_command(client_and_fake) -> None:
    """The cluster's folder is not the person's: a relative path cannot be resolved there."""
    client, fake = client_and_fake
    before = len(fake.calls)
    resp = client.post("/api/wrappers/leonardo/receipts", json={**_BODY, "binary": "rel/dir/x"})
    # The app's own envelope (the web client reads `code` and `message`), not FastAPI's list.
    assert resp.status_code == InputError.http_status
    assert resp.json()["code"] == "input_error"
    assert "full path" in resp.json()["message"]
    assert len(fake.calls) == before


@pytest.mark.parametrize(
    ("binary", "sent"),
    [
        ("/abs/x", "/abs/x"),
        ("xhpl", "xhpl"),
        ("/opt/select_nic rel/x", "/opt/select_nic rel/x"),
        # A leading ~ is left for the cluster's shell to expand, as for every remote path.
        ("~/x", "$HOME/x"),
    ],
)
def test_receipt_import_passes_other_binaries_through_unchanged(
    client_and_fake, binary, sent
) -> None:
    client, fake = client_and_fake
    resp = client.post("/api/wrappers/leonardo/receipts", json={**_BODY, "binary": binary})
    assert resp.status_code == 200
    words = _crab_words(fake.calls[-1], local=False)
    assert words[words.index("--binary") + 1] == sent
