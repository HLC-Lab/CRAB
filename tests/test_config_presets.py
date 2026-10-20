"""Regression coverage for the real shipped `src/crab/presets.json` (not a fixture).

Every shipped preset must parse as execution settings (no dropped `CRAB_*` launch key left in
`env`) and build the launch line it built before the settings existed.
"""

import json
from pathlib import Path

import pytest

from crab import paths
from crab.cli.presets import load_all_presets
from crab.core.execution.launcher import Placement, dialect, launch_line, launcher_for
from crab.core.execution.settings import ExecutionSettings, from_preset, resolve_launcher

_PRESETS_PATH = paths.shipped_presets()
_NOT_EXECUTION_PRESETS = ("_common", "example_preset")
_SLIMFLY_MPIRUN = "YOUR_OPENMPI_DIR/bin/mpirun"

_PROBED = {"local": "openmpi5"}
# The local preset runs on its own host; the others are given a node name.
_NODE = {"local": "localhost"}

_LAUNCH_LINES = {
    "alps": "srun --export=ALL --nodelist n1 --cpu-bind=map_cpu=1,73,145,217 -n 1 -N 1 ./app",
    "cluster_di": "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app",
    "lumi": "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app",
    "haicgu": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
    "leonardo": "srun --export=ALL --nodelist n1 --cpu-bind=socket -n 1 -N 1 ./app",
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
def test_shipped_preset_builds_its_launch_line(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
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
# Layers: shipped, then the user file, then the checkout's local/presets.json
# --------------------------------------------------------------------------- #
_LAYER_SHIPPED = {
    "_common": {"env": {"A": "shipped", "B": "shipped"}, "sbatch": ["--exclusive"]},
    "leonardo": {"description": "shipped", "sbatch": ["--account=PLACEHOLDER"]},
}


@pytest.fixture
def layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A root with a fixture shipped file; the user file is `tmp_path/user.json` (not yet written)."""
    shipped = tmp_path / "shipped.json"
    shipped.write_text(json.dumps(_LAYER_SHIPPED))
    monkeypatch.setattr(paths, "shipped_presets", lambda: shipped)
    monkeypatch.setenv("CRAB_PATH_PRESETS", str(tmp_path / "user.json"))
    (tmp_path / "root").mkdir()
    return tmp_path / "root"


def _write_user(layers: Path, body: dict | str) -> Path:
    path = layers.parent / "user.json"
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path


def _write_local(layers: Path, body: dict) -> None:
    (layers / "local").mkdir()
    (layers / "local" / "presets.json").write_text(json.dumps(body))


def test_a_user_file_adds_a_preset(layers: Path) -> None:
    _write_user(layers, {"mine": {"description": "user"}})
    presets = load_all_presets(layers)
    assert presets["mine"] == {"description": "user"}
    assert presets["leonardo"] == _LAYER_SHIPPED["leonardo"]


def test_a_user_file_replaces_a_shipped_preset_as_a_whole(layers: Path) -> None:
    mine = {"sbatch": ["--account=REAL"]}
    _write_user(layers, {"leonardo": mine})
    assert load_all_presets(layers)["leonardo"] == mine


def test_user_common_env_merges_key_by_key_over_shipped(layers: Path) -> None:
    _write_user(layers, {"_common": {"env": {"B": "user", "C": "user"}}})
    common = load_all_presets(layers)["_common"]
    assert common["env"] == {"A": "shipped", "B": "user", "C": "user"}
    assert common["sbatch"] == ["--exclusive"]


def test_user_common_keys_other_than_env_replace(layers: Path) -> None:
    _write_user(layers, {"_common": {"sbatch": ["--time=1"]}})
    common = load_all_presets(layers)["_common"]
    assert common["sbatch"] == ["--time=1"]
    assert common["env"] == {"A": "shipped", "B": "shipped"}


def test_local_wins_over_user(layers: Path) -> None:
    _write_user(layers, {"leonardo": {"description": "user"}, "_common": {"env": {"A": "user"}}})
    _write_local(layers, {"leonardo": {"description": "local"}, "_common": {"env": {"B": "local"}}})
    presets = load_all_presets(layers)
    assert presets["leonardo"] == {"description": "local"}
    assert presets["_common"]["env"] == {"A": "user", "B": "local"}


def test_a_missing_user_file_is_fine(layers: Path) -> None:
    assert load_all_presets(layers) == _LAYER_SHIPPED


def test_bad_json_in_the_user_file_names_that_file(layers: Path) -> None:
    user = _write_user(layers, "{not json")
    with pytest.raises(ValueError, match="not valid JSON") as exc:
        load_all_presets(layers)
    assert str(user) in str(exc.value)


def test_not_found_message_names_the_three_locations(layers: Path, tmp_path: Path) -> None:
    from crab.cli.orchestrator import load_environment_config

    with pytest.raises(KeyError) as exc:
        load_environment_config("nope", layers)
    message = str(exc.value)
    assert str(tmp_path / "shipped.json") in message
    assert str(tmp_path / "user.json") in message
    assert str(layers / "local" / "presets.json") in message


def test_the_user_presets_path_is_isolated_from_the_real_home(tmp_path: Path) -> None:
    # The autouse conftest fixture: no test reads a developer's real user presets file.
    assert not paths.user_presets().exists()
    assert tmp_path in paths.user_presets().parents
