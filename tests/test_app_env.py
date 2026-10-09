"""`run_job` puts an app's `env` in its script (before the launch line, after the pre-commands)
and, under mpirun, in the `-x` export list. It also hands the app's placed cores and shared-core
flag to the mpirun launcher."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from crab.core.execution.settings import LauncherSpec
from crab.core.process import manager as process_manager

SRUN = LauncherSpec("srun", ("srun",), "", (), ())
DIRECT = LauncherSpec("direct", (), "", (), ())
MPIRUN = LauncherSpec("mpirun", ("/opt/ompi/bin/mpirun",), "openmpi5", (), ())


class _FakePopen:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.pid = 4242
        self.returncode = None
        self.stdout = MagicMock()
        self.stdout.readline.return_value = b""


class _Job:
    def __init__(
        self,
        tmp_path: Path,
        app_env: dict[str, str],
        cpu_list: tuple[int, ...] | None = None,
        shared_cores: bool = False,
    ) -> None:
        self.id_num = 0
        self.node_list = ["n1"]
        self.run_dir = str(tmp_path / "run_1" / "app_0")
        os.makedirs(self.run_dir, exist_ok=True)
        self.app_env = app_env
        self.cpu_list = cpu_list
        self.shared_cores = shared_cores

    def run_app(self) -> str:
        return "./app -x 1"

    def set_process(self, process: Any) -> None:
        self.process = process


@pytest.fixture(autouse=True)
def _fake_popen(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(process_manager.subprocess, "Popen", _FakePopen)
    before = set(process_manager._registry)
    yield
    for pid in set(process_manager._registry) - before:
        process_manager._registry.pop(pid, None)


def _script(job: _Job) -> list[str]:
    return (Path(job.run_dir) / ".wrappers" / "app_0.sh").read_text().splitlines()


def _run(job: _Job, spec: LauncherSpec, pre: list[str] | None = None) -> None:
    with patch.dict(os.environ, {"PATH": "/p"}, clear=True):
        process_manager.run_job(job, spec, 2, MagicMock(), pre)


def test_the_script_exports_the_variable_before_the_launch_line(tmp_path: Path) -> None:
    job = _Job(tmp_path, {"NCCL_IB_HCA": "mlx5_0"})
    _run(job, DIRECT, pre=["module load foo"])
    lines = _script(job)
    assert lines.index("module load foo") < lines.index("export NCCL_IB_HCA=mlx5_0")
    assert lines.index("export NCCL_IB_HCA=mlx5_0") < lines.index("./app -x 1")


def test_a_value_with_spaces_and_quotes_is_quoted(tmp_path: Path) -> None:
    job = _Job(tmp_path, {"MSG": "a b 'c' $HOME"})
    _run(job, DIRECT)
    assert """export MSG='a b '"'"'c'"'"' $HOME'""" in _script(job)


def test_an_mpirun_launch_exports_the_app_variable_with_x_though_the_environment_lacks_it(
    tmp_path: Path,
) -> None:
    job = _Job(tmp_path, {"NCCL_IB_HCA": "mlx5_0"})
    _run(job, MPIRUN)
    lines = _script(job)
    assert "export NCCL_IB_HCA=mlx5_0" in lines
    launch = next(line for line in lines if line.startswith("/opt/ompi/bin/mpirun"))
    assert "-x NCCL_IB_HCA" in launch


@pytest.mark.parametrize("spec", [SRUN, DIRECT], ids=["srun", "direct"])
def test_srun_and_direct_have_the_export_lines_and_no_x(tmp_path: Path, spec: LauncherSpec) -> None:
    job = _Job(tmp_path, {"NCCL_IB_HCA": "mlx5_0"})
    _run(job, spec)
    lines = _script(job)
    assert "export NCCL_IB_HCA=mlx5_0" in lines
    assert not any(" -x " in line and "NCCL" in line for line in lines)


def test_an_app_without_env_adds_no_export_lines(tmp_path: Path) -> None:
    job = _Job(tmp_path, {})
    _run(job, DIRECT)
    assert not any(line.startswith("export ") for line in _script(job))


def test_an_invalid_key_raises_naming_the_app(tmp_path: Path) -> None:
    job = _Job(tmp_path, {"A B": "1"})
    with pytest.raises(ValueError, match=r"app 0.*'A B'"):
        _run(job, DIRECT)


def test_a_job_without_the_placement_attributes_still_runs(tmp_path: Path) -> None:
    job = _Job(tmp_path, {})
    del job.app_env, job.cpu_list, job.shared_cores
    _run(job, MPIRUN)
    launch = next(line for line in _script(job) if line.startswith("/opt/ompi/bin/mpirun"))
    assert launch == "/opt/ompi/bin/mpirun -n 2 --host n1:2 --display bindings ./app -x 1"


def test_mpirun_binds_the_app_to_its_placed_cores(tmp_path: Path) -> None:
    job = _Job(tmp_path, {}, cpu_list=(2, 3))
    _run(job, MPIRUN)
    launch = next(line for line in _script(job) if line.startswith("/opt/ompi/bin/mpirun"))
    assert launch == (
        "/opt/ompi/bin/mpirun -n 2 --host n1:2 --map-by pe-list=2,3:ordered "
        "--display bindings ./app -x 1"
    )


def test_mpirun_does_not_bind_an_app_on_shared_cores_without_a_core_list(tmp_path: Path) -> None:
    job = _Job(tmp_path, {}, shared_cores=True)
    _run(job, MPIRUN)
    launch = next(line for line in _script(job) if line.startswith("/opt/ompi/bin/mpirun"))
    assert launch == (
        "/opt/ompi/bin/mpirun -n 2 --host n1:2 --bind-to none --display bindings ./app -x 1"
    )


def test_placement_marks_the_apps_that_compete_for_shared_cores() -> None:
    from crab.core.execution.placement import place_experiment
    from settings_fixtures import slurm_settings

    allocation = {
        "partitions": {
            "a": {"mode": "shared", "cores": "shared"},
            "b": {"mode": "shared"},
            "c": {"mode": "shared", "cores": "shared"},
        },
        "mode": "linear",
    }
    configs = [{"partition": p} for p in ("a", "a", "b", "b", "c")]
    nodes = [["h0"], ["h0"], ["h1"], ["h1"], ["h2"]]
    placed = place_experiment(
        "e1", list("01234"), configs, nodes, allocation, 4, slurm_settings(), [], {}
    )
    # c's app runs alone on its node: nothing to compete with, so it stays bound by default.
    assert placed.shared_cores == [True, True, False, False, False]
