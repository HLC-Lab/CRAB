"""A wrapper's binary comes from, in order: the app's `binary` key in the config, its receipt,
then its declared `executable` on PATH. When none gives a binary, the experiment fails before
any run with an error naming what was tried (before, `run_app` returned "" and CRAB launched
an empty command)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import crab.setup.memory as mem
from crab.core.experiment.runner import ExperimentRunner
from crab.wrappers.base import MissingBinaryError, base


class _App(base):
    executable = "mybench"

    @property
    def benchmark_id(self) -> str:
        return "mybench"


@pytest.fixture
def receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    local = tmp_path / "local" / "receipts"
    local.mkdir(parents=True)
    monkeypatch.setattr(mem, "ENV_DIR", str(local))
    monkeypatch.setattr(mem, "LEGACY_ENV_DIR", str(tmp_path / "legacy"))
    return local


@pytest.fixture
def on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "mybench"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return exe


def _receipt(d: Path, binary: str) -> None:
    (d / "mybench.json").write_text(json.dumps({"id": "mybench", "binary_path": binary}))


def test_config_binary_wins(receipts: Path, on_path: Path) -> None:
    _receipt(receipts, "/from/receipt")
    app = _App(0, True, "-n 4")
    app.binary = "/from/config"  # what the runner injects for an app's "binary" key
    assert app.run_app() == "/from/config -n 4"


def test_receipt_comes_before_path(receipts: Path, on_path: Path) -> None:
    _receipt(receipts, "/from/receipt")
    assert _App(0, True, "").run_app() == "/from/receipt"


def test_path_lookup_of_the_declared_executable(receipts: Path, on_path: Path) -> None:
    assert _App(0, True, "-x").run_app() == f"{on_path} -x"


def test_nothing_found_names_every_source(receipts: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(MissingBinaryError) as exc:
        _App(0, True, "").run_app()
    message = str(exc.value)
    assert "binary" in message and "receipt 'mybench'" in message and "'mybench' on PATH" in message


def test_base_defaults_for_a_minimal_wrapper() -> None:
    class Minimal(base):
        pass

    app = Minimal(0, False, "")
    assert (app.metadata, app.executable, app.wrapper_api) == ([], "", 1)


def test_the_runner_fails_the_experiment_before_any_run(
    tmp_path: Path, receipts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setenv("CRAB_WL_MANAGER", "local")
    wrapper = tmp_path / "w.py"
    wrapper.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    executable = 'not_installed_anywhere'\n"
        "    metadata = [{'name': 't', 'unit': 's', 'conv': True}]\n"
    )
    runner = ExperimentRunner(
        exp_name="e1",
        config={"apps": {"0": {"path": str(wrapper), "collect": True}}},
        global_options={"numnodes": "1"},
        node_list=["localhost"],
        output_dir=str(tmp_path / "out"),
        logger=MagicMock(),
    )
    with pytest.raises(MissingBinaryError, match="not_installed_anywhere"):
        runner.setup()


def test_a_wrapper_with_its_own_run_app_is_not_checked(
    tmp_path: Path, receipts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_WL_MANAGER", "local")
    wrapper = tmp_path / "w.py"
    wrapper.write_text(
        "from crab.wrappers.base import base\n\n"
        "class app(base):\n"
        "    def run_app(self):\n"
        "        return 'echo 1'\n"
    )
    runner = ExperimentRunner(
        exp_name="e1",
        config={"apps": {"0": {"path": str(wrapper)}}},
        global_options={"numnodes": "1"},
        node_list=["localhost"],
        output_dir=str(tmp_path / "out"),
        logger=MagicMock(),
    )
    runner.setup()
    assert runner.apps[0].run_app() == "echo 1"
