"""The TUI controller hands the selected preset's execution settings to the engine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from crab.tui.controller import TUIController
from crab.tui.widgets.environment_settings import build_saved_preset
from settings_fixtures import LOCAL_DIRECT


def _run(selected_preset: str) -> tuple[MagicMock, MagicMock]:
    controller = TUIController(log_callback=lambda _line: None)
    preset_config = {"env": {}, "sbatch": [], "header": [], "settings": LOCAL_DIRECT}
    with (
        patch(
            "crab.tui.controller.load_environment_config", return_value=preset_config
        ) as mock_load,
        patch("crab.tui.controller.memory.get_all_receipts", return_value={}),
        patch("crab.tui.controller.Engine") as mock_engine,
    ):
        controller._execute_benchmark_logic({"global_options": {}}, {}, selected_preset)
    return mock_load, mock_engine


def test_engine_run_receives_the_preset_settings() -> None:
    mock_load, mock_engine = _run("lab")
    mock_load.assert_called_once_with("lab")
    assert mock_engine.return_value.run.call_args.kwargs["settings"] is LOCAL_DIRECT


def test_saved_preset_carries_the_loaded_presets_execution_fields() -> None:
    loaded = {
        "description": "Lab",
        "scheduler": "slurm",
        "launcher": "mpirun",
        "launchers": {"mpirun": {"command": "/opt/mpirun", "flags": ["--map-by", "node"]}},
        "env": {"A": "1"},
    }
    state = {"env": {"B": "2"}, "sbatch": ["--exclusive"], "header": ["module load x"]}
    saved = build_saved_preset(loaded, state)
    assert saved == {
        "scheduler": "slurm",
        "launcher": "mpirun",
        "launchers": {"mpirun": {"command": "/opt/mpirun", "flags": ["--map-by", "node"]}},
        **state,
    }


def test_saved_preset_keeps_local_fields() -> None:
    loaded = {"scheduler": "local", "launcher": "direct", "hosts": ["a:4"], "exclusive": False}
    saved = build_saved_preset(loaded, {"env": {}, "sbatch": [], "header": []})
    assert saved["scheduler"] == "local"
    assert saved["hosts"] == ["a:4"]
    assert saved["exclusive"] is False


def test_saved_preset_defaults_to_slurm_without_a_loaded_preset() -> None:
    state = {"env": {}, "sbatch": [], "header": []}
    assert build_saved_preset(None, state) == {"scheduler": "slurm", **state}


def test_every_selected_name_loads_its_settings_from_the_preset() -> None:
    """No special name skips the preset: a saved preset called "Custom" would be loaded too."""
    mock_load, mock_engine = _run("Custom")
    mock_load.assert_called_once_with("Custom")
    assert mock_engine.return_value.run.call_args.kwargs["settings"] is LOCAL_DIRECT
