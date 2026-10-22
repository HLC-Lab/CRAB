"""The run loop of ExperimentRunner.execute() (core/experiment/runner.py) keeps adding runs
until check_CI (core/data/utils.py) reports convergence, checked from `minruns` on, or
`maxruns` is reached. check_CI converges a metric when its samples have zero spread or when
the (1 - alpha) t-interval is narrower than beta * |mean|."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT


def _wrapper(path: Path, counter: Path, values: list[float], fail_first: int = 0) -> None:
    """An app whose run N prints values[(N - 1) % len(values)]; its first `fail_first` runs
    print text read_data cannot parse, which fails the run."""
    path.write_text(
        "from pathlib import Path\n"
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        f"        counter = Path({str(counter)!r})\n"
        "        run = len(counter.read_text()) + 1 if counter.exists() else 1\n"
        "        counter.write_text('x' * run)\n"
        f"        values = {values!r}\n"
        f"        if run <= {fail_first}:\n"
        "            return 'echo bad'\n"
        "        return f'echo {values[(run - 1) % len(values)]}'\n\n"
        "    def read_data(self):\n"
        "        return [{'v': float(self.stdout)}]\n"
    )


def _run(
    tmp_path: Path,
    monkeypatch,
    values: list[float],
    minruns: int,
    maxruns: int,
    fail_first: int = 0,
):
    wrapper = tmp_path / "w.py"
    _wrapper(wrapper, tmp_path / "count", values, fail_first)
    output_dir = tmp_path / "system" / "job"
    runner = ExperimentRunner(
        exp_name="exp",
        config={
            "apps": {"0": {"path": str(wrapper), "collect": True}},
            "local_options": {
                "minruns": str(minruns),
                "maxruns": str(maxruns),
                "alpha": "0.05",
                "beta": "0.05",
            },
        },
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(output_dir),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()
    runner.execute(str(output_dir))
    runner.save_results()  # the engine saves after execute returns
    data_csv = Path(runner.exp_dir) / "data_app_0.csv"  # absent when no run succeeded
    run_ids = []
    if data_csv.exists():
        with open(data_csv) as f:
            run_ids = [r["run_id"] for r in csv.DictReader(f)]
    with open(tmp_path / "system" / "metadata.csv") as f:
        (row,) = list(csv.DictReader(f))
    return run_ids, row


def test_a_metric_that_never_converges_runs_until_maxruns(tmp_path: Path, monkeypatch) -> None:
    # 1 and 100 alternating: the 95% interval spans far more than 5% of the mean at any n.
    run_ids, row = _run(tmp_path, monkeypatch, [1.0, 100.0], minruns=2, maxruns=6)

    assert run_ids == ["1", "2", "3", "4", "5", "6"]
    assert (row["status"], row["total_runs"]) == ("COMPLETED", "6")


def test_a_metric_that_converges_at_minruns_stops_there(tmp_path: Path, monkeypatch) -> None:
    # Constant samples have zero spread, so the first check (at run 3) converges.
    run_ids, row = _run(tmp_path, monkeypatch, [5.0], minruns=3, maxruns=10)

    assert run_ids == ["1", "2", "3"]
    assert (row["status"], row["total_runs"]) == ("COMPLETED", "3")


def test_failed_runs_do_not_count_toward_minruns(tmp_path: Path, monkeypatch) -> None:
    # Runs 1-3 fail to parse; the constant samples of runs 4-7 are the first 4 successes,
    # so the first convergence check (and the stop) comes at run 7, not run 4.
    run_ids, row = _run(tmp_path, monkeypatch, [5.0], minruns=4, maxruns=20, fail_first=3)

    assert run_ids == ["4", "5", "6", "7"]
    assert (row["total_runs"], row["failed_runs"]) == ("7", "3")


def test_every_run_failing_stops_at_maxruns(tmp_path: Path, monkeypatch) -> None:
    run_ids, row = _run(tmp_path, monkeypatch, [5.0], minruns=2, maxruns=5, fail_first=99)

    assert run_ids == []
    assert (row["total_runs"], row["failed_runs"]) == ("5", "5")
