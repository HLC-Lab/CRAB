"""The ``.env`` preset file is read from CRAB's home, never from the current folder."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from crab.cli.orchestrator import execute_orchestrator
from crab.core.execution.settings import SLURM_DEFAULT

PRESET_CONFIG = {"env": {}, "sbatch": [], "header": [], "settings": SLURM_DEFAULT}


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Return (home, cwd, config path); CRAB home and the cwd are separate empty folders."""
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)  # first, so teardown restores the real cwd
    monkeypatch.setattr("crab.cli.orchestrator.CRAB_ROOT", str(home))
    monkeypatch.delenv("CRAB_PRESET", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"global_options": {}, "experiments": {}}))
    return home, cwd, config_path


def _run(config_path: Path, preset_arg: str | None = None) -> str:
    """Run the orchestrator with the engine faked; return the preset name that was loaded."""
    with (
        patch(
            "crab.cli.orchestrator.load_environment_config", return_value=PRESET_CONFIG
        ) as mock_load,
        patch("crab.setup.memory.get_all_receipts", return_value={}),
        patch("crab.cli.orchestrator.prepare_execution_environment", return_value={}),
        patch("crab.core.engine.Engine.run", return_value={}),
    ):
        execute_orchestrator(str(config_path), preset_arg)
    mock_load.assert_called_once()
    return mock_load.call_args.args[0]


def _expect_no_preset(config_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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


def test_env_file_in_crab_home_selects_the_preset(folders) -> None:
    home, _cwd, config_path = folders
    (home / ".env").write_text("mycluster\n")

    assert _run(config_path) == "mycluster"


def test_env_file_in_the_current_folder_is_ignored(folders, capsys) -> None:
    _home, cwd, config_path = folders
    (cwd / ".env").write_text("fromcwd\n")

    _expect_no_preset(config_path, capsys)


def test_crab_home_env_file_wins_over_the_current_folder_one(folders) -> None:
    home, cwd, config_path = folders
    (home / ".env").write_text("fromhome\n")
    (cwd / ".env").write_text("fromcwd\n")

    assert _run(config_path) == "fromhome"


def test_preset_flag_beats_the_env_file(folders) -> None:
    home, _cwd, config_path = folders
    (home / ".env").write_text("fromhome\n")

    assert _run(config_path, "explicit") == "explicit"


def test_crab_preset_variable_beats_the_env_file(folders, monkeypatch) -> None:
    home, _cwd, config_path = folders
    (home / ".env").write_text("fromhome\n")
    monkeypatch.setenv("CRAB_PRESET", "fromenv")

    assert _run(config_path) == "fromenv"


@pytest.mark.parametrize("content", ["", "  \n\t\n"])
def test_empty_env_file_in_crab_home_is_no_preset(folders, capsys, content) -> None:
    home, _cwd, config_path = folders
    (home / ".env").write_text(content)

    _expect_no_preset(config_path, capsys)
