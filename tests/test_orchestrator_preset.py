"""A missing preset must be an error, never a silent fallback.

Since the ``local`` preset skips Slurm, silently defaulting to it meant a
forgotten ``-p`` on a cluster ran benchmarks on the login node.
"""

import json
from unittest.mock import patch

import pytest

from crab.cli.orchestrator import execute_orchestrator, load_environment_config
from crab.core.execution.settings import SLURM_DEFAULT
from settings_fixtures import LOCAL_DIRECT


def test_no_preset_anywhere_exits_with_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CRAB_PRESET", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"global_options": {}, "experiments": {}}))

    with (
        patch("crab.cli.orchestrator.load_environment_config") as mock_load,
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
        pytest.raises(SystemExit) as exc,
    ):
        execute_orchestrator(str(config_path), None, as_json=True)

    assert exc.value.code == 1
    mock_load.assert_not_called()
    mock_run.assert_not_called()
    assert "No preset selected" in capsys.readouterr().err


def test_crab_preset_env_var_still_selects_the_preset(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAB_PRESET", "somecluster")
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"global_options": {}, "experiments": {}}))
    preset_config = {"env": {}, "sbatch": [], "header": [], "settings": SLURM_DEFAULT}

    with (
        patch(
            "crab.cli.orchestrator.load_environment_config", return_value=preset_config
        ) as mock_load,
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.cli.orchestrator.prepare_execution_environment", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}),
    ):
        execute_orchestrator(str(config_path), None)

    mock_load.assert_called_once_with("somecluster")


def test_orchestrator_passes_the_preset_settings_to_the_engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"global_options": {}, "experiments": {}}))
    preset_config = {"env": {}, "sbatch": [], "header": [], "settings": LOCAL_DIRECT}

    with (
        patch("crab.cli.orchestrator.load_environment_config", return_value=preset_config),
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
    ):
        execute_orchestrator(str(config_path), "lab")

    assert mock_run.call_args.kwargs["settings"] is LOCAL_DIRECT


def test_execution_fields_in_common_are_refused() -> None:
    presets = {
        "_common": {"env": {}, "scheduler": "slurm"},
        "lab": {"scheduler": "slurm"},
    }
    with (
        patch("crab.cli.presets.load_all_presets", return_value=presets),
        pytest.raises(ValueError, match="_common.*scheduler.*each preset"),
    ):
        load_environment_config("lab")
