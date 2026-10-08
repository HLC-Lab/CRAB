"""Picks the launcher a job runs under."""

from __future__ import annotations

from typing import cast

from crab.core.execution.launcher import dialect
from crab.core.execution.launcher.base import Launcher
from crab.core.execution.launcher.direct import DirectLauncher
from crab.core.execution.launcher.mpirun import MpirunLauncher
from crab.core.execution.launcher.srun import SrunLauncher
from crab.core.execution.settings import LauncherSpec


def launcher_for(spec: LauncherSpec) -> Launcher:
    """The launcher a resolved spec describes.

    An mpirun spec with dialect `auto` asks `dialect.probe` (cached per command) which MPI the
    command belongs to; any other dialect is used as given.

    Raises:
        ValueError: the spec's kind is not `srun`, `mpirun` or `direct`, or the probe cannot tell
            which MPI the mpirun command belongs to.
    """
    if spec.kind == "srun":
        return SrunLauncher(spec.command, spec.flags)
    if spec.kind == "mpirun":
        # Called through the module so tests can replace `dialect.probe`.
        found = dialect.probe(spec.command[0]) if spec.dialect == "auto" else spec.dialect
        return MpirunLauncher(spec.command, spec.flags, cast(dialect.Dialect, found), spec.export)
    if spec.kind == "direct":
        return DirectLauncher()
    raise ValueError(f"Unknown launcher kind: {spec.kind!r}.")
