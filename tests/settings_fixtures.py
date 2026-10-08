"""Execution settings that tests pass to the runner.

A top-level helper module, imported as ``from settings_fixtures import LOCAL_DIRECT`` (pytest's
default "prepend" import mode puts ``tests/`` on sys.path, as for ``signal_guard``).
"""

from __future__ import annotations

from typing import Any

from crab.core.execution.settings import ExecutionSettings, from_preset

# A local scheduler: every app launches directly, with no srun or mpirun around it.
LOCAL_DIRECT: ExecutionSettings = from_preset({"scheduler": "local"}, "test-local")


def slurm_settings(**preset_fields: Any) -> ExecutionSettings:
    """Slurm settings built from extra preset fields, such as ``launchers={...}``."""
    return from_preset({"scheduler": "slurm", **preset_fields}, "test-slurm")
