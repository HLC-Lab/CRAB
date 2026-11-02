"""A wrapper that declares `default_msg_size` records it as the app's `msg_size` (and in its data
container, hence the CSV column) when the app's args have no `-msgsize`; an explicit flag wins, and
a wrapper without the attribute keeps recording 0."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import LOCAL_DIRECT


def _wrapper(tmp_path: Path, default: int | None) -> Path:
    declaration = "" if default is None else f"    default_msg_size = {default}\n\n"
    path = tmp_path / "sized_app.py"
    path.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        f"{declaration}"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        "        return 'true'\n\n"
        "    def read_data(self):\n"
        "        return []\n"
    )
    return path


def _runner(tmp_path: Path, wrapper: Path, args: str | None) -> ExperimentRunner:
    app: dict = {"path": str(wrapper), "collect": True}
    if args is not None:
        app["args"] = args
    return ExperimentRunner(
        exp_name="exp",
        config={"apps": {"0": app}, "local_options": {"minruns": "1", "maxruns": "1"}},
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(tmp_path / "system" / "job"),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )


def test_the_declared_default_is_recorded_when_args_have_no_msgsize(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, 1024), "-iter 5")
    runner.setup()

    assert runner.apps[0].msg_size == 1024
    assert runner.data_containers[0].msg_size == 1024


def test_the_declared_default_is_recorded_when_there_are_no_args(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, 1024), None)
    runner.setup()

    assert runner.apps[0].msg_size == 1024


def test_an_explicit_msgsize_beats_the_declared_default(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, 1024), "-msgsize 8")
    runner.setup()

    assert runner.apps[0].msg_size == 8
    assert runner.data_containers[0].msg_size == 8


def test_a_wrapper_without_the_attribute_records_zero(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _wrapper(tmp_path, None), "-iter 5")
    runner.setup()

    assert runner.apps[0].msg_size == 0
    assert runner.data_containers[0].msg_size == 0
