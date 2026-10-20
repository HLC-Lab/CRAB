"""Where `crab worker` gets its execution settings from (ADR-033, "How the worker finds its settings").

Order: `execution.json` in the work dir, else the preset named by `CRAB_PRESET`, else the Slurm
default. The worker logs the source, and a dropped `CRAB_*` launch key in its environment is fatal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from crab.cli.orchestrator import execute_worker
from crab.core.execution.settings import SLURM_DEFAULT, to_json
from settings_fixtures import LOCAL_DIRECT


def _write_workdir(
    tmp_path: Path, environment: dict[str, Any] | None, execution: object | None = None
) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"global_options": {}, "experiments": {}}))
    if environment is not None:
        (tmp_path / "environment.json").write_text(json.dumps(environment))
    if execution is not None:
        (tmp_path / "execution.json").write_text(json.dumps(execution))


def _run_worker(tmp_path: Path) -> tuple[MagicMock, MagicMock]:
    """Run the worker with a fake logger and Engine.run; return (logger, Engine.run mock)."""
    logger = MagicMock()
    with (
        patch("crab.log.get_logger", return_value=logger),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
    ):
        execute_worker(str(tmp_path))
    return logger, mock_run


def test_execution_json_is_the_first_source(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"CRAB_PRESET": "lab"}, to_json(LOCAL_DIRECT))
    logger, mock_run = _run_worker(tmp_path)
    assert mock_run.call_args.kwargs["settings"] == LOCAL_DIRECT
    logger.info.assert_any_call("Execution settings from execution.json")


def test_crab_preset_is_the_second_source(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"CRAB_PRESET": "lab"})
    with patch(
        "crab.cli.orchestrator.load_environment_config",
        return_value={"env": {}, "sbatch": [], "header": [], "settings": LOCAL_DIRECT},
    ) as mock_load:
        logger, mock_run = _run_worker(tmp_path)
    mock_load.assert_called_once_with("lab")
    assert mock_run.call_args.kwargs["settings"] == LOCAL_DIRECT
    logger.info.assert_any_call("Execution settings from preset 'lab' (CRAB_PRESET)")


def test_no_source_gives_the_slurm_default(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {})
    logger, mock_run = _run_worker(tmp_path)
    assert mock_run.call_args.kwargs["settings"] == SLURM_DEFAULT
    logger.info.assert_any_call(
        "Execution settings from defaults (Slurm with srun; no execution.json, no CRAB_PRESET)"
    )


def test_dropped_key_in_the_environment_is_fatal(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"CRAB_WL_MANAGER": "slurm"})
    logger = MagicMock()
    with (
        patch("crab.log.get_logger", return_value=logger),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
        pytest.raises(SystemExit) as exc,
    ):
        execute_worker(str(tmp_path))
    assert exc.value.code == 1
    mock_run.assert_not_called()
    message = logger.critical.call_args.args[0]
    assert "CRAB_WL_MANAGER" in message
    assert "launcher" in message


def test_malformed_execution_json_is_fatal(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {}, {"scheduler": "pbs"})
    logger = MagicMock()
    with (
        patch("crab.log.get_logger", return_value=logger),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
        pytest.raises(SystemExit) as exc,
    ):
        execute_worker(str(tmp_path))
    assert exc.value.code == 1
    mock_run.assert_not_called()
    assert "execution.json" in logger.critical.call_args.args[0]


def test_invalid_json_in_execution_json_names_the_file(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {})
    (tmp_path / "execution.json").write_text("{not json")
    logger = MagicMock()
    with (
        patch("crab.log.get_logger", return_value=logger),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
        pytest.raises(SystemExit) as exc,
    ):
        execute_worker(str(tmp_path))
    assert exc.value.code == 1
    mock_run.assert_not_called()
    assert "execution.json: not valid JSON" in logger.critical.call_args.args[0]
