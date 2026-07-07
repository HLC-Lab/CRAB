"""Tests for the on-disk results CSV tree cache (plan 065)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.settings import Settings  # noqa: E402
from crab.web.store.results_cache import ResultsCache  # noqa: E402


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "config", data_dir=tmp_path / "data")


def test_path_for_is_namespaced_by_cluster_and_job(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))

    path = cache.path_for("leonardo", "multistage-coscheduling-pipeline")

    assert (
        path
        == tmp_path / "data" / "results_cache" / "leonardo" / "multistage-coscheduling-pipeline"
    )


def test_total_size_is_zero_before_anything_is_cached(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))

    assert cache.total_size() == 0


def test_total_size_sums_bytes_across_all_cached_jobs(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))
    job_dir = cache.path_for("leonardo", "job-a")
    job_dir.mkdir(parents=True)
    (job_dir / "data_app_0.csv").write_bytes(b"12345")
    other_dir = cache.path_for("m100", "job-b")
    other_dir.mkdir(parents=True)
    (other_dir / "data_app_1.csv").write_bytes(b"1234567890")

    assert cache.total_size() == 15


def test_clear_removes_everything(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))
    job_dir = cache.path_for("leonardo", "job-a")
    job_dir.mkdir(parents=True)
    (job_dir / "data_app_0.csv").write_bytes(b"12345")

    cache.clear()

    assert cache.total_size() == 0
    assert not job_dir.exists()


def test_clear_on_empty_cache_does_not_raise(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))

    cache.clear()  # should not raise even though nothing was ever cached
