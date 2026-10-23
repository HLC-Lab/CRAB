"""examples/nplb/presets/nplb_h100.json: the NPLB H100 cluster preset users copy into their overrides.

The file is a complete preset (it has a `scheduler`), so the loader takes it as an override layer on
its own. The test needs no crab-wrappers checkout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from crab.cli.presets import resolve_presets
from crab.core.execution.launcher import Placement, dialect, launch_line, launcher_for
from crab.core.execution.settings import ExecutionSettings, from_preset, resolve_launcher

PRESET_FILE = (
    Path(__file__).resolve().parents[1] / "examples" / "nplb" / "presets" / "nplb_h100.json"
)

LAUNCH_LINE = (
    "mpirun -n 1 -H n1 --report-bindings --allow-run-as-root --mca pml ucx --mca btl ^openib ./app"
)


def _resolved() -> dict[str, dict[str, Any]]:
    return resolve_presets([], [(PRESET_FILE, json.loads(PRESET_FILE.read_text()))])


def _settings() -> ExecutionSettings:
    body = dict(_resolved()["nplb_h100"])
    body["env"] = {"CRAB_ROOT": "__CWD__", **body.get("env", {})}
    return from_preset(body, "nplb_h100")


def test_the_file_holds_only_the_nplb_h100_preset() -> None:
    assert list(_resolved()) == ["nplb_h100"]


def test_the_preset_is_a_complete_slurm_mpirun_preset() -> None:
    preset = _resolved()["nplb_h100"]
    assert preset["description"] == "NPLB test cluster, 4 H100 nodes"
    assert preset["scheduler"] == "slurm"
    assert preset["launcher"] == "mpirun"
    assert preset["sbatch"] == ["--partition=h100_4nodes"]


def test_the_preset_parses_as_execution_settings() -> None:
    settings = _settings()
    assert settings.scheduler == "slurm"
    assert settings.launcher == "mpirun"


def test_the_preset_builds_its_launch_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # The dialect is pinned to openmpi4 by the preset; the probe answer only guards against a
    # probe of the Open MPI installed on this machine.
    monkeypatch.setattr(dialect, "probe", lambda command: "openmpi4")
    launcher = launcher_for(resolve_launcher(_settings(), {}, None), {})
    assert launch_line(launcher, Placement(("n1",), 1), "./app") == LAUNCH_LINE
