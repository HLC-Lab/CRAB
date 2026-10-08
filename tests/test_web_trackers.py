"""In-memory async trackers and cache walks.

The submit and results-fetch trackers used to drop an entry only when a
terminal status was polled, so an entry nobody polled lived for the whole
process. They now expire: 1 h after reaching a terminal state, 24 h after
creation otherwise. And the results-cache directory walk and `rmtree` run off
the event loop, like the neighboring cache reads already did.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from conftest import auth_client  # noqa: E402
from crab.web.server import create_app  # noqa: E402
from crab.web.settings import Settings  # noqa: E402
from crab.web.store.results_cache import ResultsCache  # noqa: E402
from crab.web.trackers import ExpiringTracker  # noqa: E402

HOUR = 3600.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_pending_entry_lives_24_hours_after_creation():
    clock = FakeClock()
    tracker = ExpiringTracker(clock=clock)
    tracker["a"] = {"status": "pending"}

    clock.now += 24 * HOUR - 1
    assert tracker.get("a") == {"status": "pending"}
    clock.now += 2
    assert tracker.get("a") is None


def test_a_terminal_entry_lives_one_hour_after_finishing():
    clock = FakeClock()
    tracker = ExpiringTracker(clock=clock)
    tracker["a"] = {"status": "pending"}
    clock.now += 10 * HOUR
    tracker["a"] = {"status": "done"}

    clock.now += HOUR - 1
    assert tracker.get("a") == {"status": "done"}
    clock.now += 2
    assert tracker.get("a") is None


def test_expired_entries_disappear_from_every_view():
    clock = FakeClock()
    tracker = ExpiringTracker(clock=clock)
    tracker["old"] = {"status": "error", "message": "x"}
    clock.now += 2 * HOUR
    tracker["new"] = {"status": "pending"}

    assert len(tracker) == 1
    assert "old" not in tracker
    assert tracker["new"] == {"status": "pending"}


def test_pop_removes_an_entry():
    tracker = ExpiringTracker(clock=FakeClock())
    tracker["a"] = {"status": "done"}
    assert tracker.pop("a", None) == {"status": "done"}
    assert tracker.get("a") is None


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def test_the_app_trackers_expire_unpolled_entries(tmp_path: Path):
    clock = FakeClock()
    app = create_app(_settings(tmp_path))
    # The app's own trackers expire; swap in fake-clock ones to drive time.
    assert isinstance(app.state.result_fetches, ExpiringTracker)
    assert isinstance(app.state.submissions, ExpiringTracker)
    app.state.result_fetches = ExpiringTracker(clock=clock)
    app.state.submissions = ExpiringTracker(clock=clock)
    with auth_client(app) as client:
        app.state.result_fetches["f"] = {"status": "pending"}
        app.state.submissions["s"] = {"status": "pending"}
        clock.now += 25 * HOUR

        assert client.get("/api/results/c/s/j/fetch/f").status_code == 404
        assert client.get("/api/jobs/submissions/s").status_code == 404


@pytest.fixture
def to_thread_calls(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every function handed to `asyncio.to_thread`, still run for real."""
    calls: list = []
    real = asyncio.to_thread

    async def spy(func, /, *args, **kwargs):
        calls.append(getattr(func, "__func__", func))
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    return calls


def test_results_index_walks_the_cache_off_the_event_loop(tmp_path: Path, to_thread_calls):
    with auth_client(create_app(_settings(tmp_path))) as client:
        assert client.get("/api/results").status_code == 200
    assert ResultsCache.list_cached in to_thread_calls


def test_clearing_the_cache_runs_off_the_event_loop(tmp_path: Path, to_thread_calls):
    with auth_client(create_app(_settings(tmp_path))) as client:
        assert client.delete("/api/results/cache").status_code == 204
    assert ResultsCache.clear in to_thread_calls
