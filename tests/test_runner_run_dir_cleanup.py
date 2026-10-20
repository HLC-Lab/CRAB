"""Empty app run directories are removed only after the run's outputs are parsed. Before, the
cleanup in ExperimentRunner.execute() (core/experiment/runner.py) ran before collect_run, so a
wrapper whose read_data used self.run_dir found it deleted and the run failed."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT


def _wrapper(path: Path, read_data_body: str) -> None:
    """An app that prints 7 (writing no files) and parses it with `read_data_body`."""
    path.write_text(
        "import os\n"
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        "        return 'echo 7'\n\n"
        "    def read_data(self):\n"
        f"{read_data_body}"
    )


def _run(tmp_path: Path, monkeypatch, wrapper: Path) -> ExperimentRunner:
    output_dir = tmp_path / "system" / "job"
    runner = ExperimentRunner(
        exp_name="exp",
        config={
            "apps": {"0": {"path": str(wrapper), "collect": True}},
            "local_options": {"minruns": "1", "maxruns": "1"},
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
    return runner


def _registry_row(tmp_path: Path) -> dict:
    with open(tmp_path / "system" / "metadata.csv") as f:
        (row,) = list(csv.DictReader(f))
    return row


def test_read_data_can_use_its_run_dir_even_when_the_app_wrote_nothing_there(
    tmp_path: Path, monkeypatch
) -> None:
    wrapper = tmp_path / "w.py"
    _wrapper(
        wrapper,
        "        scratch = os.path.join(self.run_dir, 'scratch.txt')\n"
        "        with open(scratch, 'w') as f:\n"
        "            f.write(self.stdout)\n"
        "        with open(scratch) as f:\n"
        "            return [[float(f.read())]]\n",
    )
    runner = _run(tmp_path, monkeypatch, wrapper)

    assert _registry_row(tmp_path)["status"] == "COMPLETED"
    with open(Path(runner.exp_dir) / "data_app_0.csv") as f:
        rows = list(csv.DictReader(f))
    assert [(r["run_id"], float(r["0_v_x"])) for r in rows] == [("1", 7.0)]


def test_an_empty_run_dir_is_still_removed_after_the_run(tmp_path: Path, monkeypatch) -> None:
    wrapper = tmp_path / "w.py"
    _wrapper(wrapper, "        return [[float(self.stdout)]]\n")
    runner = _run(tmp_path, monkeypatch, wrapper)

    assert _registry_row(tmp_path)["status"] == "COMPLETED"
    assert not (Path(runner.exp_dir) / "run_1" / "app_0").exists()
    assert not (Path(runner.exp_dir) / "run_1").exists()
