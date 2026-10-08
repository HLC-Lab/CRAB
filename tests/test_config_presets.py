"""Regression coverage for the real repo config/presets.json (not a fixture).

Every shipped preset must parse as execution settings (no dropped `CRAB_*` launch key left in
`env`) and build the launch line it built before the settings existed.
"""

import json
from pathlib import Path

import pytest

from crab.core.execution.launcher import Placement, launch_line, launcher_for
from crab.core.execution.settings import ExecutionSettings, from_preset, resolve_launcher

_PRESETS_PATH = Path(__file__).resolve().parents[1] / "config" / "presets.json"
_NOT_EXECUTION_PRESETS = ("_common", "example_preset")
_SLIMFLY_MPIRUN = "/scratch/2/t2hx/dep/openmpi/bin/mpirun"

_LAUNCH_LINES = {
    "alps": "srun --export=ALL --nodelist n1 --cpu-bind=map_cpu=1,73,145,217 -n 1 -N 1 ./app",
    "cluster_di": "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app",
    "lumi": "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app",
    "haicgu": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
    "leonardo": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
    "cresco8": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
    "nanjing": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
    "slimfly": (
        f"{_SLIMFLY_MPIRUN} -mca plm_rsh_no_tree_spawn 1 --map-by node -mca btl openib,self,sm "
        "-mca btl_openib_if_include mlx4_0 -mca orte_base_help_aggregate 0 --map-by node "
        "-np 1 ./app"
    ),
    "local": "./app",
}


def _presets() -> dict:
    return json.loads(_PRESETS_PATH.read_text())


def _settings(name: str) -> ExecutionSettings:
    presets = _presets()
    body = dict(presets[name])
    body["env"] = {**presets["_common"].get("env", {}), **body.get("env", {})}
    return from_preset(body, name)


_EXECUTION_NAMES = [name for name in _presets() if name not in _NOT_EXECUTION_PRESETS]


@pytest.mark.parametrize("name", _EXECUTION_NAMES)
def test_every_shipped_preset_parses_as_execution_settings(name: str) -> None:
    _settings(name)


def test_the_table_covers_every_shipped_preset() -> None:
    assert sorted(_LAUNCH_LINES) == sorted(_EXECUTION_NAMES)


@pytest.mark.parametrize("name", sorted(_LAUNCH_LINES))
def test_shipped_preset_builds_its_launch_line(name: str) -> None:
    settings = _settings(name)
    launcher = launcher_for(resolve_launcher(settings, {}, None))
    assert launch_line(launcher, Placement(("n1",), 1), "./app") == _LAUNCH_LINES[name]


def test_local_preset_uses_the_local_scheduler_and_no_launcher() -> None:
    settings = _settings("local")
    assert settings.scheduler == "local"
    assert settings.launcher == "direct"


def test_slurm_presets_keep_their_non_launch_env() -> None:
    assert _settings("leonardo").env["CRAB_IB_DEVICES"] == "mlx5_0#mlx5_1#mlx5_2#mlx5_3"
