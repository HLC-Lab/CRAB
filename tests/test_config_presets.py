"""Regression coverage for CRAB's real presets, read from crab-wrappers' `presets/` (not a fixture).

Every preset must parse as execution settings (no dropped `CRAB_*` launch key left in `env`) and
build the launch line it built before the settings existed. The tests skip when no crab-wrappers
checkout with `presets/` is found (see `WRAPPERS_PRESETS` in conftest.py).
"""

from pathlib import Path

import pytest

from conftest import requires_wrappers_presets, wrappers_presets
from crab import paths
from crab.cli import presets
from crab.core.execution.launcher import Placement, dialect, launch_line, launcher_for
from crab.core.execution.settings import ExecutionSettings, from_preset, resolve_launcher

pytestmark = requires_wrappers_presets

_SLIMFLY_MPIRUN = "<YOUR_OPENMPI_DIR>/bin/mpirun"

_PROBED = {"local": "openmpi5"}
# The local preset runs on its own host; the others are given a node name.
_NODE = {"local": "localhost"}

_LAUNCH_LINES = {
    "alps": "srun --export=ALL --nodelist n1 --cpu-bind=map_cpu=1,73,145,217 -n 1 -N 1 ./app",
    "cluster_di": "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app",
    "lumi": "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app",
    "haicgu": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
    "leonardo": "srun --export=ALL --nodelist n1 --cpu-bind=verbose,socket -n 1 -N 1 ./app",
    "cresco8": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
    "nanjing": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
    "slimfly": (
        # binding report always on since C4
        f"{_SLIMFLY_MPIRUN} -n 1 -H n1 --report-bindings -mca plm_rsh_no_tree_spawn 1 --map-by node "
        "-mca btl openib,self,sm -mca btl_openib_if_include mlx4_0 "
        "-mca orte_base_help_aggregate 0 --map-by node ./app"
    ),
    # binding report always on since C4
    "local": "mpirun -n 1 --host localhost:1 --display bindings ./app",
}


def _settings(name: str) -> ExecutionSettings:
    body = dict(wrappers_presets()[name])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, name)


@pytest.mark.parametrize("name", sorted(_LAUNCH_LINES))
def test_every_wrappers_preset_parses_as_execution_settings(name: str) -> None:
    _settings(name)


def test_the_table_covers_every_wrappers_preset() -> None:
    assert sorted(_LAUNCH_LINES) == sorted(wrappers_presets())


@pytest.mark.parametrize("name", sorted(_LAUNCH_LINES))
def test_wrappers_preset_builds_its_launch_line(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # The dialect of slimfly (its mpirun path does not exist on this machine) and of the local
    # preset (the dialect depends on the MPI installed here) is auto: answer the probe with the
    # Open MPI each runs on. An empty environment keeps the lines free of `-x` options.
    monkeypatch.setattr(dialect, "probe", lambda command: _PROBED.get(name, "openmpi4"))
    settings = _settings(name)
    launcher = launcher_for(resolve_launcher(settings, {}, None), {})
    assert (
        launch_line(launcher, Placement((_NODE.get(name, "n1"),), 1), "./app")
        == _LAUNCH_LINES[name]
    )


def test_local_preset_uses_the_local_scheduler_and_mpirun() -> None:
    settings = _settings("local")
    assert settings.scheduler == "local"
    assert settings.launcher == "mpirun"


def test_slurm_presets_keep_their_non_launch_env() -> None:
    assert _settings("leonardo").env["CRAB_IB_DEVICES"] == "mlx5_0#mlx5_1#mlx5_2#mlx5_3"


# --------------------------------------------------------------------------- #
# Isolation: the layer loader itself is covered by tests/test_local_presets.py
# --------------------------------------------------------------------------- #
def test_the_preset_folders_are_isolated_from_the_real_machine(tmp_path: Path) -> None:
    # The autouse conftest fixture: no test reads a developer's real shared or override presets.
    assert presets.shared_preset_folders(tmp_path) == []
    assert not paths.user_presets_dir(tmp_path).exists()
    assert tmp_path in paths.user_presets_dir(tmp_path).parents
