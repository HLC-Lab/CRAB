"""An app contributes data to run N only from what it produced in run N. Before, the runner
(core/experiment/runner.py execute()) kept each app's `process`/`stdout` from the previous run,
so an app that did not run in run N (hard timeout) or whose output could not be read was
collected again under run N with the old output (collect_run gates on process.returncode)."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT


def _wrapper(path: Path, command: str) -> None:
    path.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        f"        return {command!r}\n\n"
        "    def read_data(self):\n"
        "        return [[float(self.stdout)]]\n"
    )


def _first_run_only(marker: Path, first: str, later: str) -> str:
    """A shell command that runs `first` in run 1 and `later` in every run after it."""
    return f"sh -c 'if [ -e {marker} ]; then {later}; else touch {marker}; {first}; fi'"


def _run(tmp_path: Path, monkeypatch, apps: dict, local_options: dict) -> ExperimentRunner:
    output_dir = tmp_path / "system" / "job"
    runner = ExperimentRunner(
        exp_name="exp",
        config={"apps": apps, "local_options": local_options},
        global_options={"numnodes": str(len(apps))},
        node_list=[f"n{i}" for i in range(len(apps))],  # the local launcher ignores node names
        output_dir=str(output_dir),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()
    runner.execute(str(output_dir))
    return runner


def _series(runner: ExperimentRunner, app_id: int) -> tuple[list, list]:
    (container,) = [c for c in runner.data_containers if c.app_id == app_id]
    return container.data, container.run_ids


def _registry_row(tmp_path: Path) -> dict:
    with open(tmp_path / "system" / "metadata.csv") as f:
        (row,) = list(csv.DictReader(f))
    return row


def test_apps_that_did_not_run_after_a_hard_timeout_get_no_data_for_that_run(
    tmp_path: Path, monkeypatch
) -> None:
    # App 0 hangs in run 2 until the experiment timeout kills it; app 1 starts only after
    # app 0 finishes, so it never runs in run 2.
    _wrapper(tmp_path / "hang.py", _first_run_only(tmp_path / "ran", "echo 1", "sleep 30"))
    _wrapper(tmp_path / "after.py", "echo 7")
    runner = _run(
        tmp_path,
        monkeypatch,
        apps={
            "0": {"path": str(tmp_path / "hang.py"), "collect": True},
            "1": {"path": str(tmp_path / "after.py"), "collect": True, "start": "s0"},
        },
        local_options={"minruns": "3", "maxruns": "3", "timeout": "2"},
    )

    assert _series(runner, 0) == ([1.0], [1])
    assert _series(runner, 1) == ([7.0], [1])


def test_unreadable_output_fails_the_run_and_never_reuses_the_previous_output(
    tmp_path: Path, monkeypatch
) -> None:
    # Run 2 prints bytes that are not UTF-8, so the app's output cannot be read.
    _wrapper(tmp_path / "w.py", _first_run_only(tmp_path / "ran", "echo 1", 'printf "\\377\\n"'))
    runner = _run(
        tmp_path,
        monkeypatch,
        apps={"0": {"path": str(tmp_path / "w.py"), "collect": True}},
        local_options={"minruns": "2", "maxruns": "2"},
    )

    assert _series(runner, 0) == ([1.0], [1])
    row = _registry_row(tmp_path)
    assert (row["status"], row["total_runs"], row["failed_runs"]) == ("FAILED", "2", "1")
