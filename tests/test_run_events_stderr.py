"""An app that fails on its own keeps its stderr in `error_app_<n>.log` (core/experiment/schedule.py).

The stderr pipe is drained by a reader thread (process/manager.py), so the natural-exit branch of
`run_events` must take stderr from that buffer; `communicate()` there returns b"". Real
`ExperimentRunner` on the direct launcher.
"""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT


def _failing_wrapper(path: Path, command: str) -> None:
    path.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        f"        return {command!r}\n\n"
        "    def read_data(self):\n"
        "        return [[1.0]]\n"
    )


def test_a_failed_app_that_exits_on_its_own_keeps_its_stderr_in_the_error_log(
    tmp_path: Path,
) -> None:
    app_path = tmp_path / "failing.py"
    _failing_wrapper(app_path, "sh -c 'echo boom: the reason >&2; echo partial; exit 3'")
    output_dir = tmp_path / "system" / "job"
    runner = ExperimentRunner(
        exp_name="exp",
        config={
            "apps": {"0": {"path": str(app_path), "collect": True}},
            "local_options": {"minruns": "1", "maxruns": "1", "timeout": "30"},
        },
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(output_dir),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )
    runner.setup()
    runner.execute(str(output_dir))

    error_log = (output_dir / "exp" / "error_app_0.log").read_text()
    assert "App 0 exit=3" in error_log
    assert "--- STDERR ---" in error_log
    assert "boom: the reason" in error_log
    assert "--- STDOUT ---" in error_log
    with open(tmp_path / "system" / "metadata.csv") as f:
        (row,) = list(csv.DictReader(f))
    assert row["status"] == "FAILED"
