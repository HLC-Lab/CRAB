"""A local preset launches apps with mpirun on its hosts, with the computed export set.

The expected lines are literals. Popen is faked as in `test_slurm_characterization.py`, so no MPI
and no process starts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from crab.core.execution.settings import from_preset, resolve_launcher
from crab.core.process import manager as process_manager

_ENVIRON = {"PATH": "/p", "CRAB_ROOT": "/c", "UCX_TLS": "rc"}


@pytest.fixture(autouse=True)
def _own_process_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake process must not stay in the real registry, where `stop_all_registered` finds it."""
    monkeypatch.setattr(process_manager, "_registry", {})


class _FakePopen:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.pid = 4242
        self.stdout = MagicMock()
        self.stdout.readline.return_value = b""


class _Job:
    id_num = 0

    def __init__(self, run_dir: Path, node_list: list[str]) -> None:
        self.node_list = node_list
        self.run_dir = str(run_dir)

    def run_app(self) -> str:
        return "./app"

    def set_process(self, process: Any) -> None:
        self.process = process


def test_local_mpirun_script_lists_hosts_and_the_computed_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_manager.subprocess, "Popen", _FakePopen)
    settings = from_preset(
        {
            "scheduler": "local",
            "hosts": ["ws1:4", "ws2:4"],
            "launcher": "mpirun",
            "launchers": {"mpirun": {"dialect": "openmpi5", "export": ["UCX_TLS"]}},
            "env": {"CRAB_IB_DEVICES": "x"},
        },
        "lab",
    )
    spec = resolve_launcher(settings, {}, None)
    with patch.dict(os.environ, _ENVIRON, clear=True):
        process_manager.run_job(_Job(tmp_path, ["ws1", "ws2"]), spec, 2, MagicMock())
    script = (tmp_path / ".wrappers" / "app_0.sh").read_text()
    # CRAB_IB_DEVICES is a preset env key but not set in the launching environment: not exported.
    assert script.splitlines()[-1] == (
        "mpirun -n 4 --host ws1:2,ws2:2 -x UCX_TLS -x PATH -x CRAB_ROOT ./app"
    )


def test_crab_supervisor_fd_is_never_exported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_manager.subprocess, "Popen", _FakePopen)
    settings = from_preset(
        {
            "scheduler": "local",
            "launcher": "mpirun",
            "launchers": {"mpirun": {"dialect": "openmpi5"}},
        },
        "one",
    )
    spec = resolve_launcher(settings, {}, None)
    with patch.dict(os.environ, {"PATH": "/p", "CRAB_SUPERVISOR_FD": "9"}, clear=True):
        process_manager.run_job(_Job(tmp_path, ["localhost"]), spec, 1, MagicMock())
    script = (tmp_path / ".wrappers" / "app_0.sh").read_text()
    assert script.splitlines()[-1] == "mpirun -n 1 --host localhost:1 -x PATH ./app"
