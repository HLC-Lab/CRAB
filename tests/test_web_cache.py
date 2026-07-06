"""Plan 075 S4: local fallback cache for read-only cluster data."""

from __future__ import annotations

from pathlib import Path

from crab.web.settings import Settings
from crab.web.store.cache import LocalCache


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def test_write_then_read_round_trip(tmp_path: Path):
    cache = LocalCache(_settings(tmp_path))
    cache.write("logs", "leonardo:123", {"stdout": "hello"})

    got = cache.read("logs", "leonardo:123")

    assert got is not None
    assert got["data"] == {"stdout": "hello"}
    assert "fetched_at" in got


def test_read_miss_returns_none(tmp_path: Path):
    cache = LocalCache(_settings(tmp_path))

    assert cache.read("logs", "never-written") is None


def test_read_corrupt_file_returns_none(tmp_path: Path):
    settings = _settings(tmp_path)
    cache = LocalCache(settings)
    path = settings.cache_dir / "logs" / "leonardo:123.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{")

    assert cache.read("logs", "leonardo:123") is None


def test_key_with_slash_does_not_escape_scope_dir(tmp_path: Path):
    settings = _settings(tmp_path)
    cache = LocalCache(settings)
    cache.write("experiment_logs", "abc__../../escape", {"x": 1})

    resolved = (settings.cache_dir / "experiment_logs").resolve()
    written = next((settings.cache_dir / "experiment_logs").iterdir())
    assert written.resolve().parent == resolved
