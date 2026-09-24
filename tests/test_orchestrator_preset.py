"""A missing preset must be an error, never a silent fallback.

Since the ``local`` preset skips Slurm (plan 087), silently defaulting to it meant a
forgotten ``-p`` on a cluster ran benchmarks on the login node.
"""

import json
from unittest.mock import patch

import pytest

from crab.cli.orchestrator import execute_orchestrator


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
    preset_config = {"env": {}, "sbatch": [], "header": []}

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
