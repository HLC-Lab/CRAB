"""`crab nodes [-p PRESET]`: the Slurm answer without a preset, a local preset's hosts with one.

`gather_nodes` is tested with a recording runner (a local preset must never call sinfo) and the
patched preset loader; the CLI is run in-process like `tests/test_cli_parse_receipts.py`.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import patch

import pytest

from crab.cli import contract
from crab.cli.main import cli_router
from crab.core.execution.settings import ExecutionSettings, from_preset

LOADER = "crab.cli.orchestrator.load_environment_config"
SINFO = {"%R|%a|%D": "gpu|up|2\n", "%N": "n[01-02]\n"}


class Recorder:
    """A sinfo runner that records its calls."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> str:
        self.calls.append(cmd)
        for token, out in SINFO.items():
            if token in cmd:
                return out
        raise AssertionError(f"unexpected command {cmd}")


def _loaded(preset: dict[str, Any], name: str = "lab") -> dict[str, ExecutionSettings]:
    return {"settings": from_preset(preset, name)}


def test_a_local_preset_reports_its_hosts_without_calling_sinfo() -> None:
    runner = Recorder()
    local = {"scheduler": "local", "hosts": ["ws1:32", "ws2:16"]}
    with patch(LOADER, return_value=_loaded(local)):
        data = contract.gather_nodes(runner=runner, preset="lab")

    assert data == {
        "schema": contract.CONTRACT_SCHEMA,
        "available": True,
        "scheduler": "local",
        "partitions": [],
        "nodes": ["ws1", "ws2"],
        "hosts": [{"name": "ws1", "cores": 32}, {"name": "ws2", "cores": 16}],
    }
    assert runner.calls == []


def test_a_bare_localhost_has_null_cores() -> None:
    with patch(LOADER, return_value=_loaded({"scheduler": "local"})):
        data = contract.gather_nodes(runner=Recorder(), preset="local")

    assert data["nodes"] == ["localhost"]
    assert data["hosts"] == [{"name": "localhost", "cores": None}]


def test_a_slurm_preset_gives_the_sinfo_answer_plus_the_scheduler() -> None:
    runner = Recorder()
    with patch(LOADER, return_value=_loaded({"scheduler": "slurm"})):
        data = contract.gather_nodes(runner=runner, preset="lab")

    assert data == {
        "schema": contract.CONTRACT_SCHEMA,
        "available": True,
        "scheduler": "slurm",
        "partitions": [{"name": "gpu", "avail": "up", "nodes": 2}],
        "nodes": ["n[01-02]"],
    }


def test_no_preset_keeps_todays_answer() -> None:
    data = contract.gather_nodes(runner=Recorder())

    assert "scheduler" not in data
    assert data["partitions"] == [{"name": "gpu", "avail": "up", "nodes": 2}]


def test_an_unknown_preset_is_an_error_answer() -> None:
    runner = Recorder()
    data = contract.gather_nodes(runner=runner, preset="no-such-preset")

    assert data["schema"] == contract.CONTRACT_SCHEMA
    assert "no-such-preset" in data["error"]
    assert "available" not in data
    assert runner.calls == []


def test_an_invalid_preset_is_an_error_answer() -> None:
    with patch(LOADER, side_effect=ValueError("preset 'lab': scheduler: bad")):
        data = contract.gather_nodes(runner=Recorder(), preset="lab")

    assert data["error"] == "preset 'lab': scheduler: bad"


def _crab(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["crab", *args])
    cli_router()


def test_the_cli_parses_nodes_with_a_preset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch(LOADER, return_value=_loaded({"scheduler": "local", "hosts": ["ws1:8"]})):
        _crab(monkeypatch, "nodes", "-p", "local", "--json")

    out = json.loads(capsys.readouterr().out)
    assert out["scheduler"] == "local"
    assert out["hosts"] == [{"name": "ws1", "cores": 8}]


def test_the_cli_prints_the_hosts_of_a_local_preset_as_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch(LOADER, return_value=_loaded({"scheduler": "local", "hosts": ["ws1:8"]})):
        _crab(monkeypatch, "nodes", "--preset", "lab")

    assert "ws1" in capsys.readouterr().out


def test_the_cli_fails_on_an_unknown_preset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _crab(monkeypatch, "nodes", "-p", "no-such-preset", "--json")

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "no-such-preset" in json.loads(captured.out)["error"]
    assert "no-such-preset" in captured.err
