"""The SbatchMan-path worker (no execution.json, no CRAB_PRESET) refuses `bind` and `cpus` at start.

Without settings the worker has no node topology and cannot see SbatchMan's `exclusive`, so `bind` and
`cpus` cannot work; it says so before the engine launches anything. Paths that have settings
(execution.json, CRAB_PRESET) are unchanged. App keys are read from `experiments.<name>.apps.<id>`
(`core/experiment/apps.py`, `core/execution/placement.py`).
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

BIND = {"gpu": "per-rank"}


def _write_workdir(tmp_path: Path, apps: dict[str, Any], execution: object | None = None) -> None:
    config = {"global_options": {}, "experiments": {"r1_noise": {"apps": apps}}}
    (tmp_path / "config.json").write_text(json.dumps(config))
    if execution is not None:
        (tmp_path / "execution.json").write_text(json.dumps(execution))


@pytest.fixture(autouse=True)
def _no_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRAB_PRESET", raising=False)


def _run_worker(tmp_path: Path) -> tuple[MagicMock, MagicMock]:
    """Run the worker with a fake logger and Engine.run; return (logger, Engine.run mock)."""
    logger = MagicMock()
    with (
        patch("crab.log.get_logger", return_value=logger),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
    ):
        execute_worker(str(tmp_path))
    return logger, mock_run


def _run_refused(tmp_path: Path) -> tuple[str, MagicMock]:
    """Run the worker expecting exit 1; return (the one critical message, Engine.run mock)."""
    logger = MagicMock()
    with (
        patch("crab.log.get_logger", return_value=logger),
        patch("crab.core.engine.Engine.run", return_value={}) as mock_run,
        pytest.raises(SystemExit) as exc,
    ):
        execute_worker(str(tmp_path))
    assert exc.value.code == 1
    logger.critical.assert_called_once()
    return logger.critical.call_args.args[0], mock_run


def test_bind_is_refused_before_the_engine_starts(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"0": {"path": "wrappers/x.py", "bind": BIND}})
    message, mock_run = _run_refused(tmp_path)
    mock_run.assert_not_called()
    assert "bind" in message
    assert "r1_noise" in message
    assert "app 0" in message
    assert "SbatchMan" in message


def test_cpus_is_refused_before_the_engine_starts(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"0": {"path": "wrappers/x.py", "cpus": "0-3"}})
    message, mock_run = _run_refused(tmp_path)
    mock_run.assert_not_called()
    assert "cpus" in message
    assert "r1_noise" in message
    assert "app 0" in message
    assert "SbatchMan" in message


def test_an_app_without_bind_or_cpus_runs(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"0": {"path": "wrappers/x.py"}})
    _, mock_run = _run_worker(tmp_path)
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["settings"] == SLURM_DEFAULT


def test_bind_is_not_refused_when_execution_json_exists(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"0": {"path": "wrappers/x.py", "bind": BIND}}, to_json(LOCAL_DIRECT))
    _, mock_run = _run_worker(tmp_path)
    mock_run.assert_called_once()


def test_bind_is_not_refused_when_crab_preset_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_workdir(tmp_path, {"0": {"path": "wrappers/x.py", "bind": BIND}})
    monkeypatch.setenv("CRAB_PRESET", "lab")
    with patch(
        "crab.cli.orchestrator.load_environment_config",
        return_value={"env": {}, "sbatch": [], "header": [], "settings": SLURM_DEFAULT},
    ):
        _, mock_run = _run_worker(tmp_path)
    mock_run.assert_called_once()


def test_the_message_says_what_to_do(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"0": {"path": "wrappers/x.py", "bind": BIND}})
    message, _ = _run_refused(tmp_path)
    assert "not supported" in message
    assert "Remove" in message
    assert "crab run" in message


def test_every_offender_is_listed_in_one_message_sorted_and_capped(tmp_path: Path) -> None:
    apps = {str(i): {"path": "wrappers/x.py", "cpus": "0-3"} for i in range(7)}
    apps["1"]["bind"] = BIND
    _write_workdir(tmp_path, apps)
    message, _ = _run_refused(tmp_path)
    assert message.count("app ") == 5
    assert "and 3 more" in message
    assert message.index("app 0") < message.index("app 1") < message.index("app 2")


def test_an_empty_bind_or_cpus_is_not_an_offender(tmp_path: Path) -> None:
    _write_workdir(tmp_path, {"0": {"path": "wrappers/x.py", "bind": {}, "cpus": ""}})
    _, mock_run = _run_worker(tmp_path)
    mock_run.assert_called_once()
