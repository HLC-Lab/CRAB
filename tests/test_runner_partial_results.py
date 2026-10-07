"""Runs measured before an experiment fails midway are kept. Before, an exception escaping the
run loop of ExperimentRunner.execute() (core/experiment/runner.py) skipped both save_results
(called by the engine only after execute returns) and the registry row, so runs 1-2 of an
experiment whose app could not be launched in run 3 were lost."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crab.core.experiment.runner import ExperimentRunner


def _wrapper_failing_at_launch(path: Path, counter: Path, failing_run: int) -> None:
    """An app that prints 1, 2, ... and whose launch raises in run `failing_run`."""
    path.write_text(
        "from pathlib import Path\n"
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        f"        counter = Path({str(counter)!r})\n"
        "        run = len(counter.read_text()) + 1 if counter.exists() else 1\n"
        "        counter.write_text('x' * run)\n"
        f"        if run == {failing_run}:\n"
        "            raise FileNotFoundError('no such binary: bench')\n"
        "        return f'echo {run}'\n\n"
        "    def read_data(self):\n"
        "        return [[float(self.stdout)]]\n"
    )


def _runner(tmp_path: Path, monkeypatch, wrapper: Path) -> ExperimentRunner:
    monkeypatch.setenv("CRAB_WL_MANAGER", "local")
    output_dir = tmp_path / "system" / "job"
    runner = ExperimentRunner(
        exp_name="exp",
        config={
            "apps": {"0": {"path": str(wrapper), "collect": True}},
            "local_options": {"minruns": "5", "maxruns": "5"},
        },
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(output_dir),
        logger=MagicMock(),
    )
    runner.setup()
    return runner


def _registry_rows(tmp_path: Path) -> list[dict]:
    with open(tmp_path / "system" / "metadata.csv") as f:
        return list(csv.DictReader(f))


def test_runs_completed_before_a_launch_error_are_saved_and_the_experiment_is_failed(
    tmp_path: Path, monkeypatch
) -> None:
    wrapper = tmp_path / "w.py"
    _wrapper_failing_at_launch(wrapper, tmp_path / "count", failing_run=3)
    runner = _runner(tmp_path, monkeypatch, wrapper)

    with pytest.raises(FileNotFoundError, match="no such binary"):
        runner.execute(str(tmp_path / "system" / "job"))

    with open(Path(runner.exp_dir) / "data_app_0.csv") as f:
        rows = list(csv.DictReader(f))
    assert [(r["run_id"], float(r["0_v_x"])) for r in rows] == [("1", 1.0), ("2", 2.0)]
    (row,) = _registry_rows(tmp_path)
    assert (row["status"], row["total_runs"], row["failed_runs"]) == ("FAILED", "3", "1")


def test_a_launch_error_in_the_first_run_writes_no_data_but_a_failed_registry_row(
    tmp_path: Path, monkeypatch
) -> None:
    wrapper = tmp_path / "w.py"
    _wrapper_failing_at_launch(wrapper, tmp_path / "count", failing_run=1)
    runner = _runner(tmp_path, monkeypatch, wrapper)

    with pytest.raises(FileNotFoundError, match="no such binary"):
        runner.execute(str(tmp_path / "system" / "job"))

    assert not (Path(runner.exp_dir) / "data_app_0.csv").exists()
    (row,) = _registry_rows(tmp_path)
    assert (row["status"], row["total_runs"], row["failed_runs"]) == ("FAILED", "1", "1")
