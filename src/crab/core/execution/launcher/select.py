"""Picks the launcher a job runs under."""

from __future__ import annotations

from crab.core.execution.launcher.base import Launcher
from crab.core.execution.launcher.direct import DirectLauncher
from crab.core.execution.launcher.mpirun import MpirunLauncher
from crab.core.execution.launcher.srun import SrunLauncher
from crab.core.execution.settings import LauncherSpec


def launcher_for(spec: LauncherSpec) -> Launcher:
    """The launcher a resolved spec describes.

    Raises:
        ValueError: the spec's kind is not `srun`, `mpirun` or `direct`.
    """
    if spec.kind == "srun":
        return SrunLauncher(spec.command, spec.flags)
    if spec.kind == "mpirun":
        return MpirunLauncher(spec.command, spec.flags)
    if spec.kind == "direct":
        return DirectLauncher()
    raise ValueError(f"Unknown launcher kind: {spec.kind!r}.")
