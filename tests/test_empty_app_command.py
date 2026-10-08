"""An app whose launch command is empty fails the experiment with an error naming the app and its
wrapper. Before, core/process/manager.py run_job replaced an empty command with
`echo a > /dev/null`, which exits 0, so the run counted as successful with no data.

A wrapper that builds its own command (overrides run_app) but names a binary source (a receipt
via benchmark_id, or an executable) gets the binary pre-flight at setup too, like the QE
wrappers. Before, core/experiment/runner.py setup() skipped every run_app override, so a
missing QE install only failed at launch, after the allocation started."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import crab.setup.memory as mem
from crab.core.experiment.runner import ExperimentRunner
from crab.wrappers.base import MissingBinaryError
from settings_fixtures import LOCAL_DIRECT

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = tmp_path / "local" / "receipts"
    local.mkdir(parents=True)
    monkeypatch.setattr(mem, "ENV_DIR", str(local))
    monkeypatch.setattr(mem, "LEGACY_ENV_DIR", str(tmp_path / "legacy"))


def _runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: dict) -> ExperimentRunner:
    return ExperimentRunner(
        exp_name="exp",
        config={"apps": {"0": app}, "local_options": {"minruns": "1", "maxruns": "1"}},
        global_options={"numnodes": "1"},
        node_list=["n0"],
        output_dir=str(tmp_path / "system" / "job"),
        logger=MagicMock(),
        settings=LOCAL_DIRECT,
    )


@pytest.mark.parametrize("command", ["", None])
def test_an_empty_command_fails_the_experiment_naming_app_and_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str | None
) -> None:
    wrapper = tmp_path / "blank_cmd.py"
    wrapper.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
        "    def run_app(self):\n"
        f"        return {command!r}\n\n"
        "    def read_data(self):\n"
        "        return []\n"
    )
    runner = _runner(tmp_path, monkeypatch, {"path": str(wrapper), "collect": True})
    runner.setup()

    with pytest.raises(RuntimeError, match="empty") as exc:
        runner.execute(str(tmp_path / "system" / "job"))
    assert "app 0" in str(exc.value) and "blank_cmd" in str(exc.value)

    with open(tmp_path / "system" / "metadata.csv") as f:
        (row,) = list(csv.DictReader(f))
    assert row["status"] == "FAILED"


def test_a_qe_wrapper_without_its_install_fails_at_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_receipts: None
) -> None:
    wrapper = REPO / "wrappers" / "quantum-espresso" / "pw" / "v7.py"
    runner = _runner(tmp_path, monkeypatch, {"path": str(wrapper), "collect": True})

    with pytest.raises(MissingBinaryError, match="receipt 'qe-v7'"):
        runner.setup()
