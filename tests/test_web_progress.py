"""The progress board: what each cluster's slow operation is doing right now, per cluster name."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.cli.progress import Stage, stage, use_reporter  # noqa: E402
from crab.web.progress import ProgressBoard  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402

CONNECT = Stage("ssh-connect", "Connecting", "the login node", 1000.0, 30)
CHECK = Stage("crab-check", "Checking CRAB", "crab info --json", 1002.0, 30)


class FakeClock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


def test_a_begun_stage_is_listed_until_it_ends():
    board = ProgressBoard()
    reporter = board.reporter("leonardo")
    reporter.begin(CONNECT)
    assert board.snapshot("leonardo") == [CONNECT]
    reporter.end("ssh-connect", True)
    assert board.snapshot("leonardo") == []


def test_a_stage_that_ended_not_ok_is_removed_too():
    board = ProgressBoard()
    reporter = board.reporter("leonardo")
    reporter.begin(CONNECT)
    reporter.end("ssh-connect", False)
    assert board.snapshot("leonardo") == []


def test_clusters_are_independent_and_an_unknown_one_is_empty():
    board = ProgressBoard()
    board.reporter("a").begin(CONNECT)
    board.reporter("b").begin(CHECK)
    assert board.snapshot("a") == [CONNECT]
    assert board.snapshot("b") == [CHECK]
    assert board.snapshot("ghost") == []


def test_stages_are_listed_in_the_order_they_began():
    board = ProgressBoard()
    reporter = board.reporter("a")
    reporter.begin(CHECK)
    reporter.begin(CONNECT)
    assert [s.stage for s in board.snapshot("a")] == ["ssh-connect", "crab-check"]


def test_a_name_that_looks_like_another_key_does_not_collide():
    board = ProgressBoard()
    board.reporter("a").begin(CONNECT)
    board.reporter("a:ssh").begin(CHECK)
    assert board.snapshot("a") == [CONNECT]


def test_a_stage_whose_operation_crashed_expires_by_itself():
    clock = FakeClock()
    board = ProgressBoard(ttl_clock=clock)
    board.reporter("a").begin(CONNECT)
    clock.now += 600
    assert board.snapshot("a") == [CONNECT]
    clock.now += 2000
    assert board.snapshot("a") == []


def test_stage_context_manager_drives_the_board_through_use_reporter():
    board = ProgressBoard()
    with use_reporter(board.reporter("a")):
        with stage("install", "Installing", "pip", 600, clock=lambda: 7.0):
            assert board.snapshot("a") == [Stage("install", "Installing", "pip", 7.0, 600)]
    assert board.snapshot("a") == []


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def test_the_app_has_a_board(tmp_path: Path):
    assert isinstance(create_app(_settings(tmp_path)).state.progress, ProgressBoard)


def test_the_progress_route_lists_the_current_stages_with_the_clock(tmp_path: Path):
    app = create_app(_settings(tmp_path))
    app.state.progress.reporter("leonardo").begin(CONNECT)
    with auth_client(app) as client:
        body = client.get("/api/remotes/leonardo/progress").json()
    assert isinstance(body["now"], float)
    assert body["stages"] == [
        {
            "stage": "ssh-connect",
            "label": "Connecting",
            "waiting_for": "the login node",
            "started_at": 1000.0,
            "timeout_s": 30,
        }
    ]


def test_the_progress_route_answers_an_empty_list_for_an_unknown_remote(tmp_path: Path):
    with auth_client(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/remotes/ghost/progress")
    assert response.status_code == 200
    assert response.json()["stages"] == []


def test_the_progress_route_needs_the_api_token(tmp_path: Path):
    from fastapi.testclient import TestClient

    client = TestClient(create_app(_settings(tmp_path)), base_url="http://127.0.0.1")
    assert client.get("/api/remotes/x/progress").status_code in (401, 403)
