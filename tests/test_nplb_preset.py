"""examples/nplb/presets/nplb_h100.json: the NPLB H100 cluster preset users copy into their overrides.

The file is a complete preset (it has a `scheduler`), so the loader takes it as an override layer on
its own. The test needs no crab-wrappers checkout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from crab.cli.presets import resolve_presets
from crab.core.execution.launcher import Placement, dialect, launch_line, launcher_for
from crab.core.execution.settings import ExecutionSettings, from_preset, resolve_launcher

PRESET_FILE = (
    Path(__file__).resolve().parents[1] / "examples" / "nplb" / "presets" / "nplb_h100.json"
)

LAUNCH_LINE = (
    "mpirun -n 1 -H n1 --report-bindings --allow-run-as-root --mca pml ucx --mca btl ^openib ./app"
)


def _resolved() -> dict[str, dict[str, Any]]:
    return resolve_presets([], [(PRESET_FILE, json.loads(PRESET_FILE.read_text()))])


def _settings() -> ExecutionSettings:
    body = dict(_resolved()["nplb_h100"])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, "nplb_h100")


def test_the_file_holds_only_the_nplb_h100_preset() -> None:
    assert list(_resolved()) == ["nplb_h100"]


def test_the_preset_is_a_complete_slurm_mpirun_preset() -> None:
    preset = _resolved()["nplb_h100"]
    assert preset["description"] == "NPLB test cluster, 4 H100 nodes"
    assert preset["scheduler"] == "slurm"
    assert preset["launcher"] == "mpirun"
    assert preset["sbatch"] == ["--partition=h100_4nodes"]


def test_the_preset_parses_as_execution_settings() -> None:
    settings = _settings()
    assert settings.scheduler == "slurm"
    assert settings.launcher == "mpirun"


def test_the_preset_builds_its_launch_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # The dialect is pinned to openmpi4 by the preset; the probe answer only guards against a
    # probe of the Open MPI installed on this machine.
    monkeypatch.setattr(dialect, "probe", lambda command: "openmpi4")
    launcher = launcher_for(resolve_launcher(_settings(), {}, None), {})
    assert launch_line(launcher, Placement(("n1",), 1), "./app") == LAUNCH_LINE


GPU_PRESET_FILE = PRESET_FILE.with_name("nplb_h100_gpu.json")
GPU_MPIRUN = "/root/crab/lab/ompi-cuda/bin/mpirun"
GPU_LD_LIBRARY_PATH = (
    "/root/crab/lab/ucx-cuda/lib:/usr/local/cuda-12.3/targets/x86_64-linux/lib"
    ":/root/crab/lab/ompi-cuda/lib:$LD_LIBRARY_PATH"
)


def _gpu_resolved() -> dict[str, dict[str, Any]]:
    return resolve_presets([], [(GPU_PRESET_FILE, json.loads(GPU_PRESET_FILE.read_text()))])


def _gpu_settings() -> ExecutionSettings:
    body = dict(_gpu_resolved()["nplb_h100_gpu"])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, "nplb_h100_gpu")


def test_the_gpu_file_holds_only_the_nplb_h100_gpu_preset() -> None:
    assert list(_gpu_resolved()) == ["nplb_h100_gpu"]


def test_the_gpu_preset_is_the_base_with_another_mpirun() -> None:
    preset = _gpu_resolved()["nplb_h100_gpu"]
    base = _resolved()["nplb_h100"]
    assert preset["description"] == (
        "NPLB test cluster, 4 H100 nodes, CUDA-aware Open MPI and UCX (GPU memory)"
    )
    assert preset["scheduler"] == base["scheduler"] == "slurm"
    assert preset["launcher"] == base["launcher"] == "mpirun"
    assert preset["sbatch"] == base["sbatch"] == ["--partition=h100_4nodes"]
    mpirun = preset["launchers"]["mpirun"]
    assert mpirun["dialect"] == base["launchers"]["mpirun"]["dialect"]
    assert mpirun["flags"] == base["launchers"]["mpirun"]["flags"]


def test_the_gpu_preset_names_the_cuda_aware_mpirun_by_absolute_path() -> None:
    command = _gpu_resolved()["nplb_h100_gpu"]["launchers"]["mpirun"]["command"]
    assert command == GPU_MPIRUN
    assert Path(command).is_absolute()


def test_the_gpu_preset_puts_the_cuda_aware_libraries_first_on_the_library_path() -> None:
    assert _gpu_resolved()["nplb_h100_gpu"]["env"]["LD_LIBRARY_PATH"] == GPU_LD_LIBRARY_PATH


def test_the_gpu_preset_builds_its_launch_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dialect, "probe", lambda command: "openmpi4")
    launcher = launcher_for(resolve_launcher(_gpu_settings(), {}, None), {})
    assert launch_line(launcher, Placement(("n1",), 1), "./app") == LAUNCH_LINE.replace(
        "mpirun", GPU_MPIRUN, 1
    )


def test_the_gpu_preset_forwards_the_library_path_to_the_ranks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `-x` names only variables set where the job starts (settings.py names LD_LIBRARY_PATH).
    monkeypatch.setattr(dialect, "probe", lambda command: "openmpi4")
    environ = {"LD_LIBRARY_PATH": "/usr/lib"}
    launcher = launcher_for(resolve_launcher(_gpu_settings(), {}, None), environ)
    line = launch_line(launcher, Placement(("n1",), 1), "./app")
    assert line == LAUNCH_LINE.replace(
        "mpirun -n 1 -H n1", f"{GPU_MPIRUN} -n 1 -H n1 -x LD_LIBRARY_PATH"
    )
