"""`ppn` is set per app, per experiment (`local_options`) and globally; the most specific wins.

Before: the runner read one job-wide `ppn` from `global_options` only, a `ppn` in `local_options`
was ignored and a `ppn` on an app became a stray wrapper attribute.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.core.experiment.runner import ExperimentRunner
from crab.core.process import manager as process_manager
from settings_fixtures import slurm_settings

_WRAPPER = (
    "from crab.wrappers.base import base\n\n"
    "class app(base):\n"
    "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
    "    def run_app(self):\n"
    "        return 'echo 1'\n"
)


class _FakePopen:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.pid = 4242
        self.stdout = MagicMock()
        self.stdout.readline.return_value = b""


@pytest.fixture(autouse=True)
def _forget_registered_processes() -> Iterator[None]:
    """run_job registers every process it starts; drop them so later tests see a clean registry."""
    before = set(process_manager._registry)
    yield
    for pid in set(process_manager._registry) - before:
        process_manager._registry.pop(pid, None)


def _runner(
    tmp_path: Path,
    app_options: list[dict[str, Any]],
    local: dict[str, Any] | None = None,
    global_ppn: int | None = 1,
) -> ExperimentRunner:
    wrapper = tmp_path / "w.py"
    wrapper.write_text(_WRAPPER)
    apps = {
        str(i): {"path": str(wrapper), "collect": False, **options}
        for i, options in enumerate(app_options)
    }
    global_options: dict[str, Any] = {"numnodes": "1", "name": "job"}
    if global_ppn is not None:
        global_options["ppn"] = global_ppn
    return ExperimentRunner(
        exp_name="exp",
        config={"apps": apps, "local_options": {"allocation": {"mode": "shared"}, **(local or {})}},
        global_options=global_options,
        node_list=["n0"],
        output_dir=str(tmp_path / "system" / "job_2026-01-01_00-00-00"),
        logger=MagicMock(),
        settings=slurm_settings(),
    )


def _launch_lines(runner: ExperimentRunner, tmp_path: Path, monkeypatch) -> list[str]:
    """Each app's script launch line, written by the real run_job on the app as setup left it."""
    monkeypatch.setattr(process_manager.subprocess, "Popen", _FakePopen)
    runner.setup()
    lines = []
    for app, spec in zip(runner.apps, runner.launchers, strict=True):
        app.run_dir = str(tmp_path / f"run_app_{app.id_num}")
        Path(app.run_dir).mkdir()
        process_manager.run_job(app, spec, MagicMock())
        script = Path(app.run_dir) / ".wrappers" / f"app_{app.id_num}.sh"
        lines.append(script.read_text().splitlines()[-1])
    return lines


def test_an_app_ppn_sets_the_ranks_per_host_of_that_app_only(tmp_path, monkeypatch):
    runner = _runner(tmp_path, [{"ppn": 4}, {}])
    first, second = _launch_lines(runner, tmp_path, monkeypatch)
    assert " -n 4 -N 1 " in first
    assert " -n 1 -N 1 " in second


def test_local_options_ppn_beats_the_global_one_and_an_app_ppn_beats_both(tmp_path, monkeypatch):
    runner = _runner(tmp_path, [{}, {"ppn": 3}], local={"ppn": 2})
    first, second = _launch_lines(runner, tmp_path, monkeypatch)
    assert " -n 2 -N 1 " in first
    assert " -n 3 -N 1 " in second


def test_without_any_ppn_an_app_runs_one_rank_per_host(tmp_path):
    runner = _runner(tmp_path, [{}], global_ppn=None)
    runner.setup()
    assert runner.apps[0].ppn == 1


def test_a_global_ppn_alone_gives_every_app_that_ppn(tmp_path, monkeypatch):
    runner = _runner(tmp_path, [{}, {}], global_ppn=8)
    lines = _launch_lines(runner, tmp_path, monkeypatch)
    assert all(" -n 8 -N 1 " in line for line in lines)


def test_the_app_ppn_is_an_integer_read_by_the_runner_not_a_raw_wrapper_attribute(tmp_path):
    runner = _runner(tmp_path, [{"ppn": "3"}])
    runner.setup()
    assert runner.apps[0].ppn == 3


def test_the_registry_row_holds_the_experiments_ppn(tmp_path):
    runner = _runner(tmp_path, [{"ppn": 6}], local={"ppn": 2}, global_ppn=1)
    runner._write_to_registry(status="COMPLETED", total_runs=1, failed_runs=0)
    with open(tmp_path / "system" / "metadata.csv", newline="") as f:
        (row,) = list(csv.DictReader(f))
    assert row["ppn"] == "2"


def test_the_registry_row_falls_back_to_the_global_ppn(tmp_path):
    runner = _runner(tmp_path, [{}], global_ppn=4)
    runner._write_to_registry(status="COMPLETED", total_runs=1, failed_runs=0)
    with open(tmp_path / "system" / "metadata.csv", newline="") as f:
        (row,) = list(csv.DictReader(f))
    assert row["ppn"] == "4"


def test_setup_places_each_app_with_its_own_ppn(tmp_path, monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    from settings_fixtures import LOCAL_DIRECT

    runner = _runner(tmp_path, [{"ppn": 2}, {}])
    runner.settings = LOCAL_DIRECT
    runner.setup()
    assert [app.cpu_list for app in runner.apps] == [(0, 1), (2,)]
    assert [app.ppn for app in runner.apps] == [2, 1]


def test_a_ppn_of_an_app_beyond_its_cpus_is_refused_at_setup(tmp_path):
    runner = _runner(tmp_path, [{"cpus": "0-1", "ppn": 4}])
    with pytest.raises(ValueError, match=r"experiment exp: app 0: cpus lists 2 core\(s\)"):
        runner.setup()
