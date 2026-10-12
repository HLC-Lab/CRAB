"""Apps that share nodes launch with srun's per-app binding and ntasks-per-node (ADR-036).

Before: nothing set `Placement.co_run` or `job_ranks_per_host`, so every srun line was the plain
`-n <ranks> -N <hosts>` one and co-located apps competed for the same cores.
"""

from __future__ import annotations

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
_NODES = ["n0", "n1"]


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


@pytest.fixture(autouse=True)
def _no_slurm_job_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLURM_NTASKS_PER_NODE", raising=False)


def _lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_options: list[dict[str, Any]],
    allocation: dict[str, Any],
) -> list[str]:
    """Each app's script launch line, written by the real run_job on the app as setup left it."""
    wrapper = tmp_path / "w.py"
    wrapper.write_text(_WRAPPER)
    apps = {
        str(i): {"path": str(wrapper), "collect": False, **options}
        for i, options in enumerate(app_options)
    }
    runner = ExperimentRunner(
        exp_name="exp",
        config={"apps": apps, "local_options": {"allocation": allocation}},
        global_options={"numnodes": "2", "name": "job", "sbatch_directives": ["--exclusive"]},
        node_list=_NODES,
        output_dir=str(tmp_path / "system" / "job_2026-01-01_00-00-00"),
        logger=MagicMock(),
        settings=slurm_settings(),
    )
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


_SHARED = {"mode": "shared"}


def test_disjoint_co_run_apps_get_exact_binding_and_their_own_ntasks_per_node(
    tmp_path, monkeypatch
):
    first, second = _lines(tmp_path, monkeypatch, [{"ppn": 1}, {"ppn": 4}], _SHARED)
    assert (
        " --exact --mem=0 --cpu-bind=verbose,cores --ntasks-per-node=1 -n 2 -N 2 echo 1" in first
    ), first
    assert (
        " --exact --mem=0 --cpu-bind=verbose,cores --ntasks-per-node=4 -n 8 -N 2 echo 1" in second
    ), second
    assert "--overlap" not in first


def test_shared_cores_co_run_apps_overlap_and_are_not_bound(tmp_path, monkeypatch):
    lines = _lines(
        tmp_path, monkeypatch, [{"ppn": 1}, {"ppn": 4}], {"mode": "shared", "cores": "shared"}
    )
    assert " --overlap --cpu-bind=verbose,none --ntasks-per-node=1 -n 2 -N 2 " in lines[0]
    assert " --overlap --cpu-bind=verbose,none --ntasks-per-node=4 -n 8 -N 2 " in lines[1]


def test_a_co_run_app_with_cpus_is_bound_to_them_under_overlap(tmp_path, monkeypatch):
    first, second = _lines(tmp_path, monkeypatch, [{"cpus": "0-1", "ppn": 2}, {"ppn": 1}], _SHARED)
    assert " --overlap --cpu-bind=verbose,map_cpu:0,1 --ntasks-per-node=2 -n 4 -N 2 " in first
    assert " --exact --mem=0 --cpu-bind=verbose,cores --ntasks-per-node=1 " in second


def test_an_app_alone_on_its_nodes_keeps_the_plain_line(tmp_path, monkeypatch):
    (line,) = _lines(tmp_path, monkeypatch, [{"ppn": 3}], {})
    assert line.endswith(" -n 6 -N 2 echo 1")
    assert "--ntasks-per-node" not in line and "--cpu-bind" not in line


def test_a_lone_app_with_cpus_is_bound_without_overlap(tmp_path, monkeypatch):
    (line,) = _lines(tmp_path, monkeypatch, [{"cpus": "0-1", "ppn": 2}], {})
    assert " --cpu-bind=verbose,map_cpu:0,1 " in line
    assert "--overlap" not in line


def test_a_job_with_other_tasks_per_node_sets_the_apps_ppn(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_NTASKS_PER_NODE", "4")
    (line,) = _lines(tmp_path, monkeypatch, [{"ppn": 1}], {})
    assert line.endswith(" --ntasks-per-node=1 -n 2 -N 2 echo 1")


def test_a_job_with_the_same_tasks_per_node_adds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_NTASKS_PER_NODE", "1")
    (line,) = _lines(tmp_path, monkeypatch, [{"ppn": 1}], {})
    assert line.endswith(" -n 2 -N 2 echo 1")
    assert "--ntasks-per-node" not in line


@pytest.mark.parametrize("value", ["", "0", "-2", "4(x2)", "four", "2.5"])
def test_a_tasks_per_node_that_is_not_a_positive_integer_is_ignored(tmp_path, monkeypatch, value):
    monkeypatch.setenv("SLURM_NTASKS_PER_NODE", value)
    (line,) = _lines(tmp_path, monkeypatch, [{"ppn": 1}], {})
    assert "--ntasks-per-node" not in line
