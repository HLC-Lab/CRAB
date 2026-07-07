"""Tests for the on-disk results CSV tree cache (plan 065)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from crab.web.settings import Settings  # noqa: E402
from crab.web.store.results_cache import ResultsCache  # noqa: E402


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "config", data_dir=tmp_path / "data")


def test_path_for_is_namespaced_by_cluster_system_and_job(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))

    path = cache.path_for("leonardo", "leonardo", "multistage-coscheduling-pipeline")

    assert path == (
        tmp_path
        / "data"
        / "results_cache"
        / "leonardo"
        / "leonardo"
        / "multistage-coscheduling-pipeline"
    )


def test_total_size_is_zero_before_anything_is_cached(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))

    assert cache.total_size() == 0


def test_total_size_sums_bytes_across_all_cached_jobs(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))
    job_dir = cache.path_for("leonardo", "leonardo", "job-a")
    job_dir.mkdir(parents=True)
    (job_dir / "data_app_0.csv").write_bytes(b"12345")
    other_dir = cache.path_for("m100", "m100", "job-b")
    other_dir.mkdir(parents=True)
    (other_dir / "data_app_1.csv").write_bytes(b"1234567890")

    assert cache.total_size() == 15


def test_clear_removes_everything(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))
    job_dir = cache.path_for("leonardo", "leonardo", "job-a")
    job_dir.mkdir(parents=True)
    (job_dir / "data_app_0.csv").write_bytes(b"12345")

    cache.clear()

    assert cache.total_size() == 0
    assert not job_dir.exists()


def test_clear_on_empty_cache_does_not_raise(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))

    cache.clear()  # should not raise even though nothing was ever cached


def test_list_cached_is_empty_before_anything_is_cached(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))

    assert cache.list_cached() == []


def _make_job_dir(cache: ResultsCache, cluster: str, system: str, job: str) -> Path:
    """A realistic fetched job dir: always at least one experiment subfolder
    with a CSV inside, mirroring what `fetch_tree` actually copies (never
    CSVs directly at the job level) -- this shape is exactly what
    `list_cached()` uses to tell a real job dir apart from a stale leftover."""
    job_dir = cache.path_for(cluster, system, job)
    exp_dir = job_dir / "01_baseline"
    exp_dir.mkdir(parents=True)
    (exp_dir / "data_app_0.csv").write_text("x", encoding="utf-8")
    return job_dir


def test_list_cached_returns_every_cluster_system_job_triple(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))
    _make_job_dir(cache, "leonardo", "leonardo", "job-a")
    _make_job_dir(cache, "leonardo", "booster", "job-b")
    _make_job_dir(cache, "m100", "m100", "job-c")

    assert sorted(cache.list_cached()) == [
        ("leonardo", "booster", "job-b"),
        ("leonardo", "leonardo", "job-a"),
        ("m100", "m100", "job-c"),
    ]


def test_list_cached_ignores_stray_files_and_shallow_dirs(tmp_path: Path):
    cache = ResultsCache(_settings(tmp_path))
    root = cache._settings.results_cache_dir
    root.mkdir(parents=True)
    (root / "stray.txt").write_text("not a cache entry", encoding="utf-8")
    (root / "leonardo").mkdir()  # a cluster dir with no system subdir yet
    (root / "leonardo" / "stray.csv").write_text("x", encoding="utf-8")
    _make_job_dir(cache, "leonardo", "leonardo", "job-a")

    assert cache.list_cached() == [("leonardo", "leonardo", "job-a")]


def test_list_cached_ignores_a_stale_pre_077_layout_leftover(tmp_path: Path):
    """Plan 077 S4 changed the on-disk cache layout from
    `<cluster>/<job_basename>/` (065) to `<cluster>/<system>/<job_basename>/`.
    A leftover 065-layout directory, walked with the new 3-level assumption,
    misreads its job_basename as a "system" and one of its experiment
    subfolders as the "job_basename" -- exactly the bogus entries (an
    experiment name like "10_512KiB" with a job name sitting in the system
    slot) the owner saw in the real picker. The distinguishing signal: a
    genuine job_basename leaf always contains experiment SUBFOLDERS; a
    misread experiment leaf contains CSV FILES directly, no subdirectories."""
    cache = ResultsCache(_settings(tmp_path))
    root = cache._settings.results_cache_dir
    stale_experiment_leaf = root / "leonardo" / "msgsize_study_2026-07-05" / "10_512KiB"
    stale_experiment_leaf.mkdir(parents=True)
    (stale_experiment_leaf / "data_app_0.csv").write_text("x", encoding="utf-8")

    assert cache.list_cached() == []


def test_list_cached_returns_the_real_job_alongside_a_stale_leftover(tmp_path: Path):
    """The real-world case: the same job got re-fetched under the new layout
    (so a genuine 3-level dir exists), while its pre-migration 065-layout
    leftover is still sitting on disk too -- only the genuine one should
    ever surface."""
    cache = ResultsCache(_settings(tmp_path))
    _make_job_dir(cache, "leonardo", "leonardo", "msgsize_study_2026-07-05")
    root = cache._settings.results_cache_dir
    stale_experiment_leaf = root / "leonardo" / "msgsize_study_2026-07-05" / "10_512KiB"
    stale_experiment_leaf.mkdir(parents=True)
    (stale_experiment_leaf / "data_app_0.csv").write_text("x", encoding="utf-8")

    assert cache.list_cached() == [("leonardo", "leonardo", "msgsize_study_2026-07-05")]
